"""Lead Agent 工厂——DeerFlow Agent 的唯一构造入口。

完整 Agent 构造流程：
```
make_lead_agent(config)                       ← langgraph.json 注册入口
  ↓
_make_lead_agent(config, app_config=...)
  │
  ├─ 1. 解析运行时参数
  │     cfg = _get_runtime_config(config)
  │     model_name, thinking_enabled, is_plan_mode, subagent_enabled, agent_name...
  │
  ├─ 2. 模型选择
  │     _resolve_model_name(requested or agent_config.model)
  │     → 请求指定 → agent 配置 → 全局默认 → 回退 + 警告
  │
  ├─ 3. 注入追踪回调（图根级别，必须在所有 create_chat_model 之前）
  │     build_tracing_callbacks() → config["callbacks"]
  │     ⚠️ 此后所有 create_chat_model() 必须传 attach_tracing=False
  │
  ├─ 4. 加载技能（用于工具策略过滤）
  │     _load_enabled_skills_for_tool_policy(available_skills)
  │
  ├─ 5. 加载工具
  │     get_available_tools(model_name, groups, subagent_enabled)
  │     → sandbox(ls/bash/read/write) + MCP + community + subagent(task)
  │     + extra_tools (setup_agent / update_agent)
  │
  ├─ 6. 工具策略过滤 + 延迟工具组装
  │     filter_tools_by_skill_allowed_tools() → 按技能白名单过滤
  │     _assemble_deferred() → 分离 MCP 工具 schema（延迟绑定）
  │
  ├─ 7. 构建中间件链（19 个，按严格顺序）
  │     _build_middlewares(config, model_name, agent_name, deferred_setup)
  │
  ├─ 8. 渲染系统提示
  │     apply_prompt_template(subagent_enabled, agent_name, available_skills, ...)
  │
  └─ 9. 创建 Agent 图
        create_agent(model, tools, middleware, system_prompt, state_schema=ThreadState)
```

**关键不变量 —— ``attach_tracing=False``：**

追踪回调（Langfuse、LangSmith）由本模块在**图调用根**注入。此后所有
``create_chat_model(...)`` 调用都必须传入 ``attach_tracing=False``，否则：
- 产生重复 span（图根一个 + 模型一个）
- Langfuse 的 ``propagate_attributes`` 无法触发，``session_id``/``user_id`` 永远写不入 trace

当前四个调用点：
1. bootstrap agent 的 ``create_chat_model``
2. 默认 agent 的 ``create_chat_model``
3. ``_create_summarization_middleware`` 内部的 summarization 模型
4. ``TitleMiddleware`` 内部的标题生成模型

**自定义 Agent 支持：**
- 启动阶段（``is_bootstrap=True``）：使用精简 prompt + ``setup_agent`` 工具
- 常规运行（``agent_name="xxx"``）：加载 ``SOUL.md`` + agent 配置 + ``update_agent`` 工具
- 默认运行（``agent_name=None``）：无 SOUL，无 self-update 能力
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.memory.summarization_hook import memory_flush_hook
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.summarization_middleware import BeforeSummarizationHook, DeerFlowSummarizationMiddleware
from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.models import create_chat_model
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
from deerflow.skills.types import Skill
from deerflow.tracing import build_tracing_callbacks

if TYPE_CHECKING:
    from langchain.tools import BaseTool

    from deerflow.tools.builtins.tool_search import DeferredToolSetup

logger = logging.getLogger(__name__)


def _get_runtime_config(config: RunnableConfig) -> dict:
    """合并 ``configurable`` 配置项与 LangGraph 运行期 context。

    LangGraph 有两层配置通道：
    - ``config["configurable"]``：用户通过 API 传入的配置（如 model_name、is_plan_mode）
    - ``config["context"]``：LangGraph 运行期注入的上下文（如 thread_id、run_id）

    本函数将它们合并为单层 dict，context 中的值覆盖 configurable 中的同名字段。

    输入示例：
    ```python
    config = {
        "configurable": {"model_name": "deepseek-v3", "thread_id": "t1"},
        "context": {"thread_id": "t1", "run_id": "r1", "agent_name": "my-agent"},
    }
    cfg = _get_runtime_config(config)
    # → {"model_name": "deepseek-v3", "thread_id": "t1", "run_id": "r1", "agent_name": "my-agent"}
    ```

    Args:
        config: LangGraph RunnableConfig（含 ``configurable`` 和 ``context`` 两层）。

    Returns:
        合并后的扁平配置字典。
    """
    if not isinstance(config, Mapping):
        return {}

    cfg: dict = {}
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        cfg.update(configurable)

    context = config.get("context")
    if isinstance(context, Mapping):
        cfg.update(context)

    return cfg


def _resolve_model_name(requested_model_name: str | None = None, *,
                        app_config: AppConfig | None = None) -> str:
    """安全地解析运行期模型名，若名称无效则回退到默认。

    解析优先级：
    ```
    请求指定 model_name="deepseek-v3"
      → app_config.get_model_config("deepseek-v3") 命中？ → 使用 "deepseek-v3"
      → 未命中？→ 警告日志 + 回退到默认模型
      → 未指定？→ 使用 config.models[0].name（全局默认）
      → 无模型配置？→ raise ValueError
    ```

    使用示例：
    ```python
    # 正常解析
    model = _resolve_model_name("deepseek-v3")  # → "deepseek-v3"

    # 模型不存在，回退到默认
    model = _resolve_model_name("nonexistent-model")
    # 日志: Model 'nonexistent-model' not found in config; fallback to default model 'deepseek-v3'
    # → "deepseek-v3"

    # 未指定，使用默认
    model = _resolve_model_name()  # → config.models[0].name
    ```

    Args:
        requested_model_name: 请求中指定的模型名（可为 None）。
        app_config: 可选的应用配置，便于测试注入。

    Returns:
        解析后的有效模型名。

    Raises:
        ValueError: 配置中没有可用模型时。
    """
    app_config = app_config or get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware(*, app_config: AppConfig | None = None) -> (
        DeerFlowSummarizationMiddleware | None):
    """根据配置创建摘要中间件，并挂上记忆冲刷钩子。

    摘要中间件是上下文工程的“历史压缩层”：当消息窗口超过阈值时，
    它把较早消息压缩为 summary 消息，同时保留最近消息和近期加载的技能文件。
    若记忆功能启用，还会在压缩前调用 ``memory_flush_hook``，防止即将被删除的
    消息还没来得及写入长期记忆。
    """
    resolved_app_config = app_config or get_app_config()
    config = resolved_app_config.summarization

    if not config.enabled:
        return None

    # 将 YAML 配置里的触发阈值对象转换为 LangChain 摘要中间件期望的 tuple。
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # keep 决定压缩后保留多少近期上下文，通常用于保证最后几轮对话不被摘要替代。
    keep = config.keep.to_tuple()

    # 摘要使用独立模型调用；打上 middleware:summarize 标签后，RunJournal 能把
    # 这部分 token 归因到中间件，而不是主 Agent。追踪回调已在图根注入，
    # 此处必须 attach_tracing=False，避免重复 span 和 trace 属性传播失败。
    if config.model_name:
        model = create_chat_model(name=config.model_name, thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    else:
        model = create_chat_model(thinking_enabled=False, app_config=resolved_app_config, attach_tracing=False)
    model = model.with_config(tags=["middleware:summarize"])

    # 只向父类传递已启用的可选参数，保持默认行为由 LangChain 中间件控制。
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    hooks: list[BeforeSummarizationHook] = []
    if resolved_app_config.memory.enabled:
        hooks.append(memory_flush_hook)

    # 技能路径和读取工具名用于“技能恢复”：摘要压缩时保留最近读过的技能文件内容。
    # 这里假设 DeerFlowSummarizationMiddleware 只通过本工厂创建，运行期配置不会漂移。
    skills_container_path = resolved_app_config.skills.container_path or "/mnt/skills"

    return DeerFlowSummarizationMiddleware(
        **kwargs,
        skills_container_path=skills_container_path,
        skill_file_read_tool_names=config.skill_file_read_tool_names,
        before_summarization=hooks,
        preserve_recent_skill_count=config.preserve_recent_skill_count,
        preserve_recent_skill_tokens=config.preserve_recent_skill_tokens,
        preserve_recent_skill_tokens_per_skill=config.preserve_recent_skill_tokens_per_skill,
    )


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """创建并配置 TodoList 中间件。

    Args:
        is_plan_mode: 是否启用计划模式及其 TodoList 中间件。

    Returns:
        启用计划模式时返回 ``TodoMiddleware`` 实例，否则返回 ``None``。
    """
    if not is_plan_mode:
        return None

    # 符合 DeerFlow 风格的提示模板
    system_prompt = """
<todo_list_system>
你可以使用 `write_todos` 工具来管理和追踪复杂的多步骤目标。

**关键规则：**
- 每完成一步后立即将 todo 标记为已完成——不要批量完成
- 任何时刻保持恰好一个任务处于 `in_progress` 状态（可并行的任务除外）
- 实时更新 todo 列表——让用户随时了解你的进度
- 不要对简单任务（少于 3 步）使用本工具——直接完成即可

**何时使用：**
本工具适用于需要系统化追踪的复杂目标：
- 需要 3 个或以上明确步骤的复杂多步任务
- 需要仔细规划和执行的非平凡任务
- 用户明确要求使用 todo 列表
- 用户提供了多个任务（编号或逗号分隔的列表）
- 计划可能需要根据中间结果进行调整

**何时不使用：**
- 单一的、直接的任务
- 简单任务（少于 3 步）
- 纯对话或信息查询
- 方法明确的简单工具调用

**最佳实践：**
- 将复杂任务分解为更小、可执行的步骤
- 使用清晰、描述性的任务名称
- 移除不再相关的任务
- 添加实现过程中发现的新任务
- 随着了解加深，勇于调整 todo 列表

**任务管理：**
编写 todo 需要时间和 token——仅在管理复杂问题时使用，而非简单请求。
</todo_list_system>
"""

    tool_description = """使用本工具为复杂工作会话创建和管理结构化的任务列表。

**重要：仅对复杂任务（3 步以上）使用本工具。对于简单请求，直接完成工作即可。**

## 何时使用

在以下场景使用本工具：
1. **复杂多步任务**：需要 3 个或以上明确步骤或操作
2. **非平凡任务**：需要仔细规划或多个操作
3. **用户明确要求**：用户直接要求你追踪任务
4. **多个任务**：用户提供了待办事项列表
5. **动态规划**：计划可能需要根据中间结果更新

## 何时不使用

以下情况跳过本工具：
1. 任务直接明了，少于 3 步
2. 任务琐碎，追踪没有收益
3. 纯对话或信息查询
4. 该做什么很明确，直接做就行

## 如何使用

1. **启动任务**：在开始工作之前将其标记为 `in_progress`
2. **完成任务**：完成后立即将其标记为 `completed`
3. **更新列表**：按需添加新任务、移除无关任务或更新描述
4. **批量更新**：可以一次进行多个更新（例如完成一个任务并启动下一个）

## 任务状态

- `pending`：任务尚未开始
- `in_progress`：正在处理中（可并行的任务可以有多个）
- `completed`：任务已成功完成

## 任务完成要求

**关键：只有在完全完成任务后才能将其标记为已完成。**

以下情况绝不应标记为已完成：
- 存在未解决的问题或错误
- 工作是部分或不完整的
- 遇到阻止完成的障碍
- 找不到必要的资源或依赖
- 质量标准未达到

如被阻塞，保持任务 `in_progress` 并创建新任务描述需要解决的问题。

## 最佳实践

- 创建具体、可执行的项目
- 将复杂任务分解为更小、可管理的步骤
- 使用清晰、描述性的任务名称
- 实时更新任务状态
- 完成后立即标记（不要批量完成）
- 移除不再相关的任务
- **重要**：编写 todo 列表时，立即将第一个任务标记为 `in_progress`
- **重要**：除非所有任务已完成，始终保持至少一个任务 `in_progress` 以展示进度

主动管理任务体现了严谨性，确保所有需求顺利完成。

**记住**：如果只需少量工具调用即可完成任务且方法明确，直接完成工作而不使用本工具是更好的选择。
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


def _build_middlewares(
    config: RunnableConfig,
    model_name: str | None,
    agent_name: str | None = None,
    custom_middlewares: list[AgentMiddleware] | None = None,
    *,
    app_config: AppConfig | None = None,
    deferred_setup=None,
):
    """根据运行期配置构建 Lead Agent 中间件链。

    链顺序不是任意的——每个中间件的位置由依赖关系和拦截点语义决定：

    ```
    位置  中间件                      拦截点            排序原因
    ──────────────────────────────────────────────────────────────────────
     1   ThreadDataMiddleware         before_agent      必须在所有中间件之前（创建线程目录）
     2   UploadsMiddleware            before_agent      依赖 ThreadData 提供的 thread_id
     3   DynamicContextMiddleware     before_agent      将记忆/日期注入首条 HumanMessage
     4   SummarizationMiddleware      before_model      尽早压缩上下文，减轻后续中间件负担
     5   TodoListMiddleware           before_model      plan_mode 下的写 todo 管理
     6   TokenUsageMiddleware         after_model       token 用量记录
     7   TitleMiddleware              after_agent       首轮交换后自动生成标题
     8   MemoryMiddleware             after_agent       在 Title 之后入队（避免标题消息混入记忆）
     9   ViewImageMiddleware          before_model      图片注入须在 LLM 调用前
    10   DeferredToolFilterMiddleware  wrap_model_call   隐藏 MCP schema 直到 tool_search 提升
    11   SubagentLimitMiddleware      after_model       截断多余 task 调用
    12   LoopDetectionMiddleware      after_model       检测并中断重复 tool_call 循环
    13   自定义中间件                  —                 用户注入位置（Clarification 之前）
    14   SafetyFinishReasonMiddleware after_model       安全终止时剥离 tool_call（在 Loop 之后）
    15   ClarificationMiddleware      wrap_tool_call    拦截 ask_clarification → 中断（必须最后）
    ```

    条件性启用：
    | 中间件                        | 条件                                        |
    |-------------------------------|---------------------------------------------|
    | SummarizationMiddleware        | ``summarization.enabled=true``              |
    | TodoListMiddleware             | ``is_plan_mode=true`` 且非 bootstrap        |
    | TokenUsageMiddleware           | ``token_usage.enabled=true``                |
    | ViewImageMiddleware            | 当前模型 ``supports_vision=true``            |
    | DeferredToolFilterMiddleware   | ``tool_search.enabled=true`` 且有延迟工具     |
    | SubagentLimitMiddleware        | ``subagent_enabled=true``                   |
    | LoopDetectionMiddleware        | ``loop_detection.enabled=true``             |
    | SafetyFinishReasonMiddleware   | ``safety_finish_reason.enabled=true``       |

    Args:
        config: 运行期配置，包含 ``is_plan_mode`` 等可配置项。
        model_name: 已解析的模型名称，用于判断是否启用视觉相关中间件。
        agent_name: 若提供，``MemoryMiddleware`` 将按 Agent 隔离存储记忆。
        custom_middlewares: 可选，注入到链中的自定义中间件列表（插在 SafetyFinish 之前）。
        app_config: 可选的应用配置对象，便于测试或非默认配置注入。
        deferred_setup: 可选的延迟工具集合元数据（``DeferredToolSetup``）。

    Returns:
        按严格顺序排列的中间件实例列表。
    """
    resolved_app_config = app_config or get_app_config()
    middlewares = build_lead_runtime_middlewares(app_config=resolved_app_config, lazy_init=True)

    # 动态上下文不放进 system prompt，而是以隐藏 HumanMessage 注入：
    # system prompt 因此能跨用户/会话保持静态，提升模型前缀缓存命中率。
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    middlewares.append(DynamicContextMiddleware(agent_name=agent_name, app_config=resolved_app_config))

    # 历史压缩层：在模型调用前控制消息窗口大小，并在压缩前冲刷长期记忆。
    summarization_middleware = _create_summarization_middleware(app_config=resolved_app_config)
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # 计划模式下额外注入 todo 工具，属于上下文中的“显式工作记忆”。
    cfg = _get_runtime_config(config)
    is_plan_mode = cfg.get("is_plan_mode", False)
    todo_list_middleware = _create_todo_list_middleware(is_plan_mode)
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)

    # token 统计用于观察上下文膨胀、摘要和子 Agent 调用成本。
    if resolved_app_config.token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())

    # 标题生成在 after_agent 执行，不参与模型输入上下文。
    middlewares.append(TitleMiddleware(app_config=resolved_app_config))

    # 长期记忆写入在标题之后执行，避免标题生成消息污染记忆抽取。
    middlewares.append(MemoryMiddleware(agent_name=agent_name, memory_config=resolved_app_config.memory))

    # 仅视觉模型需要图片内联上下文；模型名使用运行期解析结果，避免读取过期配置。
    model_config = resolved_app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # 延迟工具 schema 默认不进入模型上下文，直到 tool_search 提升后才绑定，
    # 这是控制 MCP 工具上下文体积的关键策略。
    if deferred_setup is not None and deferred_setup.deferred_names:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware(deferred_setup.deferred_names, deferred_setup.catalog_hash))

    # 子 Agent 并发限制用于约束一次模型响应产生的 task 调用数量。
    subagent_enabled = cfg.get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # 循环检测会在重复工具调用时注入提醒或强制停止，避免上下文被无效工具轮次撑爆。
    loop_detection_config = resolved_app_config.loop_detection
    if loop_detection_config.enabled:
        middlewares.append(LoopDetectionMiddleware.from_config(loop_detection_config))

    # 自定义中间件插在澄清中间件之前，保留 Clarification 的最终拦截权。
    if custom_middlewares:
        middlewares.extend(custom_middlewares)

    # 安全终止时先剥离不完整 tool_calls；LangChain 的 after_model 逆序执行，
    # 因此这里注册在靠后位置，让后续循环/子 Agent 统计看到已清理的消息。
    safety_config = resolved_app_config.safety_finish_reason
    if safety_config.enabled:
        middlewares.append(SafetyFinishReasonMiddleware.from_config(safety_config))

    # 澄清工具必须最后注册，确保它能作为最终工具调用拦截点中断运行。
    middlewares.append(ClarificationMiddleware())
    return middlewares


def _assemble_deferred(filtered_tools: list[BaseTool], *, enabled: bool) -> tuple[list[BaseTool], DeferredToolSetup]:
    """在工具策略过滤之后构建最终工具列表与延迟工具集合。

    当 ``tool_search`` 启用时，MCP 工具的 schema 不会直接绑定到模型（避免上下文膨胀），
    而是通过 ``tool_search`` 工具按需发现。本函数将 MCP 工具从活跃列表分离到延迟集合，
    并构造 ``tool_search`` 工具本身。

    示例（tool_search 启用，3 个工具）：
    ```python
    tools = [
        bash_tool,           # 普通工具
        mcp_tool_a,          # MCP 工具 → 移入延迟集合
        mcp_tool_b,          # MCP 工具 → 移入延迟集合
    ]
    final_tools, setup = _assemble_deferred(tools, enabled=True)
    # final_tools = [bash_tool, tool_search_tool]     ← LLM 绑定的 schema
    # setup.deferred_names = {"mcp_tool_a", "mcp_tool_b"} ← 存入 system prompt
    # setup.tool_search_tool → tool_search            ← 发现延迟工具
    ```

    示例（tool_search 禁用）：
    ```python
    final_tools, setup = _assemble_deferred(tools, enabled=False)
    # final_tools = [bash_tool, mcp_tool_a, mcp_tool_b]  ← 全部直接绑定
    # setup.deferred_names = frozenset()                 ← 空
    ```

    "失败即停"策略：若 ``tool_search`` 启用 + MCP 工具幸存在过滤后 +
    未恢复出延迟集合 → ``RuntimeError``，避免 MCP schema 静默丢失。

    Args:
        filtered_tools: 经过技能白名单过滤后的工具列表。
        enabled: ``tool_search`` 是否启用。

    Returns:
        ``(final_tools, deferred_setup)`` 元组。

    Raises:
        RuntimeError: 启用 tool_search 但延迟集合异常为空时。
    """
    from deerflow.tools.builtins.tool_search import build_deferred_tool_setup
    from deerflow.tools.mcp_metadata import is_mcp_tool

    deferred_setup = build_deferred_tool_setup(filtered_tools, enabled=enabled)
    if enabled and not deferred_setup.deferred_names and any(is_mcp_tool(t) for t in filtered_tools):
        raise RuntimeError("tool_search enabled and MCP tools survived policy filtering, but no deferred set was recovered — refusing to bind MCP schemas (fail-closed).")
    final_tools = list(filtered_tools)
    if deferred_setup.tool_search_tool:
        final_tools.append(deferred_setup.tool_search_tool)
    return final_tools, deferred_setup


def _available_skill_names(agent_config, is_bootstrap: bool) -> set[str] | None:
    """根据 Agent 配置和启动模式返回可用技能白名单。

    - bootstrap 模式：固定返回 ``{"bootstrap"}``（只有 bootstrap 技能）
    - 自定义 Agent 有显式 skills 配置：返回配置的集合
    - 默认运行：返回 ``None``（不限制，全部技能可用）

    输入示例：
    ```python
    # bootstrap 模式
    _available_skill_names(None, is_bootstrap=True)  # → {"bootstrap"}

    # 自定义 Agent 配置了 skills: ["code-review", "testing"]
    _available_skill_names(agent_config, is_bootstrap=False)  # → {"code-review", "testing"}

    # 默认 Agent（无配置）
    _available_skill_names(None, is_bootstrap=False)  # → None（全部可用）
    ```

    Args:
        agent_config: 自定义 Agent 配置对象（可为 None）。
        is_bootstrap: 是否为首次启动的 bootstrap Agent。

    Returns:
        技能名白名单集合；``None`` 表示不限制。
    """
    if is_bootstrap:
        return {"bootstrap"}
    if agent_config and agent_config.skills is not None:
        return set(agent_config.skills)
    return None


def _load_enabled_skills_for_tool_policy(available_skills: set[str] | None, *, app_config: AppConfig) -> list[Skill]:
    """加载已启用技能并按白名单过滤，返回工具策略过滤所需的 Skill 列表。

    从存储中加载全部已启用技能，然后按 ``available_skills`` 白名单过滤。
    过滤后的列表传给 ``filter_tools_by_skill_allowed_tools()``，确保 Agent
    只能使用白名单内技能声明的工具。

    输入示例：
    ```python
    # 全部技能可用
    skills = _load_enabled_skills_for_tool_policy(None, app_config=config)
    # → [Skill("bootstrap"), Skill("code-review"), Skill("testing")]

    # 白名单限制
    skills = _load_enabled_skills_for_tool_policy({"code-review"}, app_config=config)
    # → [Skill("code-review")]
    ```

    Args:
        available_skills: 白名单技能名集合；``None`` 表示不限制。
        app_config: 应用配置，用于解析技能存储路径。

    Returns:
        按白名单过滤后的 Skill 列表。

    Raises:
        Exception: 技能加载失败时传播原始异常（加载是基础能力，失败应阻止 Agent 创建）。
    """


def make_lead_agent(config: RunnableConfig):
    """LangGraph 图工厂——``langgraph.json`` 注册的唯一入口。

    由 LangGraph Server / Gateway 在每次创建 Agent 时调用。
    从 ``config`` 中提取 ``app_config`` 覆盖（如有），委托给 ``_make_lead_agent``。

    调用链：
    ```
    Gateway POST /runs/stream
      → RunManager → run_agent(worker.py)
        → make_lead_agent(config)
          → _make_lead_agent(config, app_config=...)
    ```

    Args:
        config: LangGraph RunnableConfig（含 ``configurable`` 和 ``context``）。

    Returns:
        编译后的 LangGraph Agent 图。
    """
    runtime_config = _get_runtime_config(config)
    runtime_app_config = runtime_config.get("app_config")
    return _make_lead_agent(config, app_config=runtime_app_config or get_app_config())


def _make_lead_agent(config: RunnableConfig, *, app_config: AppConfig):
    """构造 Lead Agent 的核心工厂（9 步流程，详见模块 docstring）。"""
    # 延迟导入，避免循环依赖（deerflow.tools 会反向引用 agent 模块）
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins import setup_agent, update_agent

    # ═══════════════════════════════════════════════════════════════
    # 步骤 1：从 config 中提取运行时参数
    # ═══════════════════════════════════════════════════════════════
    cfg = _get_runtime_config(config)        # 扁平化 configurable + context
    resolved_app_config = app_config         # 调用方注入的 AppConfig 快照

    # ── 模型相关 ──
    thinking_enabled = cfg.get("thinking_enabled", True)          # 是否启用扩展思考
    reasoning_effort = cfg.get("reasoning_effort", None)          # 推理强度等级
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")  # API 请求指定的模型

    # ── 模式开关 ──
    is_plan_mode = cfg.get("is_plan_mode", False)                 # 计划模式（启用 TodoList 中间件）
    subagent_enabled = cfg.get("subagent_enabled", False)         # 子代理委派
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)  # 单轮最大并发子代理数
    is_bootstrap = cfg.get("is_bootstrap", False)                 # 首次启动：用精简 prompt + setup_agent
    agent_name = validate_agent_name(cfg.get("agent_name"))       # 校验 agent_name 格式安全性

    # ═══════════════════════════════════════════════════════════════
    # 步骤 2：加载 Agent 配置 + 技能白名单
    # ═══════════════════════════════════════════════════════════════
    # bootstrap 阶段不加载自定义 agent 配置（此时还没有任何 agent）
    agent_config = load_agent_config(agent_name) if not is_bootstrap else None
    available_skills = _available_skill_names(agent_config, is_bootstrap)
    # 自定义 Agent 可通过 config 指定默认模型；未指定时回退到全局默认
    agent_model_name = agent_config.model if agent_config and agent_config.model else None

    # ═══════════════════════════════════════════════════════════════
    # 步骤 3：模型选择（四级回退）
    # ═══════════════════════════════════════════════════════════════
    # 优先级：请求指定 → agent 配置 → 全局默认 → ValueError
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=resolved_app_config)
    model_config = resolved_app_config.get_model_config(model_name)

    # 防御：配置中没有模型（极端情况，正常部署不应出现）
    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    # 模型不支持 thinking 但请求开启了 → 自动降级并告警
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    # ── 记录最终生效的 Agent 参数（方便运维排查）──
    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    # ═══════════════════════════════════════════════════════════════
    # 步骤 4：注入运行元数据 + 追踪回调
    # ═══════════════════════════════════════════════════════════════
    # 元数据会被写入 LangSmith/Langfuse trace，便于按 agent_name/model_name 过滤
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
            "tool_groups": agent_config.tool_groups if agent_config else None,
            "available_skills": sorted(available_skills) if available_skills is not None else None,
        }
    )

    # ⚠️ 追踪回调必须在图根注入（而非模型级）——
    # 这样整个 LangGraph run 产生单一 trace，所有 node/LLM/tool 作为子 span
    # Langfuse 处理器只有在图根 on_chain_start(parent_run_id=None) 时才传播 session_id/user_id。
    tracing_callbacks = build_tracing_callbacks()
    if tracing_callbacks:
        existing = config.get("callbacks") or []
        if not isinstance(existing, list):
            existing = list(existing)
        # 追加而非覆盖：保留调用方已注入的 callback
        config["callbacks"] = [*existing, *tracing_callbacks]

    # ═══════════════════════════════════════════════════════════════
    # 步骤 5：加载技能 → 按白名单过滤（用于工具策略）
    # ═══════════════════════════════════════════════════════════════
    # 每个 Skill 可声明 allowed-tools 白名单，filter_tools_by_skill_allowed_tools
    # 会在步骤 7 中剔除白名单外的工具
    skills_for_tool_policy = _load_enabled_skills_for_tool_policy(available_skills, app_config=resolved_app_config)

    # ═══════════════════════════════════════════════════════════════
    # 分支 A：Bootstrap Agent（首次启动，用于创建自定义 Agent）
    # ═══════════════════════════════════════════════════════════════
    if is_bootstrap:
        # bootstrap 使用精简 prompt（仅含 setup_agent 相关指令）+ setup_agent 工具
        raw_tools = get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled, app_config=resolved_app_config) + [setup_agent]
        filtered = filter_tools_by_skill_allowed_tools(raw_tools, skills_for_tool_policy)
        final_tools, setup = _assemble_deferred(filtered, enabled=resolved_app_config.tool_search.enabled)
        return create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, app_config=resolved_app_config, attach_tracing=False),
            tools=final_tools,
            middleware=_build_middlewares(config, model_name=model_name, app_config=resolved_app_config, deferred_setup=setup),
            system_prompt=apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                available_skills=set(["bootstrap"]),   # 只有 bootstrap 技能
                app_config=resolved_app_config,
                deferred_names=setup.deferred_names,
            ),
            state_schema=ThreadState,
        )

    # ═══════════════════════════════════════════════════════════════
    # 分支 B：默认 / 自定义 Agent（常规运行）
    # ═══════════════════════════════════════════════════════════════
    # 自定义 Agent（agent_name 非空）获得 update_agent 工具以持久化自我更新；
    # 默认 Agent（agent_name=None）看不到此工具
    extra_tools = [update_agent] if agent_name else []
    # 按 agent_config.tool_groups 过滤工具（如配置了则只加载指定组的工具）
    raw_tools = get_available_tools(model_name=model_name, groups=agent_config.tool_groups if agent_config else None, subagent_enabled=subagent_enabled, app_config=resolved_app_config)
    # 步骤 6+7：技能工具策略过滤 → 延迟工具分离（MCP schema 延迟绑定）
    filtered = filter_tools_by_skill_allowed_tools(raw_tools + extra_tools, skills_for_tool_policy)
    final_tools, setup = _assemble_deferred(filtered, enabled=resolved_app_config.tool_search.enabled)

    # 步骤 8+9：构建中间件链 + 渲染系统提示 → create_agent
    # 推理强度仅在非 bootstrap 分支传入（bootstrap 不需要深度推理）。
    return create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort, app_config=resolved_app_config, attach_tracing=False),
        tools=final_tools,
        middleware=_build_middlewares(config, model_name=model_name, agent_name=agent_name, app_config=resolved_app_config, deferred_setup=setup),
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled,
            max_concurrent_subagents=max_concurrent_subagents,
            agent_name=agent_name,              # 有 agent_name 时注入 SOUL.md + self_update 提示
            available_skills=set(agent_config.skills) if agent_config and agent_config.skills is not None else None,
            app_config=resolved_app_config,
            deferred_names=setup.deferred_names,
        ),
        state_schema=ThreadState,
    )
