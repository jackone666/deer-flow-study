"""用于认证的 User Pydantic 模型。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def _utc_now() -> datetime:
    """返回当前 UTC 时间（带时区）。"""
    return datetime.now(UTC)


class User(BaseModel):
    """内部用户表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4, description="主键")
    email: EmailStr = Field(..., description="唯一的邮箱地址")
    password_hash: str | None = Field(None, description="bcrypt 哈希；OAuth 用户可为空")
    system_role: Literal["admin", "user"] = Field(default="user")
    created_at: datetime = Field(default_factory=_utc_now)

    # OAuth 关联（可选）
    oauth_provider: str | None = Field(None, description="例如 'github'、'google'")
    oauth_id: str | None = Field(None, description="OAuth Provider 给出的用户 ID")

    # 认证生命周期
    needs_setup: bool = Field(default=False, description="重置后的账户需要完成 setup 时为 True")
    token_version: int = Field(default=0, description="改密时递增，用于作废旧 JWT")


class UserResponse(BaseModel):
    """用户信息接口的响应模型。"""

    id: str
    email: str
    system_role: Literal["admin", "user"]
    needs_setup: bool = False
