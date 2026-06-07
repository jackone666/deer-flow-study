"""集中访问存储在 ``app.state`` 上的单例对象。

**Getters**（供路由器使用）：当某个被要求的依赖缺失时抛出 503，
唯一的例外是 ``get_store``，它在缺失时返回 ``None``。

``AppConfig`` **不**会缓存到 ``app.state`` 上。路由器与运行路径都通过
:func:`deerflow.config.app_config.get_app_config` 解析配置，该函数会执行基于 mtime 的
热加载，因此对 ``config.yaml`` 的修改会在下一个请求时即时生效，无需重启进程。
:func:`langgraph_runtime` 中创建的各种引擎（stream bridge、persistence、checkpointer、
store、run-event store）都接受一个 ``startup_config`` 快照——它们按设计需要重启才能
变更，并保持与该快照绑定，从而使运行中的进程始终保持自洽。

初始化工作由 ``app.py`` 通过 :class:`AsyncExitStack` 直接处理。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, TypeVar, cast

from fastapi import FastAPI, HTTPException, Request
from langgraph.types import Checkpointer

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.persistence.feedback import FeedbackRepository
from deerflow.runtime import RunContext, RunManager, StreamBridge
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.gateway.auth.local_provider import LocalAuthProvider
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.persistence.thread_meta.base import ThreadMetaStore
    from deerflow.runtime import RunRecord


T = TypeVar("T")


async def _mark_latest_recovered_threads_error(
    run_manager: RunManager,
    thread_store: ThreadMetaStore,
    recovered_runs: list[RunRecord],
) -> None:
    """仅在某个线程最近一次运行被恢复时，才把它的状态标记为 error。"""
    recovered_by_thread: dict[str, set[str]] = {}
    for record in recovered_runs:
        recovered_by_thread.setdefault(record.thread_id, set()).add(record.run_id)

    for thread_id, recovered_run_ids in recovered_by_thread.items():
        try:
            latest_runs = await run_manager.list_by_thread(thread_id, user_id=None, limit=1)
        except Exception:
            logger.warning("Failed to find latest run for thread %s during run reconciliation", thread_id, exc_info=True)
            continue
        if not latest_runs or latest_runs[0].run_id not in recovered_run_ids:
            continue
        try:
            await thread_store.update_status(thread_id, "error", user_id=None)
        except Exception:
            logger.warning("Failed to mark thread %s as error during run reconciliation", thread_id, exc_info=True)


def get_config() -> AppConfig:
    """返回当前请求所需的最新 ``AppConfig``。

    通过 :func:`deerflow.config.app_config.get_app_config` 解析配置，该函数会响应运行期
    ``ContextVar`` 覆盖，并在 ``config.yaml`` 的 mtime 变更时从磁盘重新加载。``AppConfig``
    根本不会被缓存到 ``app.state``——唯一的启动期快照作为 ``lifespan()`` 内部的
    ``startup_config`` 局部变量存在，并被显式传给 :func:`langgraph_runtime` 中那些按设计
    需要重启的引擎。所有请求都走 :func:`get_app_config` 解析配置，从而修复
    bytedance/deer-flow issue #3107 BUG-001 描述的脑裂问题：worker / lead-agent 线程原本
    会读到一份过时的启动快照。

    任何在构造配置时发生的失败（文件缺失、权限不足、YAML 解析错误、校验错误）
    都会以 503 形式报告——其语义是 "Gateway 在没有可用配置的情况下无法处理请求"——
    并在日志中附带原始异常，便于运维排查。
    """
    try:
        return get_app_config()
    except Exception as exc:  # noqa: BLE001 - 请求边界：记录日志并优雅降级
        logger.exception("Failed to load AppConfig at request time")
        raise HTTPException(status_code=503, detail="Configuration not available") from exc


@asynccontextmanager
async def langgraph_runtime(app: FastAPI, startup_config: AppConfig) -> AsyncGenerator[None, None]:
    """启动并拆除所有 LangGraph 运行时单例。

    ``startup_config`` 是 ``lifespan()`` 期间一次性采集的 ``AppConfig`` 快照，用于
    基础设施的引导。这里构造的引擎与存储（stream bridge、persistence 引擎、
    checkpointer、store、run-event store）按设计需要重启才能更换——它们持有
    活动连接、文件句柄或单例 provider——因此绑定到这一份快照并跨越
    ``config.yaml`` 修改继续生效。需要热加载配置的请求期消费者仍应通过
    :func:`get_config` 访问相关字段。详见 ``backend/CLAUDE.md`` 中的
    "Config Hot-Reload Boundary"。

    与之匹配的 ``run_events_config`` 会被冻结到 ``app.state`` 上，使 :func:`get_run_context`
    把刚加载的 ``AppConfig`` 与底层 ``event_store`` 启动时绑定的 *启动期* run-events 配置
    配对使用——否则运行时可能会把一份新的、刚热加载的 ``run_events_config`` 跟仍绑定
    在旧后端的 event store 组合在一起。

    在 ``app.py`` 中的用法::

        async with langgraph_runtime(app, startup_config):
            yield
    """
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
    from deerflow.runtime import make_store, make_stream_bridge
    from deerflow.runtime.checkpointer.async_provider import make_checkpointer
    from deerflow.runtime.events.store import make_run_event_store

    async with AsyncExitStack() as stack:
        config = startup_config

        app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge(config))

        # Initialize persistence engine BEFORE checkpointer so that
        # auto-create-database logic runs first (postgres backend).
        await init_engine_from_config(config.database)

        app.state.checkpointer = await stack.enter_async_context(make_checkpointer(config))
        app.state.store = await stack.enter_async_context(make_store(config))

        # Initialize repositories — one get_session_factory() call for all.
        sf = get_session_factory()
        if sf is not None:
            from deerflow.persistence.feedback import FeedbackRepository
            from deerflow.persistence.run import RunRepository

            app.state.run_store = RunRepository(sf)
            app.state.feedback_repo = FeedbackRepository(sf)
        else:
            from deerflow.runtime.runs.store.memory import MemoryRunStore

            app.state.run_store = MemoryRunStore()
            app.state.feedback_repo = None

        from deerflow.persistence.thread_meta import make_thread_store

        app.state.thread_store = make_thread_store(sf, app.state.store)

        # Run event store. The store and the matching ``run_events_config`` are
        # both frozen at startup so ``get_run_context`` does not combine a
        # freshly-reloaded ``AppConfig.run_events`` with a store still bound to
        # the previous backend.
        run_events_config = getattr(config, "run_events", None)
        app.state.run_events_config = run_events_config
        app.state.run_event_store = make_run_event_store(run_events_config)

        # RunManager with store backing for persistence
        app.state.run_manager = RunManager(store=app.state.run_store)
        if getattr(config.database, "backend", None) == "sqlite":
            from deerflow.utils.time import now_iso

            # Startup-only recovery: clean shutdowns return no active rows and
            # the thread-status update below becomes a no-op.
            recovered_runs = await app.state.run_manager.reconcile_orphaned_inflight_runs(
                error="Gateway restarted before this run reached a durable final state.",
                before=now_iso(),
            )
            await _mark_latest_recovered_threads_error(app.state.run_manager, app.state.thread_store, recovered_runs)

        try:
            yield
        finally:
            await close_engine()


# ---------------------------------------------------------------------------
# Getters – called by routers per-request
# ---------------------------------------------------------------------------


def _require(attr: str, label: str) -> Callable[[Request], T]:
    """创建一个 FastAPI 依赖，用于返回 ``app.state.<attr>`` 或在缺失时抛出 503。"""

    def dep(request: Request) -> T:
        """``_require`` 工厂生成的实际依赖函数：从 ``app.state`` 取值或抛出 503。"""
        val = getattr(request.app.state, attr, None)
        if val is None:
            raise HTTPException(status_code=503, detail=f"{label} not available")
        return cast(T, val)

    dep.__name__ = dep.__qualname__ = f"get_{attr}"
    return dep


get_stream_bridge: Callable[[Request], StreamBridge] = _require("stream_bridge", "Stream bridge")
get_run_manager: Callable[[Request], RunManager] = _require("run_manager", "Run manager")
get_checkpointer: Callable[[Request], Checkpointer] = _require("checkpointer", "Checkpointer")
get_run_event_store: Callable[[Request], RunEventStore] = _require("run_event_store", "Run event store")
get_feedback_repo: Callable[[Request], FeedbackRepository] = _require("feedback_repo", "Feedback")
get_run_store: Callable[[Request], RunStore] = _require("run_store", "Run store")


def get_store(request: Request):
    """返回全局 store 对象（未配置时为 ``None``）。"""
    return getattr(request.app.state, "store", None)


def get_thread_store(request: Request) -> ThreadMetaStore:
    """返回线程元数据存储（SQL 或内存后端）。"""
    val = getattr(request.app.state, "thread_store", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Thread metadata store not available")
    return val


def get_run_context(request: Request) -> RunContext:
    """基于 ``app.state`` 单例构造一个 :class:`RunContext`。

    返回的是仅包含基础设施依赖的基础上下文。``app_config`` 字段在请求期实时解析，
    以保证 ``models[*].max_tokens`` 等与运行期相关的字段能够跟随 ``config.yaml``
    的修改；``event_store`` / ``run_events_config`` 配对仍冻结于
    :func:`langgraph_runtime` 启动期采集的快照，避免出现 event store 绑定在
    旧后端却与指向新后端的配置配对的错误状态。
    """
    return RunContext(
        checkpointer=get_checkpointer(request),
        store=get_store(request),
        event_store=get_run_event_store(request),
        run_events_config=getattr(request.app.state, "run_events_config", None),
        thread_store=get_thread_store(request),
        app_config=get_config(),
    )


# ---------------------------------------------------------------------------
# Auth helpers (used by authz.py and auth middleware)
# ---------------------------------------------------------------------------

# Cached singletons to avoid repeated instantiation per request
_cached_local_provider: LocalAuthProvider | None = None
_cached_repo: SQLiteUserRepository | None = None


def get_local_provider() -> LocalAuthProvider:
    """获取或创建缓存的 ``LocalAuthProvider`` 单例。

    必须在 ``init_engine_from_config()`` 之后调用——构造用户仓库需要共享的
    session factory。
    """
    global _cached_local_provider, _cached_repo
    if _cached_repo is None:
        from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            raise RuntimeError("get_local_provider() called before init_engine_from_config(); cannot access users table")
        _cached_repo = SQLiteUserRepository(sf)
    if _cached_local_provider is None:
        from app.gateway.auth.local_provider import LocalAuthProvider

        _cached_local_provider = LocalAuthProvider(repository=_cached_repo)
    return _cached_local_provider


async def get_current_user_from_request(request: Request):
    """从请求的 cookie 中获取当前已认证用户。

    未认证时抛出 ``HTTPException`` 401。
    """
    from app.gateway.auth import decode_token
    from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse, TokenError, token_error_to_code

    access_token = request.cookies.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.NOT_AUTHENTICATED, message="Not authenticated").model_dump(),
        )

    payload = decode_token(access_token)
    if isinstance(payload, TokenError):
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=token_error_to_code(payload), message=f"Token error: {payload.value}").model_dump(),
        )

    provider = get_local_provider()
    user = await provider.get_user(payload.sub)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.USER_NOT_FOUND, message="User not found").model_dump(),
        )

    # Token 版本不匹配 → 密码已修改，token 失效
    if user.token_version != payload.ver:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.TOKEN_INVALID, message="Token revoked (password changed)").model_dump(),
        )

    return user


async def get_optional_user_from_request(request: Request):
    """从请求中获取可选的已认证用户。

    未认证时返回 ``None``，不会抛出异常。
    """
    try:
        return await get_current_user_from_request(request)
    except HTTPException:
        return None


async def get_current_user(request: Request) -> str | None:
    """从请求 cookie 中提取用户 ID，未认证时返回 ``None``。

    该便捷函数只返回字符串形式的用户 ID，供只关心身份标识的调用方
    （例如 ``feedback.py``）使用。需要完整用户对象的调用方应使用
    ``get_current_user_from_request`` 或 ``get_optional_user_from_request``。
    """
    user = await get_optional_user_from_request(request)
    return str(user.id) if user else None
