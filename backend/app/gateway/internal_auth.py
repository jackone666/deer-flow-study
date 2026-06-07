"""用于受信任的 Gateway 内部调用方的身份认证。"""

from __future__ import annotations

import os
import secrets
from types import SimpleNamespace

from deerflow.runtime.user_context import DEFAULT_USER_ID

INTERNAL_AUTH_HEADER_NAME = "X-DeerFlow-Internal-Token"
INTERNAL_AUTH_ENV_VAR = "DEER_FLOW_INTERNAL_AUTH_TOKEN"
INTERNAL_SYSTEM_ROLE = "internal"


def _load_internal_auth_token() -> str:
    """从环境变量加载内部认证令牌，若未设置则生成一个随机的强令牌。

    Returns:
        str: 用于内部调用的令牌字符串。
    """
    token = os.environ.get(INTERNAL_AUTH_ENV_VAR)
    if token:
        return token
    return secrets.token_urlsafe(32)


_INTERNAL_AUTH_TOKEN = _load_internal_auth_token()


def create_internal_auth_headers() -> dict[str, str]:
    """构造一组用于通过 Gateway 内部认证的请求头。

    Returns:
        包含 ``X-DeerFlow-Internal-Token`` 头的字典。
    """
    return {INTERNAL_AUTH_HEADER_NAME: _INTERNAL_AUTH_TOKEN}


def is_valid_internal_auth_token(token: str | None) -> bool:
    """校验传入的令牌是否与本 Gateway 工作进程的内部令牌匹配。

    Args:
        token: 客户端提供的待校验令牌。

    Returns:
        匹配返回 ``True``，否则返回 ``False``。
    """
    return bool(token) and secrets.compare_digest(token, _INTERNAL_AUTH_TOKEN)


def get_internal_user():
    """返回用于受信任内部频道调用的合成用户对象。"""
    return SimpleNamespace(id=DEFAULT_USER_ID, system_role=INTERNAL_SYSTEM_ROLE)
