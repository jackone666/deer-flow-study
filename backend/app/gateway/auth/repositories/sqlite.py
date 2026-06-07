"""基于 SQLAlchemy 的 ``UserRepository`` 实现。

使用 ``deerflow.persistence.engine`` 提供的共享异步 session factory——
``users`` 表与 ``threads_meta``、``runs``、``run_events``、``feedback``
等表位于同一个数据库中。

构造函数直接接收 session factory（与 ``deerflow.persistence.*`` 中其它
四个仓储保持一致）。调用方应在 ``init_engine_from_config()`` 之后
再构造本类。
"""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.auth.models import User
from app.gateway.auth.repositories.base import UserNotFoundError, UserRepository
from deerflow.persistence.user.model import UserRow


class SQLiteUserRepository(UserRepository):
    """由共享 SQLAlchemy 引擎支撑的异步用户仓储。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化仓储，保存传入的 session factory。

        Args:
            session_factory: 来自 ``deerflow.persistence.engine`` 的异步 session factory。
        """
        self._sf = session_factory

    # ── 转换器 ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_user(row: UserRow) -> User:
        """把 ``UserRow`` ORM 行转换为 ``User`` 业务对象。"""
        return User(
            id=UUID(row.id),
            email=row.email,
            password_hash=row.password_hash,
            system_role=row.system_role,  # type: ignore[arg-type]
            # SQLite 读出时丢失 tzinfo；这里重新挂回 UTC 以保证下游
            # 时间戳比较可靠。
            created_at=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
            oauth_provider=row.oauth_provider,
            oauth_id=row.oauth_id,
            needs_setup=row.needs_setup,
            token_version=row.token_version,
        )

    @staticmethod
    def _user_to_row(user: User) -> UserRow:
        """把 ``User`` 业务对象转换为 ``UserRow`` ORM 行。"""
        return UserRow(
            id=str(user.id),
            email=user.email,
            password_hash=user.password_hash,
            system_role=user.system_role,
            created_at=user.created_at,
            oauth_provider=user.oauth_provider,
            oauth_id=user.oauth_id,
            needs_setup=user.needs_setup,
            token_version=user.token_version,
        )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create_user(self, user: User) -> User:
        """插入一个新用户；邮箱重复时抛出 ``ValueError``。

        Args:
            user: 待创建的用户对象。

        Returns:
            User: 传入的 ``user``（已落库）。

        Raises:
            ValueError: 邮箱已被注册时抛出。
        """
        row = self._user_to_row(user)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(f"Email already registered: {user.email}") from exc
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        """按 ID 查找用户。"""
        async with self._sf() as session:
            row = await session.get(UserRow, user_id)
            return self._row_to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> User | None:
        """按邮箱查找用户。"""
        stmt = select(UserRow).where(UserRow.email == email)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def update_user(self, user: User) -> User:
        """更新已有用户；并发删除时抛出 :class:`UserNotFoundError`。

        Args:
            user: 带有更新字段的用户对象。

        Returns:
            User: 传入的 ``user``（已落库）。

        Raises:
            UserNotFoundError: ``user.id`` 对应的行已被并发删除时抛出。
        """
        async with self._sf() as session:
            row = await session.get(UserRow, str(user.id))
            if row is None:
                # 并发删除时硬失败：调用方（reset_admin、改密处理器、
                # _ensure_admin_user）都在调用本方法前刚取过该用户；
                # 此处缺失说明行已经在我们脚下消失。静默成功会让调用方
                # 把“password reset”日志写到已经不存在的行上。
                raise UserNotFoundError(f"User {user.id} no longer exists")
            row.email = user.email
            row.password_hash = user.password_hash
            row.system_role = user.system_role
            row.oauth_provider = user.oauth_provider
            row.oauth_id = user.oauth_id
            row.needs_setup = user.needs_setup
            row.token_version = user.token_version
            await session.commit()
        return user

    async def count_users(self) -> int:
        """统计已注册用户数。"""
        stmt = select(func.count()).select_from(UserRow)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def count_admin_users(self) -> int:
        """统计 ``system_role == 'admin'`` 的用户数。"""
        stmt = select(func.count()).select_from(UserRow).where(UserRow.system_role == "admin")
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """按 OAuth Provider + OAuth ID 查找用户。"""
        stmt = select(UserRow).where(UserRow.oauth_provider == provider, UserRow.oauth_id == oauth_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None
