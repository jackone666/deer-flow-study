"""在同步 Agent 调用路径中调用异步工具的辅助工具。"""

import asyncio
import atexit
import concurrent.futures
import contextvars
import functools
import logging
from collections.abc import Callable
from typing import Any, get_type_hints

from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

# Shared thread pool for sync tool invocation in async environments.
_SYNC_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="tool-sync")

atexit.register(lambda: _SYNC_TOOL_EXECUTOR.shutdown(wait=False))


def _get_runnable_config_param(func: Callable[..., Any]) -> str | None:
    """返回协程签名中类型为 ``RunnableConfig`` 的参数名。

    Args:
        func: 待检查的可调用对象。

    Returns:
        形参名;无 ``RunnableConfig`` 形参或解析失败时返回 None。
    """
    if isinstance(func, functools.partial):
        func = func.func

    try:
        type_hints = get_type_hints(func)
    except Exception:
        return None

    for name, type_ in type_hints.items():
        if type_ is RunnableConfig:
            return name
    return None


def make_sync_tool_wrapper(coro: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """为异步工具协程构造同步包装函数。

    Args:
        coro: 支撑 LangChain 工具的异步可调用对象。
        tool_name: 用于错误日志的工具名。

    Returns:
        适合赋值给 ``BaseTool.func`` 的同步可调用对象。

    注意:
        如果 ``coro`` 声明了 ``RunnableConfig`` 形参,本包装会对外暴露
        ``config: RunnableConfig``,以让 LangChain 注入运行时配置,然后转发到协程
        实际期望的配置参数。这覆盖了 DeerFlow 当前对配置敏感的工具(如
        ``invoke_acp_agent``)。

        本包装故意不动态合成函数签名。如果未来某个异步工具同时存在一个普通用
        户参数叫 ``config`` 以及一个 ``RunnableConfig`` 形参叫 ``run_config``
        这样的命名,会与 LangChain 注入的 ``config`` 冲突——请在那种签名下
        重命名用户字段或扩展本工具。
    """
    config_param = _get_runnable_config_param(coro)

    def run_coroutine(*args: Any, **kwargs: Any) -> Any:
        """执行相应操作。
        
                Args:
                    *args: Any。
                    **kwargs: Any。
        
                Returns:
                    Any。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is not None and loop.is_running():
                context = contextvars.copy_context()
                future = _SYNC_TOOL_EXECUTOR.submit(context.run, lambda: asyncio.run(coro(*args, **kwargs)))
                return future.result()
            return asyncio.run(coro(*args, **kwargs))
        except Exception as e:
            logger.error("Error invoking tool %r via sync wrapper: %s", tool_name, e, exc_info=True)
            raise

    if config_param:

        def sync_wrapper(*args: Any, config: RunnableConfig = None, **kwargs: Any) -> Any:
            """执行相应操作。
            
                    Args:
                        *args: Any。
                        config: RunnableConfig: 关键字参数。
                        **kwargs: Any。
            
                    Returns:
                        Any。
            """
            if config is not None or config_param not in kwargs:
                kwargs[config_param] = config
            return run_coroutine(*args, **kwargs)

        return sync_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        """返回值。
        
                Args:
                    *args: Any。
                    **kwargs: Any。
        
                Returns:
                    Any。
        """
        return run_coroutine(*args, **kwargs)

    return sync_wrapper
