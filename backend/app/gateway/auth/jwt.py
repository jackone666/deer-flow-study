"""JWT token 的创建与校验。"""

from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel

from app.gateway.auth.config import get_auth_config
from app.gateway.auth.errors import TokenError


class TokenPayload(BaseModel):
    """JWT 令牌的负载。"""

    sub: str  # user_id
    exp: datetime
    iat: datetime | None = None
    ver: int = 0  # token_version —— 必须与 User.token_version 匹配


def create_access_token(user_id: str, expires_delta: timedelta | None = None, token_version: int = 0) -> str:
    """创建一个 JWT 访问令牌。

    Args:
        user_id: 用户的 UUID 字符串。
        expires_delta: 可选的自定义过期时长，默认为 7 天。
        token_version: 用户当前的 ``token_version``，用于在改密时作废旧 JWT。

    Returns:
        str: 编码后的 JWT 字符串。
    """
    config = get_auth_config()
    expiry = expires_delta or timedelta(days=config.token_expiry_days)

    now = datetime.now(UTC)
    payload = {"sub": user_id, "exp": now + expiry, "iat": now, "ver": token_version}
    return jwt.encode(payload, config.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> TokenPayload | TokenError:
    """解码并校验一个 JWT 令牌。

    Returns:
        TokenPayload | TokenError: 校验通过时返回负载对象，否则返回具体的
        ``TokenError`` 枚举值。
    """
    config = get_auth_config()
    try:
        payload = jwt.decode(token, config.jwt_secret, algorithms=["HS256"])
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError:
        return TokenError.EXPIRED
    except jwt.InvalidSignatureError:
        return TokenError.INVALID_SIGNATURE
    except jwt.PyJWTError:
        return TokenError.MALFORMED
