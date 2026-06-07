"""Thread 元数据持久化——ORM、抽象 store 与具体实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from deerflow.persistence.thread_meta.base import InvalidMetadataFilterError, ThreadMetaStore
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "InvalidMetadataFilterError",
    "MemoryThreadMetaStore",
    "ThreadMetaRepository",
    "ThreadMetaRow",
    "ThreadMetaStore",
    "make_thread_store",
]


def make_thread_store(
    session_factory: async_sessionmaker[AsyncSession] | None,
    store: BaseStore | None = None,
) -> ThreadMetaStore:
    """根据可用后端构造合适的 :class:`ThreadMetaStore`。

    当提供 ``session_factory`` 时返回基于 SQL 的实现,否则回退到
    LangGraph 内存版 ``Store`` 的实现。

    Args:
        session_factory: SQLAlchemy 异步 session 工厂,提供时启用 SQL 后端。
        store: LangGraph ``BaseStore`` 实例,作为内存回退后端。

    Returns:
        适配环境的 :class:`ThreadMetaStore` 实例。

    Raises:
        ValueError: 当 ``session_factory`` 和 ``store`` 都未提供时抛出。
    """
    if session_factory is not None:
        return ThreadMetaRepository(session_factory)
    if store is None:
        raise ValueError("make_thread_store requires either a session_factory (SQL) or a store (memory)")
    return MemoryThreadMetaStore(store)
