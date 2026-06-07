"""用户仓储接口，用于抽象数据库操作。"""

from abc import ABC, abstractmethod

from app.gateway.auth.models import User


class UserNotFoundError(LookupError):
    """当用户仓储操作针对不存在的行时抛出。

    继承自 :class:`LookupError`，因此已经把 ``LookupError`` 当作“实体缺失”
    处理的调用方可以继续工作；具体调用点可以单独捕获该类，以便把
    “并发删除期间更新”与其它查询区分开。
    """


class UserRepository(ABC):
    """用户数据存储的抽象接口。

    实现该接口即可支持不同的存储后端（SQLite 等）。
    """

    @abstractmethod
    async def create_user(self, user: User) -> User:
        """创建一个新用户。

        Args:
            user: 待创建的用户对象。

        Returns:
            User: 已创建并分配好 ID 的用户。

        Raises:
            ValueError: 当邮箱已存在时抛出。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        """按 ID 获取用户。

        Args:
            user_id: 用户的 UUID 字符串。

        Returns:
            User | None: 找到时返回用户，否则返回 ``None``。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """按邮箱获取用户。

        Args:
            email: 用户邮箱地址。

        Returns:
            User | None: 找到时返回用户，否则返回 ``None``。
        """
        raise NotImplementedError

    @abstractmethod
    async def update_user(self, user: User) -> User:
        """更新一个已有用户。

        Args:
            user: 已修改字段的用户对象。

        Returns:
            User: 更新后的用户。

        Raises:
            UserNotFoundError: 当 ``user.id`` 对应的行不存在时抛出。这是
                硬错误（不是 no-op），调用方不会把“并发删除竞态”误认成
                “更新成功”。
        """
        raise NotImplementedError

    @abstractmethod
    async def count_users(self) -> int:
        """返回已注册用户的总数。"""
        raise NotImplementedError

    @abstractmethod
    async def count_admin_users(self) -> int:
        """返回 ``system_role == 'admin'`` 的用户数量。"""
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """按 OAuth Provider + OAuth ID 获取用户。

        Args:
            provider: OAuth Provider 名（例如 ``'github'``、``'google'``）。
            oauth_id: OAuth Provider 给出的用户 ID。

        Returns:
            User | None: 找到时返回用户，否则返回 ``None``。
        """
        raise NotImplementedError
