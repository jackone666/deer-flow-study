"""基于 ``discord.py`` 的 Discord 渠道集成。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.channels.base import Channel
from app.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)

_DISCORD_MAX_MESSAGE_LEN = 2000


class DiscordChannel(Channel):
    """Discord 机器人渠道。

    ``config.yaml`` 中 ``channels.discord`` 下的配置键：
        - ``bot_token``：Discord Bot Token。
        - ``allowed_guilds``：（可选）允许的 Discord Guild ID 列表。空 = 全部允许。
        - ``mention_only``：（可选）若为真，仅在消息 @ 机器人时响应。
        - ``allowed_channels``：（可选）始终接受消息的频道 ID 列表
          （即使在 ``mention_only`` 模式下也接受）。空 = 全部按 ``mention_only`` 规则处理。
        - ``thread_mode``：（可选）若为真，把频道会话汇总到一个 Thread。
          默认与 ``mention_only`` 相同。
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        """初始化 Discord 渠道，从配置中读取 token、允许的 guild、线程模式等。"""
        super().__init__(name="discord", bus=bus, config=config)
        self._bot_token = str(config.get("bot_token", "")).strip()
        # 解析允许的 guild ID 列表
        self._allowed_guilds: set[int] = set()
        for guild_id in config.get("allowed_guilds", []):
            try:
                self._allowed_guilds.add(int(guild_id))
            except (TypeError, ValueError):
                continue
        self._mention_only: bool = bool(config.get("mention_only", False))
        # thread_mode 默认与 mention_only 一致：仅在 @机器人时创建独立线程
        self._thread_mode: bool = config.get("thread_mode", self._mention_only)
        # 始终接受的频道白名单（即使在 mention_only 模式下）
        self._allowed_channels: set[str] = set()
        for channel_id in config.get("allowed_channels", []):
            self._allowed_channels.add(str(channel_id))

        # 会话跟踪：channel_id -> Discord thread_id（内存中，并持久化到 JSON）。
        # 使用独立的 JSON 文件，与 ChannelStore 区分（后者映射 IM 会话到 DeerFlow 主题 ID）。
        self._active_threads: dict[str, str] = {}
        # 反向索引集合，用于 O(1) 校验 thread ID（避免对 _active_threads.values() 做 O(n) 扫描）。
        self._active_thread_ids: set[str] = set()
        # 保护 _active_threads 和 JSON 文件的锁。
        # _run_client（Discord 事件循环线程）和主线程都会读写。
        self._thread_store_lock = threading.Lock()
        store = config.get("channel_store")
        if store is not None:
            self._thread_store_path = store._path.parent / "discord_threads.json"
        else:
            self._thread_store_path = Path.home() / ".deer-flow" / "channels" / "discord_threads.json"

        # 正在打字（typing）状态管理
        self._typing_tasks: dict[str, asyncio.Task] = {}

        # Discord 客户端在独立线程中运行（discord.py 使用自己的事件循环）
        self._client = None
        self._thread: threading.Thread | None = None
        self._discord_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._discord_module = None

    async def start(self) -> None:
        """启动渠道：构造 Discord 客户端、注册事件、加载历史 thread 映射、跑事件循环线程。"""
        if self._running:
            return

        try:
            import discord
        except ImportError:
            logger.error("discord.py is not installed. Install it with: uv add discord.py")
            return

        if not self._bot_token:
            logger.error("Discord channel requires bot_token")
            return

        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True

        client = discord.Client(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._client = client
        self._discord_module = discord
        self._main_loop = asyncio.get_event_loop()

        @client.event
        async def on_message(message) -> None:
            """Discord ``on_message`` 事件桥接：转交给 ``_on_message`` 处理。"""
            await self._on_message(message)

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        self._thread = threading.Thread(target=self._run_client, daemon=True)
        self._thread.start()
        self._load_active_threads()
        logger.info("Discord channel started")

    def _load_active_threads(self) -> None:
        """在启动时从独立 JSON 文件恢复 Discord thread 映射。"""
        with self._thread_store_lock:
            try:
                if not self._thread_store_path.exists():
                    logger.debug("[Discord] no thread mappings file at %s", self._thread_store_path)
                    return
                data = json.loads(self._thread_store_path.read_text())
                self._active_threads.clear()
                self._active_thread_ids.clear()
                for channel_id, thread_id in data.items():
                    self._active_threads[channel_id] = thread_id
                    self._active_thread_ids.add(thread_id)
                if self._active_threads:
                    logger.info("[Discord] restored %d thread mappings from %s", len(self._active_threads), self._thread_store_path)
            except Exception:
                logger.exception("[Discord] failed to load thread mappings")

    def _save_thread(self, channel_id: str, thread_id: str) -> None:
        """把一条 Discord thread 映射持久化到独立 JSON 文件。"""
        with self._thread_store_lock:
            try:
                data: dict[str, str] = {}
                if self._thread_store_path.exists():
                    data = json.loads(self._thread_store_path.read_text())
                old_id = data.get(channel_id)
                data[channel_id] = thread_id
                # 更新反向索引
                if old_id:
                    self._active_thread_ids.discard(old_id)
                self._active_thread_ids.add(thread_id)
                self._thread_store_path.parent.mkdir(parents=True, exist_ok=True)
                self._thread_store_path.write_text(json.dumps(data, indent=2))
            except Exception:
                logger.exception("[Discord] failed to save thread mapping for channel %s", channel_id)

    async def stop(self) -> None:
        """停止渠道：取消 typing 任务、关闭客户端、清理状态。"""
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)

        # 取消所有正在进行的 typing 任务
        for target_id, task in list(self._typing_tasks.items()):
            if not task.done():
                task.cancel()
            logger.debug("[Discord] cancelled typing task for target %s", target_id)
        self._typing_tasks.clear()

        if self._client and self._discord_loop and self._discord_loop.is_running():
            close_future = asyncio.run_coroutine_threadsafe(self._client.close(), self._discord_loop)
            try:
                await asyncio.wait_for(asyncio.wrap_future(close_future), timeout=10)
            except TimeoutError:
                logger.warning("[Discord] client close timed out after 10s")
            except Exception:
                logger.exception("[Discord] error while closing client")

        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

        self._client = None
        self._discord_loop = None
        self._discord_module = None
        logger.info("Discord channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """把出站文本消息按 Discord 长度限制切片并发送。"""
        # 一旦开始发送回复就停止 typing 指示
        stop_future = asyncio.run_coroutine_threadsafe(self._stop_typing(msg.chat_id, msg.thread_ts), self._discord_loop)
        await asyncio.wrap_future(stop_future)

        target = await self._resolve_target(msg)
        if target is None:
            logger.error("[Discord] target not found for chat_id=%s thread_ts=%s", msg.chat_id, msg.thread_ts)
            return

        text = msg.text or ""
        for chunk in self._split_text(text):
            send_future = asyncio.run_coroutine_threadsafe(target.send(chunk), self._discord_loop)
            await asyncio.wrap_future(send_future)

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """把单个文件附件上传到对应 channel/thread。"""
        stop_future = asyncio.run_coroutine_threadsafe(self._stop_typing(msg.chat_id, msg.thread_ts), self._discord_loop)
        await asyncio.wrap_future(stop_future)

        target = await self._resolve_target(msg)
        if target is None:
            logger.error("[Discord] target not found for file upload chat_id=%s thread_ts=%s", msg.chat_id, msg.thread_ts)
            return False

        if self._discord_module is None:
            return False

        try:
            fp = open(str(attachment.actual_path), "rb")  # noqa: SIM115
            file = self._discord_module.File(fp, filename=attachment.filename)
            send_future = asyncio.run_coroutine_threadsafe(target.send(file=file), self._discord_loop)
            await asyncio.wrap_future(send_future)
            logger.info("[Discord] file uploaded: %s", attachment.filename)
            return True
        except Exception:
            logger.exception("[Discord] failed to upload file: %s", attachment.filename)
            return False

    async def _start_typing(self, channel, chat_id: str, thread_ts: str | None = None) -> None:
        """启动一个循环，定期发送 typing 指示。"""
        target_id = thread_ts or chat_id
        if target_id in self._typing_tasks:
            return  # 已经在该目标上 typing

        async def _typing_loop():
            """周期性触发 Discord typing 指示的后台协程。"""
            try:
                while True:
                    try:
                        await channel.trigger_typing()
                    except Exception:
                        pass
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_typing_loop())
        self._typing_tasks[target_id] = task

    async def _stop_typing(self, chat_id: str, thread_ts: str | None = None) -> None:
        """停止指定目标的 typing 循环。"""
        target_id = thread_ts or chat_id
        task = self._typing_tasks.pop(target_id, None)
        if task and not task.done():
            task.cancel()
            logger.debug("[Discord] stopped typing indicator for target %s", target_id)

    async def _add_reaction(self, message) -> None:
        """给原消息加上 ✅ 表情，提示已收到。"""
        try:
            await message.add_reaction("✅")
        except Exception:
            logger.debug("[Discord] failed to add reaction to message %s", message.id, exc_info=True)

    async def _on_message(self, message) -> None:
        """处理 Discord 消息事件：路由到 thread 并发布到消息总线。"""
        if not self._running or not self._client:
            return

        if message.author.bot:
            return

        if self._client.user and message.author.id == self._client.user.id:
            return

        guild = message.guild
        if self._allowed_guilds:
            if guild is None or guild.id not in self._allowed_guilds:
                return

        text = (message.content or "").strip()
        if not text:
            return

        if self._discord_module is None:
            return

        # 判断消息是否 @ 了机器人
        user = self._client.user if self._client else None
        if user:
            bot_mention = user.mention  # <@ID>
            alt_mention = f"<@!{user.id}>"  # <@!ID>（ping 变体）
            standard_mention = f"<@{user.id}>"
        else:
            bot_mention = None
            alt_mention = None
            standard_mention = ""
        has_mention = (bot_mention and bot_mention in message.content) or (alt_mention and alt_mention in message.content) or (standard_mention and standard_mention in message.content)

        # 去掉文本中的 @ 机器人标记
        if has_mention:
            text = text.replace(bot_mention or "", "").replace(alt_mention or "", "").replace(standard_mention or "", "").strip()
            # 即便去掉后为空也继续（例如：仅 @ 也需要创建 thread）

        # --- 决定 thread/channel 路由以及 typing 目标 ---
        thread_id = None
        chat_id = None
        typing_target = None  # Discord 对象，要对其发送 typing

        if isinstance(message.channel, self._discord_module.Thread):
            # --- 消息已经位于某个 thread 中 ---
            thread_obj = message.channel
            thread_id = str(thread_obj.id)
            chat_id = str(thread_obj.parent_id or thread_obj.id)
            typing_target = thread_obj

            # 若该 thread 是已知活动 thread，则按续接处理
            if thread_id in self._active_thread_ids:
                msg_type = InboundMessageType.COMMAND if text.startswith("/") else InboundMessageType.CHAT
                inbound = self._make_inbound(
                    chat_id=chat_id,
                    user_id=str(message.author.id),
                    text=text,
                    msg_type=msg_type,
                    thread_ts=thread_id,
                    metadata={
                        "guild_id": str(guild.id) if guild else None,
                        "channel_id": str(message.channel.id),
                        "message_id": str(message.id),
                    },
                )
                inbound.topic_id = thread_id
                self._publish(inbound)
                # 在 thread 内启动 typing 指示
                if typing_target:
                    asyncio.create_task(self._start_typing(typing_target, chat_id, thread_id))
                asyncio.create_task(self._add_reaction(message))
                return

            # 该 thread 不在跟踪集合（孤立）—— 下文会创建新 thread
            logger.debug("[Discord] message in orphaned thread %s, will create new thread", thread_id)
            thread_id = None
            typing_target = None

        # 此处一定位于 channel 中而非 thread（Thread 分支已处理）。
        # 对所有非 thread 消息应用 mention_only，无需特殊处理。
        channel_id = str(message.channel.id)

        # 检查该 channel 是否已有活动 thread
        if channel_id in self._active_threads:
            # 遵守 mention_only：仅在 @ 机器人的消息才处理
            # （除非 channel 处于 allowed_channels）
            # thread 内的消息总是被接受（续接对话）。
            # 此处已知消息位于 channel 中而非 thread（Thread 分支已处理），
            # 因此总是应用该检查。
            if self._mention_only and not has_mention and channel_id not in self._allowed_channels:
                logger.debug("[Discord] skipping no-@ message in channel %s (not in thread)", channel_id)
                return
            # mention_only + 新的 @ → 创建新 thread 而非路由到旧 thread
            if self._mention_only and has_mention:
                thread_obj = await self._create_thread(message)
                if thread_obj is not None:
                    target_thread_id = str(thread_obj.id)
                    self._active_threads[channel_id] = target_thread_id
                    self._save_thread(channel_id, target_thread_id)
                    thread_id = target_thread_id
                    chat_id = channel_id
                    typing_target = thread_obj
                    logger.info("[Discord] created new thread %s in channel %s on mention (replacing existing thread)", target_thread_id, channel_id)
                else:
                    logger.info("[Discord] thread creation failed in channel %s, falling back to channel replies", channel_id)
                    thread_id = channel_id
                    chat_id = channel_id
                    typing_target = message.channel
            else:
                # 已存在会话 → 路由到现有 thread
                target_thread_id = self._active_threads[channel_id]
                logger.debug("[Discord] routing message in channel %s to existing thread %s", channel_id, target_thread_id)
                thread_id = target_thread_id
                chat_id = channel_id
                typing_target = await self._get_channel_or_thread(target_thread_id)
        elif self._mention_only and not has_mention and channel_id not in self._allowed_channels:
            # 既无 @ 又不在 allowed_channel → 跳过
            logger.debug("[Discord] skipping message without mention in channel %s", channel_id)
            return
        elif self._mention_only and has_mention:
            # 该 channel 上首次 @ → 创建 thread
            thread_obj = await self._create_thread(message)
            if thread_obj is not None:
                target_thread_id = str(thread_obj.id)
                self._active_threads[channel_id] = target_thread_id
                self._save_thread(channel_id, target_thread_id)
                thread_id = target_thread_id
                chat_id = channel_id
                typing_target = thread_obj  # 在新 thread 内 typing
                logger.info("[Discord] created thread %s in channel %s for user %s", target_thread_id, channel_id, message.author.display_name)
            else:
                # 回退：thread 创建失败（功能关闭 / 权限不足），在 channel 中回复
                logger.info("[Discord] thread creation failed in channel %s, falling back to channel replies", channel_id)
                thread_id = channel_id
                chat_id = channel_id
                typing_target = message.channel  # 在 channel 内 typing
        elif self._thread_mode:
            # thread_mode 但 mention_only 为 False → 仍创建 thread 以汇总会话
            thread_obj = await self._create_thread(message)
            if thread_obj is None:
                # thread 创建失败，回退到 channel 回复
                logger.info("[Discord] thread creation failed in channel %s, falling back to channel replies", channel_id)
                thread_id = channel_id
                chat_id = channel_id
                typing_target = message.channel  # 在 channel 内 typing
            else:
                target_thread_id = str(thread_obj.id)
                self._active_threads[channel_id] = target_thread_id
                self._save_thread(channel_id, target_thread_id)
                thread_id = target_thread_id
                chat_id = channel_id
                typing_target = thread_obj  # 在新 thread 内 typing
        else:
            # 不开 thread —— 直接在 channel 内回复
            thread_id = channel_id
            chat_id = channel_id
            typing_target = message.channel  # 在 channel 内 typing

        msg_type = InboundMessageType.COMMAND if text.startswith("/") else InboundMessageType.CHAT
        inbound = self._make_inbound(
            chat_id=chat_id,
            user_id=str(message.author.id),
            text=text,
            msg_type=msg_type,
            thread_ts=thread_id,
            metadata={
                "guild_id": str(guild.id) if guild else None,
                "channel_id": str(message.channel.id),
                "message_id": str(message.id),
            },
        )
        inbound.topic_id = thread_id

        # 在正确的目标（thread 或 channel）启动 typing 指示
        if typing_target:
            asyncio.create_task(self._start_typing(typing_target, chat_id, thread_id))

        self._publish(inbound)
        asyncio.create_task(self._add_reaction(message))

    def _publish(self, inbound) -> None:
        """将入站消息跨线程发布到主事件循环。"""
        if self._main_loop and self._main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._main_loop)
            future.add_done_callback(lambda f: logger.exception("[Discord] publish_inbound failed", exc_info=f.exception()) if f.exception() else None)

    def _run_client(self) -> None:
        """在独立线程中跑 Discord 客户端的事件循环。"""
        self._discord_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._discord_loop)
        try:
            self._discord_loop.run_until_complete(self._client.start(self._bot_token))
        except Exception:
            if self._running:
                logger.exception("Discord client error")
        finally:
            try:
                if self._client and not self._client.is_closed():
                    self._discord_loop.run_until_complete(self._client.close())
            except Exception:
                logger.exception("Error during Discord shutdown")

    async def _create_thread(self, message):
        """尝试在频道下创建一个用于会话的 thread，失败返回 ``None``。"""
        try:
            if self._discord_module is None:
                return None

            # 仅 TextChannel (type 0) 和 NewsChannel (type 10) 支持 thread
            channel_type = message.channel.type
            if channel_type not in (
                self._discord_module.ChannelType.text,
                self._discord_module.ChannelType.news,
            ):
                logger.info(
                    "[Discord] channel type %s (%s) does not support threads",
                    channel_type.value,
                    channel_type.name,
                )
                return None

            thread_name = f"deerflow-{message.author.display_name}-{message.id}"[:100]
            return await message.create_thread(name=thread_name)
        except self._discord_module.errors.HTTPException as exc:
            if exc.code == 50024:
                logger.info(
                    "[Discord] cannot create thread in channel %s (error code 50024): %s",
                    message.channel.id,
                    channel_type.name if (channel_type := message.channel.type) else "unknown",
                )
            else:
                logger.exception(
                    "[Discord] failed to create thread for message=%s (HTTPException %s)",
                    message.id,
                    exc.code,
                )
            return None
        except Exception:
            logger.exception("[Discord] failed to create thread for message=%s (threads may be disabled or missing permissions)", message.id)
            return None

    async def _resolve_target(self, msg: OutboundMessage):
        """解析出站消息对应的 Discord 发送目标（thread 或 channel）。"""
        if not self._client or not self._discord_loop:
            return None

        target_ids: list[str] = []
        if msg.thread_ts:
            target_ids.append(msg.thread_ts)
        if msg.chat_id and msg.chat_id not in target_ids:
            target_ids.append(msg.chat_id)

        for raw_id in target_ids:
            target = await self._get_channel_or_thread(raw_id)
            if target is not None:
                return target
        return None

    async def _get_channel_or_thread(self, raw_id: str):
        """跨线程从 Discord 客户端获取指定 ID 的 channel/thread 对象。"""
        if not self._client or not self._discord_loop:
            return None

        try:
            target_id = int(raw_id)
        except (TypeError, ValueError):
            return None

        get_future = asyncio.run_coroutine_threadsafe(self._fetch_channel(target_id), self._discord_loop)
        try:
            return await asyncio.wrap_future(get_future)
        except Exception:
            logger.exception("[Discord] failed to resolve target id=%s", raw_id)
            return None

    async def _fetch_channel(self, target_id: int):
        """在 Discord 客户端的缓存或通过 API 获取 channel 对象。"""
        if not self._client:
            return None

        channel = self._client.get_channel(target_id)
        if channel is not None:
            return channel

        try:
            return await self._client.fetch_channel(target_id)
        except Exception:
            return None

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """把长文本切成若干不超过 Discord 消息长度限制的片段。"""
        if not text:
            return [""]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > _DISCORD_MAX_MESSAGE_LEN:
            split_at = remaining.rfind("\n", 0, _DISCORD_MAX_MESSAGE_LEN)
            if split_at <= 0:
                split_at = _DISCORD_MAX_MESSAGE_LEN
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

        if remaining:
            chunks.append(remaining)

        return chunks
