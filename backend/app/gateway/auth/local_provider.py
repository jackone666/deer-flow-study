"""本地邮箱/密码认证 Provider。"""

import logging

from app.gateway.auth.models import User
from app.gateway.auth.password import hash_password_async, needs_rehash, verify_password_async
from app.gateway.auth.providers import AuthProvider
from app.gateway.auth.repositories.base import UserRepository

logger = logging.getLogger(__name__)


class LocalAuthProvider(AuthProvider):
    """使用本地数据库的邮箱/密码认证 Provider。"""

    def __init__(self, repository: UserRepository):
        """初始化本地 Provider。

        Args:
            repository: ``UserRepository`` 实现（SQLite）。
        """
        self._repo = repository

    async def authenticate(self, credentials: dict) -> User | None:
        """使用邮箱和密码进行认证。

        Args:
            credentials: 包含 ``email`` 和 ``password`` 键的字典。

        Returns:
            User | None: 认证成功返回 ``User``，否则返回 ``None``。
        """
        email = credentials.get("email")
        password = credentials.get("password")

        if not email or not password:
            return None

        user = await self._repo.get_user_by_email(email)
        if user is None:
            return None

        if user.password_hash is None:
            # 没有本地密码的 OAuth 用户
            return None

        if not await verify_password_async(password, user.password_hash):
            return None

        if needs_rehash(user.password_hash):
            try:
                user.password_hash = await hash_password_async(password)
                await self._repo.update_user(user)
            except Exception:
                # rehash 是机会性升级，瞬时 DB 错误不应阻断本就合法的登录。
                logger.warning("Failed to rehash password for user %s; login will still succeed", user.email, exc_info=True)

        return user

    async def get_user(self, user_id: str) -> User | None:
        """按 ID 获取用户。"""
        return await self._repo.get_user_by_id(user_id)

    async def create_user(self, email: str, password: str | None = None, system_role: str = "user", needs_setup: bool = False) -> User:
        """创建一个本地用户。

        Args:
            email: 用户邮箱。
            password: 明文密码（将被哈希）。
            system_role: 角色，取值 ``"admin"`` 或 ``"user"``。
            needs_setup: 若为 ``True``，用户首次登录需要走 setup 流程。

        Returns:
            User: 创建好的用户对象。
        """
        password_hash = await hash_password_async(password) if password else None
        user = User(
            email=email,
            password_hash=password_hash,
            system_role=system_role,
            needs_setup=needs_setup,
        )
        return await self._repo.create_user(user)

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """按 OAuth Provider + OAuth ID 查找用户。"""
        return await self._repo.get_user_by_oauth(provider, oauth_id)

    async def count_users(self) -> int:
        """返回已注册用户总数。"""
        return await self._repo.count_users()

    async def count_admin_users(self) -> int:
        """返回管理员用户数量。"""
        return await self._repo.count_admin_users()

    async def update_user(self, user: User) -> User:
        """更新已有用户。"""
        return await self._repo.update_user(user)

    async def get_user_by_email(self, email: str) -> User | None:
        """按邮箱查找用户。"""
        return await self._repo.get_user_by_email(email)
