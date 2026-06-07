"""RunEvent 存储抽象与各后端实现入口。

- :class:`RunEventStore`：异步抽象接口。
- :class:`MemoryRunEventStore`：纯内存实现。
- :class:`DbRunEventStore`：基于 SQLAlchemy 的数据库实现。
- :class:`JsonlRunEventStore`：基于 JSONL 文件的实现。
"""

from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore


def make_run_event_store(config=None) -> RunEventStore:
    """根据 ``run_events.backend`` 配置创建对应的 :class:`RunEventStore`。

    Args:
        config: ``run_events`` 配置节，可空；为空时退回 :class:`MemoryRunEventStore`。
            包含以下字段：
            - ``backend``: ``"memory"`` / ``"db"`` / ``"jsonl"``。
            - ``max_trace_content``: 仅 ``db`` 后端生效，trace 内容的最大字节数。

    Returns:
        根据后端配置选定的 :class:`RunEventStore` 实现。

    Raises:
        ValueError: 后端名称无法识别。
    """
    if config is None or config.backend == "memory":
        return MemoryRunEventStore()
    if config.backend == "db":
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            # database.backend=memory but run_events.backend=db -> fallback
            return MemoryRunEventStore()
        from deerflow.runtime.events.store.db import DbRunEventStore

        return DbRunEventStore(sf, max_trace_content=config.max_trace_content)
    if config.backend == "jsonl":
        from deerflow.runtime.events.store.jsonl import JsonlRunEventStore

        return JsonlRunEventStore()
    raise ValueError(f"Unknown run_events backend: {config.backend!r}")


__all__ = ["MemoryRunEventStore", "RunEventStore", "make_run_event_store"]
