"""LangGraph 兼容模式的认证处理器——与 Gateway 共享 JWT 逻辑。

默认情况下 DeerFlow 运行时内嵌在 FastAPI Gateway 中，普通脚本和 Docker 部署并不会加载本模块。
该模块专门为 ``langgraph.json`` 中 ``auth.path`` 字段所指定的 LangGraph 工具链、Studio
或直接的 LangGraph Server 兼容模式保留。

当该兼容路径被启用时，本模块复用与 Gateway 完全一致的 JWT 与 CSRF 规则，
从而保证两种模式下的会话校验行为一致。

整体分为两层：
  1. ``@auth.authenticate`` —— 校验 JWT cookie，提取 ``user_id``，并在
     状态变更方法（POST/PUT/DELETE/PATCH）上强制执行 CSRF 检查
  2. ``@auth.on`` —— 返回元数据过滤条件，确保每个用户只能看到自己的线程
"""

import secrets

from langgraph_sdk import Auth

from app.gateway.auth.errors import TokenError
from app.gateway.auth.jwt import decode_token
from app.gateway.deps import get_local_provider

auth = Auth()

# 需要执行 CSRF 校验的 HTTP 方法（依据 RFC 7231 的状态变更方法定义）。
_CSRF_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _check_csrf(request) -> None:
    """对状态变更请求执行 Double Submit Cookie 形式的 CSRF 校验。

    与 Gateway 的 ``CSRFMiddleware`` 行为保持一致，确保被 nginx 直接代理的
    LangGraph 路由享有同样的 CSRF 保护。

    Args:
        request: LangGraph 提供的请求对象。
    """
    method = getattr(request, "method", "") or ""
    if method.upper() not in _CSRF_METHODS:
        return

    cookie_token = request.cookies.get("csrf_token")
    header_token = request.headers.get("x-csrf-token")

    if not cookie_token or not header_token:
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="CSRF token missing. Include X-CSRF-Token header.",
        )

    if not secrets.compare_digest(cookie_token, header_token):
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="CSRF token mismatch.",
        )


@auth.authenticate
async def authenticate(request):
    """校验会话 cookie，解析 JWT，并核对 ``token_version``。

    与 Gateway 的 ``get_current_user_from_request`` 走同一条校验链：
    cookie → 解码 JWT → 数据库查询 → ``token_version`` 比对。
    同时对状态变更方法执行 CSRF 校验。

    Args:
        request: LangGraph 提供的请求对象。

    Returns:
        通过认证的用户 ID（``payload.sub``）。
    """
    # 先于认证执行 CSRF 检查，使伪造的跨站请求能在携带有效 JWT 时也被尽早拒绝。
    _check_csrf(request)

    token = request.cookies.get("access_token")
    if not token:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    payload = decode_token(token)
    if isinstance(payload, TokenError):
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Invalid token",
        )

    user = await get_local_provider().get_user(payload.sub)
    if user is None:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="User not found",
        )
    if user.token_version != payload.ver:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Token revoked (password changed)",
        )

    return payload.sub


@auth.on
async def add_owner_filter(ctx: Auth.types.AuthContext, value: dict):
    """在写入时注入 ``user_id`` 元数据，在读取时按 ``user_id`` 过滤。

    Gateway 将线程所有权存储在 ``metadata.user_id`` 字段中。
    本处理器保证 LangGraph Server 强制执行与 Gateway 一致的隔离策略。

    Args:
        ctx: LangGraph 提供的认证上下文。
        value: 当前操作的载荷字典。

    Returns:
        用于 LangGraph 搜索/读取/删除操作的过滤条件字典。
    """
    # 在创建/更新时：将 user_id 写入 metadata
    metadata = value.setdefault("metadata", {})
    metadata["user_id"] = ctx.user.identity

    # 返回过滤条件字典——LangGraph 会将其应用到 search/read/delete
    return {"user_id": ctx.user.identity}
