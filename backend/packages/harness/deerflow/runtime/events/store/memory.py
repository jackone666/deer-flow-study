"""内存版 RunEventStore。

用于 ``run_events.backend=memory``（默认）以及测试场景。同一进程内
async 访问是线程安全的——所有变更都发生在同一事件循环中，无需加锁。
"""

from __future__ import annotations

from datetime import UTC, datetime

from deerflow.runtime.events.store.base import RunEventStore


class MemoryRunEventStore(RunEventStore):
    """纯内存版 RunEventStore，仅在单进程内有效。"""

    def __init__(self) -> None:
        """初始化 self。"""
        self._events: dict[str, list[dict]] = {}  # thread_id -> sorted event list
        self._seq_counters: dict[str, int] = {}  # thread_id -> last assigned seq

    def _next_seq(self, thread_id: str) -> int:
        """为指定线程分配并返回下一个自增 ``seq``。"""
        current = self._seq_counters.get(thread_id, 0)
        next_val = current + 1
        self._seq_counters[thread_id] = next_val
        return next_val

    def _put_one(
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
        """分配 ``seq`` 并写入单条事件，返回完整记录。"""
        seq = self._next_seq(thread_id)
        record = {
            "thread_id": thread_id,
            "run_id": run_id,
            "event_type": event_type,
            "category": category,
            "content": content,
            "metadata": metadata or {},
            "seq": seq,
            "created_at": created_at or datetime.now(UTC).isoformat(),
        }
        self._events.setdefault(thread_id, []).append(record)
        return record

    async def put(
        self,
        *,
        thread_id,
        run_id,
        event_type,
        category,
        content="",
        metadata=None,
        created_at=None,
    ):
        """写入一条事件，自动分配 ``seq``，返回完整记录。"""
        return self._put_one(
            thread_id=thread_id,
            run_id=run_id,
            event_type=event_type,
            category=category,
            content=content,
            metadata=metadata,
            created_at=created_at,
        )

    async def put_batch(self, events):
        """批量写入事件，按顺序分配自增 ``seq``。"""
        results = []
        for ev in events:
            record = self._put_one(**ev)
            results.append(record)
        return results

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None):
        """返回线程下 ``category=message`` 的消息，支持双向游标分页。"""
        all_events = self._events.get(thread_id, [])
        messages = [e for e in all_events if e["category"] == "message"]

        if before_seq is not None:
            messages = [e for e in messages if e["seq"] < before_seq]
            # Take the last `limit` records
            return messages[-limit:]
        elif after_seq is not None:
            messages = [e for e in messages if e["seq"] > after_seq]
            return messages[:limit]
        else:
            # Return the latest `limit` records, ascending
            return messages[-limit:]

    async def list_events(self, thread_id, run_id, *, event_types=None, limit=500):
        """返回指定 Run 的全部事件，可选按 ``event_types`` 过滤。"""
        all_events = self._events.get(thread_id, [])
        filtered = [e for e in all_events if e["run_id"] == run_id]
        if event_types is not None:
            filtered = [e for e in filtered if e["event_type"] in event_types]
        return filtered[:limit]

    async def list_messages_by_run(self, thread_id, run_id, *, limit=50, before_seq=None, after_seq=None):
        """返回指定 Run 下的 ``category=message`` 消息，支持双向游标分页。"""
        all_events = self._events.get(thread_id, [])
        filtered = [e for e in all_events if e["run_id"] == run_id and e["category"] == "message"]
        if before_seq is not None:
            filtered = [e for e in filtered if e["seq"] < before_seq]
        if after_seq is not None:
            filtered = [e for e in filtered if e["seq"] > after_seq]
        if after_seq is not None:
            return filtered[:limit]
        else:
            return filtered[-limit:] if len(filtered) > limit else filtered

    async def count_messages(self, thread_id):
        """统计线程下 ``category=message`` 的消息数。"""
        all_events = self._events.get(thread_id, [])
        return sum(1 for e in all_events if e["category"] == "message")

    async def delete_by_thread(self, thread_id):
        """删除线程的全部事件并清空对应 ``seq`` 计数器。"""
        events = self._events.pop(thread_id, [])
        self._seq_counters.pop(thread_id, None)
        return len(events)

    async def delete_by_run(self, thread_id, run_id):
        """删除指定 Run 的全部事件，保留其他 Run 的事件。"""
        all_events = self._events.get(thread_id, [])
        if not all_events:
            return 0
        remaining = [e for e in all_events if e["run_id"] != run_id]
        removed = len(all_events) - len(remaining)
        self._events[thread_id] = remaining
        return removed
