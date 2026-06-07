"""基于请求的 user context，用于按用户进行授权。

本模块持有一个 :class:`~contextvars.ContextVar`，由 Gateway 的认证中间件
在认证成功后写入。仓储方法通过哨兵默认参数读取该 ContextVar，避免
Router 编写 ``user_id`` 样板代码。

仓储 ``user_id`` 参数的三态语义（消费侧位于 ``deerflow.persistence.*``）：

- ``_AUTO``（模块私有哨兵，默认）：从 ContextVar 读取；若未设置则
  抛出 :class:`RuntimeError`。
- 显式 ``str``：使用提供的值，覆盖 ContextVar。
- 显式 ``None``：不附加 WHERE 条件——仅供迁移脚本与有意绕过隔离的
  管理 CLI 使用。

依赖方向
--------------------
``persistence``（下层）从本模块读取；``gateway.auth``（上层）向其写入。
``CurrentUser`` 在本模块中以 :class:`typing.Protocol` 定义，因此
``persistence`` 永远无需从 ``gateway.auth.models`` 导入具体 ``User`` 类。
任何具有 ``.id: str`` 属性的对象都能结构化地满足该协议。

Asyncio 语义
-----------------
``ContextVar`` 在 asyncio 下是 task-local 的，而非线程局部。每个 FastAPI
请求都在各自的任务中运行，因此上下文天然隔离。``asyncio.create_task`` 与
``asyncio.to_thread`` 会继承父任务的上下文，通常正是预期行为；若某个
后台任务 *不应* 看到前台的 user，可用 ``contextvars.copy_context()`` 包裹
以获得干净的副本。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final, Protocol, runtime_checkable


@runtime_checkable
class CurrentUser(Protocol):
    """当前已认证用户的结构化类型。

    任何具有 ``.id: str`` 属性的对象都满足该协议。具体实现位于
    ``app.gateway.auth.models.User``。
    """

    id: str


_current_user: Final[ContextVar[CurrentUser | None]] = ContextVar("deerflow_current_user", default=None)


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    """为当前异步任务设置当前 user。

    返回一个重置 Token，应在 ``finally`` 块中传给
    :func:`reset_current_user` 以恢复先前的上下文。
    """
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    """将上下文恢复到 *token* 捕获时的状态。"""
    _current_user.reset(token)


def get_current_user() -> CurrentUser | None:
    """返回当前 user，未设置时返回 ``None``。

    可在任意上下文中安全调用。供即便没有 user 也能继续的代码路径
    （如迁移脚本、公开端点）使用。
    """
    return _current_user.get()


def require_current_user() -> CurrentUser:
    """返回当前 user，若未设置则抛出 :class:`RuntimeError`。

    供绝不应在请求认证上下文之外调用的仓储代码使用。错误信息经过
    精心措辞，便于调试栈时定位违规的代码路径。
    """
    user = _current_user.get()
    if user is None:
        raise RuntimeError("repository accessed without user context")
    return user


# ---------------------------------------------------------------------------
# Effective user_id helpers (filesystem isolation)
# ---------------------------------------------------------------------------

DEFAULT_USER_ID: Final[str] = "default"


def get_effective_user_id() -> str:
    """返回当前 user 的 id（字符串形式），未设置时返回 ``DEFAULT_USER_ID``。

    与 :func:`require_current_user` 不同，本函数永不抛错——它专为文件系统
    路径解析设计，那些场景必须始终能拿到一个有效的 user 桶。
    """
    user = _current_user.get()
    if user is None:
        return DEFAULT_USER_ID
    return str(user.id)


def resolve_runtime_user_id(runtime: object | None) -> str:
    """工具/中间件 ``effective user_id`` 的单一权威解析入口。

    解析顺序（优先级从高到低）：
      1. ``runtime.context["user_id"]`` —— 由 Gateway 中的
         ``inject_authenticated_user_context`` 从通过认证的
         ``request.state.user`` 写入。这是唯一能跨过 ContextVar 可能
         丢失的边界（脱离请求任务的后台任务、不 ``copy_context`` 的
         工作线程池以及未来的跨进程驱动）的来源。
      2. ``_current_user`` ContextVar —— 由认证中间件在请求进入时写入。
         在任务内调用可靠；会被 ``asyncio`` 子任务与
         ``ContextThreadPoolExecutor`` 复制。
      3. ``DEFAULT_USER_ID`` —— 末位兜底，使未经认证的 CLI / 迁移 /
         测试路径也能继续工作而不会抛错。

    持久化按 user 隔离状态的工具（custom agents、memory、uploads）
    必须调用本函数，而不是直接调用 ``get_effective_user_id()``，从而
    利用 ``setup_agent`` 已依赖的 ``runtime.context`` 通道。
    """
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        ctx_user_id = context.get("user_id")
        if ctx_user_id:
            return str(ctx_user_id)
    return get_effective_user_id()


# ---------------------------------------------------------------------------
# Sentinel-based user_id resolution
# ---------------------------------------------------------------------------
#
# Repository methods accept a ``user_id`` keyword-only argument that
# defaults to ``AUTO``. The three possible values drive distinct
# behaviours; see the docstring on :func:`resolve_user_id`.


class _AutoSentinel:
    """单例哨兵，含义为“从 ContextVar 解析 user_id”。"""

    _instance: _AutoSentinel | None = None

    def __new__(cls) -> _AutoSentinel:
        """执行相应操作。
        
                Args:
                    cls: 参数说明。
        
                Returns:
                    _AutoSentinel。
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        """返回对象的可读字符串表示。"""
        return "<AUTO>"


AUTO: Final[_AutoSentinel] = _AutoSentinel()


def resolve_user_id(
    value: str | None | _AutoSentinel,
    *,
    method_name: str = "repository method",
) -> str | None:
    """解析传给仓储方法的 ``user_id`` 参数。

    三态语义：

    - :data:`AUTO`（默认）：从 ContextVar 读取；若上下文中无 user 则
      抛出 :class:`RuntimeError`。这是请求作用域调用的常见情况。
    - 显式 ``str``：原样使用提供的 id，覆盖任何 ContextVar 值。便于
      测试与管理覆盖流程。
    - 显式 ``None``：不过滤——仓储应完全跳过 ``user_id`` 的 WHERE 条件。
      仅供有意绕过隔离的迁移脚本与 CLI 工具使用。
    """
    if isinstance(value, _AutoSentinel):
        user = _current_user.get()
        if user is None:
            raise RuntimeError(f"{method_name} called with user_id=AUTO but no user context is set; pass an explicit user_id, set the contextvar via auth middleware, or opt out with user_id=None for migration/CLI paths.")
        # Coerce to ``str`` at the boundary: ``User.id`` is typed as
        # ``UUID`` for the API surface, but the persistence layer
        # stores ``user_id`` as ``String(64)`` and aiosqlite cannot
        # bind a raw UUID object to a VARCHAR column ("type 'UUID' is
        # not supported"). Honour the documented return type here
        # rather than ripple a type change through every caller.
        return str(user.id)
    return value
