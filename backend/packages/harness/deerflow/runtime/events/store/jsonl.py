"""基于 JSONL 文件的 ``RunEventStore`` 实现。

每个 Run 的事件存放在单个文件：
``.deer-flow/threads/{thread_id}/runs/{run_id}.jsonl``

所有类别（message、trace、lifecycle）保存在同一文件中。该后端适合轻量
级单节点部署。

**单进程保证**：内存中的 ``seq`` 计数器是进程本地的。共享同一目录的多
进程部署会产生重复或非单调的 seq 值。多进程或高并发场景请使用
``DbRunEventStore``。

文件 I/O 通过 ``asyncio.to_thread`` 卸载到线程池，永不阻塞事件循环。
按线程的 ``asyncio.Lock`` 在单进程内串行化写入，避免 JSONL 行交错。

已知权衡：``list_messages()`` 必须扫描线程下的所有 Run 文件，因为多
个 Run 的消息需要统一的 seq 顺序。``list_events()`` 只读单个文件——是
快路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from deerflow.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class JsonlRunEventStore(RunEventStore):
    """继承自 ``RunEventStore`` 的类。"""
    def __init__(self, base_dir: str | Path | None = None):
        """初始化 self。"""
        self._base_dir = Path(base_dir) if base_dir else Path(".deer-flow")
        self._seq_counters: dict[str, int] = {}  # thread_id -> current max seq
        # Per-thread asyncio.Lock — serialises concurrent writes within one process.
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _get_write_lock(self, thread_id: str) -> asyncio.Lock:
        """返回值。"""
        return self._write_locks.setdefault(thread_id, asyncio.Lock())

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        """校验 ID 在文件系统路径中的安全性。"""
        if not value or not _SAFE_ID_PATTERN.match(value):
            raise ValueError(f"Invalid {label}: must be alphanumeric/dash/underscore, got {value!r}")
        return value

    def _thread_dir(self, thread_id: str) -> Path:
        """内部辅助方法。"""
        self._validate_id(thread_id, "thread_id")
        return self._base_dir / "threads" / thread_id / "runs"

    def _run_file(self, thread_id: str, run_id: str) -> Path:
        """内部辅助方法。"""
        self._validate_id(run_id, "run_id")
        return self._thread_dir(thread_id) / f"{run_id}.jsonl"

    def _next_seq(self, thread_id: str) -> int:
        """执行赋值。"""
        self._seq_counters[thread_id] = self._seq_counters.get(thread_id, 0) + 1
        return self._seq_counters[thread_id]

    def _compute_max_seq(self, thread_id: str) -> int:
        """扫描线程下所有 Run 文件并返回当前最大 seq（阻塞 I/O）。"""
        max_seq = 0
        thread_dir = self._thread_dir(thread_id)
        if thread_dir.exists():
            for f in thread_dir.glob("*.jsonl"):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    try:
                        record = json.loads(line)
                        max_seq = max(max_seq, record.get("seq", 0))
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed JSONL line in %s", f)
        return max_seq

    async def _ensure_seq_loaded(self, thread_id: str) -> None:
        """从已有文件将最大 seq 加载到内存计数器（非阻塞）。"""
        if thread_id in self._seq_counters:
            return
        max_seq = await asyncio.to_thread(self._compute_max_seq, thread_id)
        self._seq_counters[thread_id] = max_seq

    def _write_record(self, record: dict) -> None:
        """把单条记录追加到对应 Run 的 JSONL 文件（阻塞 I/O）。"""
        path = self._run_file(record["thread_id"], record["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    def _read_thread_events(self, thread_id: str) -> list[dict]:
        """读取线程的全部事件并按 ``seq`` 升序返回（阻塞 I/O）。"""
        events = []
        thread_dir = self._thread_dir(thread_id)
        if not thread_dir.exists():
            return events
        for f in sorted(thread_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().splitlines():
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSONL line in %s", f)
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _read_run_events(self, thread_id: str, run_id: str) -> list[dict]:
        """读取指定 Run 文件中的事件（阻塞 I/O）。"""
        path = self._run_file(thread_id, run_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in %s", path)
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _delete_thread_files(self, thread_id: str) -> None:
        """删除线程 runs 目录下的所有 JSONL 文件（阻塞 I/O）。"""
        thread_dir = self._thread_dir(thread_id)
        if thread_dir.exists():
            for f in thread_dir.glob("*.jsonl"):
                f.unlink()

    def _delete_run_file(self, thread_id: str, run_id: str) -> None:
        """删除指定 Run 的 JSONL 文件（阻塞 I/O）。"""
        path = self._run_file(thread_id, run_id)
        if path.exists():
            path.unlink()

    async def put(self, *, thread_id, run_id, event_type, category, content="", metadata=None, created_at=None):
        """写入一条事件，自动分配 ``seq`` 并落盘。

        在每线程的 ``asyncio.Lock`` 保护下进行，文件 I/O 通过
        ``asyncio.to_thread`` 卸载到线程池。
        """
        async with self._get_write_lock(thread_id):
            await self._ensure_seq_loaded(thread_id)
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
            await asyncio.to_thread(self._write_record, record)
            return record

    async def put_batch(self, events):
        """批量写入事件，逐条复用 :meth:`put` 语义。"""
        if not events:
            return []
        results = []
        for ev in events:
            record = await self.put(**ev)
            results.append(record)
        return results

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None):
        """返回线程下 ``category=message`` 的消息，支持双向游标分页。

        需扫描线程全部 Run 文件以得到统一的 ``seq`` 排序。
        """
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
        messages = [e for e in all_events if e.get("category") == "message"]

        if before_seq is not None:
            messages = [e for e in messages if e["seq"] < before_seq]
            return messages[-limit:]
        elif after_seq is not None:
            messages = [e for e in messages if e["seq"] > after_seq]
            return messages[:limit]
        else:
            return messages[-limit:]

    async def list_events(self, thread_id, run_id, *, event_types=None, limit=500):
        """返回指定 Run 的全部事件，可选按 ``event_types`` 过滤（快路径，只读单文件）。"""
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
        if event_types is not None:
            events = [e for e in events if e.get("event_type") in event_types]
        return events[:limit]

    async def list_messages_by_run(self, thread_id, run_id, *, limit=50, before_seq=None, after_seq=None):
        """返回指定 Run 下的 ``category=message`` 消息，支持双向游标分页。"""
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
        filtered = [e for e in events if e.get("category") == "message"]
        if before_seq is not None:
            filtered = [e for e in filtered if e.get("seq", 0) < before_seq]
        if after_seq is not None:
            filtered = [e for e in filtered if e.get("seq", 0) > after_seq]
        if after_seq is not None:
            return filtered[:limit]
        else:
            return filtered[-limit:] if len(filtered) > limit else filtered

    async def count_messages(self, thread_id):
        """统计线程下 ``category=message`` 的消息数。"""
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
        return sum(1 for e in all_events if e.get("category") == "message")

    async def delete_by_thread(self, thread_id):
        """删除线程的全部 JSONL 文件并清理 ``seq``/锁缓存。"""
        async with self._get_write_lock(thread_id):
            all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
            count = len(all_events)
            await asyncio.to_thread(self._delete_thread_files, thread_id)
            self._seq_counters.pop(thread_id, None)
            # Pop the lock inside the held scope to minimise the window where a new caller
            # could obtain a fresh lock while a waiting coroutine still holds the old one.
            # Note: coroutines that already acquired a reference to this lock before the
            # delete will still proceed after we release — this is an accepted narrow race.
            self._write_locks.pop(thread_id, None)
            return count

    async def delete_by_run(self, thread_id, run_id):
        """删除指定 Run 的 JSONL 文件。"""
        async with self._get_write_lock(thread_id):
            events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
            count = len(events)
            await asyncio.to_thread(self._delete_run_file, thread_id, run_id)
            return count
