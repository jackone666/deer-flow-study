# 03 task_tool 全流程 — 600 行源码逐段拆解

> 面试口径：`task_tool` 是 DeerFlow 主子通信的**唯一桥梁**。它本质是一个 LangChain `@tool`，从 LLM 视角它是"调一次返回字符串"的普通工具；但内部承担了 **配置解析 → 上下文提取 → 启动后台任务 → 5s 轮询 → SSE 推送 → 异常清理 → Token 上报** 7 大职责。源码 609 行，13 个内部辅助函数 + 1 个主函数。把这一章吃透，主子通信细节就稳了。

**本章课程目标：**

- 把 `task_tool.py` 的 13 个辅助函数按职责分组（缓存 / 终止判断 / 清理 / 上报 / 配置）
- 主函数 `task_tool` 7 大职责按代码顺序逐段解析
- 重点理解 `asyncio.CancelledError` / `asyncio.shield` / `_subagent_usage_cache` 这三个最容易被追问的细节

**学习建议：** 这章源码量大，建议**对照源码读** —— 把 `task_tool.py` 在 IDE 里打开，按本章的小节标题跳行号。每读完一小节，回答一个问题："这段如果删掉，会出现什么 bug？" 答不出来就重读。

---

## 1、本章导读

`task_tool.py` 的所有代码可以按以下结构分类：

```
task_tool.py (609 行)
│
├─ 全局状态 (54-97)
│   _subagent_usage_cache: dict[str, dict[str, int]]
│   _token_usage_cache_enabled / _cache_subagent_usage / pop_cached_subagent_usage
│
├─ 终止判断与清理辅助 (100-186)
│   _is_subagent_terminal
│   _await_subagent_terminal
│   _deferred_cleanup_subagent_task
│   _log_cleanup_failure
│   _schedule_deferred_subagent_cleanup
│
├─ 用量上报辅助 (189-268)
│   _find_usage_recorder
│   _summarize_usage
│   _report_subagent_usage
│
├─ 配置辅助 (271-306)
│   _get_runtime_app_config
│   _merge_skill_allowlists
│
└─ 主函数 task_tool (312-609)
    ① 解析运行时配置
    ② 解析子 Agent 配置
    ③ 提取父 Agent 上下文
    ④ 技能白名单合并
    ⑤ 获取过滤后的工具
    ⑥ 创建 Executor 并启动
    ⑦ 主轮询循环
    └ 异常处理（CancelledError / 其他异常）
```

---

## 2、全局状态：`_subagent_usage_cache`

### 2.1 它是什么

```python
# task_tool.py:59
_subagent_usage_cache: dict[str, dict[str, int]] = {}
```

一个**进程内全局字典**，索引键是 `tool_call_id`，值是聚合后的 token 用量字典 `{"input_tokens": N, "output_tokens": N, "total_tokens": N}`。

### 2.2 为什么要全局缓存

设计目的：**让子 Agent 的 token 出现在调度它的那条 `AIMessage.usage_metadata` 上**。

数据流：

```
子 Agent 完成
  ↓
task_tool 在轮询循环里检测到 COMPLETED
  ↓
_cache_subagent_usage(tool_call_id, usage)  ← 写入 _subagent_usage_cache
  ↓
task_tool return "Task Succeeded..."
  ↓
LangGraph 包装成 ToolMessage 加到 state.messages
  ↓
主 Agent 下一轮 LLM 调用前
  ↓
TokenUsageMiddleware.after_model
  ↓
pop_cached_subagent_usage(tool_call_id)  ← 从 _subagent_usage_cache 取出
  ↓
合并到对应 AIMessage.usage_metadata
```

**关键问题：为什么不直接通过 ToolMessage 传递？**

因为 `ToolMessage` 是 LangChain 标准消息类型，没有 `usage_metadata` 字段，加进去会破坏序列化。用全局 dict 当**带外通道（out-of-band channel）**最干净。

### 2.3 三个工具函数

```python
def _token_usage_cache_enabled(app_config) -> bool:
    """检查 token_usage 配置开关，没开就跳过缓存（节省内存）"""
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))

def _cache_subagent_usage(tool_call_id, usage, *, enabled=True):
    """只在 enabled=True 且 usage 非空时写入"""
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage

def pop_cached_subagent_usage(tool_call_id) -> dict | None:
    """取出后立即移除，每个 tool_call_id 只 pop 一次（防泄漏）"""
    return _subagent_usage_cache.pop(tool_call_id, None)
```

**面试可能追问：**

> Q: 这个全局 dict 是不是单进程瓶颈？多 worker 的话怎么办？
> A: 它是**进程内带外通道**，假设父子 Agent 在同一进程。如果跨进程（比如 task_tool 在 worker A，TokenUsageMiddleware 在 worker B），就需要换成 Redis 或类似的共享存储。当前 DeerFlow 是单 worker 多线程架构，不存在这个问题。

---

## 3、终止判断与清理辅助（task_tool.py:100-186）

### 3.1 `_is_subagent_terminal`

```python
def _is_subagent_terminal(result: Any) -> bool:
    """是否处于可清理的终止状态"""
    return result.status in {COMPLETED, FAILED, CANCELLED, TIMED_OUT} \
        or getattr(result, "completed_at", None) is not None
```

**关键设计：**
- 不只看 `status` 还看 `completed_at` —— 防御性编程，应对状态可能未及时同步的边缘情况
- 4 个终止状态都视为可清理，不再细分（清理逻辑相同）

### 3.2 `_await_subagent_terminal`

```python
async def _await_subagent_terminal(task_id, max_polls):
    """轮询直到子 Agent 终止，或超过 max_polls"""
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None
```

**它什么时候被调用？**

主函数里只在一处：`asyncio.CancelledError` 异常路径中（`task_tool.py:585`）。

**场景：** 主 Agent 被取消（用户点停止 / 上层超时），需要等子 Agent 优雅退出后**最后一次拿 token 用量快照**再返回。

### 3.3 `_deferred_cleanup_subagent_task`

```python
async def _deferred_cleanup_subagent_task(task_id, trace_id, max_polls):
    """对已取消的子 Agent 持续轮询，直到可以安全清理"""
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return  # 已被清理
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            return  # 兜底超时
        await asyncio.sleep(5)
        cleanup_poll_count += 1
```

**为什么需要"延迟清理"？**

场景：主 Agent 轮询超时（超过 `max_poll_count`），`task_tool` 调用了 `request_cancel_background_task(task_id)` 后 return。但**后台子 Agent 的工作线程可能还在跑长工具调用**，cancel_event 要等下一个 chunk 才生效。

如果立即 `cleanup_background_task(task_id)` 把 `_background_tasks[task_id]` 删掉，子 Agent 终止时找不到自己的 result 对象 —— 数据丢失 + 可能空指针。

延迟清理协程的责任：**在后台异步等子 Agent 真的终止再清理**，主流程已经返回不阻塞。

### 3.4 `_schedule_deferred_subagent_cleanup`

```python
def _schedule_deferred_subagent_cleanup(task_id, trace_id, max_polls):
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(...))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, ...))
```

**关键点：**
- `asyncio.create_task` 启动一个独立的 task，不会被当前协程的 cancel 影响
- `add_done_callback` 处理失败日志（如果延迟清理协程异常，记录到日志，不抛上层）

---

## 4、用量上报辅助（task_tool.py:189-268）

### 4.1 `_find_usage_recorder`

```python
def _find_usage_recorder(runtime: Any) -> Any | None:
    """从 runtime config 的 callbacks 中找 RunJournal"""
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    
    # AsyncCallbackManager 不可迭代，先解包
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks or not isinstance(callbacks, list):
        return None
    
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None
```

**关键点：**
- LangChain 的 `callbacks` 在不同上下文有 3 种形态：`None` / `list` / `BaseCallbackManager` —— 都得处理
- 用 **duck typing**（`hasattr`）判断 RunJournal —— 避免循环 import
- `record_external_llm_usage_records` 是 RunJournal 的特征方法

### 4.2 `_summarize_usage`

```python
def _summarize_usage(records: list[dict] | None) -> dict | None:
    """把多条 LLM 调用记录聚合成一条"""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }
```

**为什么有多条记录？**

子 Agent 通常会进行多轮 LLM 调用（Think → Act → Observe → 再 Think...）。`SubagentTokenCollector.on_llm_end` 每次 LLM 完成调用都记一条。聚合时三个字段累加。

### 4.3 `_report_subagent_usage`

```python
def _report_subagent_usage(runtime: Any, result: Any) -> None:
    if getattr(result, "usage_reported", True):
        return  # 已上报，跳过（防重复上报）
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True  # ← 标记已上报
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)
```

**重复上报防护：**

`SubagentResult.usage_reported` 默认 `False`，上报后置 `True`。task_tool 在 4 个终止状态都会调用 `_report_subagent_usage`，正常路径只有一次会真正上报；异常路径（CancelledError）也调用，靠 `usage_reported` 字段去重。

---

## 5、配置辅助（task_tool.py:271-306）

### 5.1 `_get_runtime_app_config`

```python
def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    """从 runtime context 提取 AppConfig（Gateway 注入）"""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None
```

**为什么要从 context 取，不用 `get_app_config()` 全局单例？**

DeerFlow 支持**多租户模式**：Gateway 在每次运行时根据用户身份注入定制化的 AppConfig（不同用户不同模型 / 不同 token 限额 / 不同沙箱配置）。如果用全局单例，用户 A 的请求可能拿到用户 B 的配置。

回退到 `get_app_config()` 只在 context 里没注入时（开发模式 / 单元测试）。

### 5.2 `_merge_skill_allowlists`

```python
def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """父子技能白名单合并：取交集"""
    if parent is None:
        return child  # 父不限制，沿用子配置
    if child is None:
        return list(parent)  # 子继承所有，限制为父白名单
    
    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]  # 取交集
```

**真值表：**

| parent | child | 结果 | 含义 |
| --- | --- | --- | --- |
| None | None | None | 完全不限制 |
| None | ["a", "b"] | ["a", "b"] | 子配置生效 |
| ["a", "b"] | None | ["a", "b"] | 限制为父白名单 |
| ["a", "b", "c"] | ["b", "c", "d"] | ["b", "c"] | 交集 |
| ["a"] | ["b"] | [] | 互不交叉，子无技能可用 |

**设计哲学：父的限制是"绝对边界"，子的限制是"自我约束"，子无法突破父。**

---

## 6、主函数 task_tool 七大职责（task_tool.py:312-609）

### 6.1 函数签名

```python
@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,    # 任务简称（3-5 词），日志/前端展示用
    prompt: str,         # 任务详细描述
    subagent_type: str,  # 子 Agent 类型 ("general-purpose" / "bash" / 自定义)
    tool_call_id: Annotated[str, InjectedToolCallId],  # LangChain 注入的工具调用 ID
) -> str:                # 返回字符串（被 LangGraph 包成 ToolMessage）
```

**关键点：**
- `runtime: Runtime` —— LangGraph 自动注入，包含 state / context / config / metadata
- `tool_call_id: Annotated[..., InjectedToolCallId]` —— LangChain 把当前 tool_call 的 id 注入进来，用作 task_id

### 6.2 职责①：解析运行时配置

```python
runtime_app_config = _get_runtime_app_config(runtime)
cache_token_usage = _token_usage_cache_enabled(runtime_app_config)

available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) \
    if runtime_app_config is not None else get_available_subagent_names()
```

**做了什么：**
1. 从 runtime.context 取 AppConfig（多租户支持）
2. 检查是否启用 token 用量缓存（节省内存）
3. 获取当前可用的子 Agent 类型列表

### 6.3 职责②：解析子 Agent 配置

```python
config = get_subagent_config(subagent_type, app_config=runtime_app_config)
if config is None:
    available = ", ".join(available_subagent_names)
    return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

# bash 子 Agent 安全检查
if subagent_type == "bash":
    host_bash_allowed = is_host_bash_allowed(runtime_app_config)
    if not host_bash_allowed:
        return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"
```

**做了什么：**
1. 从注册表查 SubagentConfig（详见第 5 章 registry.py 解析）
2. 找不到就返回错误字符串（让 LLM 看到错误后能改正）
3. bash 子 Agent 额外安全检查（本地未显式允许 → 拒绝）

### 6.4 职责③：从 runtime 提取父 Agent 上下文

```python
sandbox_state = None    # 沙箱状态
thread_data = None      # 线程运行时数据
thread_id = None        # LangGraph thread_id
parent_model = None     # 父模型名（inherit 用）
trace_id = None         # 分布式追踪 ID
metadata: dict = {}

if runtime is not None:
    sandbox_state = runtime.state.get("sandbox")    # 引用传递
    thread_data = runtime.state.get("thread_data")  # 引用传递
    
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        thread_id = runtime.config.get("configurable", {}).get("thread_id")
    
    metadata = runtime.config.get("metadata", {})
    parent_model = metadata.get("model_name")
    trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]
```

**关键点：**
- **引用传递**：`sandbox` / `thread_data` 是 dict，子 Agent 改了主 Agent 看得见
- **thread_id 双源**：先 context，再 configurable —— 兼容不同启动方式
- **trace_id 自动生成**：上层没传就生成 8 位 UUID，确保日志能关联

### 6.5 职责④：技能白名单合并

```python
overrides: dict = {}
parent_available_skills = metadata.get("available_skills")
if parent_available_skills is not None:
    overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

if overrides:
    config = replace(config, **overrides)
```

**做了什么：**
- 取父 Agent 的可用技能白名单（来自 metadata）
- 与子 Agent 配置中的技能白名单**取交集**（见 5.2 真值表）
- 用 `dataclasses.replace` 创建新的 config，不修改原 config

### 6.6 职责⑤：获取过滤后的工具列表

```python
from deerflow.tools import get_available_tools

parent_tool_groups = metadata.get("tool_groups")
resolved_app_config = runtime_app_config

if config.model == "inherit" and parent_model is None and resolved_app_config is None:
    resolved_app_config = get_app_config()

effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

available_tools_kwargs = {
    "model_name": effective_model,
    "groups": parent_tool_groups,
    "subagent_enabled": False,  # ← 关键！防止递归
}
if resolved_app_config is not None:
    available_tools_kwargs["app_config"] = resolved_app_config
tools = get_available_tools(**available_tools_kwargs)
```

**关键点：**
- **延迟导入** `from deerflow.tools import get_available_tools` —— 避免循环依赖
- **`subagent_enabled=False`** —— 子 Agent 拿到的工具列表里没有 `task` 工具，**这是递归防护的核心**
- **继承 tool_groups** —— 子 Agent 的工具范围不能超过父 Agent

### 6.7 职责⑥：创建 Executor 并启动后台任务

```python
executor_kwargs = {
    "config": config,
    "tools": tools,
    "parent_model": parent_model,
    "sandbox_state": sandbox_state,
    "thread_data": thread_data,
    "thread_id": thread_id,
    "trace_id": trace_id,
}
if resolved_app_config is not None:
    executor_kwargs["app_config"] = resolved_app_config
executor = SubagentExecutor(**executor_kwargs)

# tool_call_id 直接当 task_id（一对一关系）
task_id = executor.execute_async(prompt, task_id=tool_call_id)
```

**关键设计：`task_id = tool_call_id`**

为什么用 tool_call_id？
- 唯一性：LangChain 保证每个 tool_call 的 id 在 trace 内唯一
- 可关联：前端拿到 SSE 事件里的 task_id 就能定位到对应的 AIMessage tool_call
- Token 缓存键：`_subagent_usage_cache[tool_call_id]` 直接复用

### 6.8 职责⑦：主轮询循环（前面 Step 6 已经详细解析）

略，详见第 2 章 §3.6 或 `task_tool.py:475-575`。

### 6.9 异常处理：CancelledError

```python
except asyncio.CancelledError:
    # 通知后台子 Agent 协同停止
    request_cancel_background_task(task_id)
    
    # 用 asyncio.shield 保护等待逻辑
    terminal_result = None
    try:
        terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
    except asyncio.CancelledError:
        pass  # shield 也可能被穿透
    
    # 上报最终用量
    final_result = terminal_result or get_background_task_result(task_id)
    if final_result is not None:
        _report_subagent_usage(runtime, final_result)
    
    # 清理：已终止立即清理，否则调度延迟清理
    if final_result is not None and _is_subagent_terminal(final_result):
        cleanup_background_task(task_id)
    else:
        _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
    
    _subagent_usage_cache.pop(tool_call_id, None)
    raise  # 重新抛出
```

**为什么要 `asyncio.shield`？**

`asyncio.shield` 让被 wrap 的协程**不受外层 cancel 影响**。在这里的目的：
- 主 Agent 已经被 cancel，正在向上抛 `CancelledError`
- 但我们想**抢救一下子 Agent 的 token 数据**才退出
- 用 shield 包住 `_await_subagent_terminal`，给它一个机会跑完
- 如果 shield 也被穿透（外层连续 cancel 两次），就放弃等待

**为什么最后要 `raise`？**

不能吞掉 `CancelledError`，必须重抛 —— LangGraph 框架要靠它知道协程被取消，做后续清理（比如更新 state 标记任务被取消）。

### 6.10 异常处理：其他异常

```python
except Exception:
    _subagent_usage_cache.pop(tool_call_id, None)
    raise
```

**做了什么：**
- 任何未预期异常都清理 usage cache（避免内存泄漏）
- 重新抛出让 LangGraph 的 ToolErrorHandlingMiddleware 捕获 → 转成错误 ToolMessage 给 LLM

---

## 7、本章 ❓→💡 问答

### Q1：`tool_call_id` 是怎么 inject 进来的？

**A：** LangChain 的 `Annotated[str, InjectedToolCallId]` 是一个特殊标记。LangChain 在调用工具前会扫描签名，遇到 `InjectedToolCallId` 注解的参数，自动从当前 tool_call 上下文取 id 注入进去。这是 LangChain 提供的"注入式参数"机制，类似 FastAPI 的 `Depends`。

LLM **不需要**也**不能**为这个参数提供值（参数会被工具描述自动隐藏）。

### Q2：如果两个 task 调用 ID 相同会怎样？

**A：** 不会发生。LangChain 保证同一个 LLM 响应内 tool_call 的 id 唯一（通常是 `call_xxx_{index}` 或 UUID）。但即使理论上发生：
- `_background_tasks[task_id] = result` 会覆盖前一个
- `_subagent_usage_cache[tool_call_id] = usage` 也会覆盖
- 第一个子 Agent 的结果丢失

实际不会发生，所以代码没做去重检查。如果担心可以在 `executor.execute_async` 加 `if task_id in _background_tasks: raise ValueError`。

### Q3：为什么 `_summarize_usage` 不直接做 token 累加，要 `_report` 单独再做一次？

**A：** 两个目的：
- `_summarize_usage` 生成的 `usage` 是**给前端 SSE 事件用的**（一次性快照），写入 `_subagent_usage_cache` 给 TokenUsageMiddleware 取用
- `_report_subagent_usage` 写入 RunJournal 是**逐条 LLM 调用记录**（保留原始记录用于审计）

前者粗粒度（聚合），后者细粒度（逐条）。两套数据不同用途。

### Q4：什么情况下 `_find_usage_recorder` 返回 None？

**A：** 三种情况：
1. **runtime 为 None**（不应发生，防御性）
2. **callbacks 没有 RunJournal**（开发模式没启用追踪）
3. **callbacks 形态异常**（不是 list 也不是 BaseCallbackManager）

返回 None 时 `_report_subagent_usage` 静默跳过 —— 不抛错，不阻塞主流程。代价：token 数据不会写入 RunJournal，但 SSE 推送和 cache 都正常。

---

## 8、本章总结

**`task_tool.py` 的灵魂：**

| 职责 | 核心机制 | 关键防护 |
| --- | --- | --- |
| 配置解析 | `get_subagent_config` + 错误字符串返回 | 找不到子 Agent 类型不抛错 |
| 上下文提取 | `runtime.state.get` 引用传递 | sandbox/thread_data 共享 |
| 启动后台 | `executor.execute_async` 立即返回 task_id | 用 tool_call_id 当 task_id |
| 5s 轮询 | `while True` + `await asyncio.sleep(5)` | 双层超时（线程池 + 轮询） |
| SSE 推送 | `task_started/running/completed/failed/cancelled/timed_out` | 增量推送防重复 |
| Token 上报 | `_cache_subagent_usage` + `_report_subagent_usage` | `usage_reported` 标记防重复 |
| 异常清理 | `asyncio.shield` + 延迟清理协程 | 重抛 CancelledError |

下一章（第 4 章 SubagentExecutor）会深入 `executor.py:474-668` 的 `_aexecute` 主循环 —— 看完那章你就知道 "持久 daemon loop" 到底解决了什么生产级问题。
