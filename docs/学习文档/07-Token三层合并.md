# 07 Token 三层合并 — Collector / Cache / Middleware 完整链路

> 面试口径：DeerFlow 把子 Agent 的 token 用量"挂"回主 Agent 的 `AIMessage.usage_metadata` 上，靠的是**三层合并**：① `SubagentTokenCollector`（LangChain 回调，子 Agent 内部累加）② `_subagent_usage_cache`（全局 dict，按 `tool_call_id` 索引的带外通道）③ `TokenUsageMiddleware.after_model`（合并到调度它的 AIMessage）。**RunJournal 是另一条独立链路**（细粒度审计），不要和 usage_metadata 混淆。读完这章你能解释"为什么前端看到的某条 AIMessage token = 主 LLM token + 它派出的所有子 Agent token 之和"。

**本章课程目标：**

- 看清楚 token 数据从产生到展示的两条独立链路（消息维度 / 审计维度）
- 理解为什么需要"带外通道"`_subagent_usage_cache`，能不能直接用 ToolMessage 传
- 吃透 `TokenUsageMiddleware._apply` 的反向遍历算法（应对多并发 task）
- 知道 RunJournal 怎么按 caller 分桶（`subagent:xxx` / `lead_agent` / `middleware:xxx`）

**学习建议：** 这章关键是**看清两条链路**。建议读完后画一个表：横轴是数据条目（usage_metadata / RunJournal 桶），纵轴是写入时机（子 Agent 完成 / TokenUsageMiddleware 执行）。两条链路的写入时机相同（子 Agent 完成时同时触发），但消费者不同。

---

## 1、本章导读

DeerFlow 的 token 追踪需求：

1. **消息维度**：前端展示每条消息的 token 用量。一条 AIMessage 派了 5 个 task → 这条消息显示自己 + 5 个子 Agent 累加的 token（= 这条消息"一共烧了多少钱"）。
2. **审计维度**：后端记录每次 LLM 调用的 token，能按 caller（lead_agent / subagent:xxx / middleware:xxx）分桶汇总，用于计费 / 监控 / 告警。

两个需求要两条独立链路，因为：
- 消息维度按 `tool_call_id` 索引（每个 ID 一次性 pop）
- 审计维度按 `source_run_id` 去重 + caller 分桶（每条 LLM 调用都记）

```
       子 Agent 跑了 3 轮 LLM 调用（生成 3 条 records）
                        │
                        ▼
       SubagentTokenCollector.snapshot_records()
                        │
       ┌────────────────┴────────────────┐
       │                                 │
       ▼                                 ▼
   消息维度链路                       审计维度链路
   (usage_metadata)                  (RunJournal)
       │                                 │
   _summarize_usage 聚合                逐条记录 source_run_id
       │                                 │
   _cache_subagent_usage              record_external_llm_usage_records
       │                                 │
   _subagent_usage_cache[tool_call_id]   按 caller 分桶累加
       │                                 │
   TokenUsageMiddleware.after_model       ┌─ subagent_tokens
       │                                 ├─ lead_agent_tokens
   合并到 AIMessage.usage_metadata        └─ middleware_tokens
       │
   前端展示
```

---

## 2、第一层：SubagentTokenCollector

### 2.1 它是什么

`subagents/token_collector.py:1-76`

```python
class SubagentTokenCollector(BaseCallbackHandler):
    """LangChain 回调，在子 Agent 内累加 LLM 用量。"""
    
    def __init__(self, caller: str):
        super().__init__()
        self.caller = caller   # 调用方标识："subagent:general-purpose"
        self._records: list[dict[str, int | str]] = []
        self._counted_run_ids: set[str] = set()
    
    def on_llm_end(self, response, *, run_id, tags=None, **kwargs):
        """每次 LLM 调用结束时被回调。"""
        rid = str(run_id)
        if rid in self._counted_run_ids:
            return  # 已计数，跳过
        
        for generation in response.generations:
            for gen in generation:
                if not hasattr(gen, "message"):
                    continue
                usage = getattr(gen.message, "usage_metadata", None)
                usage_dict = dict(usage) if usage else {}
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk <= 0:
                    total_tk = input_tk + output_tk
                if total_tk <= 0:
                    continue
                
                self._counted_run_ids.add(rid)
                self._records.append({
                    "source_run_id": rid,
                    "caller": self.caller,
                    "input_tokens": input_tk,
                    "output_tokens": output_tk,
                    "total_tokens": total_tk,
                })
                return  # 一个 run_id 只记一次
    
    def snapshot_records(self) -> list[dict[str, int | str]]:
        return list(self._records)  # 浅拷贝
```

### 2.2 它的工作位置

```python
# executor.py:507-516
collector_caller = f"subagent:{self.config.name}"
collector = SubagentTokenCollector(caller=collector_caller)

run_config: RunnableConfig = {
    "recursion_limit": self.config.max_turns,
    "callbacks": [collector],   # ← 注入到子 Agent 的 callbacks
    "tags": [collector_caller],
}
```

子 Agent 的 `agent.astream(state, config=run_config)` 跑起来后，每次 LLM 调用结束都会触发 `collector.on_llm_end`。

### 2.3 三个关键设计

| 设计点 | 代码 | 解决问题 |
| --- | --- | --- |
| 按 `run_id` 去重 | `if rid in self._counted_run_ids: return` | LangChain 可能因为重试重复回调，去重防多记 |
| `total_tokens` 兜底 | `if total_tk <= 0: total_tk = input + output` | 部分 provider 不返回 total，自己算 |
| `total <= 0` 跳过 | `if total_tk <= 0: continue` | 没 token 数据的 generation 不记（如纯路由） |

### 2.4 为什么不直接累加成一个数

设计选择：保留每条 LLM 调用的原始记录（list of dicts），而不是直接 sum 成 `(input, output, total)`。

理由：
- **审计维度需要原始记录**（按 source_run_id 去重）
- **消息维度需要聚合**（用 `_summarize_usage` 即时聚合）
- 保留原始数据 = 两个用途都能满足

---

## 3、第二层：_subagent_usage_cache（带外通道）

### 3.1 数据结构

```python
# task_tool.py:59
_subagent_usage_cache: dict[str, dict[str, int]] = {}
```

- **键**：`tool_call_id`（LangChain 注入的 ID，全局唯一）
- **值**：`{"input_tokens": N, "output_tokens": N, "total_tokens": N}`（已聚合）

### 3.2 写入时机

`task_tool` 的轮询循环里，子 Agent 进入终止状态时：

```python
# task_tool.py:519-553（COMPLETED 路径，FAILED/CANCELLED/TIMED_OUT 同理）
if result.status == SubagentStatus.COMPLETED:
    usage = _summarize_usage(result.token_usage_records)  # 聚合 list → dict
    _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)  # 写入
    _report_subagent_usage(runtime, result)  # 同时上报审计链路
    writer({"type": "task_completed", "task_id": task_id, "usage": usage})
    cleanup_background_task(task_id)
    return f"Task Succeeded. Result: {result.result}"
```

### 3.3 读取时机

`TokenUsageMiddleware.after_model`（下一轮主 LLM 调用后）：

```python
# token_usage_middleware.py:292-300
from deerflow.tools.builtins.task_tool import pop_cached_subagent_usage

idx = len(messages) - 2
while idx >= 0:
    tool_msg = messages[idx]
    if not isinstance(tool_msg, ToolMessage) or not tool_msg.tool_call_id:
        break
    
    subagent_usage = pop_cached_subagent_usage(tool_msg.tool_call_id)  # 取出 + 移除
    ...
```

### 3.4 为什么用全局 dict 不直接走 ToolMessage

**朴素方案：把 usage 字段塞进 ToolMessage**

```python
# 错误示范
return f"Task Succeeded. Result: {result.result}|||usage={usage}"
# 然后在 Middleware 里解析字符串
```

❌ 三个问题：
1. **破坏 LLM 上下文**：子 Agent 的 token 信息出现在 LLM 看到的 ToolMessage 里 → LLM 可能错误地"思考"它
2. **JSON 解析复杂**：要约定分隔符 / escape 字符
3. **类型不安全**：ToolMessage 的 content 是 str，没法存 dict

**正确方案：带外通道（out-of-band channel）**

- 业务数据走主通道（`return result_string` → ToolMessage.content → LLM 看到）
- 元数据走旁路（`_subagent_usage_cache[tool_call_id] = usage` → Middleware 看到）

### 3.5 为什么用 `pop` 不用 `get`

```python
def pop_cached_subagent_usage(tool_call_id) -> dict | None:
    return _subagent_usage_cache.pop(tool_call_id, None)
```

**两个理由：**

1. **防内存泄漏**：每次轮询循环都会 cache，如果不 pop，dict 越来越大
2. **防重复合并**：如果 LangGraph 因为某种原因重新执行 `after_model`（比如 retry），第二次 get 会重复加 token

---

## 4、第三层：TokenUsageMiddleware._apply（核心算法）

### 4.1 完整代码

```python
# token_usage_middleware.py:275-323
class TokenUsageMiddleware(AgentMiddleware):
    def _apply(self, state: AgentState):
        messages = state.get("messages", [])
        if not messages:
            return None
        
        # ── 步骤 1：反向遍历，找连续的 ToolMessage ──
        state_updates: dict[int, AIMessage] = {}
        if len(messages) >= 2:
            from deerflow.tools.builtins.task_tool import pop_cached_subagent_usage
            
            idx = len(messages) - 2  # 倒数第二条（最后一条是新 AIMessage）
            while idx >= 0:
                tool_msg = messages[idx]
                if not isinstance(tool_msg, ToolMessage) or not tool_msg.tool_call_id:
                    break  # 找到非 ToolMessage 就停
                
                subagent_usage = pop_cached_subagent_usage(tool_msg.tool_call_id)
                if subagent_usage:
                    # ── 步骤 2：反向找调度它的 AIMessage ──
                    dispatch_idx = idx - 1
                    while dispatch_idx >= 0:
                        candidate = messages[dispatch_idx]
                        if isinstance(candidate, AIMessage) and _has_tool_call(candidate, tool_msg.tool_call_id):
                            # ── 步骤 3：合并 usage_metadata ──
                            existing_update = state_updates.get(dispatch_idx)
                            prev = existing_update.usage_metadata if existing_update else (candidate.usage_metadata or {})
                            merged = {
                                **prev,
                                "input_tokens": prev.get("input_tokens", 0) + subagent_usage["input_tokens"],
                                "output_tokens": prev.get("output_tokens", 0) + subagent_usage["output_tokens"],
                                "total_tokens": prev.get("total_tokens", 0) + subagent_usage["total_tokens"],
                            }
                            state_updates[dispatch_idx] = candidate.model_copy(update={"usage_metadata": merged})
                            break
                        dispatch_idx -= 1
                idx -= 1
        ...
```

### 4.2 算法要点

#### 要点 1：反向遍历找连续 ToolMessage

```
messages = [
    HumanMessage("..."),
    AIMessage(tool_calls=[t1, t2, t3]),   # 调度了 3 个 task
    ToolMessage(tool_call_id="t1"),         ← 反向第 1 个
    ToolMessage(tool_call_id="t2"),         ← 反向第 2 个
    ToolMessage(tool_call_id="t3"),         ← 反向第 3 个
    AIMessage("最终回答..."),                ← 当前新消息（最后一条）
]
```

从倒数第二条开始反向走，遇到非 ToolMessage 就停 —— 因为更早的 ToolMessage 已经在前几轮处理过。

#### 要点 2：每个 ToolMessage 反向找 AIMessage

```python
dispatch_idx = idx - 1
while dispatch_idx >= 0:
    candidate = messages[dispatch_idx]
    if isinstance(candidate, AIMessage) and _has_tool_call(candidate, tool_msg.tool_call_id):
        ...
        break
    dispatch_idx -= 1
```

为什么不直接用 `idx - 1`？因为多个 task 调用时：
```
AIMessage(tool_calls=[t1, t2, t3])     ← dispatch
ToolMessage(tool_call_id="t1")           ← idx - 3
ToolMessage(tool_call_id="t2")           ← idx - 2
ToolMessage(tool_call_id="t3")           ← idx - 1  当前
AIMessage("...")                         ← idx
```

要往前走多步才能找到包含 `t3` 的 AIMessage。`_has_tool_call(candidate, tool_call_id)` 检查 AIMessage 里是否真的包含这个 id。

#### 要点 3：state_updates 累加

```python
existing_update = state_updates.get(dispatch_idx)
prev = existing_update.usage_metadata if existing_update else (candidate.usage_metadata or {})
merged = {
    **prev,
    "input_tokens": prev.get("input_tokens", 0) + subagent_usage["input_tokens"],
    ...
}
state_updates[dispatch_idx] = candidate.model_copy(update={"usage_metadata": merged})
```

5 个并发 task 都映射到同一条 AIMessage（因为它们都在同一轮 LLM 输出）。每次合并都从 `state_updates` 拿"上一次合并结果"作为基础，而不是原 candidate —— 这样 5 次合并相加。

#### 要点 4：`model_copy` 不修改原对象

```python
state_updates[dispatch_idx] = candidate.model_copy(update={"usage_metadata": merged})
```

Pydantic v2 风格：返回**新对象**，不改原对象。LangGraph 用对象的 `id` 字段判断"这是替换还是新增" —— 同 id = 替换。

---

## 5、第二条链路：RunJournal 审计

### 5.1 写入入口

`task_tool` 在子 Agent 完成时同时上报：

```python
# task_tool.py:521-525
_cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)  # 消息维度
_report_subagent_usage(runtime, result)                                 # 审计维度
```

### 5.2 `_report_subagent_usage` 实现

```python
# task_tool.py:244-268
def _report_subagent_usage(runtime, result):
    if getattr(result, "usage_reported", True):
        return  # 已上报
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)
```

### 5.3 RunJournal 接收

```python
# journal.py:509-553
def record_external_llm_usage_records(self, records):
    if not self._track_tokens:
        return
    for record in records:
        source_id = str(record.get("source_run_id", ""))
        if not source_id:
            continue
        if source_id in self._counted_external_source_ids:
            continue  # ← 按 source_run_id 去重
        
        total_tk = record.get("total_tokens", 0) or 0
        if total_tk <= 0:
            total_tk = (record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0)
        if total_tk <= 0:
            continue
        
        self._counted_external_source_ids.add(source_id)
        self._total_input_tokens += record.get("input_tokens", 0) or 0
        self._total_output_tokens += record.get("output_tokens", 0) or 0
        self._total_tokens += total_tk
        
        # ── 按 caller 分桶 ──
        caller = str(record.get("caller", ""))
        if caller.startswith("subagent:"):
            self._subagent_tokens += total_tk
        elif caller.startswith("middleware:"):
            self._middleware_tokens += total_tk
        else:
            self._lead_agent_tokens += total_tk
        
        self._schedule_progress_flush()
```

### 5.4 三个分桶

| 桶 | tag 前缀 | 来源 |
| --- | --- | --- |
| `_lead_agent_tokens` | 无 / 其他 | 主 Agent 的 LLM 调用 |
| `_subagent_tokens` | `subagent:` | 所有子 Agent（按名细分可看 records） |
| `_middleware_tokens` | `middleware:` | Summarization / Title 等中间件内部 LLM 调用 |

### 5.5 caller 怎么打 tag

主 Agent 通过 `_identify_caller(tags)` 自动识别：

```python
def _identify_caller(self, tags):
    _tags = tags or []
    for tag in _tags:
        if isinstance(tag, str) and (tag.startswith("subagent:") or tag.startswith("middleware:") or tag == "lead_agent"):
            return tag
    return "lead_agent"  # 默认
```

打 tag 的位置：
- 子 Agent：`run_config["tags"] = [f"subagent:{config.name}"]`（executor.py:516）
- 中间件 LLM 调用：`model = model.with_config(tags=["middleware:summarize"])`（agent.py:223）
- 主 Agent：默认 `lead_agent`（不打 tag 也算这个桶）

---

## 6、两条链路对比

| 维度 | 消息维度（usage_metadata） | 审计维度（RunJournal） |
| --- | --- | --- |
| 数据结构 | `dict[tool_call_id, usage]` | `int` 累加 + `set[source_run_id]` 去重 |
| 索引键 | `tool_call_id`（每个一次性） | `source_run_id`（每条 LLM 调用） |
| 去重方式 | `pop`（取出移除） | `set.add`（标记已计） |
| 写入位置 | `_subagent_usage_cache`（全局 dict） | `RunJournal._total_*`（实例属性） |
| 消费者 | 主 Agent 下一轮 LLM 之前的 `TokenUsageMiddleware` | 异步 flush 到持久化层（审计 / 监控） |
| 粒度 | 聚合（一个 task 一条记录） | 细粒度（每次 LLM 一条记录） |
| 用途 | 前端展示每条消息成本 | 后端按 caller 桶汇总用户成本 |

---

## 7、并发场景全推演

**场景：** LLM 一次响应输出 3 个 task 调用（截断后）。

```
T0: LLM 输出 AIMessage(tool_calls=[t1, t2, t3])
        ↓
T1-T2-T3: 三个 task_tool 同时执行（三个独立协程，三个独立 SubagentExecutor）
        │
        ├── T1: SubagentTokenCollector("subagent:general-purpose") 累加 records1
        ├── T2: SubagentTokenCollector("subagent:general-purpose") 累加 records2
        └── T3: SubagentTokenCollector("subagent:general-purpose") 累加 records3
        ↓
T4: 子 Agent 1 完成
        _summarize_usage(records1) → usage1
        _cache_subagent_usage(t1, usage1)        ← _subagent_usage_cache[t1] = usage1
        _report_subagent_usage → RunJournal records1 累加（subagent_tokens += sum1）
        SSE: task_completed
        
T5: 子 Agent 2 完成
        _cache_subagent_usage(t2, usage2)
        _report_subagent_usage → RunJournal records2 累加
        
T6: 子 Agent 3 完成
        _cache_subagent_usage(t3, usage3)
        _report_subagent_usage → RunJournal records3 累加
        ↓
T7: task_tool 全部 return → ToolMessage(t1) / ToolMessage(t2) / ToolMessage(t3) 加到 messages
        ↓
T8: 主 Agent 下一轮 LLM 调用
        ↓
T9: TokenUsageMiddleware.after_model 触发
        反向遍历 ToolMessage：
            - pop(t3) → usage3 → 找到 AIMessage(包含 t3) → 合并
            - pop(t2) → usage2 → 找到 AIMessage(包含 t2，同一条) → 累加合并
            - pop(t1) → usage1 → 找到 AIMessage(包含 t1，同一条) → 累加合并
        最终 AIMessage.usage_metadata = original_usage + usage1 + usage2 + usage3
```

**最终状态：**
- `_subagent_usage_cache`：空（全部 pop 完）
- `AIMessage.usage_metadata`：包含主 LLM 自己的 token + 3 个子 Agent 的 token
- `RunJournal._subagent_tokens`：累加 records1 + records2 + records3 的总和
- `RunJournal._counted_external_source_ids`：包含所有 source_run_id

---

## 8、本章 ❓→💡 问答

### Q1：如果两个 token_collector 用同一个 caller，会冲突吗？

**A：** 不会。每个 SubagentExecutor 实例化时**独立创建一个 collector**：

```python
collector = SubagentTokenCollector(caller=collector_caller)  # 每次 _aexecute 都新建
```

同一个 caller name（比如 `subagent:general-purpose`）的多个子 Agent 各自有自己的 collector。它们的 records 通过 `source_run_id` 区分。

### Q2：`_subagent_usage_cache` 的并发安全吗？

**A：** Python 的 dict 在 CPython 实现里**单个 set / get / pop 操作是原子的**（GIL 保护），所以并发读写不会损坏 dict 结构。但是：

- 多个子 Agent 不会同时写同一个键（key 是 tool_call_id 唯一）
- TokenUsageMiddleware 在主 Agent 的协程里执行（单协程），不会并发 pop

所以**没显式加锁也安全**。如果将来要做跨进程，得换成 Redis 或加 `threading.Lock`。

### Q3：如果 `_report_subagent_usage` 失败，会不会导致 cache 中数据丢失？

**A：** 不会。`_cache_subagent_usage` 和 `_report_subagent_usage` 是**独立调用**：

```python
_cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)  # 先 cache
_report_subagent_usage(runtime, result)  # 后 report
```

cache 已经写入。即使 report 失败：
- 消息维度：不受影响（TokenUsageMiddleware 仍能 pop 到）
- 审计维度：丢失这次记录（warning 日志）

只丢审计数据，不丢前端展示数据。

### Q4：子 Agent 里也有中间件 LLM 调用，怎么算？

**A：** 子 Agent 也有 `Summarization` / `Title` 等可能触发 LLM 的中间件吗？

❌ **不会**。子 Agent 的中间件链 (`build_subagent_runtime_middlewares`) 不包含 Summarization / Title / Memory 等。所以子 Agent 里只有"主 LLM 调用" → 全部归到 `subagent:xxx` 桶。

如果将来给子 Agent 加 Summarization 中间件，要让它的 model 也打 tag `middleware:summarize` —— RunJournal 会归类到 middleware 桶（不会因为是子 Agent 内部就归到 subagent 桶）。

### Q5：为什么 RunJournal 还要去重？collector 不是已经去重过吗？

**A：** 双重防护：
- **collector 内部去重**：同一 collector 实例内的同一 run_id 只记一次（防 LangChain 重试触发多次回调）
- **RunJournal 去重**：跨 collector 的去重（虽然实践中不会发生，但防御性更强）

`source_run_id` 是 LangChain 全局唯一，理论上不会跨 collector 冲突。但代码留了这层防护成本极低（一个 set），收益是**任何来源的重复都拦得住**。

---

## 9、本章总结

**Token 三层链路：**

```
Layer 1: SubagentTokenCollector (BaseCallbackHandler)
   ├─ 子 Agent 内部，按 run_id 去重累加
   └─ 输出：records list

Layer 2: _subagent_usage_cache (全局 dict)
   ├─ 索引：tool_call_id
   ├─ 数据：聚合后的单一 usage dict
   └─ 用途：消息维度（usage_metadata 合并）

Layer 3: TokenUsageMiddleware.after_model
   ├─ 反向遍历 ToolMessage
   ├─ 每个 pop → 反向找 AIMessage → 合并 usage_metadata
   └─ 多 task 累加在同一 AIMessage 上
```

**审计链路（独立）：**

```
SubagentTokenCollector.records
   ↓
RunJournal.record_external_llm_usage_records
   ├─ 按 source_run_id 去重
   └─ 按 caller (subagent:/middleware:/lead_agent) 分桶累加
```

**记忆口诀：**
> 三层合并三个键 —— `run_id` 去重，`tool_call_id` 索引，`caller` 分桶。
> 消息走 cache 走 middleware，审计走 journal 走分桶。

下一章（第 8 章 协作式取消）会专门讲 `threading.Event` + `asyncio.shield` + 延迟清理协程的组合 —— 重点是 `Future.cancel` 为什么不行、`asyncio.CancelledError` 怎么穿透。
