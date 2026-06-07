"""基于 SQLAlchemy 的 thread 元数据 repository。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.json_compat import json_match
from deerflow.persistence.thread_meta.base import InvalidMetadataFilterError, ThreadMetaStore
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)


class ThreadMetaRepository(ThreadMetaStore):
    """Thread 元数据的 SQLAlchemy repository。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """构造 repository。

        Args:
            session_factory: 异步 session 工厂。
        """
        self._sf = session_factory

    @staticmethod
    def _row_to_dict(row: ThreadMetaRow) -> dict[str, Any]:
        """把 :class:`ThreadMetaRow` 序列化为 dict。"""
        d = row.to_dict()
        d["metadata"] = d.pop("metadata_json", None) or {}
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                # SQLite 即便声明 ``DateTime(timezone=True)`` 也会丢 tzinfo；
                # ``coerce_iso`` 把 naive 值视作 UTC，确保线上格式始终带 tz。
                d[key] = coerce_iso(val)
        return d

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

        显式 ``user_id=None`` 会创建 orphan 行（供迁移脚本使用）。
        """
        # AUTO 时从 contextvar 解析；显式 None 创建 orphan 行（迁移脚本使用）
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.create")
        now = datetime.now(UTC)
        row = ThreadMetaRow(
            thread_id=thread_id,
            assistant_id=assistant_id,
            user_id=resolved_user_id,
            display_name=display_name,
            metadata_json=metadata or {},
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_dict(row)

    async def get(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> dict | None:
        """按 ID 获取 thread；非所属用户返回 ``None``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.get")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return None
            # 显式 bypass（user_id=None）时跳过 owner 过滤
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        """检查 ``user_id`` 对 ``thread_id`` 的访问权限。

        同一行支持两种语义，取决于调用方的意图：

        - ``require_existing=False``（默认，宽松）：
          当行缺失（未追踪的历史 thread）、``row.user_id`` 为 ``None``
          （共享/无 auth 数据）或 ``row.user_id == user_id`` 时返回
          ``True``。用于**只读**装饰器，把未追踪的 thread 视为可访问
          以保持向后兼容。

        - ``require_existing=True``（严格）：
          仅在行存在且 (``row.user_id == user_id`` 或 ``row.user_id``
          为 ``None``) 时返回 ``True``。用于**破坏性/写入**装饰器
          （DELETE、PATCH、状态更新），避免「已被删除」的 thread 被
          任何调用方重新命中——关闭「行消失时其他用户都被视为拥有者」
          这一 delete-idempotence 跨用户漏洞。
        """
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return not require_existing
            if row.user_id is None:
                return True
            return row.user_id == user_id

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """按 metadata / status 搜索 thread。

        默认强制 owner 过滤：调用方必须处于某个 user context 中。
        传 ``user_id=None`` 可以绕过（迁移/CLI）。
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.search")
        stmt = select(ThreadMetaRow).order_by(ThreadMetaRow.updated_at.desc(), ThreadMetaRow.thread_id.desc())
        if resolved_user_id is not None:
            stmt = stmt.where(ThreadMetaRow.user_id == resolved_user_id)
        if status:
            stmt = stmt.where(ThreadMetaRow.status == status)

        if metadata:
            applied = 0
            for key, value in metadata.items():
                try:
                    stmt = stmt.where(json_match(ThreadMetaRow.metadata_json, key, value))
                    applied += 1
                except (ValueError, TypeError) as exc:
                    logger.warning("Skipping metadata filter key %s: %s", ascii(key), exc)
            if applied == 0:
                # 逗号分隔的纯字符串（不带 list repr / 嵌套引号），
                # 让 Gateway 抛出的 400 detail 对客户端可读；排序以保证稳定。
                rejected_keys = ", ".join(sorted(str(k) for k in metadata))
                raise InvalidMetadataFilterError(f"All metadata filter keys were rejected as unsafe: {rejected_keys}")

        stmt = stmt.limit(limit).offset(offset)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def _check_ownership(self, session: AsyncSession, thread_id: str, resolved_user_id: str | None) -> bool:
        """行存在且属于当前用户（或显式 bypass）时返回 ``True``。"""
        if resolved_user_id is None:
            return True  # 显式 bypass
        row = await session.get(ThreadMetaRow, thread_id)
        return row is not None and row.user_id == resolved_user_id

    async def update_display_name(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """更新 thread 的展示名（标题）。"""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_display_name")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(display_name=display_name, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_status(
        self,
        thread_id: str,
        status: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """更新 thread 状态。"""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_status")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(status=status, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """把 ``metadata`` 合并到 ``metadata_json``。

        在同一 session/事务内做 read-modify-write，保证并发调用者看到
        一致状态。当行不存在或 user_id 校验失败时为 no-op。
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_metadata")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            merged = dict(row.metadata_json or {})
            merged.update(metadata)
            row.metadata_json = merged
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def delete(
        self,
        thread_id: str,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> None:
        """按 ID 删除 thread；非所属用户不删除。"""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.delete")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()
