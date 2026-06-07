"""无状态的 runs 端点——在不预先创建线程的情况下进行流式或阻塞式调用。

当请求体中没有提供 ``thread_id`` 时，这些端点会自动创建一个临时线程。
当请求体中提供了 ``thread_id`` 时，则会复用该线程，使跨调用的会话历史得以保留。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.gateway.authz import require_permission
from app.gateway.deps import get_checkpointer, get_feedback_repo, get_run_event_store, get_run_manager, get_run_store, get_stream_bridge
from app.gateway.pagination import trim_run_message_page
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import sse_consumer, start_run, wait_for_run_completion
from deerflow.runtime import serialize_channel_values

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runs", tags=["runs"])


def _resolve_thread_id(body: RunCreateRequest) -> str:
    """从请求体中取出 ``thread_id``，若不存在则生成一个新的。"""
    thread_id = (body.config or {}).get("configurable", {}).get("thread_id")
    if thread_id:
        return str(thread_id)
    return str(uuid.uuid4())


@router.post("/stream")
async def stateless_stream(body: RunCreateRequest, request: Request) -> StreamingResponse:
    """创建一个运行并通过 SSE 流式返回事件。

    如果请求中提供了 ``config.configurable.thread_id``，则在该线程上创建运行，
    以便保留会话历史。否则会创建一个新的临时线程。
    """
    thread_id = _resolve_thread_id(body)
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/wait", response_model=dict)
async def stateless_wait(body: RunCreateRequest, request: Request) -> dict:
    """创建一个运行并阻塞等待其完成。

    如果请求中提供了 ``config.configurable.thread_id``，则在该线程上创建运行，
    以便保留会话历史。否则会创建一个新的临时线程。
    """
    thread_id = _resolve_thread_id(body)
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    completed = True
    if record.task is not None:
        completed = await wait_for_run_completion(bridge, record, request, run_mgr)

    if completed:
        checkpointer = get_checkpointer(request)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            checkpoint_tuple = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is not None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                channel_values = checkpoint.get("channel_values", {})
                return serialize_channel_values(channel_values)
        except Exception:
            logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": record.status.value, "error": record.error}


# ---------------------------------------------------------------------------
# Run-scoped read endpoints
# ---------------------------------------------------------------------------


async def _resolve_run(run_id: str, request: Request) -> dict:
    """按 ``run_id`` 拉取运行记录并执行用户归属校验。不存在时抛出 404。"""
    run_store = get_run_store(request)
    record = await run_store.get(run_id)  # user_id=AUTO filters by contextvar
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record


@router.get("/{run_id}/messages")
@require_permission("runs", "read")
async def run_messages(
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """返回某次运行的分页消息（基于游标）。

    分页规则：
    - ``after_seq``：获取 ``seq > after_seq`` 的消息（向前翻页）
    - ``before_seq``：获取 ``seq < before_seq`` 的消息（向后翻页）
    - 两者都未提供：取最新的消息

    响应格式：``{ data: [...], has_more: bool }``
    """
    run = await _resolve_run(run_id, request)
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages_by_run(
        run["thread_id"],
        run_id,
        limit=limit + 1,
        before_seq=before_seq,
        after_seq=after_seq,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    return {"data": data, "has_more": has_more}


@router.get("/{run_id}/feedback")
@require_permission("runs", "read")
async def run_feedback(run_id: str, request: Request) -> list[dict]:
    """返回某次运行的所有反馈。"""
    run = await _resolve_run(run_id, request)
    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.list_by_run(run["thread_id"], run_id)
