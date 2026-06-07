"""DeerFlow 的 IM 渠道集成。

提供一个可插拔的渠道系统，通过 ``ChannelManager`` 将外部即时通讯平台
（飞书/Lark、Slack、Telegram、钉钉、企业微信、微信、Discord）连接到 DeerFlow 智能体。
``ChannelManager`` 使用 ``langgraph-sdk`` 与 Gateway 的 LangGraph 兼容 API 通信。
"""

from app.channels.base import Channel
from app.channels.message_bus import InboundMessage, MessageBus, OutboundMessage

__all__ = [
    "Channel",
    "InboundMessage",
    "MessageBus",
    "OutboundMessage",
]
