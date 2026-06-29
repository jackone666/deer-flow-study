"""Sandbox 中间件:在 Agent 执行上下文中按需获取与释放沙箱。

本模块实现了 LangChain :class:`AgentMiddleware`,负责把沙箱生命周期接入 Agent
的 ``before_agent`` / ``after_agent`` 钩子,使 Agent 在同一线程多次轮转中复用沙箱。
"""

import asyncio
import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import SandboxState, ThreadDataState
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """与 ``ThreadState`` schema 兼容的中间件状态。

    Attributes:
        sandbox: 当前线程关联的沙箱状态,可为 None。
        thread_data: 当前线程的运行时数据,可为 None。
    """

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """为 Agent 创建并分配沙箱环境的中间件。

    生命周期:
    - ``lazy_init=True``(默认):首次工具调用时才获取沙箱
    - ``lazy_init=False``:在 ``before_agent`` 时立即获取沙箱
    - 同一线程内多轮对话复用同一沙箱
    - 不会在每次 Agent 调用后释放沙箱,以避免不必要的重建
    - 最终清理发生在应用关闭时,通过 :meth:`SandboxProvider.shutdown` 进行
    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """初始化沙箱中间件。

        Args:
            lazy_init: 为 True 时延迟到首次工具调用才获取沙箱;为 False 时在
                ``before_agent`` 阶段就立即获取。默认为 True 以获得最优性能。
        """
        super().__init__()
        self._lazy_init = lazy_init

    def _acquire_sandbox(self, thread_id: str) -> str:
        """同步获取沙箱并记录日志。

        Args:
            thread_id: 线程 ID。

        Returns:
            新获取的沙箱 ID。
        """
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _acquire_sandbox_async(self, thread_id: str) -> str:
        """异步获取沙箱并记录日志。

        Args:
            thread_id: 线程 ID。

        Returns:
            新获取的沙箱 ID。
        """
        provider = get_sandbox_provider()
        sandbox_id = await provider.acquire_async(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    async def _release_sandbox_async(self, sandbox_id: str) -> None:
        """在工作线程中释放沙箱(避免阻塞事件循环)。"""
        await asyncio.to_thread(get_sandbox_provider().release, sandbox_id)

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        """Agent 启动前的钩子,在 eager 模式下获取沙箱。

        Args:
            state: 当前中间件状态。
            runtime: LangGraph 运行时。

        Returns:
            当需要写入沙箱分配结果时返回对应字典;否则返回 None。
        """
        # 懒加载模式下不在 Agent 启动时创建 sandbox，等首次文件/bash 工具调用再申请。
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # eager 模式保持旧行为：Agent 启动前立即申请 sandbox。
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                return super().before_agent(state, runtime)
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)

    @override
    async def abefore_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        """异步版 :meth:`before_agent`,走异步提供者钩子以避免阻塞事件循环。

        Args:
            state: 当前中间件状态。
            runtime: LangGraph 运行时。

        Returns:
            需要写入沙箱分配结果时返回对应字典,否则返回 None。
        """
        # 懒加载模式下不在 Agent 启动时创建 sandbox，等首次文件/bash 工具调用再申请。
        if self._lazy_init:
            return await super().abefore_agent(state, runtime)

        # eager 模式保持旧行为；异步路径必须使用 provider 的 async hook，
        # 避免容器启动/健康检查阻塞 event loop。
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                return await super().abefore_agent(state, runtime)
            sandbox_id = await self._acquire_sandbox_async(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return await super().abefore_agent(state, runtime)

    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        """Agent 结束后的钩子,释放沙箱(仅在 eager 模式下有意义)。

        Args:
            state: 当前中间件状态。
            runtime: LangGraph 运行时。

        Returns:
            无返回值。
        """
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None

        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            get_sandbox_provider().release(sandbox_id)
            return None

        # 当前轮没有实际申请 sandbox，交给父类继续处理。
        return super().after_agent(state, runtime)

    @override
    async def aafter_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        """异步版 :meth:`after_agent`,在事件循环外释放沙箱。"""
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            await self._release_sandbox_async(sandbox_id)
            return None

        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            await self._release_sandbox_async(sandbox_id)
            return None

        # 当前轮没有实际申请 sandbox，交给父类继续处理。
        return await super().aafter_agent(state, runtime)
