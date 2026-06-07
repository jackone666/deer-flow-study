"""Run 事件存储的抽象接口。

:class:`RunEventStore` 是 Run 事件流的统一存储抽象。前端展示用的消息
和调试/审计用的执行 trace 都走同一个接口，仅以 ``category`` 字段区分。

已实现：
- :class:`MemoryRunEventStore`：纯内存 dict（开发与测试）。
- :class:`DbRunEventStore`：基于 SQLAlchemy 的数据库实现。
- :class:`JsonlRunEventStore`：基于 JSONL 文件的实现。
"""

from __future__ import annotations

import abc


class RunEventStore(abc.ABC):
    """Run 事件流存储接口。

    所有实现必须保证：
    1. 通过 :meth:`put` 写入的事件能在后续查询中被读取到。
    2. 同一线程内 ``seq`` 严格递增。
    3. :meth:`list_messages` 只返回 ``category="message"`` 的事件。
    4. :meth:`list_events` 返回指定 Run 的全部事件。
    5. 返回的字典结构与 :class:`RunEvent` 字段一致。
    """

    @abc.abstractmethod
    async def put(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_type: str,
        category: str,
        content: str | dict = "",
        metadata: dict | None = None,
        created_at: str | None = None,
    ) -> dict:
        """写入一条事件，自动分配 ``seq``，返回完整记录。

        Args:
            thread_id: 线程 ID。
            run_id: Run ID。
            event_type: 事件类型名。
            category: ``"message"`` / ``"trace"`` / ``"middleware"`` / ``"error"`` / ``"outputs"`` 等。
            content: 事件内容。
            metadata: 附加元数据。
            created_at: ISO 时间字符串；不传则用当前时间。

        Returns:
            包含 ``seq``、``created_at`` 等字段的完整记录。
        """

    @abc.abstractmethod
    async def put_batch(self, events: list[dict]) -> list[dict]:
        """批量写入事件，供 :class:`RunJournal` flush 缓冲使用。

        Args:
            events: 每项字典的 key 与 :meth:`put` 的关键字参数保持一致。

        Returns:
            含已分配 ``seq`` 的完整记录列表。
        """

    @abc.abstractmethod
    async def list_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
    ) -> list[dict]:
        """返回线程下可展示的消息（``category=message``），按 ``seq`` 升序。

        支持双向游标分页：
        - ``before_seq``：返回 ``seq < before_seq`` 的最后 ``limit`` 条（升序）。
        - ``after_seq``：返回 ``seq > after_seq`` 的前 ``limit`` 条（升序）。
        - 都不传：返回最新的 ``limit`` 条（升序）。
        """

    @abc.abstractmethod
    async def list_events(
        self,
        thread_id: str,
        run_id: str,
        *,
        event_types: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """返回指定 Run 的完整事件流，按 ``seq`` 升序。

        Args:
            thread_id: 线程 ID。
            run_id: Run ID。
            event_types: 可选事件类型过滤白名单。
            limit: 返回的最大记录数。
        """

    @abc.abstractmethod
    async def list_messages_by_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        limit: int = 50,
        before_seq: int | None = None,
        after_seq: int | None = None,
    ) -> list[dict]:
        """返回指定 Run 下的可展示消息（``category=message``），按 ``seq`` 升序。

        支持双向游标分页：
        - ``after_seq``：返回 ``seq > after_seq`` 的前 ``limit`` 条。
        - ``before_seq``：返回 ``seq < before_seq`` 的最后 ``limit`` 条。
        - 都不传：返回最新的 ``limit`` 条。
        """

    @abc.abstractmethod
    async def count_messages(self, thread_id: str) -> int:
        """统计线程下 ``category=message`` 的消息数。"""

    @abc.abstractmethod
    async def delete_by_thread(self, thread_id: str) -> int:
        """删除指定线程下的全部事件。

        Returns:
            被删除的事件数。
        """

    @abc.abstractmethod
    async def delete_by_run(self, thread_id: str, run_id: str) -> int:
        """删除指定 Run 的全部事件。

        Returns:
            被删除的事件数。
        """
