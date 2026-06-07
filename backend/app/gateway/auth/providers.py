"""认证 Provider 抽象。"""

from abc import ABC, abstractmethod


class AuthProvider(ABC):
    """认证 Provider 的抽象基类。"""

    @abstractmethod
    async def authenticate(self, credentials: dict) -> "User | None":
        """使用给定凭据进行认证。

        Returns:
            User | None: 认证成功返回 ``User``，否则返回 ``None``。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user(self, user_id: str) -> "User | None":
        """按 ID 检索用户。"""
        raise NotImplementedError


# 在运行时再 import User 以避免循环导入
from app.gateway.auth.models import User  # noqa: E402
