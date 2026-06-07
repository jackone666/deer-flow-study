"""Sandbox 提供者的抽象基类与全局单例管理。

集中维护 ``_default_sandbox_provider`` 单例,提供 :func:`get_sandbox_provider`、
:func:`reset_sandbox_provider`、:func:`shutdown_sandbox_provider` 等生命周期管理
辅助函数,让上层 Agent 在不同运行环境(本地/远程等)中复用同一获取入口。
"""

import asyncio
from abc import ABC, abstractmethod

from deerflow.config import get_app_config
from deerflow.reflection import resolve_class
from deerflow.sandbox.sandbox import Sandbox


class SandboxProvider(ABC):
    """沙箱提供者的抽象基类。"""

    uses_thread_data_mounts: bool = False
    needs_upload_permission_adjustment: bool = True

    @abstractmethod
    def acquire(self, thread_id: str | None = None) -> str:
        """获取一个沙箱环境并返回其 ID。

        Returns:
            已获取的沙箱环境 ID。
        """
        pass

    async def acquire_async(self, thread_id: str | None = None) -> str:
        """在不阻塞事件循环的情况下获取沙箱。

        大多数沙箱提供者暴露的是同步生命周期 API(本地 Docker/资源调配等操作是阻塞的)。
        异步运行时应当调用本方法,让这些阻塞操作跑在工作线程中,而非阻塞事件循环。

        Args:
            thread_id: 可选的会话线程 ID。

        Returns:
            已获取的沙箱环境 ID。
        """
        return await asyncio.to_thread(self.acquire, thread_id)

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """根据 ID 获取沙箱实例。

        Args:
            sandbox_id: 需要查询的沙箱环境 ID。
        """
        pass

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """释放沙箱环境。

        Args:
            sandbox_id: 需要销毁的沙箱环境 ID。
        """
        pass

    def reset(self) -> None:
        """清理跨实例替换仍存在的缓存状态。"""
        pass


_default_sandbox_provider: SandboxProvider | None = None


def get_sandbox_provider(**kwargs) -> SandboxProvider:
    """获取沙箱提供者单例。

    返回缓存的单例实例。可使用 :func:`reset_sandbox_provider` 清空缓存,或使用
    :func:`shutdown_sandbox_provider` 正常关闭后再清空。

    Returns:
        沙箱提供者实例。
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is None:
        config = get_app_config()
        cls = resolve_class(config.sandbox.use, SandboxProvider)
        _default_sandbox_provider = cls(**kwargs)
    return _default_sandbox_provider


def reset_sandbox_provider() -> None:
    """重置沙箱提供者单例。

    仅清空缓存的实例,不会调用 ``shutdown``。下一次调用 :func:`get_sandbox_provider`
    时将创建新实例。常用于测试或需要切换配置的场景。

    子类可以重写 ``reset()`` 来清理跨实例保留的模块级状态(例如
    ``LocalSandboxProvider`` 缓存的 ``LocalSandbox`` 单例)。否则配置/挂载点变更
    在下一次 ``acquire()`` 时不会生效。

    注意:如果提供者当前还有活动沙箱,这些沙箱将变为孤儿,请使用
    :func:`shutdown_sandbox_provider` 进行完整清理。
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is not None:
        _default_sandbox_provider.reset()
        _default_sandbox_provider = None


def shutdown_sandbox_provider() -> None:
    """关闭并重置沙箱提供者。

    在清空单例前正确关闭提供者(释放所有沙箱)。在应用关闭或需要彻底重置沙箱系统时调用。
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is not None:
        if hasattr(_default_sandbox_provider, "shutdown"):
            _default_sandbox_provider.shutdown()
        _default_sandbox_provider = None


def set_sandbox_provider(provider: SandboxProvider) -> None:
    """设置自定义的沙箱提供者实例。

    主要用于在测试中注入自定义或 Mock 提供者。

    Args:
        provider: 需要使用的 :class:`SandboxProvider` 实例。
    """
    global _default_sandbox_provider
    _default_sandbox_provider = provider
