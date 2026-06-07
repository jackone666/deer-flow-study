"""反馈相关端点——创建、列出、统计、删除。

允许用户对运行提交赞成/反对的反馈，可以选择性地限定到某一条具体消息。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_current_user, get_feedback_repo, get_run_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["feedback"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class FeedbackCreateRequest(BaseModel):
    """创建反馈请求体。"""

    rating: int = Field(..., description="反馈评分：+1（赞同）或 -1（反对）")
    comment: str | None = Field(default=None, description="可选的文本反馈")
    message_id: str | None = Field(default=None, description="可选：将反馈限定到某一条具体消息")


class FeedbackUpsertRequest(BaseModel):
    """创建或更新反馈请求体。"""

    rating: int = Field(..., description="反馈评分：+1（赞同）或 -1（反对）")
    comment: str | None = Field(default=None, description="可选的文本反馈")


class FeedbackResponse(BaseModel):
    """反馈响应模型。"""

    feedback_id: str
    run_id: str
    thread_id: str
    user_id: str | None = None
    message_id: str | None = None
    rating: int
    comment: str | None = None
    created_at: str = ""


class FeedbackStatsResponse(BaseModel):
    """反馈统计响应模型。"""

    run_id: str
    total: int = 0
    positive: int = 0
    negative: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.put("/{thread_id}/runs/{run_id}/feedback", response_model=FeedbackResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def upsert_feedback(
    thread_id: str,
    run_id: str,
    body: FeedbackUpsertRequest,
    request: Request,
) -> dict[str, Any]:
    """为某次运行创建或更新反馈（幂等操作）。"""
    if body.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be +1 or -1")

    user_id = await get_current_user(request)

    run_store = get_run_store(request)
    run = await run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.get("thread_id") != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found in thread {thread_id}")

    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.upsert(
        run_id=run_id,
        thread_id=thread_id,
        rating=body.rating,
        user_id=user_id,
        comment=body.comment,
    )


@router.delete("/{thread_id}/runs/{run_id}/feedback")
@require_permission("threads", "delete", owner_check=True, require_existing=True)
async def delete_run_feedback(
    thread_id: str,
    run_id: str,
    request: Request,
) -> dict[str, bool]:
    """删除当前用户对某次运行的反馈。"""
    user_id = await get_current_user(request)
    feedback_repo = get_feedback_repo(request)
    deleted = await feedback_repo.delete_by_run(
        thread_id=thread_id,
        run_id=run_id,
        user_id=user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="No feedback found for this run")
    return {"success": True}


@router.post("/{thread_id}/runs/{run_id}/feedback", response_model=FeedbackResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_feedback(
    thread_id: str,
    run_id: str,
    body: FeedbackCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """为某次运行提交反馈（赞成/反对）。"""
    if body.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be +1 or -1")

    user_id = await get_current_user(request)

    # Validate run exists and belongs to thread
    run_store = get_run_store(request)
    run = await run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.get("thread_id") != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found in thread {thread_id}")

    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.create(
        run_id=run_id,
        thread_id=thread_id,
        rating=body.rating,
        user_id=user_id,
        message_id=body.message_id,
        comment=body.comment,
    )


@router.get("/{thread_id}/runs/{run_id}/feedback", response_model=list[FeedbackResponse])
@require_permission("threads", "read", owner_check=True)
async def list_feedback(
    thread_id: str,
    run_id: str,
    request: Request,
) -> list[dict[str, Any]]:
    """列出某次运行的所有反馈。"""
    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.list_by_run(thread_id, run_id)


@router.get("/{thread_id}/runs/{run_id}/feedback/stats", response_model=FeedbackStatsResponse)
@require_permission("threads", "read", owner_check=True)
async def feedback_stats(
    thread_id: str,
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    """获取某次运行的反馈聚合统计（赞同/反对计数）。"""
    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.aggregate_by_run(thread_id, run_id)


@router.delete("/{thread_id}/runs/{run_id}/feedback/{feedback_id}")
@require_permission("threads", "delete", owner_check=True, require_existing=True)
async def delete_feedback(
    thread_id: str,
    run_id: str,
    feedback_id: str,
    request: Request,
) -> dict[str, bool]:
    """删除一条反馈记录。"""
    feedback_repo = get_feedback_repo(request)
    # Verify feedback belongs to the specified thread/run before deleting
    existing = await feedback_repo.get(feedback_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Feedback {feedback_id} not found")
    if existing.get("thread_id") != thread_id or existing.get("run_id") != run_id:
        raise HTTPException(status_code=404, detail=f"Feedback {feedback_id} not found in run {run_id}")
    deleted = await feedback_repo.delete(feedback_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Feedback {feedback_id} not found")
    return {"success": True}
