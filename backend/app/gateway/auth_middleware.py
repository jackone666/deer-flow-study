"""全局身份认证中间件——失败关闭的安全兜底。

对非公开路径拒绝未认证的请求并返回 401。当请求通过 cookie 校验后，会将
JWT 负载解析为真实的 ``User`` 对象，并同时写入 ``request.state.user`` 与
``deerflow.runtime.user_context`` 上下文变量，使仓库层的归属过滤能够通过
哨兵模式自动生效。

更细粒度的权限检查仍由 ``authz.py`` 中的装饰器负责。
"""

from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.internal_auth import INTERNAL_AUTH_HEADER_NAME, get_internal_user, is_valid_internal_auth_token
from deerflow.runtime.user_context import reset_current_user, set_current_user

# Paths that never require authentication.
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Exact auth paths that are public (login/register/status check).
# /api/v1/auth/me, /api/v1/auth/change-password etc. are NOT public.
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/register",
        "/api/v1/auth/logout",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/initialize",
    }
)


def _is_public(path: str) -> bool:
    """判断当前路径是否在白名单中（无需鉴权）。"""
    stripped = path.rstrip("/")
    if stripped in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """严格的认证门：拒绝没有有效会话的请求。

    对非公开路径执行两阶段校验：

    1. Cookie 存在性——缺失时返回 401 ``NOT_AUTHENTICATED``
    2. 通过 ``get_optional_user_from_request`` 进行 JWT 校验——当 token 缺失、
       格式错误、已过期或签发用户不存在/已作废时返回 401 ``TOKEN_INVALID``

    校验通过后，会将用户信息同时写入 ``request.state.user`` 和
    ``deerflow.runtime.user_context`` 上下文变量，使仓库层的归属过滤
    可以在下游生效，而不需要每个路由都显式使用 ``@require_auth`` 装饰器。
    那些需要按资源粒度鉴权（例如"用户 A 不允许靠猜 URL 读取用户 B 的线程"）
    的路由应当再叠加 ``@require_permission(..., owner_check=True)`` 来
    显式执行——但身份认证本身完全在本中间件中完成。
    """

    def __init__(self, app: ASGIApp) -> None:
        """初始化中间件，保存 ASGI 应用引用。"""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理每个 HTTP 请求：执行鉴权、注入用户上下文，调用下游并清理。"""
        if _is_public(request.url.path):
            return await call_next(request)

        internal_user = None
        if is_valid_internal_auth_token(request.headers.get(INTERNAL_AUTH_HEADER_NAME)):
            internal_user = get_internal_user()

        # Non-public path: require session cookie
        if internal_user is None and not request.cookies.get("access_token"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": AuthErrorResponse(
                        code=AuthErrorCode.NOT_AUTHENTICATED,
                        message="Authentication required",
                    ).model_dump()
                },
            )

        # Strict JWT validation: reject junk/expired tokens with 401
        # right here instead of silently passing through. This closes
        # the "junk cookie bypass" gap (AUTH_TEST_PLAN test 7.5.8):
        # without this, non-isolation routes like /api/models would
        # accept any cookie-shaped string as authentication.
        #
        # We call the *strict* resolver so that fine-grained error
        # codes (token_expired, token_invalid, user_not_found, …)
        # propagate from AuthErrorCode, not get flattened into one
        # generic code. BaseHTTPMiddleware doesn't let HTTPException
        # bubble up, so we catch and render it as JSONResponse here.
        from app.gateway.deps import get_current_user_from_request

        if internal_user is not None:
            user = internal_user
        else:
            try:
                user = await get_current_user_from_request(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        # Stamp both request.state.user (for the contextvar pattern)
        # and request.state.auth (so @require_permission's "auth is
        # None" branch short-circuits instead of running the entire
        # JWT-decode + DB-lookup pipeline a second time per request).
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)
