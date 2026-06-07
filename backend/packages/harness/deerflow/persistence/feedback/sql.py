"""基于 SQLAlchemy 的反馈存储。

每个方法各自获取并释放短生命周期的 session。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso


class FeedbackRepository:
    """feedback 表的 SQLAlchemy repository。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """构造 repository。

        Args:
            session_factory: 异步 session 工厂。
        """
        self._sf = session_factory

    @staticmethod
    def _row_to_dict(row: FeedbackRow) -> dict:
        """把 :class:`FeedbackRow` 序列化为 dict，``created_at`` 归一化时区。"""
        d = row.to_dict()
        val = d.get("created_at")
        if isinstance(val, datetime):
            # SQLite 读出时会丢掉 tzinfo；通过 ``coerce_iso`` 归一化，确保带 tz。
            d["created_at"] = coerce_iso(val)
        return d

    async def create(
        self,
        *,
        run_id: str,
        thread_id: str,
        rating: int,
        user_id: str | None | _AutoSentinel = AUTO,
        message_id: str | None = None,
        comment: str | None = None,
    ) -> dict:
        """创建一条 feedback 记录，``rating`` 必须是 ``+1`` 或 ``-1``。

        Args:
            run_id: 目标 run ID。
            thread_id: 所在 thread ID。
            rating: 评分，+1 或 -1。
            user_id: 用户 ID；``AUTO`` 时回退到当前请求用户。
            message_id: 可选的 RunEventStore 事件 ID。
            comment: 可选文字反馈。

        Returns:
            dict: 写入后的记录字典。

        Raises:
            ValueError: ``rating`` 不是 ``+1`` 或 ``-1``。
        """
        if rating not in (1, -1):
            raise ValueError(f"rating must be +1 or -1, got {rating}")
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.create")
        row = FeedbackRow(
            feedback_id=str(uuid.uuid4()),
            run_id=run_id,
            thread_id=thread_id,
            user_id=resolved_user_id,
            message_id=message_id,
            rating=rating,
            comment=comment,
            created_at=datetime.now(UTC),
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_dict(row)

    async def get(
        self,
        feedback_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> dict | None:
        """按 ID 获取一条 feedback；非所属用户返回 ``None``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.get")
        async with self._sf() as session:
            row = await session.get(FeedbackRow, feedback_id)
            if row is None:
                return None
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def list_by_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        limit: int = 100,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict]:
        """列出指定 ``(thread_id, run_id)`` 的 feedback。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.list_by_run")
        stmt = select(FeedbackRow).where(FeedbackRow.thread_id == thread_id, FeedbackRow.run_id == run_id)
        if resolved_user_id is not None:
            stmt = stmt.where(FeedbackRow.user_id == resolved_user_id)
        stmt = stmt.order_by(FeedbackRow.created_at.asc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict]:
        """列出指定 thread 的 feedback。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.list_by_thread")
        stmt = select(FeedbackRow).where(FeedbackRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(FeedbackRow.user_id == resolved_user_id)
        stmt = stmt.order_by(FeedbackRow.created_at.asc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def delete(
        self,
        feedback_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> bool:
        """按 ID 删除一条 feedback；非所属用户返回 ``False``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.delete")
        async with self._sf() as session:
            row = await session.get(FeedbackRow, feedback_id)
            if row is None:
                return False
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def upsert(
        self,
        *,
        run_id: str,
        thread_id: str,
        rating: int,
        user_id: str | None | _AutoSentinel = AUTO,
        comment: str | None = None,
    ) -> dict:
        """针对 ``(thread_id, run_id, user_id)`` 创建或更新一条 feedback，``rating`` 必须是 ``+1`` 或 ``-1``。"""
        if rating not in (1, -1):
            raise ValueError(f"rating must be +1 or -1, got {rating}")
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.upsert")
        async with self._sf() as session:
            stmt = select(FeedbackRow).where(
                FeedbackRow.thread_id == thread_id,
                FeedbackRow.run_id == run_id,
                FeedbackRow.user_id == resolved_user_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                row.rating = rating
                row.comment = comment
                row.created_at = datetime.now(UTC)
            else:
                row = FeedbackRow(
                    feedback_id=str(uuid.uuid4()),
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=resolved_user_id,
                    rating=rating,
                    comment=comment,
                    created_at=datetime.now(UTC),
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_dict(row)

    async def delete_by_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> bool:
        """删除当前用户对某 run 的 feedback；删除到记录时返回 ``True``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.delete_by_run")
        async with self._sf() as session:
            stmt = select(FeedbackRow).where(
                FeedbackRow.thread_id == thread_id,
                FeedbackRow.run_id == run_id,
                FeedbackRow.user_id == resolved_user_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def list_by_thread_grouped(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> dict[str, dict]:
        """返回按 ``run_id`` 分组的 feedback：``{run_id: feedback_dict}``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="FeedbackRepository.list_by_thread_grouped")
        stmt = select(FeedbackRow).where(FeedbackRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(FeedbackRow.user_id == resolved_user_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return {row.run_id: self._row_to_dict(row) for row in result.scalars()}

    async def aggregate_by_run(self, thread_id: str, run_id: str) -> dict:
        """使用 SQL 端聚合函数统计某 run 的 feedback 数量。

        Returns:
            dict: 形如 ``{"run_id": ..., "total": int, "positive": int, "negative": int}``。
        """
        stmt = select(
            func.count().label("total"),
            func.coalesce(func.sum(case((FeedbackRow.rating == 1, 1), else_=0)), 0).label("positive"),
            func.coalesce(func.sum(case((FeedbackRow.rating == -1, 1), else_=0)), 0).label("negative"),
        ).where(FeedbackRow.thread_id == thread_id, FeedbackRow.run_id == run_id)
        async with self._sf() as session:
            row = (await session.execute(stmt)).one()
            return {
                "run_id": run_id,
                "total": row.total,
                "positive": row.positive,
                "negative": row.negative,
            }
