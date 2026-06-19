"""钉钉渠道实现。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app.channels.base import Channel
from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)

DINGTALK_API_BASE = "https://api.dingtalk.com"

_TOKEN_REFRESH_MARGIN_SECONDS = 300

_CONVERSATION_TYPE_P2P = "1"
_CONVERSATION_TYPE_GROUP = "2"

_MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024


def _normalize_conversation_type(raw: Any) -> str:
    """将 ``conversationType`` 归一化为 ``"1"``（单聊）或 ``"2"``（群聊）。

    流式负载里该字段可能为 int 或 str。
    """
    if raw is None:
        return _CONVERSATION_TYPE_P2P
    s = str(raw).strip()
    if s == _CONVERSATION_TYPE_GROUP:
        return _CONVERSATION_TYPE_GROUP
    return _CONVERSATION_TYPE_P2P


def _normalize_allowed_users(allowed_users: Any) -> set[str]:
    """把 ``allowed_users`` 规范化为钉钉 staff id 集合，类型不对时打印警告。"""
    if allowed_users is None:
        return set()
    if isinstance(allowed_users, str):
        values = [allowed_users]
    elif isinstance(allowed_users, (list, tuple, set)):
        values = allowed_users
    else:
        logger.warning(
            "DingTalk allowed_users should be a list of user IDs; treating %s as one string value",
            type(allowed_users).__name__,
        )
        values = [allowed_users]
    return {str(uid) for uid in values if str(uid)}


def _is_dingtalk_command(text: str) -> bool:
    """判断文本是否以已知斜杠命令开头。"""
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS


def _extract_text_from_rich_text(rich_text_list: list) -> str:
    """从钉钉富文本列表中抽取所有 ``text`` 字段并以空格连接。"""
    parts: list[str] = []
    for item in rich_text_list:
        if isinstance(item, dict) and "text" in item:
            parts.append(item["text"])
    return " ".join(parts)


_FENCED_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HORIZONTAL_RULE_RE = re.compile(r"^-{3,}$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\|[-:| ]+\|$", re.MULTILINE)


def _convert_markdown_table(text: str) -> str:
    """把 Markdown 表格转成钉钉 sampleMarkdown 可渲染的引用块。"""
    # DingTalk sampleMarkdown 不会渲染由竖线分隔的表格。
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检测表格：表头行后紧跟分隔行
        if i + 1 < len(lines) and line.strip().startswith("|") and _TABLE_SEPARATOR_RE.match(lines[i + 1].strip()):
            headers = [h.strip() for h in line.strip().strip("|").split("|")]
            i += 2  # 跳过表头 + 分隔行
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                for h, c in zip(headers, cells):
                    result.append(f"> **{h}**: {c}")
                result.append("")
                i += 1
        else:
            result.append(line)
            i += 1
    return "\n".join(result)


def _adapt_markdown_for_dingtalk(text: str) -> str:
    """将 Markdown 转换为钉钉 sampleMarkdown 渲染器能识别的子集。

    钉钉的 sampleMarkdown 不支持标准 Markdown 的代码块、内联代码、表格和水平线。
    本函数做以下转换：
    - 围栏代码块 → 引用块（带语言标签）
    - 内联代码 → 加粗文本
    - Markdown 表格 → 引用块格式的键值对
    - 水平线 → 等宽字符分隔线
    """

    def _code_block_to_quote(match: re.Match) -> str:
        """把 Markdown 代码块改写成引用块。"""
        lang = match.group(1)
        code = match.group(2).rstrip("\n")
        prefix = f"> **{lang}**\n" if lang else ""
        quoted_lines = "\n".join(f"> {line}" for line in code.split("\n"))
        return f"{prefix}{quoted_lines}\n"

    text = _FENCED_CODE_BLOCK_RE.sub(_code_block_to_quote, text)
    text = _INLINE_CODE_RE.sub(r"**\1**", text)
    text = _convert_markdown_table(text)
    text = _HORIZONTAL_RULE_RE.sub("───────────", text)
    return text


class DingTalkChannel(Channel):
    """基于 Stream Push（WebSocket，免公网 IP）的钉钉 IM 渠道。"""

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        """初始化钉钉渠道，从配置中读取凭据、卡片模板和允许用户等。

        Args:
            bus: 共享消息总线。
            config: 渠道配置字典，键见模块注释。
        """
        super().__init__(name="dingtalk", bus=bus, config=config)
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._client_id: str = ""
        self._client_secret: str = ""
        self._allowed_users: set[str] = _normalize_allowed_users(config.get("allowed_users"))
        self._cached_token: str = ""
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._card_template_id: str = config.get("card_template_id", "")
        self._card_track_ids: dict[str, str] = {}
        self._dingtalk_client: Any = None
        self._stream_client: Any = None
        self._incoming_messages: dict[str, Any] = {}
        self._incoming_messages_lock = threading.Lock()
        self._card_repliers: dict[str, Any] = {}

    @property
    def supports_streaming(self) -> bool:
        """是否启用 AI Card 流式模式。"""
        return bool(self._card_template_id)

    async def start(self) -> None:
        """启动渠道：检查依赖、获取主循环、订阅总线并启动 Stream Push 线程。"""
        if self._running:
            return

        try:
            import dingtalk_stream  # noqa: F401
        except ImportError:
            logger.error("dingtalk-stream is not installed. Install it with: uv add dingtalk-stream")
            return

        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")

        if not client_id or not client_secret:
            logger.error("DingTalk channel requires client_id and client_secret")
            return

        self._client_id = client_id
        self._client_secret = client_secret
        self._main_loop = asyncio.get_running_loop()

        if self._card_template_id:
            logger.info("[DingTalk] AI Card mode enabled (template=%s)", self._card_template_id)

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        self._thread = threading.Thread(
            target=self._run_stream,
            args=(client_id, client_secret),
            daemon=True,
        )
        self._thread.start()
        logger.info("DingTalk channel started")

    async def stop(self) -> None:
        """停止渠道：解绑订阅、关闭流客户端、清理状态、等待线程结束。"""
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)

        stream_client = self._stream_client
        if stream_client is not None:
            try:
                if hasattr(stream_client, "disconnect"):
                    stream_client.disconnect()
            except Exception:
                logger.debug("[DingTalk] error disconnecting stream client", exc_info=True)

        self._dingtalk_client = None
        self._stream_client = None
        with self._incoming_messages_lock:
            self._incoming_messages.clear()
        self._card_repliers.clear()
        self._card_track_ids.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("DingTalk channel stopped")

    def _resolve_routing(self, msg: OutboundMessage) -> tuple[str, str, str]:
        """返回 ``(conversation_type, sender_staff_id, conversation_id)``。

        优先使用 ``msg.chat_id`` 作为路由键，元数据作为回退。
        """
        conversation_type = _normalize_conversation_type(msg.metadata.get("conversation_type"))
        sender_staff_id = msg.metadata.get("sender_staff_id", "")
        conversation_id = msg.metadata.get("conversation_id", "")
        if conversation_type == _CONVERSATION_TYPE_GROUP:
            conversation_id = msg.chat_id or conversation_id
        else:
            sender_staff_id = msg.chat_id or sender_staff_id
        return conversation_type, sender_staff_id, conversation_id

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        """向钉钉发送一条出站消息。

        在 AI Card 模式下优先复用已有卡片流式更新；否则用 sampleMarkdown
        发送，带指数退避重试。

        Args:
            msg: 待发送的出站消息。
            _max_retries: sampleMarkdown 模式下的最大重试次数。
        """
        conversation_type, sender_staff_id, conversation_id = self._resolve_routing(msg)
        robot_code = self._client_id

        # 卡片模式：流式更新现有 AI 卡片
        source_key = self._make_card_source_key_from_outbound(msg)
        out_track_id = self._card_track_ids.get(source_key)

        # 启用 ``card_template_id`` 时支持 ``runs.stream``（非最终 + 最终出站）。
        # 如果卡片创建失败，则跳过非最终片段以避免重复消息。
        if self._card_template_id and not out_track_id and not msg.is_final:
            return

        if out_track_id:
            try:
                await self._stream_update_card(
                    out_track_id,
                    msg.text,
                    is_finalize=msg.is_final,
                )
            except Exception:
                logger.warning("[DingTalk] card stream failed, falling back to sampleMarkdown")
                if msg.is_final:
                    self._card_track_ids.pop(source_key, None)
                    self._card_repliers.pop(out_track_id, None)
                    await self._send_markdown_fallback(robot_code, conversation_type, sender_staff_id, conversation_id, msg.text)
                    return
            if msg.is_final:
                self._card_track_ids.pop(source_key, None)
                self._card_repliers.pop(out_track_id, None)
            return

        # 非卡片模式：使用 sampleMarkdown 发送，附带重试
        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                if conversation_type == _CONVERSATION_TYPE_GROUP:
                    await self._send_group_message(robot_code, conversation_id, msg.text, at_user_ids=[sender_staff_id] if sender_staff_id else None)
                else:
                    await self._send_p2p_message(robot_code, sender_staff_id, msg.text)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt
                    logger.warning(
                        "[DingTalk] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[DingTalk] send failed after %d attempts: %s", _max_retries, last_exc)
        if last_exc is None:
            raise RuntimeError("DingTalk send failed without an exception from any attempt")
        raise last_exc

    async def _send_markdown_fallback(
        self,
        robot_code: str,
        conversation_type: str,
        sender_staff_id: str,
        conversation_id: str,
        text: str,
    ) -> None:
        """卡片流失败后的 sampleMarkdown 回退。"""
        try:
            if conversation_type == _CONVERSATION_TYPE_GROUP:
                await self._send_group_message(robot_code, conversation_id, text)
            else:
                await self._send_p2p_message(robot_code, sender_staff_id, text)
        except Exception:
            logger.exception("[DingTalk] markdown fallback also failed")
            raise

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """将单个文件附件上传并发送给用户或群聊。

        Returns:
            bool: 成功返回 ``True``，否则 ``False``（含大小超限等）。
        """
        if attachment.size > _MAX_UPLOAD_SIZE_BYTES:
            logger.warning("[DingTalk] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        conversation_type, sender_staff_id, conversation_id = self._resolve_routing(msg)
        robot_code = self._client_id

        try:
            media_id = await self._upload_media(attachment.actual_path, "image" if attachment.is_image else "file")
            if not media_id:
                return False

            if attachment.is_image:
                msg_key = "sampleImageMsg"
                msg_param = json.dumps({"photoURL": media_id})
            else:
                msg_key = "sampleFile"
                msg_param = json.dumps(
                    {
                        "fileUrl": media_id,
                        "fileName": attachment.filename,
                        "fileSize": str(attachment.size),
                    }
                )

            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                if conversation_type == _CONVERSATION_TYPE_GROUP:
                    response = await client.post(
                        f"{DINGTALK_API_BASE}/v1.0/robot/groupMessages/send",
                        headers=self._api_headers(token),
                        json={
                            "msgKey": msg_key,
                            "msgParam": msg_param,
                            "robotCode": robot_code,
                            "openConversationId": conversation_id,
                        },
                    )
                else:
                    response = await client.post(
                        f"{DINGTALK_API_BASE}/v1.0/robot/oToMessages/batchSend",
                        headers=self._api_headers(token),
                        json={
                            "msgKey": msg_key,
                            "msgParam": msg_param,
                            "robotCode": robot_code,
                            "userIds": [sender_staff_id],
                        },
                    )
                response.raise_for_status()

            logger.info("[DingTalk] file sent: %s", attachment.filename)
            return True
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
            logger.exception("[DingTalk] failed to send file: %s", attachment.filename)
            return False

    # -- 流客户端（运行在专用线程中） ----------------------------------------

    def _run_stream(self, client_id: str, client_secret: str) -> None:
        """在独立线程中运行钉钉 Stream Push 客户端。"""
        try:
            import dingtalk_stream

            credential = dingtalk_stream.Credential(client_id, client_secret)
            client = dingtalk_stream.DingTalkStreamClient(credential)
            self._stream_client = client
            client.register_callback_handler(
                dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                _DingTalkMessageHandler(self),
            )
            client.start_forever()
        except Exception:
            if self._running:
                logger.exception("DingTalk Stream Push error")
        finally:
            self._stream_client = None

    def _on_chatbot_message(self, message: Any) -> None:
        """处理一条机器人聊天消息并发布到消息总线。"""
        if not self._running:
            return
        try:
            sender_staff_id = message.sender_staff_id or ""
            conversation_type = _normalize_conversation_type(message.conversation_type)
            conversation_id = message.conversation_id or ""
            msg_id = message.message_id or ""
            sender_nick = message.sender_nick or ""

            if self._allowed_users and sender_staff_id not in self._allowed_users:
                logger.debug("[DingTalk] ignoring message from non-allowed user: %s", sender_staff_id)
                return

            text = self._extract_text(message)
            if not text:
                logger.info("[DingTalk] empty text, ignoring message")
                return

            logger.info(
                "[DingTalk] parsed message: conv_type=%s, msg_id=%s, sender=%s(%s), text=%r",
                conversation_type,
                msg_id,
                sender_staff_id,
                sender_nick,
                text[:100],
            )

            if _is_dingtalk_command(text):
                msg_type = InboundMessageType.COMMAND
            else:
                msg_type = InboundMessageType.CHAT

            # P2P: topic_id=None（每个用户单一主题，类似 Telegram 私聊）
            # Group: topic_id=msg_id（每条消息开始一个新主题，类似飞书）
            topic_id: str | None = msg_id if conversation_type == _CONVERSATION_TYPE_GROUP else None

            # chat_id：群聊用 conversation_id，P2P 用 sender_staff_id
            chat_id = conversation_id if conversation_type == _CONVERSATION_TYPE_GROUP else sender_staff_id

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_staff_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                metadata={
                    "conversation_type": conversation_type,
                    "conversation_id": conversation_id,
                    "sender_staff_id": sender_staff_id,
                    "sender_nick": sender_nick,
                    "message_id": msg_id,
                },
            )
            inbound.topic_id = topic_id

            if self._card_template_id:
                source_key = self._make_card_source_key(inbound)
                with self._incoming_messages_lock:
                    self._incoming_messages[source_key] = message

            if self._main_loop and self._main_loop.is_running():
                logger.info("[DingTalk] publishing inbound message to bus (type=%s, msg_id=%s)", msg_type.value, msg_id)
                fut = asyncio.run_coroutine_threadsafe(
                    self._prepare_inbound(chat_id, inbound),
                    self._main_loop,
                )
                fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "prepare_inbound", mid))
            else:
                logger.warning("[DingTalk] main loop not running, cannot publish inbound message")
        except Exception:
            logger.exception("[DingTalk] error processing chatbot message")

    @staticmethod
    def _extract_text(message: Any) -> str:
        """从钉钉消息对象抽取纯文本。"""
        msg_type = message.message_type
        if msg_type == "text" and message.text:
            return message.text.content.strip()
        if msg_type == "richText" and message.rich_text_content:
            return _extract_text_from_rich_text(message.rich_text_content.rich_text_list).strip()
        return ""

    async def _prepare_inbound(self, chat_id: str, inbound: InboundMessage) -> None:
        """在发布入站消息前先发送“正在处理”回复。"""
        # 必须先发完 running reply 再 publish_inbound，保证 AI card track
        # 在调度器发送流式出站之前就注册好。
        await self._send_running_reply(chat_id, inbound)
        await self.bus.publish_inbound(inbound)

    async def _send_running_reply(self, chat_id: str, inbound: InboundMessage) -> None:
        """发送“⏳ 正在处理”的占位回复。"""
        conversation_type = inbound.metadata.get("conversation_type", _CONVERSATION_TYPE_P2P)
        sender_staff_id = inbound.metadata.get("sender_staff_id", "")
        conversation_id = inbound.metadata.get("conversation_id", "")
        text = "\u23f3 Working on it..."

        try:
            if self._card_template_id:
                source_key = self._make_card_source_key(inbound)
                with self._incoming_messages_lock:
                    chatbot_message = self._incoming_messages.pop(source_key, None)
                out_track_id = await self._create_and_deliver_card(
                    text,
                    chatbot_message=chatbot_message,
                )
                if out_track_id:
                    self._card_track_ids[source_key] = out_track_id
                    logger.info("[DingTalk] AI card running reply sent for chat=%s", chat_id)
                    return

            robot_code = self._client_id
            if conversation_type == _CONVERSATION_TYPE_GROUP:
                await self._send_text_message_to_group(robot_code, conversation_id, text)
            else:
                await self._send_text_message_to_user(robot_code, sender_staff_id, text)
            logger.info("[DingTalk] 'Working on it...' reply sent for chat=%s", chat_id)
        except Exception:
            logger.exception("[DingTalk] failed to send running reply for chat=%s", chat_id)

    # -- 钉钉 API 辅助方法 ----------------------------------------------

    async def _get_access_token(self) -> str:
        """获取（必要时刷新）钉钉应用的 access token。"""
        if self._cached_token and time.monotonic() < self._token_expires_at:
            return self._cached_token
        async with self._token_lock:
            if self._cached_token and time.monotonic() < self._token_expires_at:
                return self._cached_token
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(
                    f"{DINGTALK_API_BASE}/v1.0/oauth2/accessToken",
                    json={"appKey": self._client_id, "appSecret": self._client_secret},  # 钉钉 API 字段名
                )
                response.raise_for_status()
                data = response.json()

                if not isinstance(data, dict):
                    raise ValueError(f"DingTalk access token response must be a JSON object, got {type(data).__name__}")

                access_token = data.get("accessToken")
                if not isinstance(access_token, str) or not access_token.strip():
                    raise ValueError("DingTalk access token response did not contain a usable accessToken")

                raw_expires_in = data.get("expireIn", 7200)
                try:
                    expires_in = int(raw_expires_in)
                except (TypeError, ValueError):
                    logger.warning("[DingTalk] invalid expireIn value %r, using default 7200s", raw_expires_in)
                    expires_in = 7200

                self._cached_token = access_token.strip()
                self._token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_MARGIN_SECONDS
                return self._cached_token

    @staticmethod
    def _api_headers(token: str) -> dict[str, str]:
        """构造钉钉 OpenAPI 通用请求头。"""
        return {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

    async def _send_text_message_to_user(self, robot_code: str, user_id: str, text: str) -> None:
        """通过 sampleText 发送纯文本给单聊用户。"""
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{DINGTALK_API_BASE}/v1.0/robot/oToMessages/batchSend",
                headers=self._api_headers(token),
                json={
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": text}),
                    "robotCode": robot_code,
                    "userIds": [user_id],
                },
            )
            response.raise_for_status()

    async def _send_text_message_to_group(self, robot_code: str, conversation_id: str, text: str) -> None:
        """通过 sampleText 发送纯文本到群会话。"""
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{DINGTALK_API_BASE}/v1.0/robot/groupMessages/send",
                headers=self._api_headers(token),
                json={
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": text}),
                    "robotCode": robot_code,
                    "openConversationId": conversation_id,
                },
            )
            response.raise_for_status()

    async def _send_p2p_message(self, robot_code: str, user_id: str, text: str) -> None:
        """向单聊用户发送 sampleMarkdown 消息。"""
        text = _adapt_markdown_for_dingtalk(text)
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{DINGTALK_API_BASE}/v1.0/robot/oToMessages/batchSend",
                headers=self._api_headers(token),
                json={
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"title": "DeerFlow", "text": text}),
                    "robotCode": robot_code,
                    "userIds": [user_id],
                },
            )
            response.raise_for_status()
            data = response.json()
            if data.get("processQueryKey"):
                logger.info("[DingTalk] P2P message sent to user=%s", user_id)
            else:
                logger.warning("[DingTalk] P2P send response: %s", data)

    async def _send_group_message(
        self,
        robot_code: str,
        conversation_id: str,
        text: str,
        *,
        at_user_ids: list[str] | None = None,  # noqa: ARG002
    ) -> None:
        """向群会话发送 sampleMarkdown 消息。

        注：``at_user_ids`` 为调用方兼容性而保留，并不会真正传给 API
        （sampleMarkdown 不支持 @ 提及）。
        """
        text = _adapt_markdown_for_dingtalk(text)
        token = await self._get_access_token()

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{DINGTALK_API_BASE}/v1.0/robot/groupMessages/send",
                headers=self._api_headers(token),
                json={
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({"title": "DeerFlow", "text": text}),
                    "robotCode": robot_code,
                    "openConversationId": conversation_id,
                },
            )
            response.raise_for_status()
            data = response.json()
            if data.get("processQueryKey"):
                logger.info("[DingTalk] group message sent to conversation=%s", conversation_id)
            else:
                logger.warning("[DingTalk] group send response: %s", data)

    # -- AI Card 流式辅助 ------------------------------------------------

    def _make_card_source_key(self, inbound: InboundMessage) -> str:
        """为入站消息构造 AI Card 流的源键。"""
        m = inbound.metadata
        return f"{m.get('conversation_type', '')}:{m.get('sender_staff_id', '')}:{m.get('conversation_id', '')}:{m.get('message_id', '')}"

    def _make_card_source_key_from_outbound(self, msg: OutboundMessage) -> str:
        """为出站消息构造 AI Card 流的源键。"""
        m = msg.metadata
        correlation_id = m.get("message_id") or msg.thread_ts or ""
        return f"{m.get('conversation_type', '')}:{m.get('sender_staff_id', '')}:{m.get('conversation_id', '')}:{correlation_id}"

    async def _create_and_deliver_card(
        self,
        initial_text: str,
        *,
        chatbot_message: Any = None,
    ) -> str | None:
        """创建并下发一张 AI Card，返回 ``outTrackId``，失败时返回 ``None``。"""
        if self._dingtalk_client is None or chatbot_message is None:
            logger.warning("[DingTalk] SDK client or chatbot_message unavailable, skipping AI card")
            return None

        try:
            from dingtalk_stream.card_replier import AICardReplier
        except ImportError:
            logger.warning("[DingTalk] dingtalk-stream card_replier not available")
            return None

        try:
            replier = AICardReplier(self._dingtalk_client, chatbot_message)
            card_instance_id = await replier.async_create_and_deliver_card(
                card_template_id=self._card_template_id,
                card_data={"content": initial_text},
            )
            if not card_instance_id:
                return None

            self._card_repliers[card_instance_id] = replier
            logger.info("[DingTalk] AI card created: outTrackId=%s", card_instance_id)
            return card_instance_id
        except Exception:
            logger.exception("[DingTalk] failed to create AI card")
            return None

    async def _stream_update_card(
        self,
        out_track_id: str,
        content: str,
        *,
        is_finalize: bool = False,
        is_error: bool = False,
    ) -> None:
        """向已存在的 AI Card 推送一段流式更新。"""
        replier = self._card_repliers.get(out_track_id)
        if not replier:
            raise RuntimeError(f"No AICardReplier found for track ID {out_track_id}")

        await replier.async_streaming(
            card_instance_id=out_track_id,
            content_key="content",
            content_value=content,
            append=False,
            finished=is_finalize,
            failed=is_error,
        )

    # -- 媒体上传 -------------------------------------------------------

    async def _upload_media(self, file_path: str | Path, media_type: str) -> str | None:
        """把本地文件上传到钉钉，返回 ``mediaId``。"""
        try:
            file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.post(
                    f"{DINGTALK_API_BASE}/v1.0/files/upload",
                    headers={"x-acs-dingtalk-access-token": token},
                    files={"file": ("upload", file_bytes)},
                    data={"type": media_type},
                )
                response.raise_for_status()
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    logger.exception("[DingTalk] failed to decode upload response JSON: %s", file_path)
                    return None
                if not isinstance(payload, dict):
                    logger.warning("[DingTalk] unexpected upload response type %s for %s", type(payload).__name__, file_path)
                    return None
                return payload.get("mediaId")
        except (httpx.HTTPError, OSError):
            logger.exception("[DingTalk] failed to upload media: %s", file_path)
            return None

    @staticmethod
    def _log_future_error(fut: Any, name: str, msg_id: str) -> None:
        """``run_coroutine_threadsafe`` future 的错误回调，统一打印。"""
        try:
            exc = fut.exception()
            if exc:
                logger.error("[DingTalk] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass


class _DingTalkMessageHandler:
    """注册到 ``dingtalk-stream`` 的回调处理器。"""

    def __init__(self, channel: DingTalkChannel) -> None:
        """保存渠道引用，供回调时反查外发能力。"""
        self._channel = channel

    def pre_start(self) -> None:
        """在 SDK 启动前向渠道回填 SDK 客户端引用，供 AI Card 创建使用。"""
        if hasattr(self, "dingtalk_client") and self.dingtalk_client is not None:
            self._channel._dingtalk_client = self.dingtalk_client

    async def raw_process(self, callback_message: Any) -> Any:
        """SDK 的原始入口，转发给 ``process`` 后封装成 ACK 消息。"""
        import dingtalk_stream
        from dingtalk_stream.frames import Headers

        code, message = await self.process(callback_message)
        ack_message = dingtalk_stream.AckMessage()
        ack_message.code = code
        ack_message.headers.message_id = callback_message.headers.message_id
        ack_message.headers.content_type = Headers.CONTENT_TYPE_APPLICATION_JSON
        ack_message.data = {"response": message}
        return ack_message

    async def process(self, callback: Any) -> tuple[int, str]:
        """把 SDK 回调解析为 ``ChatbotMessage`` 并投递给渠道。"""
        import dingtalk_stream

        incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        self._channel._on_chatbot_message(incoming_message)
        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
