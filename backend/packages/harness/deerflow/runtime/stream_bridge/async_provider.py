"""异步流桥工厂。

    提供与 :func:`deerflow.runtime.checkpointer.async_provider.make_checkpointer` 风格一致的
    **异步上下文管理器**。例如在 FastAPI lifespan 中使用：

    .. code-block:: python

        from deerflow.ag
"""


from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from deerflow.config.app_config import AppConfig
from deerflow.config.stream_bridge_config import get_stream_bridge_config

from .base import StreamBridge

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def make_stream_bridge(app_config: AppConfig | None = None) -> AsyncIterator[StreamBridge]:
    """产出 :class:`StreamBridge` 的异步上下文管理器。
    
        当没有提供配置且全局也没有设置时，回退到 :class:`MemoryStreamBridge`。
    """

    if app_config is None:
        config = get_stream_bridge_config()
    else:
        config = app_config.stream_bridge

    if config is None or config.type == "memory":
        from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

        maxsize = config.queue_maxsize if config is not None else 256
        bridge = MemoryStreamBridge(queue_maxsize=maxsize)
        logger.info("Stream bridge initialised: memory (queue_maxsize=%d)", maxsize)
        try:
            yield bridge
        finally:
            await bridge.close()
        return

    if config.type == "redis":
        raise NotImplementedError("Redis stream bridge planned for Phase 2")

    raise ValueError(f"Unknown stream bridge type: {config.type!r}")
