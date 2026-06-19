"""后台 Agent 执行。

在 ``asyncio.Task`` 内运行 Agent 图，把产生的事件发布到
:class:`StreamBridge`。

使用 ``graph.astream(stream_mode=[...])``：``values`` 模式拿到完整状态
快照，``updates`` 拿到 ``{node: writes}``，``messages`` 拿到
``(chunk, metadata)`` 元组。

注意：gateway 路径不支持 ``events`` 模式——它需要 ``astream_events``，
而后者无法同时产出 ``values`` 快照。开源 JS 版 LangGraph API server 通过
未公开的 Python checkpoint 回调规避了这一点。
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, cast

from langgraph.checkpoint.base import empty_checkpoint

if TYPE_CHECKING:
    from langchain_core.messages import HumanMessage

from deerflow.config.app_config import AppConfig
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tracing import inject_langfuse_metadata

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus

logger = logging.getLogger(__name__)

# LangGraph graph.astream() 支持的原生 stream_mode 集合。
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}


def _build_runtime_context(
    thread_id: str,
    run_id: str,
    caller_context: Any | None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """构造将作为 ``ToolRuntime.context`` 暴露的字典。

    始终包含 ``thread_id`` 与 ``run_id``；调用方 ``config['context']``
    中携带的其他键（如 bootstrap 流程里的 ``agent_name``——issue #2677）
    会被合并进来，但绝不会覆盖 ``thread_id``/``run_id``。已解析的
    :class:`AppConfig` 由 worker 注入，让工具无需做全局查找即可读取。

    langgraph 1.1+ 通过存放在 ``config['configurable']['__pregel_runtime']``
    的父 runtime 将其暴露为 ``runtime.context``——见
    ``langgraph.pregel.main`` 中 ``parent_runtime.merge(...)`` 的调用。

    Args:
        thread_id: 当前线程 ID。
        run_id: 当前 Run ID。
        caller_context: 调用方 ``config['context']``（可空）。
        app_config: 解析后的全局 AppConfig（可空）。

    Returns:
        合并后的运行时上下文字典。
    """
    runtime_ctx: dict[str, Any] = {"thread_id": thread_id, "run_id": run_id}
    if isinstance(caller_context, dict):
        for key, value in caller_context.items():
            runtime_ctx.setdefault(key, value)
    if app_config is not None:
        runtime_ctx["app_config"] = app_config
    return runtime_ctx


@dataclass(frozen=True)
class RunContext:
    """单个 Agent Run 所需的基础设施依赖集合。

    Attributes:
        checkpointer: LangGraph Checkpointer。
        store: LangGraph Store（可空）。
        event_store: :class:`RunEventStore`（可空）。
        run_events_config: RunEvent 相关配置。
        thread_store: 线程元数据存储。
        app_config: 全局 AppConfig。
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)


def _install_runtime_context(config: dict, runtime_context: dict[str, Any]) -> None:
    """把运行时上下文安装到 LangGraph 的 ``config['context']`` 中。

    如果已有 ``context`` 字典，则以 ``setdefault`` 方式注入关键字段；
    否则直接用运行时上下文替换。``app_config`` 始终以最新值为准。
    """
    existing_context = config.get("context")
    if isinstance(existing_context, dict):
        existing_context.setdefault("thread_id", runtime_context["thread_id"])
        existing_context.setdefault("run_id", runtime_context["run_id"])
        if "app_config" in runtime_context:
            existing_context["app_config"] = runtime_context["app_config"]
        return

    config["context"] = dict(runtime_context)


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    """直接判断 ``agent_factory`` 是否支持 ``app_config`` 关键字。"""
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    """``_compute_agent_factory_supports_app_config`` 的 LRU 缓存版本。"""
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    """通过 LRU 缓存判断 ``agent_factory`` 是否接受 ``app_config`` 参数。"""
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # 部分可调用实例不可哈希，无法进 lru_cache；退回直接检查。
        return _compute_agent_factory_supports_app_config(agent_factory)


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """在后台执行 Agent 并把事件发布到 *bridge*。

    Args:
        bridge: 接收流式事件的 :class:`StreamBridge`。
        run_manager: 用于更新 Run 状态的 :class:`RunManager`。
        record: 当前 :class:`RunRecord`。
        ctx: 包含 checkpointer/store/event_store 等依赖的 :class:`RunContext`。
        agent_factory: 构造 Agent 图的可调用对象。
        graph_input: 传给图的初始输入。
        config: LangGraph ``RunnableConfig``（会被原地修改以注入 runtime context）。
        stream_modes: 客户端请求的流式模式列表。
        stream_subgraphs: 是否把子图也作为独立命名空间产出。
        interrupt_before: 指定的节点在执行前中断（用于人工审批）。
        interrupt_after: 指定的节点在执行后中断。
    """

    # 拆出本次运行所需的基础设施依赖，后续会挂到 Agent 图或回调上。
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store

    run_id = record.run_id
    thread_id = record.thread_id
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    snapshot_capture_failed = False
    llm_error_fallback_message: str | None = None

    journal = None

    # gateway 模式无法同时使用 events 与 values 快照，因此只记录并跳过。
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        # 初始化 RunJournal。放在 try 内是为了让事件存储失败也能进入统一的
        # except/finally 路径，最终向 SSE bridge 发布 end，避免前端流悬挂。
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                track_token_usage=getattr(run_events_config, "track_token_usage", True),
                progress_reporter=lambda snapshot: run_manager.update_run_progress(run_id, **snapshot),
            )

        # 1. 标记 Run 进入运行态。
        await run_manager.set_status(run_id, RunStatus.running)

        # 运行前保存 checkpoint 快照，失败或回滚时可恢复到本轮之前。
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }
            except Exception:
                snapshot_capture_failed = True
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. 发布元数据；前端 useStream 同时需要 run_id 和 thread_id。
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. 构建 Agent 图。
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # 手动把运行时上下文注入 config 和 __pregel_runtime。gateway 直接调用
        # agent.astream(config=...)，不会像 langgraph-cli 那样自动传 context。
        # 这一步决定中间件和工具能否通过 runtime.context 读到 thread_id/run_id。
        runtime_ctx = _build_runtime_context(thread_id, run_id, config.get("context"), ctx.app_config)
        # 将本轮 RunJournal 暴露给中间件，用于写审计事件；双下划线前缀表示内部通道。
        if journal is not None:
            runtime_ctx["__run_journal"] = journal
        _install_runtime_context(config, runtime_ctx)
        runtime = Runtime(context=cast(Any, runtime_ctx), store=store)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # RunJournal 也作为 LangChain callback：记录 LLM token 和链路生命周期事件。
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # 注入 Langfuse trace 属性，供 callback handler 提升到根 trace。
        # 与 DeerFlowClient.stream 共用 helper，保持两个入口的元数据语义一致。
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=record.assistant_id,
            model_name=record.model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        # 在 runtime context 安装后再解析 run_name，确保 agent_name 反映实际执行目标。
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        runnable_config = RunnableConfig(**config)
        if ctx.app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=runnable_config, app_config=ctx.app_config)
        else:
            agent = agent_factory(config=runnable_config)

        # Agent 工厂可能把非法模型名回退到默认模型；这里同步持久化“实际使用模型”。
        if record.model_name is not None:
            resolved = getattr(agent, "metadata", {}) or {}
            if isinstance(resolved, dict):
                effective = resolved.get("model_name")
                if effective and effective != record.model_name:
                    await run_manager.update_model_name(record.run_id, effective)

        # 4. 挂载 checkpointer 和 store，决定消息历史、状态和持久化读写。
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. 配置图中断点，用于人工审批或调试。
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. 构造 LangGraph stream_mode 列表：
        #    events 不是 astream 模式；messages-tuple 映射为 LangGraph 的 messages。
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                    # 已在上方记录原因，这里只跳过。
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # 去重但保持请求顺序，避免重复订阅同一种流。
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # 7. 通过 graph.astream 推送状态、消息增量和自定义事件。
        if len(lg_modes) == 1 and not stream_subgraphs:
            # 单模式且无子图时，astream 直接产出原始 chunk。
            single_mode = lg_modes[0]
            async for chunk in agent.astream(graph_input, config=runnable_config, stream_mode=single_mode):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break
                llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk)
                sse_event = _lg_mode_to_sse_event(single_mode)
                await bridge.publish(run_id, sse_event, serialize(chunk, mode=single_mode))
        else:
            # 多模式或子图时，astream 产出带模式/命名空间的 tuple。
            async for item in agent.astream(
                graph_input,
                config=runnable_config,
                stream_mode=lg_modes,
                subgraphs=stream_subgraphs,
            ):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break

                mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                if mode is None:
                    continue

                llm_error_fallback_message = llm_error_fallback_message or _extract_llm_error_fallback_message(chunk)
                sse_event = _lg_mode_to_sse_event(mode)
                await bridge.publish(run_id, sse_event, serialize(chunk, mode=mode))

        # 8. Final status
        if record.abort_event.is_set():
            action = record.abort_action
            if action == "rollback":
                await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception:
                    logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
            else:
                await run_manager.set_status(run_id, RunStatus.interrupted)
        elif llm_error_fallback_message or (journal is not None and journal.had_llm_error_fallback):
            error_msg = llm_error_fallback_message
            if error_msg is None and journal is not None:
                error_msg = journal.llm_error_fallback_message
            error_msg = error_msg or "LLM provider failed after retries"
            await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        else:
            await run_manager.set_status(run_id, RunStatus.success)

    except asyncio.CancelledError:
        action = record.abort_action
        if action == "rollback":
            await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception:
                logger.warning("Run %s cancellation rollback failed", run_id, exc_info=True)
        else:
            await run_manager.set_status(run_id, RunStatus.interrupted)
            logger.info("Run %s was cancelled", run_id)

    except Exception as exc:
        error_msg = f"{exc}"
        logger.exception("Run %s failed: %s", run_id, error_msg)
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    finally:
        # 冲刷 RunJournal 缓冲事件，并持久化本轮完成数据。
        if journal is not None:
            try:
                await journal.flush()
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            try:
                # 持久化 token 用量和首尾消息等便捷字段。
                completion = journal.get_completion_data()
                await run_manager.update_run_completion(run_id, status=record.status.value, **completion)
            except Exception:
                logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        # 从 checkpoint 同步标题到线程元数据，供侧边栏展示。
        if checkpointer is not None and thread_store is not None:
            try:
                ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                if ckpt_tuple is not None:
                    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                    title = ckpt.get("channel_values", {}).get("title")
                    if title:
                        await thread_store.update_display_name(thread_id, title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id)

        # 根据 Run 结果更新线程状态，成功后回到 idle。
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await thread_store.update_status(thread_id, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        await bridge.publish_end(run_id)
        asyncio.create_task(bridge.cleanup(run_id, delay=60))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _call_checkpointer_method(checkpointer: Any, async_name: str, sync_name: str, *args: Any, **kwargs: Any) -> Any:
    """调用一个 checkpointer 方法，同时支持异步与同步变体。"""
    method = getattr(checkpointer, async_name, None) or getattr(checkpointer, sync_name, None)
    if method is None:
        raise AttributeError(f"Missing checkpointer method: {async_name}/{sync_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """把线程状态恢复到 Run 启动前捕获的检查点快照。"""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    restore_marker = _new_checkpoint_marker()
    checkpoint_to_restore = {
        **checkpoint_to_restore,
        "id": restore_marker["id"],
        "ts": restore_marker["ts"],
    }
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _new_checkpoint_marker() -> dict[str, str]:
    """生成一个全新的检查点 ``id``/``ts`` 标记，避免与历史检查点冲突。"""
    marker = empty_checkpoint()
    return {"id": marker["id"], "ts": marker["ts"]}


def _lg_mode_to_sse_event(mode: str) -> str:
    """把 LangGraph 内部 ``stream_mode`` 名映射为 SSE 事件名。

    LangGraph 的 ``astream(stream_mode="messages")`` 产出 message 元组。
    客户端显式请求时 SSE 协议会叫它 ``"messages-tuple"``，但 LangGraph
    Platform 默认的 SSE 事件名是 ``"messages"``。

    Args:
        mode: LangGraph 的 stream_mode 名。

    Returns:
        与 SSE 协议约定一致的事件名。
    """
    # 当前协议中 LangGraph 模式名与 SSE 事件名一一对应。
    return mode


def _error_fallback_message_from_metadata(metadata: dict[str, Any], content: Any) -> str:
    """从 ``deerflow_error_fallback`` 上下文中抽取可读错误信息。"""
    detail = metadata.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    reason = metadata.get("error_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()[:2000]
    return "LLM provider failed after retries"


def _try_extract_from_message(obj: Any) -> str | None:
    """尝试从单个消息对象或 dict 中抽取 ``deerflow_error_fallback`` 标记。"""
    additional_kwargs = getattr(obj, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("deerflow_error_fallback"):
        return _error_fallback_message_from_metadata(additional_kwargs, getattr(obj, "content", None))

    if isinstance(obj, dict):
        nested_kwargs = obj.get("additional_kwargs")
        if isinstance(nested_kwargs, dict) and nested_kwargs.get("deerflow_error_fallback"):
            return _error_fallback_message_from_metadata(nested_kwargs, obj.get("content"))
    return None


def _extract_llm_error_fallback_message(value: Any) -> str | None:
    """在流式 LangGraph chunk 中查找 LLM fallback 标记。

    模型调用中间件返回的错误回退消息并不保证经过 ``on_llm_end`` 回调，
    但会出现在 graph state chunk 中。

    Args:
        value: 单个流式 chunk。

    Returns:
        找到时返回错误描述，否则 ``None``。
    """
    # 快路径：values chunk 通常有顶层 messages，扫描它即可，避免递归大状态对象。
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, (list, tuple)):
            for msg in messages:
                result = _try_extract_from_message(msg)
                if result is not None:
                    return result
            # fallback 标记只会挂在 messages 通道的 AIMessage 上。
            return None
        # 没有顶层 messages 时，多半是 updates chunk；体积小，可以走递归扫描。

    # updates/messages/tuple/list 模式的载荷较小，完整递归扫描成本可接受。
    seen: set[int] = set()

    def walk(obj: Any) -> str | None:
        """递归扫描对象树，找到第一条 LLM fallback 错误消息。"""
        oid = id(obj)
        if oid in seen:
            return None
        seen.add(oid)

        result = _try_extract_from_message(obj)
        if result is not None:
            return result

        if isinstance(obj, dict):
            for item in obj.values():
                result = walk(item)
                if result is not None:
                    return result
            return None

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                result = walk(item)
                if result is not None:
                    return result
        return None

    return walk(value)


def _extract_human_message(graph_input: dict) -> HumanMessage | None:
    """从 ``graph_input`` 中抽取或构造一个 :class:`HumanMessage`，用于事件记录。

    返回 LangChain :class:`HumanMessage`，方便调用方通过 ``.model_dump()``
    获得与 checkpoint 对齐的序列化格式。

    Args:
        graph_input: 传给图执行的初始输入。

    Returns:
        抽取得到的消息；若找不到有效内容则返回 ``None``。
    """
    from langchain_core.messages import HumanMessage

    messages = graph_input.get("messages")
    if not messages:
        return None
    last = messages[-1] if isinstance(messages, list) else messages
    if isinstance(last, HumanMessage):
        return last
    if isinstance(last, str):
        return HumanMessage(content=last) if last else None
    if hasattr(last, "content"):
        content = last.content
        return HumanMessage(content=content)
    if isinstance(last, dict):
        content = last.get("content", "")
        return HumanMessage(content=content) if content else None
    return None


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """把多模式或子图流式 item 解包为 ``(mode, chunk)``。

    Args:
        item: ``astream`` 产出的单个流式条目。
        lg_modes: 当前请求的 LangGraph 模式列表。
        stream_subgraphs: 是否启用了子图流式。

    Returns:
        ``(mode, chunk)``；无法解析时返回 ``(None, None)``。
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # 兜底：按第一个模式解释单元素输出。
    return lg_modes[0] if lg_modes else None, item
