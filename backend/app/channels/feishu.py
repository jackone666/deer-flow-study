"""飞书 / Lark 渠道 —— 通过 WebSocket 连接飞书（无需公网 IP）。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Literal

from app.channels.base import Channel
from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.message_bus import (
    PENDING_CLARIFICATION_METADATA_KEY,
    RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY,
    InboundMessage,
    InboundMessageType,
    MessageBus,
    OutboundMessage,
    ResolvedAttachment,
)
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)
PENDING_CLARIFICATION_TTL_SECONDS = 30 * 60


def _is_feishu_command(text: str) -> bool:
    """判断文本是否以已知斜杠命令开头。"""
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS


class FeishuChannel(Channel):
    """基于 ``lark-oapi`` WebSocket 客户端的飞书 / Lark IM 渠道。

    ``config.yaml`` 中 ``channels.feishu`` 下的配置键：
        - ``app_id``：飞书应用 ID。
        - ``app_secret``：飞书应用密钥。
        - ``verification_token``：（可选）事件验证 token。
        - ``domain``：（可选）飞书 API 域名。

    渠道使用 WebSocket 长连接模式，无需公网 IP。

    消息流：
        1. 用户发送消息 → 机器人加 "OK" 表情
        2. 机器人在主题中回复 "Working on it......"
        3. 智能体处理消息并返回结果
        4. 机器人在主题中回复结果
        5. 机器人给原消息加 "DONE" 表情
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        """初始化飞书渠道，缓存各种 lark-oapi 类型引用、running card 状态等。"""
        super().__init__(name="feishu", bus=bus, config=config)
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._PatchMessageRequest = None
        self._PatchMessageRequestBody = None
        self._background_tasks: set[asyncio.Task] = set()
        self._running_card_ids: dict[str, str] = {}
        self._running_card_tasks: dict[str, asyncio.Task] = {}
        self._pending_clarifications: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None
        self._GetMessageResourceRequest = None
        self._thread_lock = threading.Lock()

    @staticmethod
    def _non_empty_str(value: Any) -> str | None:
        """将输入裁剪为去除两端空白的字符串，空值返回 ``None``。"""
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _pending_key(chat_id: str, user_id: str) -> tuple[str, str]:
        """构造待处理澄清提示的内存索引键。"""
        return (chat_id, user_id)

    @property
    def supports_streaming(self) -> bool:
        """飞书渠道支持对 running card 做原地 patch，因此支持流式。"""
        return True

    async def start(self) -> None:
        """启动渠道：导入 lark-oapi 类型、构造 API 客户端、订阅总线、启动 WS 线程。"""
        if self._running:
            return

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
                Emoji,
                GetMessageResourceRequest,
                PatchMessageRequest,
                PatchMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install it with: uv add lark-oapi")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._Emoji = Emoji
        self._PatchMessageRequest = PatchMessageRequest
        self._PatchMessageRequestBody = PatchMessageRequestBody
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody
        self._GetMessageResourceRequest = GetMessageResourceRequest

        app_id = self.config.get("app_id", "")
        app_secret = self.config.get("app_secret", "")
        domain = self.config.get("domain", "https://open.feishu.cn")

        if not app_id or not app_secret:
            logger.error("Feishu channel requires app_id and app_secret")
            return

        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
        logger.info("[Feishu] using domain: %s", domain)
        self._main_loop = asyncio.get_event_loop()

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # ``ws.Client`` 的构造和 ``start()`` 必须在专用线程 + 独立事件循环中执行。
        # lark-oapi 在构造时会缓存当前事件循环（``lark_oapi.ws.client.loop``），
        # 之后内部会调用 ``loop.run_until_complete()``；当 uvicorn 使用 uvloop 时，
        # 缓存的是主线程已经在跑的 uvloop，再调 run_until_complete 会触发 RuntimeError。
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(app_id, app_secret, domain),
            daemon=True,
        )
        self._thread.start()
        logger.info("Feishu channel started")

    def _run_ws(self, app_id: str, app_secret: str, domain: str) -> None:
        """在线程内的全新事件循环中构造并运行 lark WS 客户端。

        lark-oapi 在 import 时会在模块级捕获事件循环（``lark_oapi.ws.client.loop``）。
        当 uvicorn 使用 uvloop 时，捕获到的是主线程的 uvloop——它已经在运行，
        导致 ``Client.start()`` 内部调用 ``loop.run_until_complete()`` 时抛 ``RuntimeError``。

        通过为该线程创建全新的 asyncio 事件循环并在调用 ``start()`` 前替换
        SDK 模块级引用的方式绕过该问题。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as _ws_client_mod

            # 替换 SDK 模块级 loop 引用，让 Client.start() 走本线程的（未运行的）事件循环，
            # 而非主线程的 uvloop。
            _ws_client_mod.loop = loop

            event_handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self._on_message).build()
            ws_client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
                domain=domain,
            )
            ws_client.start()
        except Exception:
            if self._running:
                logger.exception("Feishu WebSocket error")

    async def stop(self) -> None:
        """停止渠道：解绑订阅、取消所有后台任务、等待线程结束。"""
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        for task in list(self._running_card_tasks.values()):
            task.cancel()
        self._running_card_tasks.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        """发送一条出站消息；优先 patch running card，否则创建新 card，带指数退避重试。

        Args:
            msg: 待发送的出站消息。
            _max_retries: 失败时的最大重试次数。
        """
        if not self._api_client:
            logger.warning("[Feishu] send called but no api_client available")
            return

        logger.info(
            "[Feishu] sending reply: chat_id=%s, thread_ts=%s, text_len=%d",
            msg.chat_id,
            msg.thread_ts,
            len(msg.text),
        )

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await self._send_card_message(msg)
                return  # 成功
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "[Feishu] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[Feishu] send failed after %d attempts: %s", _max_retries, last_exc)
        if last_exc is None:
            raise RuntimeError("Feishu send failed without an exception from any attempt")
        raise last_exc

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """上传并发送文件附件到飞书。"""
        if not self._api_client:
            return False

        # 检查大小限制（图片 10MB，文件 30MB）
        if attachment.is_image and attachment.size > 10 * 1024 * 1024:
            logger.warning("[Feishu] image too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False
        if not attachment.is_image and attachment.size > 30 * 1024 * 1024:
            logger.warning("[Feishu] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        try:
            if attachment.is_image:
                file_key = await self._upload_image(attachment.actual_path)
                msg_type = "image"
                content = json.dumps({"image_key": file_key})
            else:
                file_key = await self._upload_file(attachment.actual_path, attachment.filename)
                msg_type = "file"
                content = json.dumps({"file_key": file_key})

            if msg.thread_ts:
                request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).reply_in_thread(True).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            else:
                request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.create, request)

            logger.info("[Feishu] file sent: %s (type=%s)", attachment.filename, msg_type)
            return True
        except Exception:
            logger.exception("[Feishu] failed to upload/send file: %s", attachment.filename)
            return False

    async def _upload_image(self, path) -> str:
        """把图片上传到飞书并返回 ``image_key``。"""
        with open(str(path), "rb") as f:
            request = self._CreateImageRequest.builder().request_body(self._CreateImageRequestBody.builder().image_type("message").image(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.image.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return response.data.image_key

    async def _upload_file(self, path, filename: str) -> str:
        """把文件上传到飞书并返回 ``file_key``。"""
        suffix = path.suffix.lower() if hasattr(path, "suffix") else ""
        if suffix in (".xls", ".xlsx", ".csv"):
            file_type = "xls"
        elif suffix in (".ppt", ".pptx"):
            file_type = "ppt"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".doc", ".docx"):
            file_type = "doc"
        else:
            file_type = "stream"

        with open(str(path), "rb") as f:
            request = self._CreateFileRequest.builder().request_body(self._CreateFileRequestBody.builder().file_type(file_type).file_name(filename).file(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.file.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        return response.data.file_key

    async def receive_file(self, msg: InboundMessage, thread_id: str) -> InboundMessage:
        """把飞书入站文件下载到 DeerFlow 主题的 uploads 目录中。

        成功时返回替换为沙盒虚拟路径的消息；失败时返回原消息，并把
        ``[image]``/``[file]`` 替换为 ``Failed to obtain the [...]``。
        """
        if not msg.thread_ts:
            logger.warning("[Feishu] received file message without thread_ts, cannot associate with conversation: %s", msg)
            return msg
        files = msg.files
        if not files:
            logger.warning("[Feishu] received message with no files: %s", msg)
            return msg
        text = msg.text
        for file in files:
            if file.get("image_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["image_key"], "image", thread_id)
                text = text.replace("[image]", virtual_path, 1)
            elif file.get("file_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["file_key"], "file", thread_id)
                text = text.replace("[file]", virtual_path, 1)
        msg.text = text
        return msg

    async def _receive_single_file(self, message_id: str, file_key: str, type: Literal["image", "file"], thread_id: str) -> str:
        """下载单个飞书资源并落盘到沙盒 uploads 目录，返回虚拟路径。"""
        request = self._GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(type).build()

        def inner():
            """线程内调用的同步包装：实际执行 ``message_resource.get`` 请求。"""
            return self._api_client.im.v1.message_resource.get(request)

        try:
            response = await asyncio.to_thread(inner)
        except Exception:
            logger.exception("[Feishu] resource get request failed for resource_key=%s type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not response.success():
            logger.warning(
                "[Feishu] resource get failed: resource_key=%s, type=%s, code=%s, msg=%s, log_id=%s ",
                file_key,
                type,
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return f"Failed to obtain the [{type}]"

        image_stream = getattr(response, "file", None)
        if image_stream is None:
            logger.warning("[Feishu] resource get returned no file stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        try:
            content: bytes = await asyncio.to_thread(image_stream.read)
        except Exception:
            logger.exception("[Feishu] failed to read resource stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not content:
            logger.warning("[Feishu] empty resource content: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)
        uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=user_id).resolve()

        ext = "png" if type == "image" else "bin"
        raw_filename = getattr(response, "file_name", "") or f"feishu_{file_key[-12:]}.{ext}"

        # 文件名清洗：保留扩展名，将名字部分的路径字符替换成下划线
        if "." in raw_filename:
            name_part, ext = raw_filename.rsplit(".", 1)
            name_part = re.sub(r"[./\\]", "_", name_part)
            filename = f"{name_part}.{ext}"
        else:
            filename = re.sub(r"[./\\]", "_", raw_filename)
        resolved_target = uploads_dir / filename

        def down_load():
            """线程内调用的同步包装：把下载内容写入目标文件。"""
            # 使用 thread_lock 避免文件名冲突
            with self._thread_lock:
                resolved_target.write_bytes(content)

        try:
            await asyncio.to_thread(down_load)
        except Exception:
            logger.exception("[Feishu] failed to persist downloaded resource: %s, type=%s", resolved_target, type)
            return f"Failed to obtain the [{type}]"

        virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}"

        try:
            sandbox_provider = get_sandbox_provider()
            sandbox_id = sandbox_provider.acquire(thread_id)
            if sandbox_id != "local":
                sandbox = sandbox_provider.get(sandbox_id)
                if sandbox is None:
                    logger.warning("[Feishu] sandbox not found for thread_id=%s", thread_id)
                    return f"Failed to obtain the [{type}]"
                sandbox.update_file(virtual_path, content)
        except Exception:
            logger.exception("[Feishu] failed to sync resource into non-local sandbox: %s", virtual_path)
            return f"Failed to obtain the [{type}]"

        logger.info("[Feishu] downloaded resource mapped: file_key=%s -> %s", file_key, virtual_path)
        return virtual_path

    # -- 消息格式化 ------------------------------------------------------

    @staticmethod
    def _build_card_content(text: str) -> str:
        """构造飞书可渲染 Markdown 的交互式卡片 JSON。"""
        # 飞书交互式卡片天然支持 Markdown 渲染：标题、粗斜体、代码块、列表、链接等。
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "elements": [{"tag": "markdown", "content": text}],
        }
        return json.dumps(card)

    # -- 表情反应辅助 ----------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """给消息加 emoji 表情。"""
        if not self._api_client or not self._CreateMessageReactionRequest:
            return
        try:
            request = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, request)
            logger.info("[Feishu] reaction '%s' added to message %s", emoji_type, message_id)
        except Exception:
            logger.exception("[Feishu] failed to add reaction '%s' to message %s", emoji_type, message_id)

    async def _reply_card(self, message_id: str, text: str) -> str | None:
        """以交互式卡片形式回复消息并返回新消息 ID。"""
        if not self._api_client:
            return None

        content = self._build_card_content(text)
        request = self._ReplyMessageRequest.builder().message_id(message_id).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).reply_in_thread(True).build()).build()
        response = await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
        response_data = getattr(response, "data", None)
        return getattr(response_data, "message_id", None)

    async def _create_card(self, chat_id: str, text: str) -> None:
        """在目标 chat 中创建一张新的交互式卡片。"""
        if not self._api_client:
            return

        content = self._build_card_content(text)
        request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.create, request)

    async def _update_card(self, message_id: str, text: str) -> None:
        """原地更新一张已存在的交互式卡片。"""
        if not self._api_client or not self._PatchMessageRequest:
            return

        content = self._build_card_content(text)
        request = self._PatchMessageRequest.builder().message_id(message_id).request_body(self._PatchMessageRequestBody.builder().content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.patch, request)

    def _track_background_task(self, task: asyncio.Task, *, name: str, msg_id: str) -> None:
        """保留对 fire-and-forget 后台任务的强引用，并在出错时打印日志。"""
        self._background_tasks.add(task)
        task.add_done_callback(lambda done_task, task_name=name, mid=msg_id: self._finalize_background_task(done_task, task_name, mid))

    def _finalize_background_task(self, task: asyncio.Task, name: str, msg_id: str) -> None:
        """后台任务结束时的清理回调。"""
        self._background_tasks.discard(task)
        self._log_task_error(task, name, msg_id)

    async def _create_running_card(self, source_message_id: str, text: str) -> str | None:
        """创建 running card 并缓存其消息 ID。"""
        running_card_id = await self._reply_card(source_message_id, text)
        if running_card_id:
            self._running_card_ids[source_message_id] = running_card_id
            logger.info("[Feishu] running card created: source=%s card=%s", source_message_id, running_card_id)
        else:
            logger.warning("[Feishu] running card creation returned no message_id for source=%s, subsequent updates will fall back to new replies", source_message_id)
        return running_card_id

    def _ensure_running_card_started(self, source_message_id: str, text: str = "Working on it...") -> asyncio.Task | None:
        """对每个源消息仅启动一次 running card 创建。"""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return None

        running_card_task = self._running_card_tasks.get(source_message_id)
        if running_card_task:
            return running_card_task

        running_card_task = asyncio.create_task(self._create_running_card(source_message_id, text))
        self._running_card_tasks[source_message_id] = running_card_task
        running_card_task.add_done_callback(lambda done_task, mid=source_message_id: self._finalize_running_card_task(mid, done_task))
        return running_card_task

    def _finalize_running_card_task(self, source_message_id: str, task: asyncio.Task) -> None:
        """running card 创建任务结束时的清理。"""
        if self._running_card_tasks.get(source_message_id) is task:
            self._running_card_tasks.pop(source_message_id, None)
        self._log_task_error(task, "create_running_card", source_message_id)

    async def _ensure_running_card(self, source_message_id: str, text: str = "Working on it...") -> str | None:
        """确保主题内的 running card 已存在并跟踪其消息 ID。"""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return running_card_id

        running_card_task = self._ensure_running_card_started(source_message_id, text)
        if running_card_task is None:
            return self._running_card_ids.get(source_message_id)
        return await running_card_task

    async def _send_running_reply(self, message_id: str) -> None:
        """在主题内回复一张 running card。"""
        try:
            await self._ensure_running_card(message_id)
        except Exception:
            logger.exception("[Feishu] failed to send running reply for message %s", message_id)

    async def _send_card_message(self, msg: OutboundMessage) -> None:
        """发送或更新与当前请求绑定的飞书卡片。"""
        source_message_id = msg.thread_ts
        if source_message_id:
            running_card_id = self._running_card_ids.get(source_message_id)
            awaited_running_card_task = False

            if not running_card_id:
                running_card_task = self._running_card_tasks.get(source_message_id)
                if running_card_task:
                    awaited_running_card_task = True
                    running_card_id = await running_card_task

            if running_card_id:
                try:
                    await self._update_card(running_card_id, msg.text)
                except Exception:
                    if not msg.is_final:
                        raise
                    logger.exception(
                        "[Feishu] failed to patch running card %s, falling back to final reply",
                        running_card_id,
                    )
                    fallback_card_id = await self._reply_card(source_message_id, msg.text)
                    self._remember_thread_mapping(msg, source_message_id, fallback_card_id)
                    self._remember_pending_clarification(msg, fallback_card_id)
                else:
                    self._remember_thread_mapping(msg, source_message_id, running_card_id)
                    self._remember_pending_clarification(msg, running_card_id)
                    logger.info("[Feishu] running card updated: source=%s card=%s", source_message_id, running_card_id)
            elif msg.is_final:
                final_card_id = await self._reply_card(source_message_id, msg.text)
                self._remember_thread_mapping(msg, source_message_id, final_card_id)
                self._remember_pending_clarification(msg, final_card_id)
            elif awaited_running_card_task:
                logger.warning(
                    "[Feishu] running card task finished without message_id for source=%s, skipping duplicate non-final creation",
                    source_message_id,
                )
            else:
                created_card_id = await self._ensure_running_card(source_message_id, msg.text)
                self._remember_thread_mapping(msg, source_message_id, created_card_id)

            if msg.is_final:
                self._running_card_ids.pop(source_message_id, None)
                await self._add_reaction(source_message_id, "DONE")
            return

        await self._create_card(msg.chat_id, msg.text)

    # -- 内部 ----------------------------------------------------------

    def _remember_thread_mapping(self, msg: OutboundMessage, *topic_ids: str | None) -> None:
        """为出站消息的多个候选 topic_id 写入 channel store，便于后续消息查回主题。"""
        store = self.config.get("channel_store")
        if store is None or not msg.thread_id:
            return

        metadata_topic_ids = [
            msg.metadata.get("message_id"),
            msg.metadata.get("root_id"),
            msg.metadata.get("parent_id"),
            msg.metadata.get("thread_id"),
            msg.metadata.get("topic_id"),
        ]
        user_id = ""
        raw_user_id = msg.metadata.get("user_id")
        if isinstance(raw_user_id, str):
            user_id = raw_user_id

        seen: set[str] = set()
        for topic_id in [*topic_ids, *metadata_topic_ids]:
            topic_id = self._non_empty_str(topic_id)
            if not topic_id or topic_id in seen:
                continue
            seen.add(topic_id)
            try:
                store.set_thread_id(
                    self.name,
                    msg.chat_id,
                    msg.thread_id,
                    topic_id=topic_id,
                    user_id=user_id,
                )
            except Exception:
                logger.exception("[Feishu] failed to remember thread mapping for topic_id=%s", topic_id)

    def _remember_pending_clarification(self, msg: OutboundMessage, card_message_id: str | None) -> None:
        """把一次最终消息里的澄清提示记入短期内存，便于后续纯文本续接。"""
        if not msg.is_final or msg.metadata.get(PENDING_CLARIFICATION_METADATA_KEY) is not True:
            return

        user_id = self._non_empty_str(msg.metadata.get("user_id"))
        topic_id = self._non_empty_str(msg.metadata.get("topic_id"))
        source_message_id = self._non_empty_str(msg.thread_ts) or self._non_empty_str(msg.metadata.get("message_id"))
        if not (user_id and topic_id and msg.thread_id and source_message_id and card_message_id):
            return

        key = self._pending_key(msg.chat_id, user_id)
        pending = {
            "thread_id": msg.thread_id,
            "topic_id": topic_id,
            "source_message_id": source_message_id,
            "card_message_id": card_message_id,
            "created_at": time.time(),
        }
        with self._thread_lock:
            # 普通消息的澄清续接是一个短期的内存提示；
            # 显式的飞书回复仍由持久化的 message-id 映射覆盖。
            self._pending_clarifications.setdefault(key, []).append(pending)
        logger.info(
            "[Feishu] pending clarification remembered: chat_id=%s user_id=%s topic_id=%s thread_id=%s",
            msg.chat_id,
            user_id,
            topic_id,
            msg.thread_id,
        )

    def _consume_pending_clarification(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        """取出一条待处理的澄清提示，过期项会被丢弃。"""
        key = self._pending_key(chat_id, user_id)
        with self._thread_lock:
            pending_items = self._pending_clarifications.get(key)
            if not pending_items:
                return None

            now = time.time()
            while pending_items:
                pending = pending_items.pop(0)
                created_at = pending.get("created_at")
                if isinstance(created_at, (int, float)) and now - created_at <= PENDING_CLARIFICATION_TTL_SECONDS:
                    if pending_items:
                        self._pending_clarifications[key] = pending_items
                    else:
                        self._pending_clarifications.pop(key, None)
                    return pending
                logger.info("[Feishu] pending clarification expired: chat_id=%s user_id=%s", chat_id, user_id)

            self._pending_clarifications.pop(key, None)
            return None

    def _ensure_pending_thread_mapping(self, chat_id: str, user_id: str, pending: dict[str, Any]) -> None:
        """把消费过的澄清提示对应的 topic_id 同步写回 store。"""
        store = self.config.get("channel_store")
        topic_id = self._non_empty_str(pending.get("topic_id"))
        thread_id = self._non_empty_str(pending.get("thread_id"))
        if store is None or not topic_id or not thread_id:
            return
        try:
            store.set_thread_id(self.name, chat_id, thread_id, topic_id=topic_id, user_id=user_id)
        except Exception:
            logger.exception("[Feishu] failed to restore pending clarification mapping for topic_id=%s", topic_id)

    def _resolve_topic_id(
        self,
        chat_id: str,
        msg_id: str,
        *,
        root_id: str | None,
        parent_id: str | None,
        thread_id: str | None,
    ) -> tuple[str, bool]:
        """解析应使用的 topic_id：优先使用已经映射到 DeerFlow 主题的候选。

        Returns:
            tuple[str, bool]: 选中的 topic_id 和是否来自已存储映射的标志。
        """
        store = self.config.get("channel_store")
        candidates = [root_id, parent_id, thread_id]

        if store is not None:
            for candidate in candidates:
                candidate = self._non_empty_str(candidate)
                if not candidate:
                    continue
                try:
                    if store.get_thread_id(self.name, chat_id, topic_id=candidate):
                        return candidate, True
                except Exception:
                    logger.exception("[Feishu] failed to resolve stored topic mapping for topic_id=%s", candidate)

        return root_id or msg_id, False

    @staticmethod
    def _log_future_error(fut, name: str, msg_id: str) -> None:
        """``run_coroutine_threadsafe`` future 的错误回调。"""
        try:
            exc = fut.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except Exception:
            pass

    @staticmethod
    def _log_task_error(task: asyncio.Task, name: str, msg_id: str) -> None:
        """后台 ``asyncio.Task`` 的错误回调。"""
        try:
            exc = task.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except asyncio.CancelledError:
            logger.info("[Feishu] %s cancelled for msg_id=%s", name, msg_id)
        except Exception:
            pass

    async def _prepare_inbound(self, msg_id: str, inbound) -> None:
        """在发布入站消息前异步触发飞书侧的副作用（加表情、起 running card）。"""
        reaction_task = asyncio.create_task(self._add_reaction(msg_id, "OK"))
        self._track_background_task(reaction_task, name="add_reaction", msg_id=msg_id)
        self._ensure_running_card_started(msg_id)
        await self.bus.publish_inbound(inbound)

    def _on_message(self, event) -> None:
        """lark-oapi 收到消息事件时回调（运行在 lark 线程中）。"""
        try:
            logger.info("[Feishu] raw event received: type=%s", type(event).__name__)
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            sender_id = event.event.sender.sender_id.open_id

            # root_id 在消息是飞书主题内回复时存在。
            # 用它作为 topic_id，确保所有回复共享同一个 DeerFlow 主题。
            root_id = self._non_empty_str(getattr(message, "root_id", None))
            parent_id = self._non_empty_str(getattr(message, "parent_id", None))
            feishu_thread_id = self._non_empty_str(getattr(message, "thread_id", None))

            # 解析消息内容
            content = json.loads(message.content)

            # files_list 存放飞书消息中的文件 key，后续可用于下载
            # 飞书渠道中 image_key 与 file_key 独立。
            # file_key 包含文件、视频和音频，但不包括表情包。
            files_list = []

            if "text" in content:
                # 处理纯文本消息
                text = content["text"]
            elif "file_key" in content:
                file_key = content.get("file_key")
                if isinstance(file_key, str) and file_key:
                    files_list.append({"file_key": file_key})
                    text = "[file]"
                else:
                    text = ""
            elif "image_key" in content:
                image_key = content.get("image_key")
                if isinstance(image_key, str) and image_key:
                    files_list.append({"image_key": image_key})
                    text = "[image]"
                else:
                    text = ""
            elif "content" in content and isinstance(content["content"], list):
                # 处理顶层 "content" 为列表的富文本消息（如话题组/帖子）
                text_paragraphs: list[str] = []
                for paragraph in content["content"]:
                    if isinstance(paragraph, list):
                        paragraph_text_parts: list[str] = []
                        for element in paragraph:
                            if isinstance(element, dict):
                                # 同时包含普通文本和 @ 提及
                                if element.get("tag") in ("text", "at"):
                                    text_value = element.get("text", "")
                                    if text_value:
                                        paragraph_text_parts.append(text_value)
                                elif element.get("tag") == "img":
                                    image_key = element.get("image_key")
                                    if isinstance(image_key, str) and image_key:
                                        files_list.append({"image_key": image_key})
                                        paragraph_text_parts.append("[image]")
                                elif element.get("tag") in ("file", "media"):
                                    file_key = element.get("file_key")
                                    if isinstance(file_key, str) and file_key:
                                        files_list.append({"file_key": file_key})
                                        paragraph_text_parts.append("[file]")
                        if paragraph_text_parts:
                            # 段落内文本片段用空格连接，避免出现 "helloworld"
                            text_paragraphs.append(" ".join(paragraph_text_parts))

                # 段落之间用空行连接，保留段落边界
                text = "\n\n".join(text_paragraphs)
            else:
                text = ""
            text = text.strip()

            logger.info(
                "[Feishu] parsed message: chat_id=%s, msg_id=%s, root_id=%s, parent_id=%s, thread_id=%s, sender=%s, text=%r",
                chat_id,
                msg_id,
                root_id,
                parent_id,
                feishu_thread_id,
                sender_id,
                text[:100] if text else "",
            )

            if not (text or files_list):
                logger.info("[Feishu] empty text, ignoring message")
                return

            # 仅把已知的斜杠命令识别为命令；绝对路径以及其他斜杠开头的文本按普通聊天处理。
            if _is_feishu_command(text):
                msg_type = InboundMessageType.COMMAND
            else:
                msg_type = InboundMessageType.CHAT

            # 优先使用已经映射到 DeerFlow 主题的任意平台消息 ID。
            # 即便飞书把机器人的澄清卡报告为 root，也能让用户对其的回复落在原会话中。
            topic_id, resolved_from_stored_mapping = self._resolve_topic_id(
                chat_id,
                msg_id,
                root_id=root_id,
                parent_id=parent_id,
                thread_id=feishu_thread_id,
            )
            resolved_from_pending = False
            if msg_type == InboundMessageType.CHAT and not resolved_from_stored_mapping:
                pending = self._consume_pending_clarification(chat_id, sender_id)
                pending_topic_id = self._non_empty_str(pending.get("topic_id")) if pending else None
                if pending_topic_id:
                    topic_id = pending_topic_id
                    self._ensure_pending_thread_mapping(chat_id, sender_id, pending)
                    resolved_from_pending = True

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                files=files_list,
                metadata={
                    "message_id": msg_id,
                    "root_id": root_id,
                    "parent_id": parent_id,
                    "thread_id": feishu_thread_id,
                    "topic_id": topic_id,
                    "user_id": sender_id,
                    RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY: resolved_from_pending,
                },
            )
            inbound.topic_id = topic_id

            # 调度到异步事件循环
            if self._main_loop and self._main_loop.is_running():
                logger.info("[Feishu] publishing inbound message to bus (type=%s, msg_id=%s)", msg_type.value, msg_id)
                fut = asyncio.run_coroutine_threadsafe(self._prepare_inbound(msg_id, inbound), self._main_loop)
                fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "prepare_inbound", mid))
            else:
                logger.warning("[Feishu] main loop not running, cannot publish inbound message")
        except Exception:
            logger.exception("[Feishu] error processing message")
