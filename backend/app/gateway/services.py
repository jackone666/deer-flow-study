"""运行（Run）生命周期的服务层。

集中处理创建运行、格式化 SSE 帧以及消费流桥接事件等业务逻辑。
路由器模块（``thread_runs``、``runs``）是轻量的 HTTP 处理器，会将核心逻辑委托到本模块。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

from app.gateway.deps import get_run_context, get_run_manager, get_stream_bridge
from app.gateway.internal_auth import INTERNAL_SYSTEM_ROLE
from app.gateway.utils import sanitize_log_param
from deerflow.config.app_config import get_app_config
from deerflow.runtime import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    ConflictError,
    DisconnectMode,
    RunManager,
    RunRecord,
    RunStatus,
    StreamBridge,
    UnsupportedStrategyError,
    run_agent,
)
from deerflow.runtime.runs.naming import resolve_root_run_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    """格式化单个 SSE 帧。

    字段顺序为：``event:`` -> ``data:`` -> ``id:``（可选）-> 空行。
    该格式与 LangGraph Platform 的线协议一致，兼容 ``useStream`` React Hook
    以及 Python 版 ``langgraph-sdk`` 的 SSE 解码器。

    Args:
        event: 事件名称。
        data: 事件数据载荷（可被 JSON 序列化）。
        event_id: 可选的事件 ID。

    Returns:
        拼装好的 SSE 帧字符串。
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    parts = [f"event: {event}", f"data: {payload}"]
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append("")
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Input / config helpers
# ---------------------------------------------------------------------------


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """将 ``stream_mode`` 参数标准化为字符串列表。

    默认值与 ``useStream`` 期望保持一致：``values`` + ``messages-tuple``。

    Args:
        raw: 原始 ``stream_mode`` 参数，可以是单个字符串、字符串列表或 ``None``。

    Returns:
        标准化后的字符串列表。
    """
    if raw is None:
        return ["values"]
    if isinstance(raw, str):
        return [raw]
    return raw if raw else ["values"]


def normalize_input(raw_input: dict[str, Any] | None) -> dict[str, Any]:
    """将 LangGraph Platform 的输入格式转换为 LangChain 状态字典。

    字典到消息对象的转换交由 ``langchain_core.messages.utils.convert_to_messages``
    完成，这样 ``additional_kwargs``（例如 gh #3132 中的上传文件元数据）、``id``、
    ``name`` 以及非人类角色（ai/system/tool）都能保持原样。早期的自实现版本只透传
    ``content`` 字段并将所有角色折叠为 ``HumanMessage``，导致前端传入的附件被静默丢弃。

    格式不正确的消息字典（缺少 ``role``/``type``/``content``、不支持的角色等）
    会抛出 ``HTTPException(400)`` 并附带出错索引，而不是冒泡成 500。
    Gateway 作为系统边界，逐条校验错误才是便于客户端重试的正确形态。

    Args:
        raw_input: LangGraph Platform 风格的输入字典，可能为 ``None``。

    Returns:
        转换后的 LangChain 状态字典。
    """
    if raw_input is None:
        return {}
    messages = raw_input.get("messages")
    if messages and isinstance(messages, list):
        converted: list[Any] = []
        for index, msg in enumerate(messages):
            if isinstance(msg, BaseMessage):
                converted.append(msg)
            elif isinstance(msg, dict):
                try:
                    converted.extend(convert_to_messages([msg]))
                except (ValueError, TypeError, NotImplementedError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid message at input.messages[{index}]: {exc}",
                    ) from exc
            else:
                converted.append(msg)
        return {**raw_input, "messages": converted}
    return raw_input


_DEFAULT_ASSISTANT_ID = "lead_agent"


# Whitelist of run-context keys that the langgraph-compat layer forwards from
# ``body.context`` into the run config. ``config["context"]`` exists in
# LangGraph >=0.6, but these values must be written to both ``configurable``
# (for legacy ``_get_runtime_config`` consumers) and ``context`` because
# LangGraph >=1.1.9 no longer makes ``ToolRuntime.context`` fall back to
# ``configurable`` for consumers like ``setup_agent``.
_CONTEXT_CONFIGURABLE_KEYS: frozenset[str] = frozenset(
    {
        "model_name",
        "mode",
        "thinking_enabled",
        "reasoning_effort",
        "is_plan_mode",
        "subagent_enabled",
        "max_concurrent_subagents",
        "agent_name",
        "is_bootstrap",
    }
)


def merge_run_context_overrides(config: dict[str, Any], context: Mapping[str, Any] | None) -> None:
    """将白名单中的键从 ``body.context`` 合并到 ``config['configurable']`` 与
    ``config['context']`` 两处，以便兼容旧的可配置读取器以及 LangGraph 的
    ``ToolRuntime.context`` 消费者（例如 ``setup_agent`` 工具——参见 issue #2677）。

    除白名单键以外，``user_id`` 也会被显式写入 ``config['context']``，使得非 Web
    调用方（例如 IM 频道）在 ``body.context`` 中提供身份时，该身份仍能保留在
    ``ToolRuntime.context`` 上。合并时使用 ``setdefault``，因此由
    :func:`inject_authenticated_user_context` 写入的服务端认证 ID 总是优先于客户端传入的值。
    """
    if not context:
        return
    configurable = config.setdefault("configurable", {})
    runtime_context = config.setdefault("context", {})
    for key in _CONTEXT_CONFIGURABLE_KEYS:
        if key in context:
            if isinstance(configurable, dict):
                configurable.setdefault(key, context[key])
            if isinstance(runtime_context, dict):
                runtime_context.setdefault(key, context[key])
    if "user_id" in context and isinstance(runtime_context, dict):
        runtime_context.setdefault("user_id", context["user_id"])


def inject_authenticated_user_context(config: dict[str, Any], request: Request) -> None:
    """将已认证用户写入运行上下文，以便后台工具使用。

    工具执行可能在请求处理函数返回之后才发生，因此负责持久化用户范围文件的工具
    不能仅依赖环境中的 ContextVar。该值来自服务端认证状态，永远不取自客户端上下文。
    """

    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return

    if getattr(user, "system_role", None) == INTERNAL_SYSTEM_ROLE:
        return

    runtime_context = config.setdefault("context", {})
    if isinstance(runtime_context, dict):
        runtime_context["user_id"] = str(user_id)


def resolve_agent_factory(assistant_id: str | None):
    """根据配置解析生成代理的工厂可调用对象。

    自定义代理都实现为 ``lead_agent`` 加上注入到 ``configurable`` 或 ``context``
    的 ``agent_name``，详见 :func:`build_run_config`。因此所有 ``assistant_id`` 值
    都映射到同一个工厂，路由逻辑由 ``make_lead_agent`` 读取 ``cfg["agent_name"]`` 时完成。
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent


def build_run_config(
    thread_id: str,
    request_config: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    *,
    assistant_id: str | None = None,
) -> dict[str, Any]:
    """为代理构建 ``RunnableConfig`` 字典。

    当 ``assistant_id`` 指向一个自定义代理（除 ``"lead_agent"`` / ``None`` 之外的任何值）时，
    其名称会以 ``agent_name`` 形式写入当前激活的运行时选项容器：LangGraph >= 0.6.0
    的请求使用 ``context``，否则使用 ``configurable``。
    ``make_lead_agent`` 会读取该键以加载对应的 ``agents/<name>/SOUL.md`` 和该代理的配置——
    若缺失该键，代理将悄无声息地以默认 lead agent 身份运行。

    本函数镜像了 channel manager 中 ``_resolve_run_params`` 的逻辑，从而保证
    LangGraph Platform 兼容的 HTTP API 与 IM 频道路径行为完全一致。

    Args:
        thread_id: 线程 ID。
        request_config: 客户端传入的可选 ``config`` 字典。
        metadata: 客户端传入的可选元数据字典。
        assistant_id: 可选的助手 ID，用于指代自定义代理。

    Returns:
        构造好的 ``RunnableConfig`` 字典。
    """
    config: dict[str, Any] = {"recursion_limit": 100}
    if request_config:
        # LangGraph >= 0.6.0 introduced ``context`` as the preferred way to
        # pass thread-level data and rejects requests that include both
        # ``configurable`` and ``context``.  If the caller already sends
        # ``context``, honour it and skip our own ``configurable`` dict.
        if "context" in request_config:
            if "configurable" in request_config:
                logger.warning(
                    "build_run_config: client sent both 'context' and 'configurable'; preferring 'context' (LangGraph >= 0.6.0). thread_id=%s, caller_configurable keys=%s",
                    thread_id,
                    list(request_config.get("configurable", {}).keys()),
                )
            context_value = request_config["context"]
            if context_value is None:
                context = {}
            elif isinstance(context_value, Mapping):
                context = dict(context_value)
            else:
                raise ValueError("request config 'context' must be a mapping or null.")
            config["context"] = context
        else:
            configurable = {"thread_id": thread_id}
            configurable.update(request_config.get("configurable", {}))
            config["configurable"] = configurable
        for k, v in request_config.items():
            if k not in ("configurable", "context"):
                config[k] = v
    else:
        config["configurable"] = {"thread_id": thread_id}

    # Inject custom agent name when the caller specified a non-default assistant.
    # Honour an explicit agent_name in the active runtime options container.
    if assistant_id and assistant_id != _DEFAULT_ASSISTANT_ID:
        normalized = assistant_id.strip().lower().replace("_", "-")
        if not normalized or not re.fullmatch(r"[a-z0-9-]+", normalized):
            raise ValueError(f"Invalid assistant_id {assistant_id!r}: must contain only letters, digits, and hyphens after normalization.")
        if "configurable" in config:
            target = config["configurable"]
        elif "context" in config:
            target = config["context"]
        else:
            target = config.setdefault("configurable", {})
        if target is not None and "agent_name" not in target:
            target["agent_name"] = normalized
        config.setdefault("run_name", resolve_root_run_name(config, normalized))
    if metadata:
        config.setdefault("metadata", {}).update(metadata)
    return config


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


async def start_run(
    body: Any,
    thread_id: str,
    request: Request,
) -> RunRecord:
    """创建 ``RunRecord`` 并启动后台代理任务。

    Parameters
    ----------
    body : RunCreateRequest
        校验后的请求体（声明为 ``Any`` 以避免与定义 Pydantic 模型的路由器模块产生循环导入）。
    thread_id : str
        目标线程 ID。
    request : Request
        FastAPI 请求对象——用于从 ``app.state`` 取出单例。
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)

    disconnect = DisconnectMode.cancel if body.on_disconnect == "cancel" else DisconnectMode.continue_

    body_context = getattr(body, "context", None) or {}
    model_name = body_context.get("model_name")

    # Coerce non-string model_name values to str before truncation.
    if model_name is not None and not isinstance(model_name, str):
        model_name = str(model_name)

    # Validate model against the allowlist when a model_name is provided.
    if model_name:
        app_config = get_app_config()
        resolved = app_config.get_model_config(model_name)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_name!r} is not in the configured model allowlist",
            )

    try:
        record = await run_mgr.create_or_reject(
            thread_id,
            body.assistant_id,
            on_disconnect=disconnect,
            metadata=body.metadata or {},
            kwargs={"input": body.input, "config": body.config},
            multitask_strategy=body.multitask_strategy,
            model_name=model_name,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedStrategyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # Upsert thread metadata so the thread appears in /threads/search,
    # even for threads that were never explicitly created via POST /threads
    # (e.g. stateless runs).
    try:
        existing = await run_ctx.thread_store.get(thread_id)
        if existing is None:
            await run_ctx.thread_store.create(
                thread_id,
                assistant_id=body.assistant_id,
                metadata=body.metadata,
            )
        else:
            await run_ctx.thread_store.update_status(thread_id, "running")
    except Exception:
        logger.warning("Failed to upsert thread_meta for %s (non-fatal)", sanitize_log_param(thread_id))

    agent_factory = resolve_agent_factory(body.assistant_id)
    graph_input = normalize_input(body.input)
    config = build_run_config(thread_id, body.config, body.metadata, assistant_id=body.assistant_id)

    # Merge DeerFlow-specific context overrides into both ``configurable`` and ``context``.
    # The ``context`` field is a custom extension for the langgraph-compat layer
    # that carries agent configuration (model_name, thinking_enabled, etc.).
    # Only agent-relevant keys are forwarded; unknown keys (e.g. thread_id) are ignored.
    merge_run_context_overrides(config, getattr(body, "context", None))
    inject_authenticated_user_context(config, request)

    stream_modes = normalize_stream_modes(body.stream_mode)

    task = asyncio.create_task(
        run_agent(
            bridge,
            run_mgr,
            record,
            ctx=run_ctx,
            agent_factory=agent_factory,
            graph_input=graph_input,
            config=config,
            stream_modes=stream_modes,
            stream_subgraphs=body.stream_subgraphs,
            interrupt_before=body.interrupt_before,
            interrupt_after=body.interrupt_after,
        )
    )
    record.task = task

    # Title sync is handled by worker.py's finally block which reads the
    # title from the checkpoint and calls thread_store.update_display_name
    # after the run completes.

    return record


async def sse_consumer(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
):
    """从流桥接器消费事件并产出 SSE 帧的异步生成器。

    ``finally`` 块实现了 ``on_disconnect`` 语义：
    - ``cancel``：在客户端断开时中止后台任务。
    - ``continue``：让任务继续运行，事件直接丢弃。
    """
    last_event_id = request.headers.get("Last-Event-ID")
    try:
        async for entry in bridge.subscribe(record.run_id, last_event_id=last_event_id):
            if await request.is_disconnected():
                break

            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
                continue

            if entry is END_SENTINEL:
                yield format_sse("end", None, event_id=entry.id or None)
                return

            yield format_sse(entry.event, entry.data, event_id=entry.id or None)

    finally:
        if record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)


async def wait_for_run_completion(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
) -> bool:
    """阻塞等待运行发布 ``END_SENTINEL``，并尊重 ``on_disconnect`` 语义。

    之前的非流式 ``/wait`` 端点直接 ``await record.task``，没有处理客户端断开的情况。
    当客户端（或中间 HTTP 代理）在 ``pip install`` 这类长工具调用中超时时，处理器
    会吞掉 ``CancelledError`` 并把当时恰好存在的检查点序列化返回——把一个未完成
    的运行伪装成正常完成（issue #3265）。

    本辅助函数复用 ``sse_consumer`` 所用的同一个流桥接器，因此等待路径与流式路径
    共享断开处理语义：每次唤醒都会轮询 ``request.is_disconnected()``；在真正断开
    且 ``record.on_disconnect`` 为 ``cancel`` 时取消后台运行。当代理长时间没有事件
    产出时，桥接器的心跳哨兵保证至少每个 ``heartbeat_interval`` 触发一次唤醒。

    Returns:
        当观察到 ``END_SENTINEL``（运行进入终止状态）时返回 ``True``；当因客户端断开
        而退出循环时返回 ``False``。调用方必须在返回 ``False`` 时跳过检查点序列化，
        以免把部分检查点当作正常响应返回。
    """
    completed = False
    try:
        async for entry in bridge.subscribe(record.run_id):
            # END_SENTINEL means the run reached a terminal state; honour it
            # even if the client just disconnected so the caller still serializes
            # the real final checkpoint.
            if entry is END_SENTINEL:
                completed = True
                return True
            if await request.is_disconnected():
                break
            # Heartbeats and regular events: keep waiting for END_SENTINEL.
        return completed
    finally:
        if not completed and record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)
