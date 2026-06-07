"""认证模块的类型化错误定义。

- ``AuthErrorCode``：覆盖所有认证失败场景的枚举。
- ``TokenError``：覆盖所有 JWT 解码失败场景的枚举。
- ``AuthErrorResponse``：用于 HTTP 响应的结构化错误负载。
"""

from enum import StrEnum

from pydantic import BaseModel


class AuthErrorCode(StrEnum):
    """所有认证失败场景的穷举枚举。"""

    INVALID_CREDENTIALS = "invalid_credentials"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    USER_NOT_FOUND = "user_not_found"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    PROVIDER_NOT_FOUND = "provider_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    SYSTEM_ALREADY_INITIALIZED = "system_already_initialized"


class TokenError(StrEnum):
    """所有 JWT 解码失败原因的穷举枚举。"""

    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    MALFORMED = "malformed"


class AuthErrorResponse(BaseModel):
    """结构化的错误响应——取代裸的 ``detail`` 字符串。"""

    code: AuthErrorCode
    message: str


def token_error_to_code(err: TokenError) -> AuthErrorCode:
    """将 ``TokenError`` 映射到 ``AuthErrorCode``，作为唯一真值源。

    Args:
        err: JWT 解码失败原因枚举值。

    Returns:
        AuthErrorCode: 对应的认证错误码。
    """
    if err == TokenError.EXPIRED:
        return AuthErrorCode.TOKEN_EXPIRED
    return AuthErrorCode.TOKEN_INVALID
