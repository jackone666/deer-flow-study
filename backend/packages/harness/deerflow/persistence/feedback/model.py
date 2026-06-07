"""Run 反馈的 ORM 模型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class FeedbackRow(Base):
    """用户对 run 反馈的 ORM 行。"""

    __tablename__ = "feedback"

    __table_args__ = (UniqueConstraint("thread_id", "run_id", "user_id", name="uq_feedback_thread_run_user"),)

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    message_id: Mapped[str | None] = mapped_column(String(64))
    # message_id 是可选的 :class:`RunEventStore` 事件 ID——
    # 允许反馈指向具体某条消息或整个 run

    rating: Mapped[int] = mapped_column(nullable=False)
    # +1（赞）或 -1（踩）

    comment: Mapped[str | None] = mapped_column(Text)
    # 用户可选的文字反馈

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
