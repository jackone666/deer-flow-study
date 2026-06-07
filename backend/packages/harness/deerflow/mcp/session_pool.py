"""用于有状态工具调用的持久 MCP 会话池。

当通过 langchain-mcp-adapters 以 ``session=None`` 加载 MCP 工具时,每次工具调用
都会新建一个 MCP 会话;对 Playwright 等有状态服务器来说,意味着浏览器状态
(打开的页面、填好的表单)会在调用之间丢失。

本模块提供按 ``(server_name, scope_key)``(``scope_key`` 通常是 ``thread_id``)
隔离的持久 MCP 会话池,让同线程的连续工具调用共享同一会话与服务器端状态。
池满后按 LRU 淘汰。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from mcp import ClientSession

logger = logging.getLogger(__name__)


class MCPSessionPool:
    """管理按 ``(server_name, scope_key)`` 隔离的持久 MCP 会话。"""

    MAX_SESSIONS = 256
    SESSION_CLOSE_TIMEOUT = 5.0  # 通过 run_coroutine_threadsafe 关闭会话时的等待秒数

    def __init__(self) -> None:
        """初始化池与同步锁。"""
        self._entries: OrderedDict[
            tuple[str, str],
            tuple[ClientSession, asyncio.AbstractEventLoop],
        ] = OrderedDict()
        self._context_managers: dict[tuple[str, str], Any] = {}
        # threading.Lock is not bound to any event loop, so it is safe to
        # acquire from both async paths and sync/worker-thread paths.
        self._lock = threading.Lock()

    async def get_session(
        self,
        server_name: str,
        scope_key: str,
        connection: dict[str, Any],
    ) -> ClientSession:
        """获取或创建一个持久 MCP 会话。

        若已存在的会话属于另一个事件循环(例如 sync-wrapper 路径),会被关
        闭并在当前循环中重建。

        Args:
            server_name: MCP 服务器名。
            scope_key: 隔离键,通常是 ``thread_id``。
            connection: 传给 ``create_session`` 的连接配置。

        Returns:
            已经过 :meth:`ClientSession.initialize` 的可用会话。
        """
        key = (server_name, scope_key)
        current_loop = asyncio.get_running_loop()

        # Phase 1: inspect/mutate the registry under the thread lock (no awaits).
        cms_to_close: list[tuple[tuple[str, str], Any]] = []
        with self._lock:
            if key in self._entries:
                session, loop = self._entries[key]
                if loop is current_loop:
                    self._entries.move_to_end(key)
                    return session
                # Session belongs to a different event loop – evict it.
                cm = self._context_managers.pop(key, None)
                self._entries.pop(key)
                if cm is not None:
                    cms_to_close.append((key, cm))

            # Evict LRU entries when at capacity.
            while len(self._entries) >= self.MAX_SESSIONS:
                oldest_key = next(iter(self._entries))
                cm = self._context_managers.pop(oldest_key, None)
                self._entries.pop(oldest_key)
                if cm is not None:
                    cms_to_close.append((oldest_key, cm))

        # Phase 2: async cleanup outside the lock so we never await while holding it.
        for close_key, cm in cms_to_close:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                logger.warning("Error closing MCP session %s", close_key, exc_info=True)

        from langchain_mcp_adapters.sessions import create_session

        cm = create_session(connection)
        session = await cm.__aenter__()
        await session.initialize()

        # Phase 3: register the new session under the lock.
        with self._lock:
            self._entries[key] = (session, current_loop)
            self._context_managers[key] = cm
        logger.info("Created persistent MCP session for %s/%s", server_name, scope_key)
        return session

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------

    async def _close_cm(self, key: tuple[str, str], cm: Any) -> None:
        """关闭单个 context manager(必须在未持锁状态下调用)。"""
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            logger.warning("Error closing MCP session %s", key, exc_info=True)

    async def close_scope(self, scope_key: str) -> None:
        """关闭指定 scope(如 ``thread_id``)的所有会话。"""
        with self._lock:
            keys = [k for k in self._entries if k[1] == scope_key]
            cms = [(k, self._context_managers.pop(k, None)) for k in keys]
            for k in keys:
                self._entries.pop(k, None)
        for key, cm in cms:
            if cm is not None:
                await self._close_cm(key, cm)

    async def close_server(self, server_name: str) -> None:
        """关闭指定 MCP 服务器的所有会话。"""
        with self._lock:
            keys = [k for k in self._entries if k[0] == server_name]
            cms = [(k, self._context_managers.pop(k, None)) for k in keys]
            for k in keys:
                self._entries.pop(k, None)
        for key, cm in cms:
            if cm is not None:
                await self._close_cm(key, cm)

    async def close_all(self) -> None:
        """关闭池中所有会话。"""
        with self._lock:
            cms = list(self._context_managers.items())
            self._context_managers.clear()
            self._entries.clear()
        for key, cm in cms:
            await self._close_cm(key, cm)

    def close_all_sync(self) -> None:
        """在所属事件循环上同步关闭所有会话。

        每个会话都在它被创建的那个事件循环上关闭,避免跨循环资源泄漏;可以
        在任何没有运行事件循环的线程中安全调用。
        """
        with self._lock:
            entries = list(self._entries.items())
            cms = dict(self._context_managers)
            self._entries.clear()
            self._context_managers.clear()

        for key, (_, loop) in entries:
            cm = cms.get(key)
            if cm is None or loop.is_closed():
                continue
            try:
                if loop.is_running():
                    # Schedule on the owning loop from this (different) thread.
                    future = asyncio.run_coroutine_threadsafe(cm.__aexit__(None, None, None), loop)
                    future.result(timeout=self.SESSION_CLOSE_TIMEOUT)
                else:
                    loop.run_until_complete(cm.__aexit__(None, None, None))
            except Exception:
                logger.debug("Error closing MCP session %s during sync close", key, exc_info=True)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_pool: MCPSessionPool | None = None
_pool_lock = threading.Lock()


def get_session_pool() -> MCPSessionPool:
    """返回全局的会话池单例。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = MCPSessionPool()
    return _pool


def reset_session_pool() -> None:
    """重置单例(供测试使用)。"""
    global _pool
    _pool = None
