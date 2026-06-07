"""``users`` 表的 ORM 模型。

放在 harness persistence 包中，使 :meth:`Base.metadata.create_all` 能
与 ``threads_meta`` / ``runs`` / ``run_events`` / ``feedback`` 一同注册。
共用 engine 意味着：

- 一个 SQLite/Postgres 数据库、一个连接池
- 一条 schema 初始化路径
- auth 与 persistence 读取的 async session 风格统一
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class UserRow(Base):
    """``users`` 表的 ORM 行。"""

    __tablename__ = "users"

    # UUID 以 36 字符串存储，便于跨后端移植。
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # "admin" | "user"——保留为纯字符串，避免引入新角色时的 ALTER TABLE 痛点
    system_role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # OAuth 关联（可选）。partial unique index 强制 ``(provider, oauth_id)``
    # 组合唯一；NULL/NULL 不被约束，使纯密码账户可与之共存。
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 认证生命周期标志位
    needs_setup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_version: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        Index(
            "idx_users_oauth_identity",
            "oauth_provider",
            "oauth_id",
            unique=True,
            sqlite_where=text("oauth_provider IS NOT NULL AND oauth_id IS NOT NULL"),
        ),
    )
