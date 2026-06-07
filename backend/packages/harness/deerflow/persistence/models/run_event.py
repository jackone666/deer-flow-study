"""Run 事件的 ORM 模型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class RunEventRow(Base):
    """run 事件表对应的 ORM 行。

    Attributes:
        id: 自增主键。
        thread_id: 所属 thread ID。
        run_id: 所属 run ID。
        user_id: 所属用户；auth 引入之前的数据为 ``None``，新写入由 auth
            中间件填充，启动时的 orphan 迁移会回填历史行。
        event_type: 事件类型。
        category: 事件分类，取值 ``"message"`` / ``"trace"`` / ``"lifecycle"``。
        content: 事件文本内容。
        event_metadata: 事件附带 JSON 元数据。
        seq: 同一 thread 内的事件顺序号。
        created_at: 创建时间（带 tz）。
    """

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 所属 conversation 的拥有者。引入 auth 之前的数据为 nullable；
    # 新写入由 auth 中间件填充，启动时的 orphan 迁移会回填历史行。
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    # "message" | "trace" | "lifecycle"
    content: Mapped[str] = mapped_column(Text, default="")
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    seq: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_events_thread_seq"),
        Index("ix_events_thread_cat_seq", "thread_id", "category", "seq"),
        Index("ix_events_run", "thread_id", "run_id", "seq"),
    )
