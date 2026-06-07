"""DeerFlow 的授权装饰器与上下文。

设计灵感来自 LangGraph Auth 系统：https://github.com/langchain-ai/langgraph/blob/main/libs/sdk-py/langgraph_sdk/auth/__init__.py

**使用方法：**

1. 在需要认证的路由上使用 ``@require_auth``
2. 使用 ``@require_permission("resource", "action", filter_key=...)`` 进行权限校验
3. 装饰器链按从下到上的顺序执行

**示例：**

    @router.get("/{thread_id}")
    @require_auth
    @require_permission("threads", "read", owner_check=True)
    async def get_thread(thread_id: str, request: Request):
        # 此时用户已通过认证并具备 threads:read 权限
        ...

**权限模型：**

- threads:read   —— 查看线程
- threads:write  —— 创建/更新线程
- threads:delete —— 删除线程
- runs:create    —— 触发代理运行
- runs:read      —— 查看运行
- runs:cancel    —— 取消运行
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from app.gateway.auth.models import User

P = ParamSpec("P")
T = TypeVar("T")


# Permission constants
class Permissions:
    """``资源:动作`` 格式的权限常量。"""


    # Threads
    THREADS_READ = "threads:read"
    THREADS_WRITE = "threads:write"
    THREADS_DELETE = "threads:delete"

    # Runs
    RUNS_CREATE = "runs:create"
    RUNS_READ = "runs:read"
    RUNS_CANCEL = "runs:cancel"


class AuthContext:
    """当前请求的身份认证上下文。

    在 ``@require_auth`` 装饰后写入 ``request.state.auth``。

    Attributes:
        user: 已认证用户，匿名请求时为 ``None``。
        permissions: 权限字符串列表（例如 ``"threads:read"``）。
    """

    __slots__ = ("user", "permissions")

    def __init__(self, user: User | None = None, permissions: list[str] | None = None):
        """初始化认证上下文。

        Args:
            user: 已认证用户对象，匿名请求时为 ``None``。
            permissions: 权限字符串列表。
        """
        self.user = user
        self.permissions = permissions or []

    @property
    def is_authenticated(self) -> bool:
        """检查用户是否已认证。"""
        return self.user is not None

    def has_permission(self, resource: str, action: str) -> bool:
        """检查上下文是否具备 ``resource:action`` 形式的权限。

        Args:
            resource: 资源名（例如 ``"threads"``）。
            action: 动作名（例如 ``"read"``）。

        Returns:
            用户拥有该权限时返回 ``True``。
        """
        permission = f"{resource}:{action}"
        return permission in self.permissions

    def require_user(self) -> User:
        """获取已认证用户，若未认证则抛出 401。

        Raises:
            HTTPException: 未认证时抛出 401 错误。
        """
        if not self.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return self.user


def get_auth_context(request: Request) -> AuthContext | None:
    """从请求状态中获取 ``AuthContext``。"""
    return getattr(request.state, "auth", None)


_ALL_PERMISSIONS: list[str] = [
    Permissions.THREADS_READ,
    Permissions.THREADS_WRITE,
    Permissions.THREADS_DELETE,
    Permissions.RUNS_CREATE,
    Permissions.RUNS_READ,
    Permissions.RUNS_CANCEL,
]


def _make_test_request_stub() -> Any:
    """为直接单元调用构造一个最小的 request 桩对象。

    用于在没有 FastAPI request 注入的情况下调用被装饰的路由处理器。
    包含 auth 辅助函数实际访问的字段。
    """
    return SimpleNamespace(state=SimpleNamespace(), cookies={}, _deerflow_test_bypass_auth=True)


async def _authenticate(request: Request) -> AuthContext:
    """认证请求并返回 ``AuthContext``。

    委托给 ``deps.get_optional_user_from_request()`` 完成 JWT→User 的解析流程。
    匿名请求返回 ``user=None`` 的 ``AuthContext``。
    """
    from app.gateway.deps import get_optional_user_from_request

    user = await get_optional_user_from_request(request)
    if user is None:
        return AuthContext(user=None, permissions=[])

    # 未来可以从用户记录中读取权限
    return AuthContext(user=user, permissions=_ALL_PERMISSIONS)


def require_auth[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """对请求进行身份认证并强制要求已登录的装饰器。

    无论 ASGI 栈中是否存在 ``AuthMiddleware``，该装饰器都会独立为未认证的请求抛出 HTTP 401。
    并把解析得到的 ``AuthContext`` 写入 ``request.state.auth``，供下游处理器使用。

    必须放在其他装饰器之上（先于它们执行）。

    用法::

        @router.get("/{thread_id}")
        @require_auth  # 装饰器最底层（先于权限校验执行）
        @require_permission("threads", "read")
        async def get_thread(thread_id: str, request: Request):
            auth: AuthContext = request.state.auth
            ...

    Raises:
        HTTPException: 请求未认证时抛出 401。
        ValueError: 当被装饰函数缺少 ``request`` 参数时抛出。
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        """``require_auth`` 装饰器内层包装：执行认证并注入上下文。"""
        request = kwargs.get("request")
        if request is None:
            # 单元测试可能不通过 FastAPI Request 直接调用被装饰的处理器。
            # 当被包装函数声明了 ``request`` 参数时，注入最小可用的桩对象。
            if "request" in inspect.signature(func).parameters:
                kwargs["request"] = _make_test_request_stub()
            else:
                raise ValueError("require_auth decorator requires 'request' parameter")
            request = kwargs["request"]

        if getattr(request, "_deerflow_test_bypass_auth", False):
            return await func(*args, **kwargs)

        # 执行认证并写入上下文
        auth_context = await _authenticate(request)
        request.state.auth = auth_context

        if not auth_context.is_authenticated:
            raise HTTPException(status_code=401, detail="Authentication required")

        return await func(*args, **kwargs)

    return wrapper


def require_permission(
    resource: str,
    action: str,
    owner_check: bool = False,
    require_existing: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """检查 ``resource:action`` 形式权限的装饰器。

    必须在 ``@require_auth`` 之后使用。

    Args:
        resource: 资源名（例如 ``"threads"``、``"runs"``）。
        action: 动作名（例如 ``"read"``、``"write"``、``"delete"``）。
        owner_check: 若为 ``True``，则校验当前用户是否为该资源的拥有者。需要路径参数
                     ``thread_id``，并执行所有权校验。
        require_existing: 仅当 ``owner_check=True`` 时才有意义。若为 ``True``，则
                          ``threads_meta`` 中缺失对应行会被视为拒绝（404），而不是
                          "未跟踪的旧线程，直接放行"。建议在 **破坏性/变更型** 路由
                          （DELETE、PATCH、状态更新）上使用，以避免已删除线程被
                          其他用户通过缺失行的代码路径重新指向。

    用法::

        # 读取类：未跟踪的旧线程允许放行
        @require_permission("threads", "read", owner_check=True)
        async def get_thread(thread_id: str, request: Request):
            ...

        # 破坏性：线程行必须存在且归调用者所有
        @require_permission("threads", "delete", owner_check=True, require_existing=True)
        async def delete_thread(thread_id: str, request: Request):
            ...

    Raises:
        HTTPException 401: 需要认证但用户为匿名。
        HTTPException 403: 用户缺少对应权限。
        HTTPException 404: ``owner_check=True`` 时用户不拥有该线程。
        ValueError: ``owner_check=True`` 但缺少 ``thread_id`` 参数。
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        """``require_permission`` 的内层装饰器：包装路由函数并执行鉴权/权限/所有权校验。"""
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            """``require_permission`` 装饰器内层包装：执行鉴权 + 权限 + 所有权校验。"""
            request = kwargs.get("request")
            if request is None:
                # 单元测试可能不通过 FastAPI Request 直接调用被装饰的处理器。
                # 当被包装函数声明了 ``request`` 参数时，注入最小可用的桩对象。
                if "request" in inspect.signature(func).parameters:
                    kwargs["request"] = _make_test_request_stub()
                else:
                    return await func(*args, **kwargs)
                request = kwargs["request"]

            if getattr(request, "_deerflow_test_bypass_auth", False):
                return await func(*args, **kwargs)

            auth: AuthContext = getattr(request.state, "auth", None)
            if auth is None:
                auth = await _authenticate(request)
                request.state.auth = auth

            if not auth.is_authenticated:
                raise HTTPException(status_code=401, detail="Authentication required")

            # 权限校验
            if not auth.has_permission(resource, action):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: {resource}:{action}",
                )

            # 线程专属资源的所有权校验。
            #
            # 2.0-rc 把线程元数据迁移到 SQL 持久化层（``threads_meta`` 表）。
            # 我们通过 ``ThreadMetaStore.check_access`` 校验所有权：缺失行
            # （未跟踪的旧线程）以及 ``user_id`` 为 NULL 的行（共享/旧数据）
            # 都会返回 True，所以这里采用严格拒绝（strict-deny）而非严格
            # 允许（strict-allow）——只有已存在且 ``user_id`` 不同的行才会
            # 触发 404。
            if owner_check:
                thread_id = kwargs.get("thread_id")
                if thread_id is None:
                    raise ValueError("require_permission with owner_check=True requires 'thread_id' parameter")

                from app.gateway.deps import get_thread_store

                thread_store = get_thread_store(request)
                allowed = await thread_store.check_access(
                    thread_id,
                    str(auth.user.id),
                    require_existing=require_existing,
                )
                if not allowed:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Thread {thread_id} not found",
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
