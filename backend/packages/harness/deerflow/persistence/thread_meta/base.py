"""Thread 元数据存储的抽象接口。

实现：
- :class:`ThreadMetaRepository`：SQL 后端（sqlite / postgres，通过 SQLAlchemy）
- :class:`MemoryThreadMetaStore`：包装 LangGraph :class:`BaseStore`（memory 模式）

所有修改与查询方法都接受 ``user_id`` 参数，遵循三态语义（见
:mod:`deerflow.runtime.user_context`）：

- ``AUTO``（默认）：从请求级 contextvar 解析。
- 显式 ``str``：原样使用传入值。
- 显式 ``None``：跳过 owner 过滤（仅迁移/CLI 用途）。
"""

from __future__ import annotations

import abc
from typing import Any

from deerflow.runtime.user_context import AUTO, _AutoSentinel


class InvalidMetadataFilterError(ValueError):
    """当所有客户端提供的 metadata filter 键都被拒绝时抛出。"""


class ThreadMetaStore(abc.ABC):
    """Thread 元数据存储抽象基类。"""

    @abc.abstractmethod
    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        user_id: str | None | _AutoSentinel = AUTO,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """创建一条 thread 元数据记录。"""
        pass

    @abc.abstractmethod
    async def get(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> dict | None:
        """按 ID 获取 thread 元数据。"""
        pass

    @abc.abstractmethod
    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """按 metadata / status 搜索 thread。"""
        pass

    @abc.abstractmethod
    async def update_display_name(self, thread_id: str, display_name: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """更新 thread 的展示名。"""
        pass

    @abc.abstractmethod
    async def update_status(self, thread_id: str, status: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """更新 thread 状态。"""
        pass

    @abc.abstractmethod
    async def update_metadata(self, thread_id: str, metadata: dict, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """把 ``metadata`` 合并进 thread 的 metadata 字段。

        已存在的 key 会被新值覆盖；``metadata`` 中未出现的 key 保持不变。
        当 thread 不存在或 owner 校验失败时为 no-op。
        """
        pass

    @abc.abstractmethod
    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        """检查 ``user_id`` 是否能访问 ``thread_id``。"""
        pass

    @abc.abstractmethod
    async def delete(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        """删除一条 thread 元数据记录。"""
        pass
