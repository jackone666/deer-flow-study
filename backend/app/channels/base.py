"""IM 渠道的抽象基类。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)


class Channel(ABC):
    """所有 IM 渠道实现的基类。

    每个渠道连接到一个外部即时通讯平台，并负责：
    1. 接收消息，将其包装为 ``InboundMessage`` 并发布到消息总线。
    2. 订阅出站消息，将回复发送回对应平台。

    子类必须实现 ``start``、``stop`` 和 ``send`` 方法。
    """

    def __init__(self, name: str, bus: MessageBus, config: dict[str, Any]) -> None:
        """初始化渠道实例。

        Args:
            name: 渠道名称（例如 ``"feishu"``、``"slack"``），用于消息路由。
            bus: ``MessageBus`` 实例，用于发布入站消息和订阅出站消息。
            config: 渠道配置字典，具体字段由各子类解释。
        """
        self.name = name
        self.bus = bus
        self.config = config
        self._running = False

    @property
    def is_running(self) -> bool:
        """是否已启动并正在运行。

        Returns:
            bool: 若 ``start()`` 已成功执行且未 ``stop()``，返回 ``True``。
        """
        return self._running

    @property
    def supports_streaming(self) -> bool:
        """是否支持增量（流式）出站更新。

        Returns:
            bool: 默认为 ``False``。支持流式的渠道（例如飞书、企业微信）需重写。
        """
        return False

    # -- lifecycle ---------------------------------------------------------

    @abstractmethod
    async def start(self) -> None:
        """启动渠道，开始监听来自外部平台的消息。"""

    @abstractmethod
    async def stop(self) -> None:
        """优雅地停止渠道。"""

    # -- outbound ----------------------------------------------------------

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """将消息发送回外部平台。

        实现应使用 ``msg.chat_id`` 和 ``msg.thread_ts`` 将回复路由到正确的会话/主题。
        """

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """将单个文件附件上传到平台。

        Returns:
            bool: 上传成功返回 ``True``，否则返回 ``False``。默认实现不支持文件上传，返回 ``False``。
        """
        return False

    # -- helpers -----------------------------------------------------------

    def _make_inbound(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        *,
        msg_type: InboundMessageType = InboundMessageType.CHAT,
        thread_ts: str | None = None,
        files: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InboundMessage:
        """便捷工厂方法：构造 ``InboundMessage`` 实例。

        Args:
            chat_id: 平台特定的会话标识。
            user_id: 平台特定的用户标识。
            text: 消息文本。
            msg_type: 消息类型（普通聊天或命令），默认 ``CHAT``。
            thread_ts: 可选的主题时间戳，用于在主题内回复。
            files: 可选的文件附件元数据列表。
            metadata: 任意附加元数据。

        Returns:
            InboundMessage: 构造好的入站消息实例。
        """
        return InboundMessage(
            channel_name=self.name,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            msg_type=msg_type,
            thread_ts=thread_ts,
            files=files or [],
            metadata=metadata or {},
        )

    async def _on_outbound(self, msg: OutboundMessage) -> None:
        """注册到总线的出站回调。

        仅转发本渠道对应的消息。先发送文本，再上传文件附件。
        若文本发送失败则完全跳过文件上传，避免出现“只发文件没有文本”的局部交付。
        """
        if msg.channel_name == self.name:
            try:
                await self.send(msg)
            except Exception:
                logger.exception("Failed to send outbound message on channel %s", self.name)
                return  # 文本消息失败时不再尝试上传文件

            for attachment in msg.attachments:
                try:
                    success = await self.send_file(msg, attachment)
                    if not success:
                        logger.warning("[%s] file upload skipped for %s", self.name, attachment.filename)
                except Exception:
                    logger.exception("[%s] failed to upload file %s", self.name, attachment.filename)

    async def receive_file(self, msg: InboundMessage, thread_id: str) -> InboundMessage:
        """可选地处理并落盘入站文件附件。

        默认实现不做任何处理，直接返回原消息。子类（例如 ``FeishuChannel``）
        可重写该方法以下载 ``msg.files`` 中引用的文件（图片、文档等），
        将其保存到沙盒中，并更新 ``msg.text`` 加入沙盒文件路径，供下游模型使用。

        Args:
            msg: 入站消息，可能在 ``msg.files`` 中包含文件元数据。
            thread_id: 已解析的 DeerFlow 主题 ID，用于沙盒路径上下文。

        Returns:
            InboundMessage: 已被（可能）修改的入站消息，其 ``text`` 和/或 ``files`` 已更新。
        """
        return msg
