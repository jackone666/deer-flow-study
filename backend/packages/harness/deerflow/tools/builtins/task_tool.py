"""Task 工具：把子任务委派给独立上下文的子 Agent。

本模块是主 Agent 与子 Agent 之间的桥梁。当主 Agent 的 LLM 调用 ``task()`` 工具时，
本模块负责：

1. 解析子 Agent 类型配置（内置或自定义）
2. 创建 SubagentExecutor，在独立线程/事件循环中启动子 Agent
3. 轮询等待子 Agent 完成，期间通过 SSE 向前端实时推送进度
4. 收集并上报 token 用量到父 Agent 的 RunJournal
5. 将子 Agent 的最终结果以字符串形式返回给主 Agent（成为 ToolMessage）

通信架构：
```
主 Agent LLM
  → tool_call: task(description, prompt, subagent_type)
    → task_tool() [本模块]
      → SubagentExecutor.execute_async(prompt)
        → 后台线程 → create_agent() → astream()
      → 轮询循环 (每5秒)
        → StreamWriter SSE 事件 → 前端实时展示
        → 等待 COMPLETED/FAILED/CANCELLED/TIMED_OUT
      → 返回 "Task Succeeded. Result: ..." → ToolMessage → 主 Agent LLM
```
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langgraph.config import get_stream_writer

from deerflow.config import get_app_config
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Token 用量缓存（全局 dict，按 tool_call_id 索引）
# ═══════════════════════════════════════════════════════════════════════════════
# 子 Agent 完成后，其 token 用量被缓存在这里。TokenUsageMiddleware 在 after_model
# 钩子中通过 pop_cached_subagent_usage() 取出，合并到调度该子 Agent 的 AIMessage
# 的 usage_metadata 中，实现父子 Agent token 用量的统一追踪。
_subagent_usage_cache: dict[str, dict[str, int]] = {}


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    """判断应用配置是否启用了 token 用量统计。

    如果 token_usage 未启用，则跳过用量缓存和上报，减少不必要的开销。
    处理 FileNotFoundError 是为了兼容单元测试中无 config.yaml 的场景。
    """
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    """缓存子 Agent 的 token 用量，供 TokenUsageMiddleware 写回。

    仅当 enabled=True 且 usage 非空时才写入。enabled 由 _token_usage_cache_enabled()
    决定，未启用时跳过以节省内存。

    Args:
        tool_call_id: 触发该子 Agent 的 tool_call 的 id，用作缓存键。
        usage: 聚合后的 token 用量字典 {"input_tokens": N, "output_tokens": N, "total_tokens": N}。
        enabled: 是否启用缓存，默认 True。
    """
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    """从子 Agent 用量缓存中取出并移除指定 tool_call_id 的用量。

    由 TokenUsageMiddleware 调用。取出后缓存中不再保留该条目，
    避免内存泄漏。每个 tool_call_id 只会被 pop 一次。
    """
    return _subagent_usage_cache.pop(tool_call_id, None)


def _is_subagent_terminal(result: Any) -> bool:
    """判断后台子 Agent 结果是否已处于可安全清理的终止状态。

    终止状态包括：COMPLETED、FAILED、CANCELLED、TIMED_OUT。
    额外检查 completed_at 字段作为防御性判断（某些边缘情况下状态可能未及时更新）。
    """
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _await_subagent_terminal(task_id: str, max_polls: int) -> Any | None:
    """轮询直到后台子 Agent 达到终止状态，或超过最大轮询次数。

    用于 asyncio.CancelledError 处理中：主 Agent 已被取消，但我们需要等待
    子 Agent 优雅退出以获取最终的 token 用量快照。

    Args:
        task_id: 后台任务 ID。
        max_polls: 最大轮询次数，每次间隔 5 秒。

    Returns:
        子 Agent 结果对象（已终止），或 None（超时/未找到）。
    """
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int) -> None:
    """对已取消的子 Agent 持续轮询，直到可以安全清理。

    场景：主 Agent 轮询超时，调用了 request_cancel_background_task()，
    但子 Agent 的工作线程仍在执行中（可能卡在某个长时间的工具调用上）。
    本协程在后台持续等待子 Agent 真正终止后再清理 _background_tasks 条目。

    Args:
        task_id: 后台任务 ID。
        trace_id: 分布式追踪 ID，用于日志关联。
        max_polls: 最大轮询次数。
    """
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return  # 已被其他地方清理
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    """记录延迟清理任务的失败日志。

    作为 asyncio.Task 的 done callback 使用。如果清理协程
    抛出异常（非 CancelledError），记录到日志以便排查。
    """
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int) -> None:
    """为已取消的子 Agent 启动延迟清理协程。

    创建一个独立的 asyncio.Task 在后台运行 _deferred_cleanup_subagent_task，
    不阻塞当前协程。清理完成后自动注销。

    Args:
        task_id: 后台任务 ID。
        trace_id: 分布式追踪 ID。
        max_polls: 最大轮询次数。
    """
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


def _find_usage_recorder(runtime: Any) -> Any | None:
    """从 runtime config 的 callbacks 中找到带有 ``record_external_llm_usage_records`` 的处理器。

    该处理器（通常是 RunJournal）负责将子 Agent 的 token 用量写入持久化存储。

    LangChain 可能以三种形式传入 ``config["callbacks"]``：

    - ``None``：无回调，无 recorder。
    - ``list[BaseCallbackHandler]``：直接遍历列表查找。
    - ``BaseCallbackManager`` 实例（异步工具运行的 ``AsyncCallbackManager`` 等）：
      manager 不可迭代，先通过 ``.handlers`` 属性解包。

    其它形态（如意外传入的单个 handler 对象）无法安全迭代，这里按"无 recorder"
    处理而不是抛错，避免破坏主流程。
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    # AsyncCallbackManager 等 manager 对象不可直接迭代，通过 .handlers 解包
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """把 token 用量记录列表聚合成紧凑字典，供 SSE 事件使用。

    子 Agent 可能有多轮 LLM 调用（SubagentTokenCollector 会记录每条），
    这里将它们累加为单一的 input/output/total 三元组。

    Args:
        records: SubagentTokenCollector.snapshot_records() 返回的列表。

    Returns:
        {"input_tokens": N, "output_tokens": N, "total_tokens": N} 或 None。
    """
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """把子 Agent 的 token 用量上报到父 RunJournal（若存在）。

    每个子 Agent 任务只能上报一次（由 SubagentResult.usage_reported 字段守护）。
    防止在轮询超时和正常完成两条路径中重复上报。

    Args:
        runtime: LangGraph 运行时对象，从中提取 RunJournal callback。
        result: SubagentResult，包含 token_usage_records 和 usage_reported 标志。
    """
    # 已上报过，跳过
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True  # 标记已上报，防止重复
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    """从 runtime context 中解析出 ``AppConfig``（若已注入）。

    在 Gateway 运行模式下，AppConfig 通过 context 注入到每次运行中。
    如果 context 中没有，返回 None，后续会回退到 get_app_config()。
    """
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """在父技能白名单策略下，返回子 Agent 实际可用的技能列表。

    合并规则（取交集）：
    - parent=None（不限制）→ 返回 child（沿用子 Agent 自身配置）
    - child=None（继承所有）→ 返回 parent 的副本（限制为父的白名单）
    - 两者都有 → 返回两者的交集

    Args:
        parent: 父 Agent 的技能白名单（来自 metadata.available_skills）。
        child: 子 Agent 配置中的技能列表。

    Returns:
        合并后的技能白名单；None 表示不限制。
    """
    if parent is None:
        return child  # 父不限制，沿用子配置
    if child is None:
        return list(parent)  # 子继承所有，限制为父白名单

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]  # 取交集


# ═══════════════════════════════════════════════════════════════════════════════
# task 工具定义 —— 主 Agent 与子 Agent 之间的唯一桥梁
# ═══════════════════════════════════════════════════════════════════════════════
@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str:
    """把任务委派给独立上下文的专用子 Agent。

    子 Agent 的作用:
    - 让探索与实现分离，保留主对话上下文
    - 自主处理多步复杂任务
    - 在隔离上下文中执行命令或操作

    内置子 Agent 类型:
    - **general-purpose**: 适合需要探索+操作、复杂推理、多步依赖或隔离上下文的通用 Agent。
    - **bash**: 命令执行专家。仅在显式允许主机 bash 或使用 ``AioSandboxProvider``
      等隔离 shell 沙箱时可用。

    还可以在 ``config.yaml`` 的 ``subagents.custom_agents`` 中定义自定义子 Agent
    类型，每种类型可拥有自己的 system prompt、工具、技能、模型与超时配置。提供
    未知的 ``subagent_type`` 时，错误消息会列出所有可用类型。

    使用时机:
    - 需要多步或多工具的复杂任务
    - 会产生大量输出的任务
    - 希望把上下文与主对话隔离
    - 并行的研究或探索任务

    不应使用本工具的情况:
    - 简单的单步操作（直接使用工具）
    - 需要用户交互或澄清的任务

    Args:
        description: 任务简称（3-5 个单词），用于日志/展示。请始终作为第一个参数提供。
        prompt: 子 Agent 任务描述，要具体清晰。请始终作为第二个参数提供。
        subagent_type: 子 Agent 类型。请始终作为第三个参数提供。
    """
    # ── 解析运行时配置 ──
    # 优先从 runtime context 中获取 AppConfig（Gateway 注入），
    # 找不到时后续会回退到 get_app_config()
    runtime_app_config = _get_runtime_app_config(runtime)

    # 判断是否需要缓存 token 用量（受 token_usage.enabled 控制）
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)

    # 获取当前可用的子 Agent 类型列表（bash 在本地沙箱且未显式允许时不可用）
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) \
        if runtime_app_config is not None else get_available_subagent_names()

    # ── 解析子 Agent 配置 ──
    # 从注册表查找：先查内置（general-purpose、bash），再查 config.yaml 的 custom_agents
    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"

    # bash 子 Agent 需要额外的安全检查：本地沙箱 + 未显式允许时拒绝
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    # ── 构建配置覆盖 ──
    # 技能白名单由父 Agent 的 available_skills 与子 Agent 配置的 skills 取交集
    overrides: dict = {}

    # ── 从 runtime 中提取父 Agent 上下文 ──
    # 这些上下文会被传入 SubagentExecutor，让子 Agent 共享沙箱和线程数据
    sandbox_state = None    # 沙箱状态（文件系统访问）
    thread_data = None      # 线程运行时数据（工作目录等）
    thread_id = None        # LangGraph thread_id
    parent_model = None     # 父 Agent 使用的模型名（供 inherit 模式使用）
    trace_id = None         # 分布式追踪 ID
    metadata: dict = {}     # 父 Agent 的 config.metadata

    if runtime is not None:
        # 从父 Agent 的 state 中提取沙箱和线程数据（引用传递，子 Agent 直接共享）
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")

        # thread_id：优先从 context 取，回退到 configurable
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # 父 Agent 的模型名和追踪 ID 从 config.metadata 中提取
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # 分布式追踪：优先用父 Agent 的 trace_id，没有则生成新的
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # ── 技能白名单合并 ──
    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    # 应用覆盖（如果有），生成最终的 SubagentConfig
    if overrides:
        config = replace(config, **overrides)

    # ── 获取子 Agent 工具列表 ──
    # 延迟导入避免循环依赖（deerflow.tools 会反向引用本模块）
    from deerflow.tools import get_available_tools

    # 子 Agent 继承父 Agent 的 tool_groups 限制，保持一致的权限边界
    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config

    # 如果模型配置为 "inherit" 且没有父模型和 app_config，回退到 get_app_config()
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()

    # 解析子 Agent 实际使用的模型名（config.model → parent_model → 默认模型）
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # 关键：subagent_enabled=False 防止子 Agent 递归创建孙 Agent
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,  # ← 禁止递归嵌套
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    # ── 创建 SubagentExecutor ──
    # 执行器负责在独立事件循环中运行子 Agent
    executor_kwargs = {
        "config": config,             # 子 Agent 配置（类型、超时、技能等）
        "tools": tools,               # 过滤后的工具列表（不含 task）
        "parent_model": parent_model, # 父模型名（供 inherit 模式）
        "sandbox_state": sandbox_state,  # 共享沙箱（引用传递）
        "thread_data": thread_data,      # 共享线程数据（引用传递）
        "thread_id": thread_id,          # 线程 ID（沙箱操作需要）
        "trace_id": trace_id,            # 追踪 ID（日志关联）
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    # ── 启动后台异步执行 ──
    # execute_async 将任务提交到 _scheduler_pool 线程池，立即返回 task_id
    # 使用 tool_call_id 作为 task_id 便于追踪（一个 tool_call 对应一个子 Agent）
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # ── 轮询变量初始化 ──
    poll_count = 0                # 已轮询次数
    last_status = None            # 上一次的状态（用于日志去重）
    last_message_count = 0        # 已通过 SSE 发送的 AI 消息数量
    # 轮询超时 = 执行超时 + 60 秒缓冲，每 5 秒检查一次
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    # ── 获取 StreamWriter（SSE 事件推送）──
    writer = get_stream_writer()

    # 向前端发送"任务已启动"事件
    writer({"type": "task_started", "task_id": task_id, "description": description})

    try:
        # ═══════════════════════════════════════════════════════════════
        # 主轮询循环：每 5 秒检查一次子 Agent 状态
        # ═══════════════════════════════════════════════════════════════
        while True:
            # 从全局 _background_tasks 字典中获取任务结果
            result = get_background_task_result(task_id)

            # 极端情况：后台任务记录丢失（不应发生，防御性处理）
            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background tasks"

            # 状态变化时记录日志（去重：只在首次变化时打印）
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # ── 实时推送子 Agent 的 AI 消息 ──
            # 子 Agent 在执行过程中不断产生 AIMessage，这里检查是否有新消息
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # 逐条发送 task_running 事件（每条新消息一个事件）
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,                # AIMessage 序列化字典
                            "message_index": i + 1,            # 1-based 序号（前端展示用）
                            "total_messages": current_message_count,
                        }
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # ── 检查终止状态 ──
            # 聚合 token 用量（供 SSE 事件和缓存使用）
            usage = _summarize_usage(getattr(result, "token_usage_records", None))

            if result.status == SubagentStatus.COMPLETED:
                # ✅ 子 Agent 正常完成
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_completed", "task_id": task_id, "result": result.result, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)  # 从 _background_tasks 移除
                return f"Task Succeeded. Result: {result.result}"

            elif result.status == SubagentStatus.FAILED:
                # ❌ 子 Agent 执行失败
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_failed", "task_id": task_id, "error": result.error, "usage": usage})
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                return f"Task failed. Error: {result.error}"

            elif result.status == SubagentStatus.CANCELLED:
                # 🛑 子 Agent 被用户取消
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_cancelled", "task_id": task_id, "error": result.error, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return "Task cancelled by user."

            elif result.status == SubagentStatus.TIMED_OUT:
                # ⏰ 子 Agent 执行超时（被 SubagentExecutor 的线程池超时机制触发）
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error, "usage": usage})
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}"

            # ── 仍在执行中，等待 5 秒后继续轮询 ──
            await asyncio.sleep(5)
            poll_count += 1

            # ── 轮询层安全网超时 ──
            # 正常情况下线程池超时会先触发（SubagentExecutor 的 future.result(timeout=...)），
            # 这里是兜底：防止后台任务卡死（如死锁）导致轮询永不结束
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                writer({"type": "task_timed_out", "task_id": task_id, "usage": usage})

                # 通知后台工作线程协同取消（设置 cancel_event）
                request_cancel_background_task(task_id)
                # 启动延迟清理：等后台线程真正终止后再从 _background_tasks 移除
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"

    except asyncio.CancelledError:
        # ═══════════════════════════════════════════════════════════════
        # 主 Agent 被取消（用户中断或超时）
        # ═══════════════════════════════════════════════════════════════
        # 通知后台子 Agent 线程协同停止
        request_cancel_background_task(task_id)

        # 用 asyncio.shield 保护等待逻辑，确保即使外层再次取消，
        # 也能拿到子 Agent 的最终 token 用量快照并上报
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
        except asyncio.CancelledError:
            pass  # shield 也可能被穿透，此时放弃等待

        # 上报子 Agent 已收集的用量（即使超时未完成也要上报）
        final_result = terminal_result or get_background_task_result(task_id)
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)

        # 清理：如果已终止则立即清理，否则调度延迟清理
        if final_result is not None and _is_subagent_terminal(final_result):
            cleanup_background_task(task_id)
        else:
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)

        # 清理用量缓存，避免残留
        _subagent_usage_cache.pop(tool_call_id, None)
        raise  # 重新抛出 CancelledError，让 LangGraph 处理

    except Exception:
        # 其他未预期的异常：清理用量缓存后重新抛出
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
