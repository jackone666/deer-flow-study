"""Slack 渠道 —— 通过 Socket Mode 连接（无需公网 IP）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from markdown_to_mrkdwn import SlackMarkdownConverter

from app.channels.base import Channel
from app.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)

_slack_md_converter = SlackMarkdownConverter()


def _normalize_allowed_users(allowed_users: Any) -> set[str]:
    """把 ``allowed_users`` 配置归一化为 Slack user id 集合。"""
    if allowed_users is None:
        return set()
    if isinstance(allowed_users, str):
        values = [allowed_users]
    elif isinstance(allowed_users, list | tuple | set):
        values = allowed_users
    else:
        logger.warning(
            "Slack allowed_users should be a list of Slack user IDs or a single Slack user ID string; treating %s as one string value",
            type(allowed_users).__name__,
        )
        values = [allowed_users]
    return {str(user_id) for user_id in values if str(user_id)}


class SlackChannel(Channel):
    """基于 Socket Mode（WebSocket，免公网 IP）的 Slack IM 渠道。

    ``config.yaml`` 中 ``channels.slack`` 下的配置键：
        - ``bot_token``：Slack Bot User OAuth Token（``xoxb-...``）。
        - ``app_token``：用于 Socket Mode 的 Slack App-Level Token（``xapp-...``）。
        - ``allowed_users``：（可选）允许的 Slack user ID 列表；或单个 ID 字符串。
            空 = 全部允许。其他标量会被当作单个字符串处理并打印警告。
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        """初始化 Slack 渠道，缓存客户端占位和允许用户集合。"""
        super().__init__(name="slack", bus=bus, config=config)
        self._socket_client = None
        self._web_client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._allowed_users = _normalize_allowed_users(config.get("allowed_users", []))

    async def start(self) -> None:
        """启动渠道：构造 Web Client、Socket Mode 客户端、订阅总线并开启连接。"""
        if self._running:
            return

        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            logger.error("slack-sdk is not installed. Install it with: uv add slack-sdk")
            return

        self._SocketModeResponse = SocketModeResponse

        bot_token = self.config.get("bot_token", "")
        app_token = self.config.get("app_token", "")

        if not bot_token or not app_token:
            logger.error("Slack channel requires bot_token and app_token")
            return

        self._web_client = WebClient(token=bot_token)
        self._socket_client = SocketModeClient(
            app_token=app_token,
            web_client=self._web_client,
        )
        self._loop = asyncio.get_event_loop()

        self._socket_client.socket_mode_request_listeners.append(self._on_socket_event)

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # 在后台线程中启动 Socket Mode
        asyncio.get_event_loop().run_in_executor(None, self._socket_client.connect)
        logger.info("Slack channel started")

    async def stop(self) -> None:
        """停止渠道：解绑订阅、关闭 Socket Mode 客户端。"""
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._socket_client:
            self._socket_client.close()
            self._socket_client = None
        logger.info("Slack channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        """把出站消息发到 Slack，附带回执表情和指数退避重试。"""
        if not self._web_client:
            return

        kwargs: dict[str, Any] = {
            "channel": msg.chat_id,
            "text": _slack_md_converter.convert(msg.text),
        }
        if msg.thread_ts:
            kwargs["thread_ts"] = msg.thread_ts

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await asyncio.to_thread(self._web_client.chat_postMessage, **kwargs)
                # 给主题根消息加上完成表情
                if msg.thread_ts:
                    await asyncio.to_thread(
                        self._add_reaction,
                        msg.chat_id,
                        msg.thread_ts,
                        "white_check_mark",
                    )
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "[Slack] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[Slack] send failed after %d attempts: %s", _max_retries, last_exc)
        # 出错时给主题根加失败表情
        if msg.thread_ts:
            try:
                await asyncio.to_thread(
                    self._add_reaction,
                    msg.chat_id,
                    msg.thread_ts,
                    "x",
                )
            except Exception:
                pass
        if last_exc is None:
            raise RuntimeError("Slack send failed without an exception from any attempt")
        raise last_exc

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """用 ``files_upload_v2`` 上传并发送文件附件。"""
        if not self._web_client:
            return False

        try:
            kwargs: dict[str, Any] = {
                "channel": msg.chat_id,
                "file": str(attachment.actual_path),
                "filename": attachment.filename,
                "title": attachment.filename,
            }
            if msg.thread_ts:
                kwargs["thread_ts"] = msg.thread_ts

            await asyncio.to_thread(self._web_client.files_upload_v2, **kwargs)
            logger.info("[Slack] file uploaded: %s to channel=%s", attachment.filename, msg.chat_id)
            return True
        except Exception:
            logger.exception("[Slack] failed to upload file: %s", attachment.filename)
            return False

    # -- 内部 ----------------------------------------------------------

    def _add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        """给消息加 emoji 表情（尽力而为）。"""
        if not self._web_client:
            return
        try:
            self._web_client.reactions_add(
                channel=channel_id,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            if "already_reacted" not in str(exc):
                logger.warning("[Slack] failed to add reaction %s: %s", emoji, exc)

    def _send_running_reply(self, channel_id: str, thread_ts: str) -> None:
        """在主题中发送“⏳ 正在处理”的回复（从 SDK 线程调用）。"""
        if not self._web_client:
            return
        try:
            self._web_client.chat_postMessage(
                channel=channel_id,
                text=":hourglass_flowing_sand: Working on it...",
                thread_ts=thread_ts,
            )
            logger.info("[Slack] 'Working on it...' reply sent in channel=%s, thread_ts=%s", channel_id, thread_ts)
        except Exception:
            logger.exception("[Slack] failed to send running reply in channel=%s", channel_id)

    def _on_socket_event(self, client, req) -> None:
        """slack-sdk 在每个 Socket Mode 事件到来时回调。"""
        try:
            # 先确认事件
            response = self._SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

            event_type = req.type
            if event_type != "events_api":
                return

            event = req.payload.get("event", {})
            etype = event.get("type", "")

            # 处理消息事件（私信或 @ 提及）
            if etype in ("message", "app_mention"):
                self._handle_message_event(event)

        except Exception:
            logger.exception("Error processing Slack event")

    def _handle_message_event(self, event: dict) -> None:
        """把 Slack 消息事件转换为入站消息并发布。"""
        # 忽略机器人消息
        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user", "")

        # 校验允许用户
        if self._allowed_users and user_id not in self._allowed_users:
            logger.debug("Ignoring message from non-allowed user: %s", user_id)
            return

        text = event.get("text", "").strip()
        if not text:
            return

        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")

        if text.startswith("/"):
            msg_type = InboundMessageType.COMMAND
        else:
            msg_type = InboundMessageType.CHAT

        # topic_id: 使用 thread_ts 作为主题标识。
        # 对于主题内消息，thread_ts 是根消息的 ts（共享主题）；
        # 对于非主题消息，thread_ts 是消息自身的 ts（新主题）。
        inbound = self._make_inbound(
            chat_id=channel_id,
            user_id=user_id,
            text=text,
            msg_type=msg_type,
            thread_ts=thread_ts,
        )
        inbound.topic_id = thread_ts

        if self._loop and self._loop.is_running():
            # 先加一个 👀 表情作为回执
            self._add_reaction(channel_id, event.get("ts", thread_ts), "eyes")
            # 在主题中先发“正在处理”回复（fire-and-forget）
            self._send_running_reply(channel_id, thread_ts)
            asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._loop)
