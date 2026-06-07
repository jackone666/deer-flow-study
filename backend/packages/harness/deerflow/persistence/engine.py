"""异步 SQLAlchemy 引擎生命周期管理。

在 Gateway 启动时初始化，为各 repository 提供 session factory，
关闭时统一释放。

当 ``database.backend="memory"`` 时，:func:`init_engine` 是空操作，
:func:`get_session_factory` 返回 ``None``。Repository 必须显式判空
并回退到内存实现。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _json_serializer(obj: object) -> str:
    """使用 ``ensure_ascii=False`` 的 JSON 序列化器，保留中文字符。"""
    return json.dumps(obj, ensure_ascii=False)


logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def _auto_create_postgres_db(url: str) -> None:
    """连接到 ``postgres`` 维护库并执行 ``CREATE DATABASE``。

    目标库名从 ``url`` 中提取。连接默认 ``postgres`` 库时使用
    ``AUTOCOMMIT`` 隔离级别（``CREATE DATABASE`` 不能在事务中执行）。

    Args:
        url: 目标数据库的 SQLAlchemy URL。

    Raises:
        ValueError: URL 中未包含数据库名。
    """
    from sqlalchemy import text
    from sqlalchemy.engine.url import make_url

    parsed = make_url(url)
    db_name = parsed.database
    if not db_name:
        raise ValueError("Cannot auto-create database: no database name in URL")

    # 连接到默认的 'postgres' 库以执行 CREATE DATABASE
    maint_url = parsed.set(database="postgres")
    maint_engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        logger.info("Auto-created PostgreSQL database: %s", db_name)
    finally:
        await maint_engine.dispose()


async def init_engine(
    backend: str,
    *,
    url: str = "",
    echo: bool = False,
    pool_size: int = 5,
    sqlite_dir: str = "",
) -> None:
    """创建异步 engine 与 session factory，并自动建表。

    Args:
        backend: ``memory`` / ``sqlite`` / ``postgres`` 之一。
        url: SQLAlchemy 异步 URL（sqlite/postgres 用）。
        echo: 是否回显 SQL 到日志。
        pool_size: Postgres 连接池大小。
        sqlite_dir: SQLite 使用的目录（确保存在）。

    Raises:
        ImportError: postgres 后端但未安装 ``asyncpg``。
        ValueError: 未知的 ``backend`` 名称。
    """
    global _engine, _session_factory

    if backend == "memory":
        logger.info("Persistence backend=memory -- ORM engine not initialized")
        return

    if backend == "postgres":
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise ImportError(
                "database.backend is set to 'postgres' but asyncpg is not installed.\n"
                "Install it with:\n"
                "    cd backend && uv sync --all-packages --extra postgres\n"
                "On the next `make dev` the postgres extra is auto-detected from\n"
                "config.yaml (database.backend: postgres) and reinstalled, so it\n"
                "will not be wiped again. Set UV_EXTRAS=postgres in .env to opt in\n"
                "explicitly. Or switch to backend: sqlite in config.yaml for\n"
                "single-node deployment."
            ) from None

    if backend == "sqlite":
        import os

        from sqlalchemy import event

        os.makedirs(sqlite_dir or ".", exist_ok=True)
        _engine = create_async_engine(url, echo=echo, json_serializer=_json_serializer)

        # 为每个新连接启用 WAL。SQLite 的 PRAGMA 是 per-connection 的，
        # 因此这里挂事件监听器而不是启动时执行一次。WAL 让多读单写并发
        # 互不阻塞，是任何生产 SQLite 部署的标准做法（见
        # AUTH_TEST_PLAN.md 的 TC-UPG-06）。配套的 ``synchronous=NORMAL``
        # 是安全与速度兼顾的组合——只在 WAL checkpoint 边界 fsync。
        # 注意：这里不设置 PRAGMA busy_timeout——Python 的 sqlite3 驱动
        # 本身已默认 5 秒 busy timeout（参见 ``sqlite3.connect`` 的
        # ``timeout`` kwarg），aiosqlite / SQLAlchemy 的 aiosqlite dialect
        # 继承该默认值，重复设置是 no-op。
        @event.listens_for(_engine.sync_engine, "connect")
        def _enable_sqlite_wal(dbapi_conn, _record):  # noqa: ARG001 — SQLAlchemy contract
            """为每条新建 SQLite 连接启用 WAL、同步级别与外键。"""
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
            finally:
                cursor.close()
    elif backend == "postgres":
        _engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            pool_pre_ping=True,
            json_serializer=_json_serializer,
        )
    else:
        raise ValueError(f"Unknown persistence backend: {backend!r}")

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # 自动建表（开发期便利）。生产应使用 Alembic。
    from deerflow.persistence.base import Base

    # 导入所有模型以便 ``Base.metadata`` 发现。
    # 尚无模型时（脚手架阶段），这里是 no-op。
    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        # 模型包暂不可用——不会自动建表。
        # 脚手架阶段或最小化安装时属于预期情况。
        logger.debug("deerflow.persistence.models not found; skipping auto-create tables")

    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        if backend == "postgres" and "does not exist" in str(exc):
            # 库尚未创建——尝试自动创建并重试
            await _auto_create_postgres_db(url)
            # 在新库上重建 engine
            await _engine.dispose()
            _engine = create_async_engine(url, echo=echo, pool_size=pool_size, pool_pre_ping=True, json_serializer=_json_serializer)
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            raise

    logger.info("Persistence engine initialized: backend=%s", backend)


async def init_engine_from_config(config) -> None:
    """便捷入口：从 :class:`DatabaseConfig` 初始化引擎。

    Args:
        config: ``DatabaseConfig`` 兼容对象（含 ``backend``、``app_sqlalchemy_url`` 等字段）。
    """
    if config.backend == "memory":
        await init_engine("memory")
        return
    await init_engine(
        backend=config.backend,
        url=config.app_sqlalchemy_url,
        echo=config.echo_sql,
        pool_size=config.pool_size,
        sqlite_dir=config.sqlite_dir if config.backend == "sqlite" else "",
    )


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """返回异步 session factory；``backend=memory`` 时返回 ``None``。"""
    return _session_factory


def get_engine() -> AsyncEngine | None:
    """返回异步 engine；未初始化时返回 ``None``。"""
    return _engine


async def close_engine() -> None:
    """释放 engine，关闭所有连接。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Persistence engine closed")
    _engine = None
    _session_factory = None
