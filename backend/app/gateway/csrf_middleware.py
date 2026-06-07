"""FastAPI 的 CSRF 防护中间件。

依据 RFC-001：
所有状态变更操作都必须受到 CSRF 防护。
"""

import os
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_LENGTH = 64  # bytes


def is_secure_request(request: Request) -> bool:
    """检测原始客户端请求是否通过 HTTPS 发起。"""
    return _request_scheme(request) == "https"


def generate_csrf_token() -> str:
    """生成一个安全的随机 CSRF 令牌。"""
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)


def should_check_csrf(request: Request) -> bool:
    """判断一个请求是否需要执行 CSRF 校验。

    仅对状态变更方法（POST、PUT、DELETE、PATCH）执行 CSRF 校验。
    依据 RFC 7231，GET、HEAD、OPTIONS、TRACE 方法不在校验范围内。
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return False

    path = request.url.path.rstrip("/")
    # 排除 /api/v1/auth/me 端点
    if path == "/api/v1/auth/me":
        return False
    return True


_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/logout",
        "/api/v1/auth/register",
        "/api/v1/auth/initialize",
    }
)


def is_auth_endpoint(request: Request) -> bool:
    """判断请求是否指向认证端点。

    认证端点在首次调用时无需 CSRF 校验（因为还没有 token）。
    """
    return request.url.path.rstrip("/") in _AUTH_EXEMPT_PATHS


def _host_with_optional_port(hostname: str, port: int | None, scheme: str) -> str:
    """返回标准化后的 ``host[:port]``，默认端口会被省略。"""
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host
    return f"{host}:{port}"


def _normalize_origin(origin: str) -> str | None:
    """返回标准化后的 ``scheme://host[:port]`` 来源，非法输入返回 ``None``。"""
    try:
        parsed = urlsplit(origin.strip())
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None

    # 浏览器的 Origin 只包含 scheme/host/port。拒绝带路径/凭据等 URL 形态的值。
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None

    return f"{scheme}://{_host_with_optional_port(parsed.hostname, port, scheme)}"


def _configured_cors_origins() -> set[str]:
    """返回显式配置的、可以调用认证路由的浏览器来源集合。"""
    origins = set()
    for raw_origin in os.environ.get("GATEWAY_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip()
        if not origin or origin == "*":
            continue
        normalized = _normalize_origin(origin)
        if normalized:
            origins.add(normalized)
    return origins


def get_configured_cors_origins() -> set[str]:
    """返回从 ``GATEWAY_CORS_ORIGINS`` 解析出的标准化浏览器来源集合。"""
    return _configured_cors_origins()


def _first_header_value(value: str | None) -> str | None:
    """返回代理头中以逗号分隔的多个值中的第一个。"""
    if not value:
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _forwarded_param(request: Request, name: str) -> str | None:
    """从 RFC 7239 ``Forwarded`` 头部的第一个条目中提取指定参数。"""
    forwarded = _first_header_value(request.headers.get("forwarded"))
    if not forwarded:
        return None

    for part in forwarded.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == name:
            return value.strip().strip('"') or None
    return None


def _request_scheme(request: Request) -> str:
    """从受信任的代理头中解析原始请求的协议。"""
    scheme = _forwarded_param(request, "proto") or _first_header_value(request.headers.get("x-forwarded-proto")) or request.url.scheme
    return scheme.lower()


def _request_origin(request: Request) -> str | None:
    """构造浏览器所请求 URL 的 origin。"""
    scheme = _request_scheme(request)
    host = _forwarded_param(request, "host") or _first_header_value(request.headers.get("x-forwarded-host")) or request.headers.get("host") or request.url.netloc

    forwarded_port = _first_header_value(request.headers.get("x-forwarded-port"))
    if forwarded_port and ":" not in host.rsplit("]", 1)[-1]:
        host = f"{host}:{forwarded_port}"

    return _normalize_origin(f"{scheme}://{host}")


def is_allowed_auth_origin(request: Request) -> bool:
    """仅允许来自同源或显式配置的源的认证 POST 请求。

    登录/注册/初始化等端点由于首次访问的浏览器客户端还没有 CSRF token，
    因此豁免 Double Submit 校验。但它们仍会创建会话 cookie，所以必须
    拒绝带恶意 ``Origin`` 头的浏览器请求以防止登录 CSRF / 会话固定攻击。
    不带 ``Origin`` 的请求来自 curl、移动端等非浏览器客户端时予以放行。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True

    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        return False

    request_origin = _request_origin(request)
    return normalized_origin in _configured_cors_origins() or (request_origin is not None and normalized_origin == request_origin)


class CSRFMiddleware(BaseHTTPMiddleware):
    """使用 Double Submit Cookie 模式实现 CSRF 防护的中间件。"""

    def __init__(self, app: ASGIApp) -> None:
        """初始化中间件，保存 ASGI 应用引用。"""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """执行 CSRF 校验：检查状态变更方法是否带合法的 Double Submit Cookie。"""
        _is_auth = is_auth_endpoint(request)

        if should_check_csrf(request) and _is_auth and not is_allowed_auth_origin(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site auth request denied."},
            )

        if should_check_csrf(request) and not _is_auth:
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)

            if not cookie_token or not header_token:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing. Include X-CSRF-Token header."},
                )

            if not secrets.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token mismatch."},
                )

        response = await call_next(request)

        # For auth endpoints that set up session, also set CSRF cookie
        if _is_auth and request.method == "POST":
            # Generate a new CSRF token for the session
            csrf_token = generate_csrf_token()
            is_https = is_secure_request(request)
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=csrf_token,
                httponly=False,  # Must be JS-readable for Double Submit Cookie pattern
                secure=is_https,
                samesite="strict",
            )

        return response


def get_csrf_token(request: Request) -> str | None:
    """从当前请求的 cookie 中获取 CSRF 令牌。

    在服务端渲染并需要把 token 嵌入表单或请求头时非常有用。
    """
    return request.cookies.get(CSRF_COOKIE_NAME)
