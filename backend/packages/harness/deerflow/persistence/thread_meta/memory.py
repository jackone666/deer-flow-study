"""基于 LangGraph :class:`BaseStore` 的内存版 :class:`ThreadMetaStore`。

在 ``database.backend=memory`` 时使用。底层走 LangGraph Store 的
``("threads",)`` 命名空间——与 Gateway 路由存储 thread 记录所用
的命名空间一致。
"""

from __future__ import annotations

from typing import Any

from langgraph.store.base import BaseStore

from deerflow.persistence.thread_meta.base import ThreadMetaStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso, now_iso

THREADS_NS: tuple[str, ...] = ("threads",)


class MemoryThreadMetaStore(ThreadMetaStore):
    """基于 LangGraph Store 的内存 thread 元数据存储。"""

    def __init__(self, store: BaseStore) -> None:
        """构造 store。

        Args:
            store: LangGraph :class:`BaseStore` 实例。
        """
        self._store = store

    async def _get_owned_record(
        self,
        thread_id: str,
        user_id: str | None | _AutoSentinel,
        method_name: str,
    ) -> dict | None:
        """获取记录并校验 owner；返回可变副本或 ``None``。"""
        resolved = resolve_user_id(user_id, method_name=method_name)
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return None
        if item.value is None:
            return None
        record = dict(item.value)
        if resolved is not None and record.get("user_id") != resolved:
            return None
        return record

    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        user_id: str | None | _AutoSentinel = AUTO,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """创建一条 thread 元数据记录。

        Args:
            thread_id: thread ID。
            assistant_id: 可选的 assistant 关联。
            user_id: 拥有者；``AUTO`` 时回退到当前请求用户。
            display_name: 可选的展示名（标题）。
            metadata: 初始 metadata dict。

        Returns:
            dict: 写入后的记录。
        """
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.create")
        now = now_iso()
        record: dict[str, Any] = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "display_name": display_name,
            "status": "idle",
            "metadata": metadata or {},
            "values": {},
            "created_at": now,
            "updated_at": now,
        }
        await self._store.aput(THREADS_NS, thread_id, record)
        return record

    async def get(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> dict | None:
        """按 ID 获取 thread 元数据。"""
        return await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.get")

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """按 metadata / status / owner 过滤搜索 thread。"""
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.search")
        filter_dict: dict[str, Any] = {}
        if metadata:
            filter_dict.update(metadata)
        if status:
            filter_dict["status"] = status
        if resolved_user_id is not None:
            filter_dict["user_id"] = resolved_user_id

        items = await self._store.asearch(
            THREADS_NS,
            filter=filter_dict or None,
            limit=limit,
            offset=offset,
        )
        return [self._item_to_dict(item) for item in items]

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        """检查 ``user_id`` 对 ``thread_id`` 的访问权限。"""
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return not require_existing
        if item.value is None:
            return True
        record_user_id = item.value.get("user_id")
        if record_user_id is None:
            return True
        return record_user_id == user_id

    async def update_display_name(self, thread_id: str, display_name: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """更新 thread 的展示名（标题）。"""
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_display_name")
        if record is None:
            return
        record["display_name"] = display_name
        record["updated_at"] = now_iso()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def update_status(self, thread_id: str, status: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """更新 thread 状态。"""
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_status")
        if record is None:
            return
        record["status"] = status
        record["updated_at"] = now_iso()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def update_metadata(self, thread_id: str, metadata: dict, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """合并 ``metadata`` 到 thread 的 metadata 字段。"""
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_metadata")
        if record is None:
            return
        merged = dict(record.get("metadata") or {})
        merged.update(metadata)
        record["metadata"] = merged
        record["updated_at"] = now_iso()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def delete(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """删除 thread 元数据。"""
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.delete")
        if record is None:
            return
        await self._store.adelete(THREADS_NS, thread_id)

    @staticmethod
    def _item_to_dict(item) -> dict[str, Any]:
        """把 LangGraph Store 的 SearchItem 转换为调用方期望的 dict 格式。"""
        val = item.value or {}
        return {
            "thread_id": item.key,
            "assistant_id": val.get("assistant_id"),
            "user_id": val.get("user_id"),
            "display_name": val.get("display_name"),
            "status": val.get("status", "idle"),
            "metadata": val.get("metadata", {}),
            # ``coerce_iso`` 会修复早期 Gateway 写下的 ``str(time.time())``
            # 形式的 unix-second 字符串。
            "created_at": coerce_iso(val.get("created_at", "")),
            "updated_at": coerce_iso(val.get("updated_at", "")),
        }
