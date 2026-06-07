"""MCP HTTP/SSE 服务器的 OAuth 令牌支持。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from deerflow.config.extensions_config import ExtensionsConfig, McpOAuthConfig

logger = logging.getLogger(__name__)


@dataclass
class _OAuthToken:
    """缓存中的 OAuth 令牌。

    Attributes:
        access_token: 访问令牌字符串。
        token_type: 令牌类型(通常 ``Bearer``)。
        expires_at: 令牌的过期时间(UTC)。
    """

    access_token: str
    token_type: str
    expires_at: datetime


class OAuthTokenManager:
    """为 MCP 服务器获取、缓存并按需刷新 OAuth 令牌。"""

    def __init__(self, oauth_by_server: dict[str, McpOAuthConfig]):
        """初始化令牌管理器。

        Args:
            oauth_by_server: MCP 服务器名到 OAuth 配置的映射。
        """
        self._oauth_by_server = oauth_by_server
        self._tokens: dict[str, _OAuthToken] = {}
        self._locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in oauth_by_server}

    @classmethod
    def from_extensions_config(cls, extensions_config: ExtensionsConfig) -> OAuthTokenManager:
        """从 :class:`ExtensionsConfig` 抽取启用 OAuth 的服务器并构造管理器。"""
        oauth_by_server: dict[str, McpOAuthConfig] = {}
        for server_name, server_config in extensions_config.get_enabled_mcp_servers().items():
            if server_config.oauth and server_config.oauth.enabled:
                oauth_by_server[server_name] = server_config.oauth
        return cls(oauth_by_server)

    def has_oauth_servers(self) -> bool:
        """是否管理着至少一个启用了 OAuth 的 MCP 服务器。"""
        return bool(self._oauth_by_server)

    def oauth_server_names(self) -> list[str]:
        """返回所有启用 OAuth 的 MCP 服务器名列表。"""
        return list(self._oauth_by_server.keys())

    async def get_authorization_header(self, server_name: str) -> str | None:
        """获取 ``server_name`` 对应的 ``Authorization`` 头,必要时刷新令牌。

        Args:
            server_name: MCP 服务器名。

        Returns:
            形如 ``"Bearer xxx"`` 的头值;若该服务器未启用 OAuth 则返回 None。
        """
        oauth = self._oauth_by_server.get(server_name)
        if not oauth:
            return None

        token = self._tokens.get(server_name)
        if token and not self._is_expiring(token, oauth):
            return f"{token.token_type} {token.access_token}"

        lock = self._locks[server_name]
        async with lock:
            token = self._tokens.get(server_name)
            if token and not self._is_expiring(token, oauth):
                return f"{token.token_type} {token.access_token}"

            fresh = await self._fetch_token(oauth)
            self._tokens[server_name] = fresh
            logger.info(f"Refreshed OAuth access token for MCP server: {server_name}")
            return f"{fresh.token_type} {fresh.access_token}"

    @staticmethod
    def _is_expiring(token: _OAuthToken, oauth: McpOAuthConfig) -> bool:
        """判断缓存的令牌是否已经或即将过期(留出 ``refresh_skew_seconds`` 余量)。"""
        now = datetime.now(UTC)
        return token.expires_at <= now + timedelta(seconds=max(oauth.refresh_skew_seconds, 0))

    async def _fetch_token(self, oauth: McpOAuthConfig) -> _OAuthToken:
        """通过 ``client_credentials`` 或 ``refresh_token`` 授权模式拉取新令牌。"""
        import httpx  # pyright: ignore[reportMissingImports]

        data: dict[str, str] = {
            "grant_type": oauth.grant_type,
            **oauth.extra_token_params,
        }

        if oauth.scope:
            data["scope"] = oauth.scope
        if oauth.audience:
            data["audience"] = oauth.audience

        if oauth.grant_type == "client_credentials":
            if not oauth.client_id or not oauth.client_secret:
                raise ValueError("OAuth client_credentials requires client_id and client_secret")
            data["client_id"] = oauth.client_id
            data["client_secret"] = oauth.client_secret
        elif oauth.grant_type == "refresh_token":
            if not oauth.refresh_token:
                raise ValueError("OAuth refresh_token grant requires refresh_token")
            data["refresh_token"] = oauth.refresh_token
            if oauth.client_id:
                data["client_id"] = oauth.client_id
            if oauth.client_secret:
                data["client_secret"] = oauth.client_secret
        else:
            raise ValueError(f"Unsupported OAuth grant type: {oauth.grant_type}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(oauth.token_url, data=data)
            response.raise_for_status()
            payload = response.json()

        access_token = payload.get(oauth.token_field)
        if not access_token:
            raise ValueError(f"OAuth token response missing '{oauth.token_field}'")

        token_type = str(payload.get(oauth.token_type_field, oauth.default_token_type) or oauth.default_token_type)

        expires_in_raw = payload.get(oauth.expires_in_field, 3600)
        try:
            expires_in = int(expires_in_raw)
        except (TypeError, ValueError):
            expires_in = 3600

        expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in, 1))
        return _OAuthToken(access_token=access_token, token_type=token_type, expires_at=expires_at)


def build_oauth_tool_interceptor(extensions_config: ExtensionsConfig) -> Any | None:
    """构造一个工具拦截器,把 OAuth 头注入到对应 MCP 服务器的工具调用中。

    Args:
        extensions_config: 全局扩展配置。

    Returns:
        可传给 MCP 客户端的拦截器;若没有启用 OAuth 的服务器则返回 None。
    """
    token_manager = OAuthTokenManager.from_extensions_config(extensions_config)
    if not token_manager.has_oauth_servers():
        return None

    async def oauth_interceptor(request: Any, handler: Any) -> Any:
        """为工具请求注入 ``Authorization`` 头,然后调用下层 handler。"""
        header = await token_manager.get_authorization_header(request.server_name)
        if not header:
            return await handler(request)

        updated_headers = dict(request.headers or {})
        updated_headers["Authorization"] = header
        return await handler(request.override(headers=updated_headers))

    return oauth_interceptor


async def get_initial_oauth_headers(extensions_config: ExtensionsConfig) -> dict[str, str]:
    """为 MCP 服务器初始化连接预取 OAuth 头。

    Args:
        extensions_config: 全局扩展配置。

    Returns:
        ``server_name -> Authorization 头值`` 的字典;没有 OAuth 服务器时为空。
    """
    token_manager = OAuthTokenManager.from_extensions_config(extensions_config)
    if not token_manager.has_oauth_servers():
        return {}

    headers: dict[str, str] = {}
    for server_name in token_manager.oauth_server_names():
        headers[server_name] = await token_manager.get_authorization_header(server_name) or ""

    return {name: value for name, value in headers.items() if value}
