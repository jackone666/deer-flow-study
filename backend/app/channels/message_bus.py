"""``MessageBus`` —— 异步发布/订阅中心，将渠道与智能体调度器解耦。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PENDING_CLARIFICATION_METADATA_KEY = "pending_clarification"
RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY = "resolved_from_pending_clarification"


# ---------------------------------------------------------------------------
# 消息类型
# ---------------------------------------------------------------------------


class InboundMessageType(StrEnum):
    """从 IM 渠道收到的消息类型。"""

    CHAT = "chat"
    COMMAND = "command"


@dataclass
class InboundMessage:
    """从 IM 渠道发往智能体调度器的入站消息。

    Attributes:
        channel_name: 来源渠道的名称（例如 ``"feishu"``、``"slack"``）。
        chat_id: 平台特定的会话/聊天标识。
        user_id: 平台特定的用户标识。
        text: 消息文本。
        msg_type: 是普通聊天消息还是命令。
        thread_ts: 可选的平台主题标识（用于在主题内回复）。
        topic_id: 用来映射到 DeerFlow 主题的会话主题标识。在同一个 ``chat_id`` 内
            共享同一 ``topic_id`` 的消息会复用同一个 DeerFlow 主题。
            当为 ``None`` 时，每条消息都会创建一个新主题（一次性问答）。
        files: 可选的文件附件列表（平台相关的字典）。
        metadata: 来自渠道的任意额外数据。
        created_at: 消息创建时的 Unix 时间戳。
    """

    channel_name: str
    chat_id: str
    user_id: str
    text: str
    msg_type: InboundMessageType = InboundMessageType.CHAT
    thread_ts: str | None = None
    topic_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class ResolvedAttachment:
    """已解析为主机文件系统路径、可直接上传的文件附件。

    Attributes:
        virtual_path: 原始虚拟路径（例如 ``/mnt/user-data/outputs/report.pdf``）。
        actual_path: 已解析的主机文件系统路径。
        filename: 文件的基名。
        mime_type: MIME 类型（例如 ``"application/pdf"``）。
        size: 文件字节数。
        is_image: 若 MIME 类型为 ``image/*`` 则为 ``True``（各平台对图片处理可能不同）。
    """

    virtual_path: str
    actual_path: Path
    filename: str
    mime_type: str
    size: int
    is_image: bool


@dataclass
class OutboundMessage:
    """从智能体调度器发回某渠道的出站消息。

    Attributes:
        channel_name: 目标渠道名（用于路由）。
        chat_id: 目标聊天/会话标识。
        thread_id: 产生此响应的 DeerFlow 主题 ID。
        text: 响应文本。
        artifacts: 智能体产生的产物路径列表。
        is_final: 是否为响应流中的最后一条消息。
        thread_ts: 可选的平台主题标识，用于在主题内回复。
        metadata: 任意附加数据。
        created_at: Unix 时间戳。
    """

    channel_name: str
    chat_id: str
    thread_id: str
    text: str
    artifacts: list[str] = field(default_factory=list)
    attachments: list[ResolvedAttachment] = field(default_factory=list)
    is_final: bool = True
    thread_ts: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# MessageBus
# ---------------------------------------------------------------------------

OutboundCallback = Callable[[OutboundMessage], Coroutine[Any, Any, None]]


class MessageBus:
    """连接渠道和智能体调度器的异步发布/订阅中心。

    渠道发布入站消息，调度器消费它们；调度器发布出站消息，
    各渠道通过已注册的回调接收它们。
    """

    def __init__(self) -> None:
        """初始化消息总线，创建入队队列和出站监听器列表。"""
        self._inbound_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._outbound_listeners: list[OutboundCallback] = []

    # -- inbound -----------------------------------------------------------

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """将来自渠道的入站消息入队。

        Args:
            msg: 要入队的 ``InboundMessage`` 实例。
        """
        await self._inbound_queue.put(msg)
        logger.info(
            "[Bus] inbound enqueued: channel=%s, chat_id=%s, type=%s, queue_size=%d",
            msg.channel_name,
            msg.chat_id,
            msg.msg_type.value,
            self._inbound_queue.qsize(),
        )

    async def get_inbound(self) -> InboundMessage:
        """阻塞等待直到下一条入站消息可用。

        Returns:
            InboundMessage: 从入队队列中取出的下一条消息。
        """
        return await self._inbound_queue.get()

    @property
    def inbound_queue(self) -> asyncio.Queue[InboundMessage]:
        """直接访问入队队列，供调度器使用短轮询等场景。"""
        return self._inbound_queue

    # -- outbound ----------------------------------------------------------

    def subscribe_outbound(self, callback: OutboundCallback) -> None:
        """注册一个用于接收出站消息的异步回调。

        Args:
            callback: 接收 ``OutboundMessage`` 的可等待函数。
        """
        self._outbound_listeners.append(callback)

    def unsubscribe_outbound(self, callback: OutboundCallback) -> None:
        """移除一个之前注册的出站回调。

        Args:
            callback: 之前通过 ``subscribe_outbound`` 注册的回调实例。
        """
        self._outbound_listeners = [cb for cb in self._outbound_listeners if cb is not callback]

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """将出站消息分发给所有已注册的监听器。

        Args:
            msg: 要分发的 ``OutboundMessage`` 实例。
        """
        logger.info(
            "[Bus] outbound dispatching: channel=%s, chat_id=%s, listeners=%d, text_len=%d",
            msg.channel_name,
            msg.chat_id,
            len(self._outbound_listeners),
            len(msg.text),
        )
        for callback in self._outbound_listeners:
            try:
                await callback(msg)
            except Exception:
                logger.exception("Error in outbound callback for channel=%s", msg.channel_name)
