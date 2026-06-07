"""线程数据目录中间件：为每个线程创建隔离的工作、上传与输出目录。"""

import logging
from datetime import UTC, datetime
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


class ThreadDataMiddlewareState(AgentState):
    """与 ``ThreadState`` 模式兼容的状态类型。"""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """为每个线程创建数据目录。

    创建如下目录结构：
    - ``{base_dir}/threads/{thread_id}/user-data/workspace``
    - ``{base_dir}/threads/{thread_id}/user-data/uploads``
    - ``{base_dir}/threads/{thread_id}/user-data/outputs``

    生命周期管理：
    - ``lazy_init=True``（默认）：仅计算路径，目录按需创建。
    - ``lazy_init=False``：在 ``before_agent()`` 中立即创建目录。
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """初始化中间件。

        Args:
            base_dir: 线程数据根目录，缺省时使用 ``Paths`` 解析得到的路径。
            lazy_init: 为 ``True`` 时延迟创建目录直到需要；为 ``False`` 时
                在 ``before_agent()`` 中立即创建。默认 ``True`` 以获得最佳
                性能。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """获取线程数据目录对应的路径。

        Args:
            thread_id: 线程 ID。
            user_id: 可选的用户 ID，用于按用户隔离路径。

        Returns:
            包含 ``workspace_path``、``uploads_path``、``outputs_path`` 的字典。
        """
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

    def _create_thread_directories(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """创建线程数据目录。

        Args:
            thread_id: 线程 ID。
            user_id: 可选的用户 ID，用于按用户隔离路径。

        Returns:
            包含已创建目录路径的字典。
        """
        self._paths.ensure_thread_dirs(thread_id, user_id=user_id)
        return self._get_thread_paths(thread_id, user_id=user_id)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        """Agent 启动前同步钩子，用于在状态中注入初始数据。"""
        context = runtime.context or {}
        thread_id = context.get("thread_id")
        if thread_id is None:
            config = get_config()
            thread_id = config.get("configurable", {}).get("thread_id")

        if thread_id is None:
            raise ValueError("Thread ID is required in runtime context or config.configurable")

        user_id = get_effective_user_id()

        if self._lazy_init:
            # Lazy initialization: only compute paths, don't create directories
            paths = self._get_thread_paths(thread_id, user_id=user_id)
        else:
            # Eager initialization: create directories immediately
            paths = self._create_thread_directories(thread_id, user_id=user_id)
            logger.debug("Created thread data directories for thread %s", thread_id)

        messages = list(state.get("messages", []))
        last_message = messages[-1] if messages else None

        if last_message and isinstance(last_message, HumanMessage):
            messages[-1] = HumanMessage(
                content=last_message.content,
                id=last_message.id,
                name=last_message.name or "user-input",
                additional_kwargs={**last_message.additional_kwargs, "run_id": runtime.context.get("run_id"), "timestamp": datetime.now(UTC).isoformat()},
            )

        return {
            "thread_data": {
                **paths,
            },
            "messages": messages,
        }
