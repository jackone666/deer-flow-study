"""Run 元数据的 ORM 模型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class RunRow(Base):
    """run 元数据表对应的 ORM 行。

    Attributes:
        run_id: 主键。
        thread_id: 所属 thread ID。
        assistant_id: 关联的 assistant ID。
        user_id: 所属用户 ID。
        status: 状态，取值 ``pending`` / ``running`` / ``success`` / ``error`` /
            ``timeout`` / ``interrupted``。
        model_name: 使用的模型名。
        multitask_strategy: 多任务处理策略，默认 ``reject``。
        metadata_json: 自定义元数据 JSON。
        kwargs_json: 调用参数 JSON。
        error: 错误信息（如有）。
        message_count: 消息计数（避免查询 RunEventStore）。
        first_human_message: 首条 HumanMessage 预览。
        last_ai_message: 末条 AIMessage 预览。
        total_input_tokens / total_output_tokens / total_tokens: 累计 token 用量。
        llm_call_count: 累计 LLM 调用次数。
        lead_agent_tokens / subagent_tokens / middleware_tokens: 按 caller 拆分的 token 用量。
        follow_up_to_run_id: 关联的 follow-up 源 run。
        created_at / updated_at: 创建与更新时间。
    """

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assistant_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # "pending" | "running" | "success" | "error" | "timeout" | "interrupted"

    model_name: Mapped[str | None] = mapped_column(String(128))
    multitask_strategy: Mapped[str] = mapped_column(String(20), default="reject")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    kwargs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    # 便利字段（避免列表页查询 RunEventStore）
    message_count: Mapped[int] = mapped_column(default=0)
    first_human_message: Mapped[str | None] = mapped_column(Text)
    last_ai_message: Mapped[str | None] = mapped_column(Text)

    # Token 用量（由 RunJournal 在内存中累计，run 结束时落库）
    total_input_tokens: Mapped[int] = mapped_column(default=0)
    total_output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    llm_call_count: Mapped[int] = mapped_column(default=0)
    lead_agent_tokens: Mapped[int] = mapped_column(default=0)
    subagent_tokens: Mapped[int] = mapped_column(default=0)
    middleware_tokens: Mapped[int] = mapped_column(default=0)

    # Follow-up 关联
    follow_up_to_run_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_runs_thread_status", "thread_id", "status"),)
