"""支持 OAuth Bearer 鉴权、prompt caching 与智能 thinking 的自定义 Claude provider。

支持两种鉴权模式：
  1. 标准 API key（``x-api-key`` 头）—— 默认 :class:`ChatAnthropic` 行为
  2. Claude Code OAuth token（``Authorization: Bearer`` 头）
     - 通过 ``sk-ant-oat`` 前缀检测
     - 需要 ``anthropic-beta: oauth-2025-04-20,claude-code-20250219``
     - 所有 OAuth 请求都必须在系统提示中带 billing header

自动从以下运行时交接源加载凭据：
  - ``$ANTHROPIC_API_KEY`` 环境变量
  - ``$CLAUDE_CODE_OAUTH_TOKEN`` 或 ``$ANTHROPIC_AUTH_TOKEN``
  - ``$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR``
  - ``$CLAUDE_CODE_CREDENTIALS_PATH``
  - ``~/.claude/.credentials.json``
"""

import hashlib
import json
import logging
import os
import socket
import time
import uuid
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage
from pydantic import PrivateAttr

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
THINKING_BUDGET_RATIO = 0.8

# Anthropic API 在 OAuth token 访问时必需的 billing header。
# 必须是系统提示的第一个 block。格式与 Claude Code CLI 一致。
# 若硬编码版本漂移，可通过 ANTHROPIC_BILLING_HEADER 环境变量覆盖。
_DEFAULT_BILLING_HEADER = "x-anthropic-billing-header: cc_version=2.1.85.351; cc_entrypoint=cli; cch=6c6d5;"
OAUTH_BILLING_HEADER = os.environ.get("ANTHROPIC_BILLING_HEADER", _DEFAULT_BILLING_HEADER)


class ClaudeChatModel(ChatAnthropic):
    """带 OAuth Bearer 鉴权、prompt caching 与智能 thinking 的 :class:`ChatAnthropic`。

    配置示例：
        - name: claude-sonnet-4.6
          use: deerflow.models.claude_provider:ClaudeChatModel
          model: claude-sonnet-4-6
          max_tokens: 16384
          enable_prompt_caching: true
    """

    # 自定义字段
    enable_prompt_caching: bool = True
    prompt_cache_size: int = 3
    auto_thinking_budget: bool = True
    retry_max_attempts: int = MAX_RETRIES
    _is_oauth: bool = PrivateAttr(default=False)
    _oauth_access_token: str = PrivateAttr(default="")

    model_config = {"arbitrary_types_allowed": True}

    def _validate_retry_config(self) -> None:
        """校验 ``retry_max_attempts`` 不小于 1。

        Raises:
            ValueError: 配置不合法时。
        """
        if self.retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be >= 1")

    def model_post_init(self, __context: Any) -> None:
        """自动加载凭据，必要时启用 OAuth 配置。"""
        from pydantic import SecretStr

        from deerflow.models.credential_loader import (
            OAUTH_ANTHROPIC_BETAS,
            is_oauth_token,
            load_claude_code_credential,
        )

        self._validate_retry_config()

        # 取出实际的 key 值（SecretStr.str() 返回 '**********'）
        current_key = ""
        if self.anthropic_api_key:
            if hasattr(self.anthropic_api_key, "get_secret_value"):
                current_key = self.anthropic_api_key.get_secret_value()
            else:
                current_key = str(self.anthropic_api_key)

        # 没有有效 key 时尝试显式 Claude Code OAuth 交接源
        if not current_key or current_key in ("your-anthropic-api-key",):
            cred = load_claude_code_credential()
            if cred:
                current_key = cred.access_token
                logger.info(f"Using Claude Code CLI credential (source: {cred.source})")
            else:
                logger.warning("No Anthropic API key or explicit Claude Code OAuth credential found.")

        # 检测 OAuth token 并配置 Bearer 鉴权
        if is_oauth_token(current_key):
            self._is_oauth = True
            self._oauth_access_token = current_key
            # 临时把 token 设为 api_key（之后会在 client 上替换为 auth_token）
            self.anthropic_api_key = SecretStr(current_key)
            # 为 OAuth 加上必需的 beta headers
            self.default_headers = {
                **(self.default_headers or {}),
                "anthropic-beta": OAUTH_ANTHROPIC_BETAS,
            }
            # OAuth token 限制最多 4 个 cache_control block——禁用 prompt caching
            self.enable_prompt_caching = False
            logger.info("OAuth token detected — will use Authorization: Bearer header")
        else:
            if current_key:
                self.anthropic_api_key = SecretStr(current_key)

        # 确保 api_key 是 SecretStr
        if isinstance(self.anthropic_api_key, str):
            self.anthropic_api_key = SecretStr(self.anthropic_api_key)

        super().model_post_init(__context)

        # client 创建后立即 patch，以便 OAuth 使用 Bearer 鉴权。
        # 必须在 super() 之后调用，因为 client 是惰性创建的。
        if self._is_oauth:
            self._patch_client_oauth(self._client)
            self._patch_client_oauth(self._async_client)

    def _patch_client_oauth(self, client: Any) -> None:
        """在 Anthropic SDK client 上把 ``api_key`` 替换为 ``auth_token``，启用 OAuth Bearer 鉴权。"""
        if hasattr(client, "api_key") and hasattr(client, "auth_token"):
            client.api_key = None
            client.auth_token = self._oauth_access_token

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """重写父方法，注入 prompt caching、thinking 预算与 OAuth billing 信息。

        Args:
            input_: LangChain 模型输入。
            stop: 可选的停止词列表。
            **kwargs: 透传给父方法的额外参数。

        Returns:
            dict: 调整后的请求负载。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        if self._is_oauth:
            self._apply_oauth_billing(payload)

        if self.enable_prompt_caching:
            self._apply_prompt_caching(payload)

        if self.auto_thinking_budget:
            self._apply_thinking_budget(payload)

        return payload

    def _apply_oauth_billing(self, payload: dict) -> None:
        """注入所有 OAuth 请求必需的 billing header block。

        billing block 始终放在 system 列表的首位，并移除任何已有的同
        名 block 以避免重复或顺序错乱。
        """
        billing_block = {"type": "text", "text": OAUTH_BILLING_HEADER}

        system = payload.get("system")
        if isinstance(system, list):
            # 移除已有的 billing block，然后在 index 0 插入一个
            filtered = [b for b in system if not (isinstance(b, dict) and OAUTH_BILLING_HEADER in b.get("text", ""))]
            payload["system"] = [billing_block] + filtered
        elif isinstance(system, str):
            if OAUTH_BILLING_HEADER in system:
                payload["system"] = [billing_block]
            else:
                payload["system"] = [billing_block, {"type": "text", "text": system}]
        else:
            payload["system"] = [billing_block]

        # 为 OAuth billing 校验补上 metadata.user_id
        if not isinstance(payload.get("metadata"), dict):
            payload["metadata"] = {}
        if "user_id" not in payload["metadata"]:
            # 用机器 hostname 生成稳定的 device_id
            hostname = socket.gethostname()
            device_id = hashlib.sha256(f"deerflow-{hostname}".encode()).hexdigest()
            session_id = str(uuid.uuid4())
            payload["metadata"]["user_id"] = json.dumps(
                {
                    "device_id": device_id,
                    "account_uuid": "deerflow",
                    "session_id": session_id,
                }
            )

    def _apply_prompt_caching(self, payload: dict) -> None:
        """为 system、最近消息以及最后一个 tool 定义加上 ephemeral cache_control。

        使用 ``MAX_CACHE_BREAKPOINTS = 4`` 个断点——Anthropic API 与 AWS
        Bedrock 共同强制执行的上限。断点放在最后 N 个候选 block 上，
        越靠后的断点覆盖的前缀越长，缓存命中率更高。

        系统提示被视为完全静态（不含 per-user memory 或当前日期）。
        动态上下文通过 :class:`DynamicContextMiddleware` 在每个轮次的
        首个 HumanMessage 中以 ``<system-reminder>`` 形式注入。
        """
        MAX_CACHE_BREAKPOINTS = 4

        # 按文档顺序收集候选 block：
        #   1. system 文本 block
        #   2. 最近 prompt_cache_size 条消息的内容 block
        #   3. 最后一个 tool 定义
        candidates: list[dict] = []

        # 1. System block
        system = payload.get("system")
        if system and isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    candidates.append(block)
        elif system and isinstance(system, str):
            new_block: dict = {"type": "text", "text": system}
            payload["system"] = [new_block]
            candidates.append(new_block)

        # 2. 最近消息 block
        messages = payload.get("messages", [])
        cache_start = max(0, len(messages) - self.prompt_cache_size)
        for i in range(cache_start, len(messages)):
            msg = messages[i]
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        candidates.append(block)
            elif isinstance(content, str) and content:
                new_block = {"type": "text", "text": content}
                msg["content"] = [new_block]
                candidates.append(new_block)

        # 3. 最后一个 tool 定义
        tools = payload.get("tools", [])
        if tools and isinstance(tools[-1], dict):
            candidates.append(tools[-1])

        # 仅对最后 MAX_CACHE_BREAKPOINTS 个候选应用 cache_control 以满足 API 上限
        for block in candidates[-MAX_CACHE_BREAKPOINTS:]:
            block["cache_control"] = {"type": "ephemeral"}

    def _apply_thinking_budget(self, payload: dict) -> None:
        """自动分配 thinking 预算（``max_tokens`` 的 80%）。

        Args:
            payload: 即将发送的请求负载（原地修改 ``thinking`` 字段）。
        """
        thinking = payload.get("thinking")
        if not thinking or not isinstance(thinking, dict):
            return
        if thinking.get("type") != "enabled":
            return
        if thinking.get("budget_tokens"):
            return

        max_tokens = payload.get("max_tokens", 8192)
        thinking["budget_tokens"] = int(max_tokens * THINKING_BUDGET_RATIO)

    @staticmethod
    def _strip_cache_control(payload: dict) -> None:
        """在 OAuth 请求到达 Anthropic 之前移除 ``cache_control`` 标记。"""
        for section in ("system", "messages"):
            items = payload.get(section)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item.pop("cache_control", None)
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            block.pop("cache_control", None)

        tools = payload.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    tool.pop("cache_control", None)

    def _create(self, payload: dict) -> Any:
        """同步 ``_create`` 钩子：OAuth 路径先剥掉 cache_control。"""
        if self._is_oauth:
            self._strip_cache_control(payload)
        return super()._create(payload)

    async def _acreate(self, payload: dict) -> Any:
        """异步 ``_acreate`` 钩子：OAuth 路径先剥掉 cache_control。"""
        if self._is_oauth:
            self._strip_cache_control(payload)
        return await super()._acreate(payload)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> Any:
        """同步生成入口：OAuth client patch + 限流/服务端错误的重试。"""
        if self._is_oauth:
            self._patch_client_oauth(self._client)

        last_error = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return super()._generate(messages, stop=stop, **kwargs)
            except anthropic.RateLimitError as e:
                last_error = e
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, e)
                logger.warning(f"Rate limited, retrying attempt {attempt}/{self.retry_max_attempts} after {wait_ms}ms")
                time.sleep(wait_ms / 1000)
            except anthropic.InternalServerError as e:
                last_error = e
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, e)
                logger.warning(f"Server error, retrying attempt {attempt}/{self.retry_max_attempts} after {wait_ms}ms")
                time.sleep(wait_ms / 1000)
        raise last_error

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> Any:
        """异步生成入口：OAuth client patch + 限流/服务端错误的重试。"""
        import asyncio

        if self._is_oauth:
            self._patch_client_oauth(self._async_client)

        last_error = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return await super()._agenerate(messages, stop=stop, **kwargs)
            except anthropic.RateLimitError as e:
                last_error = e
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, e)
                logger.warning(f"Rate limited, retrying attempt {attempt}/{self.retry_max_attempts} after {wait_ms}ms")
                await asyncio.sleep(wait_ms / 1000)
            except anthropic.InternalServerError as e:
                last_error = e
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, e)
                logger.warning(f"Server error, retrying attempt {attempt}/{self.retry_max_attempts} after {wait_ms}ms")
                await asyncio.sleep(wait_ms / 1000)
        raise last_error

    @staticmethod
    def _calc_backoff_ms(attempt: int, error: Exception) -> int:
        """指数退避，并附加 20% 抖动。

        Args:
            attempt: 当前重试序号（从 1 开始）。
            error: 触发的异常；若带有 ``Retry-After`` header，则优先采用。

        Returns:
            int: 等待毫秒数。
        """
        backoff_ms = 2000 * (1 << (attempt - 1))
        jitter_ms = int(backoff_ms * 0.2)
        total_ms = backoff_ms + jitter_ms

        if hasattr(error, "response") and error.response is not None:
            retry_after = error.response.headers.get("Retry-After")
            if retry_after:
                try:
                    total_ms = int(retry_after) * 1000
                except (ValueError, TypeError):
                    pass

        return total_ms
