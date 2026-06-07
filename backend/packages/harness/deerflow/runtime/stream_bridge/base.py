"""流桥的抽象协议。

    ``StreamBridge`` 将 agent worker（生产者）与 SSE 端点（消费者）解耦，
    与 LangGraph Platform 的 Queue + StreamManager 架构保持一致。
"""


from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """单条流事件。
    
        Attributes:
            id: 单调递增的事件 ID（用作 SSE ``id:`` 字段，支持 ``Last-Event-ID`` 重连）。
            event: SSE 事件名，例如 ``"metadata"``、``"updates"``、``"messages"`` 等。
            data: 事件负载（已预序列化为字符串，供 SSE 使用）。
            ts: 事件时间戳（epoch 秒，用于 TTL 簿记）。
    """


    id: str
    event: str
    data: Any


HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)


class StreamBridge(abc.ABC):
    """流桥的抽象基类。"""


    @abc.abstractmethod
    async def publish(self, run_id: str, event: str, data: Any) -> None:
        """为 *run_id* 入队一条事件（生产者侧）。"""


    @abc.abstractmethod
    async def publish_end(self, run_id: str) -> None:
        """标记 *run_id* 不再有新事件产出。"""


    @abc.abstractmethod
    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        """为 *run_id* 产出事件的异步迭代器（消费者侧）。
        
            当 *heartbeat_interval* 秒内没有事件到达时，产出 :data:`HEARTBEAT_SENTINEL`。
            生产者通过 :meth:`publish_end` 发出结束信号后，产出 :data:`END_SENTINEL`。
        """


    @abc.abstractmethod
    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        """释放与 *run_id* 关联的资源。
        
            当 *delay* > 0 时，实现应在释放前等待一段时间，
            为晚到的订阅者留出拉取剩余事件的机会。
        """


    async def close(self) -> None:
        """释放后端资源。默认是空操作。"""

