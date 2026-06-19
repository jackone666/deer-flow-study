# DeerFlow 主子智能体通信机制深度解析

## 核心架构图

```
用户输入
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│                    Worker (runtime/runs/worker.py)               │
│  agent.astream() → 驱动 LangGraph 图执行                        │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│              Lead Agent (agents/lead_agent/agent.py)             │
│  主智能体 LangGraph Agent                                       │
│  ├── _AgentConfig → 配置 (model_name, subagent_enabled, ...)    │
│  ├── System Prompt (含子智能体委派指令)                          │
│  ├── Tools → [其他工具..., task]                                │
│  ├── Middlewares → [SubagentLimit, TokenUsage, ...]              │
│  └── LLM 调用 → 输出 AIMessage(tool_calls)                      │
└────────────────────────────────────────────────────────────────┘
    │
    │  LLM 决定调用 task(description, prompt, subagent_type)
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│           task_tool (tools/builtins/task_tool.py)                │
│  通信桥梁 — 同步包装异步                                        │
│                                                                  │
│  1. get_subagent_config(subagent_type) → SubagentConfig          │
│  2. get_available_tools(subagent_enabled=False) → 过滤掉 task    │
│  3. 提取共享状态: sandbox_state(引用), thread_data(引用)         │
│  4. SubagentExecutor(config, tools, sandbox_state, thread_data)  │
│  5. executor.execute_async(prompt, task_id)                      │
│     └── ThreadPoolExecutor.submit(_aexecute, prompt, result)     │
│  6. 轮询循环 (每 5 秒):                                          │
│     ├── get_background_task_result(task_id)                      │
│     ├── SSE 事件推送 → 前端实时更新                              │
│     └── await asyncio.sleep(5)                                   │
│  7. 返回 ToolMessage("Task Succeeded. Result:\n...")            │
└────────────────────────────────────────────────────────────────┘
    │
    │  后台线程 (ThreadPoolExecutor, max_workers=3)
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│         SubagentExecutor._aexecute (subagents/executor.py)       │
│  子智能体执行引擎                                                │
│                                                                  │
│  1. try_set_terminal(RUNNING)                                    │
│  2. _build_initial_state(task):                                  │
│     [SystemMessage(子智能体自己的 prompt), HumanMessage(task)]     │
│     + sandbox_state(引用), thread_data(引用)                      │
│  3. _create_agent():                                             │
│     新的 LangGraph Agent (独立 runtime, 独立图, 过滤后的工具)     │
│  4. agent.astream(state, stream_mode="values"):                  │
│     ├── SubagentTokenCollector 收集 token 使用                    │
│     ├── 检查 cancel_event → 协作式取消                           │
│     └── 捕获 AIMessage → result.ai_messages                     │
│  5. _extract_final_result():                                     │
│     从最后一条 AIMessage 提取文本 (string 或 list-of-blocks)      │
│  6. try_set_terminal(COMPLETED, result=final_text)               │
└────────────────────────────────────────────────────────────────┘
```

## 核心数据模型

### SubagentStatus — 生命周期状态 (executor.py)

```
PENDING ──→ RUNNING ──→ COMPLETED
               │
               ├──→ FAILED
               ├──→ CANCELLED (协作式取消)
               └──→ TIMED_OUT (超时)
```

### SubagentResult — 结果容器 (executor.py)

```python
@dataclass
class SubagentResult:
    task_id: str                    # 任务 ID
    trace_id: str                   # 分布式追踪 ID
    status: SubagentStatus          # 当前状态
    result: str | None = None       # 最终输出文本
    error: str | None = None        # 错误信息
    ai_messages: list[dict]         # 所有 AI 消息
    token_usage_records: list       # Token 使用记录
    usage_reported: bool = False    # 去重标志
    cancel_event: threading.Event   # 协作式取消信号
```

### SubagentConfig — 子智能体配置 (config.py)

```python
@dataclass
class SubagentConfig:
    name: str                       # 子智能体名称
    description: str                # 描述
    system_prompt: str | None       # 子智能体自己的 System Prompt
    tools: list[str] | None         # 工具白名单 (None=继承父级)
    disallowed_tools: list[str]     # 工具黑名单 (默认 ["task"])
    skills: list[str] | None        # 技能白名单 (None=继承父级)
    model: str = "inherit"          # 模型 ("inherit"=继承父级)
    max_turns: int = 50             # 最大轮数
    timeout_seconds: int = 900      # 超时时间 (15分钟)
```

### _AgentConfig — 主智能体内部配置 (agent.py)

```python
@dataclass
class _AgentConfig:
    model_name: str = ""
    tool_groups: list[str] | None = None
    subagent_enabled: bool = False
    max_concurrent_subagents: int = 3
    max_turns: int = 50
```

**使用方式**：`make_lead_agent()` 创建 `_AgentConfig` 实例后，从传入的 `agent_config: dict` 参数读取配置值填充：

```python
cfg = _AgentConfig()
cfg.model_name = agent_config.get("model_name", "")
cfg.tool_groups = agent_config.get("tool_groups")
cfg.subagent_enabled = agent_config.get("subagent_enabled", subagent_enabled)
cfg.max_concurrent_subagents = agent_config.get("max_concurrent_subagents", 3)
cfg.max_turns = agent_config.get("max_turns", 50)
```

### ThreadState — 共享状态 Schema (thread_state.py)

```python
class ThreadState(TypedDict):
    messages: Sequence[BaseMessage]    # 对话消息 (主/子独立)
    sandbox: dict                      # 沙盒状态 (引用共享)
    thread_data: dict                  # 线程数据 (引用共享)
    metadata: dict                     # 元数据
    runtime: Runtime                   # 运行时上下文 (主智能体特有)
```

## 配置解析流程 (subagents/registry.py)

```
get_subagent_config(subagent_type, app_config)
    │
    ├── 1. 检查内建子智能体:
    │      "general-purpose" → GENERAL_PURPOSE_CONFIG
    │      "bash" → BASH_CONFIG
    │
    ├── 2. 检查 config.yaml custom_agents:
    │      自定义类型 → _parse_custom_agent_config()
    │
    ├── 3. 检查 config.yaml agents 覆盖:
    │      覆盖设置 → _apply_agent_overrides()
    │
    └── 4. 未找到 → 返回 None (task_tool 报错)
```

### 内建子智能体

**general-purpose** (builtins/general_purpose.py):
```python
SubagentConfig(
    name="general-purpose",
    tools=None,              # 继承父级所有工具
    disallowed_tools=["task"],  # 但不能调用 task
    skills=None,             # 继承父级所有技能
    model="inherit",
    max_turns=50,
    timeout_seconds=900,
)
```

**bash** (builtins/bash_agent.py):
```python
SubagentConfig(
    name="bash",
    tools=["bash"],          # 只有 bash 工具
    disallowed_tools=[],
    skills=None,
    model="inherit",
    max_turns=60,
    timeout_seconds=900,
)
```

## 工具过滤机制 (tools/tools.py)

```python
SUBAGENT_TOOLS = [task_tool]

def get_available_tools(..., subagent_enabled: bool = True):
    builtin_tools = [...]  # 所有工具
    
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)  # 主智能体有 task 工具
    # 子智能体: subagent_enabled=False → 没有 task 工具
```

## 并发控制三层防护

### 1. System Prompt 指令 (prompt.py)
```
**Hard limit: at most {n} `task` calls per response, this is not optional.**
```

### 2. SubagentLimitMiddleware (middlewares/subagent_limit_middleware.py)
```python
class SubagentLimitMiddleware:
    """在 after_model 钩子中截断多余的 task 调用"""
    
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max(2, min(4, max_concurrent))
    
    async def after_model(self, context, result):
        tool_calls = result.get("tool_calls", [])
        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        
        if len(task_indices) > self.max_concurrent:
            indices_to_drop = set(task_indices[self.max_concurrent:])
            result["tool_calls"] = [
                tc for i, tc in enumerate(tool_calls) 
                if i not in indices_to_drop
            ]
        return result
```

### 3. ThreadPoolExecutor (executor.py)
```python
self._scheduler_pool = ThreadPoolExecutor(max_workers=3)
```

## Token 追踪完整链路

```
子智能体执行 → SubagentTokenCollector (LangChain 回调)
    │  收集每次 LLM 调用的 input_tokens + output_tokens
    │
    ▼
result.token_usage_records (SubagentResult 中暂存)
    │
    ▼
task_tool 轮询到 COMPLETED:
    ├── _cache_subagent_usage(task_id, records)
    │   → 存入全局 _subagent_usage_cache
    │
    └── _report_subagent_usage(journal, "subagent:xxx", task_id, records)
        → RunJournal.external_llm_usage["subagent:xxx"] += tokens
            │
            ▼
TokenUsageMiddleware.after_model() (下一轮 LLM 调用前):
    从 _subagent_usage_cache 读取 → 合并到主智能体 usage_metadata
            │
            ▼
    主智能体 AIMessage.usage_metadata 包含子智能体的 token 消耗
    RunJournal 保留按子智能体分类的明细
```

## 取消机制

```
主智能体侧 (task_tool.py):
    request_cancel_background_task(task_id)
        → result.cancel_event.set()  # 设置事件标志

子智能体侧 (executor.py):
    if result.cancel_event.is_set():
        result.try_set_terminal(SubagentStatus.CANCELLED)
        return  # 退出执行循环

超时处理 (task_tool.py 轮询循环):
    if elapsed > config.timeout_seconds:
        request_cancel_background_task(task_id)
        return "Task Timed Out after {timeout}s."
```

## 隐式反向通信：共享状态引用传递

```
task_tool 从主智能体 runtime 提取:
    sandbox_state = runtime.state.get("sandbox")   # dict 引用
    thread_data = runtime.state.get("thread_data")  # dict 引用

传入 SubagentExecutor(..., sandbox_state=sandbox_state, thread_data=thread_data)

子智能体在 _build_initial_state 中:
    state["sandbox"] = self.sandbox_state      # 同一个对象
    state["thread_data"] = self.thread_data     # 同一个对象

子智能体修改 → 主智能体可见 (无需额外通信)
```

## 完整通信序列 (从用户输入到最终回复)

```
Step 1: 用户输入
    Worker.run_agent() → agent.astream(input={messages: [HumanMessage(用户问题)]})

Step 2: 主智能体执行
    LangGraph 图执行:
    ├── before_model 中间件链
    ├── LLM 调用 (含 System Prompt 中的子智能体指令)
    └── after_model 中间件链

Step 3: LLM 决定调用 task 工具
    输出 AIMessage(tool_calls=[{
        name: "task",
        args: {description, prompt, subagent_type},
        id: "call_xxx"
    }])

Step 4: task_tool 执行
    1. get_subagent_config("general-purpose") → SubagentConfig
    2. get_available_tools(subagent_enabled=False) → 去掉 task 工具
    3. SubagentExecutor(config, tools, sandbox_state, thread_data, trace_id)
    4. executor.execute_async(prompt, task_id="call_xxx")
       └── ThreadPoolExecutor.submit(_aexecute, prompt, result)
    5. 轮询循环开始

Step 5: 子智能体后台执行
    _aexecute(task, result):
    1. try_set_terminal(RUNNING)
    2. _build_initial_state(task):
       [SystemMessage(子智能体 prompt), HumanMessage(task)]
    3. _create_agent(): 新的 LangGraph Agent
    4. agent.astream(state):
       ├── SubagentTokenCollector 收集 token
       ├── 检查 cancel_event
       └── 捕获 AIMessage
    5. _extract_final_result() → final_text
    6. try_set_terminal(COMPLETED, result=final_text)

Step 6: task_tool 轮询到完成
    1. _cache_subagent_usage(task_id, records)
    2. _report_subagent_usage(journal, "subagent:general-purpose", ...)
    3. return "Task Succeeded. Result:\n{final_text}"

Step 7: 结果返回给主智能体 LLM
    ToolMessage(content="Task Succeeded. Result:\n...", tool_call_id="call_xxx")
    加入 state["messages"]

Step 8: 下一轮 LLM 调用
    主智能体 LLM 看到 ToolMessage 中的结果
    ├── TokenUsageMiddleware: 合并子智能体 token 到 usage_metadata
    └── LLM 输出最终回复给用户
```

## 关键文件索引

| 层次 | 文件 | 核心类/函数 |
|------|------|-------------|
| **入口** | `agents/factory.py` | `create_deerflow_agent()` |
| **主智能体** | `agents/lead_agent/agent.py` | `make_lead_agent()`, `_AgentConfig` |
| **System Prompt** | `agents/lead_agent/prompt.py` | `build_prompt()`, `_SUBAGENT_INSTRUCTIONS`, `_TEMPLATE_VARIABLES` |
| **状态 Schema** | `agents/thread_state.py` | `ThreadState` |
| **特性开关** | `agents/features.py` | `RuntimeFeatures` |
| **中间件组装** | `agents/middlewares/__init__.py` | `build_middleware_chain()` |
| **并发限制** | `agents/middlewares/subagent_limit_middleware.py` | `SubagentLimitMiddleware` |
| **Token 合并** | `agents/middlewares/token_usage_middleware.py` | `TokenUsageMiddleware` |
| **工具组装** | `tools/tools.py` | `get_available_tools()` |
| **通信桥梁** | `tools/builtins/task_tool.py` | `task_tool()` |
| **执行引擎** | `subagents/executor.py` | `SubagentExecutor`, `SubagentResult` |
| **配置模型** | `subagents/config.py` | `SubagentConfig` |
| **配置解析** | `subagents/registry.py` | `get_subagent_config()` |
| **Token 收集** | `subagents/token_collector.py` | `SubagentTokenCollector` |
| **内建子** | `subagents/builtins/general_purpose.py` | `GENERAL_PURPOSE_CONFIG` |
| **Bash 子** | `subagents/builtins/bash_agent.py` | `BASH_CONFIG` |
| **记账本** | `runtime/journal.py` | `RunJournal` |
| **执行驱动** | `runtime/runs/worker.py` | `run_agent()` |
| **运行管理** | `runtime/runs/manager.py` | `RunManager` |

## 一句话总结

> **DeerFlow 的主子智能体通信 = 主智能体 LLM 通过 System Prompt 知道有 `task` 工具 → 调用 `task(description, prompt, subagent_type)` → `task_tool` 在后台线程启动独立 LangGraph Agent 执行 → 轮询等待结果 → 通过 ToolMessage 返回给主智能体 LLM。没有直接消息通道，所有通信通过工具调用机制完成。**
