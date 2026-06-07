"""流桥——将 agent worker 与 SSE 端点解耦。

    ``StreamBridge`` 位于运行 agent 的后台任务（生产者）与向客户端推送
    Server-Sent Events 的 HTTP 端点（消费者）之间。
    每个运行都会获得一个独立的桥；事件通过 :meth:`publish` 产出，
    通过 :meth:`subscribe` 消费。
"""


from .async_provider import make_stream_bridge
from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent
from .memory import MemoryStreamBridge

__all__ = [
    "END_SENTINEL",
    "HEARTBEAT_SENTINEL",
    "MemoryStreamBridge",
    "StreamBridge",
    "StreamEvent",
    "make_stream_bridge",
]
