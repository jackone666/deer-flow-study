"""用于记忆机制的中间件。

在每次 Agent 完成后将筛选后的会话入队等待 LLM 记忆更新：

```
Agent 完成一轮对话（after_agent 钩子触发）
       ↓
1. 检查 memory.enabled → 否 → 跳过
2. 获取 thread_id（runtime.context → config.configurable 回退）
3. filter_messages_for_memory() → 仅保留人类输入 + 最终 AI 回复
4. 检查是否同时存在用户消息和 AI 回复 → 否 → 跳过
5. detect_correction() / detect_reinforcement() → 信号检测
6. get_effective_user_id() → user_id（在 ContextVar 存活时捕获）
7. queue.add(thread_id, messages, user_id=...) → 入队去抖
       ↓
30s 后队列触发 → MemoryUpdater.update_memory() → LLM 提取事实 → memory.json
```

关键设计决策：
- 使用 ``add()``（非 ``add_nowait()``）：正常对话结束后允许去抖合并，避免高频触发
- user_id 在入队时**立即**捕获（第 99 行），因为 ``threading.Timer`` 在新线程触发时不继承 ContextVar"""


import logging
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.memory.message_processing import detect_correction, detect_reinforcement, filter_messages_for_memory
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import get_effective_user_id

if TYPE_CHECKING:
    from deerflow.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryMiddlewareState(AgentState):
    """与 ``ThreadState`` 模式兼容的状态类型。"""

    pass


class MemoryMiddleware(AgentMiddleware[MemoryMiddlewareState]):
    """在 Agent 执行结束后将会话入队等待记忆更新的中间件。

    该中间件：
    1. 每次 Agent 结束后将会话入队等待记忆更新；
    2. 仅保留用户输入与最终助手回复（忽略工具调用）；
    3. 队列通过去抖将多次更新合并处理；
    4. 通过 LLM 摘要异步更新记忆。
    """

    state_schema = MemoryMiddlewareState

    def __init__(self, agent_name: str | None = None, *, memory_config: "MemoryConfig | None" = None):
        """初始化 ``MemoryMiddleware``。

        Args:
            agent_name: 若提供则按 Agent 隔离存储记忆；为 ``None`` 时使用全局记忆。
            memory_config: 显式传入的记忆配置；省略时回退到全局配置。
        """
        super().__init__()
        self._agent_name = agent_name
        self._memory_config = memory_config

    @override
    def after_agent(self, state: MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """在 Agent 结束后将会话入队等待记忆更新。

        Args:
            state: 当前 Agent 状态。
            runtime: 运行期 context。

        Returns:
            始终返回 ``None``，本中间件不修改状态。
        """
        config = self._memory_config or get_memory_config()
        if not config.enabled:
            return None

        # thread_id 是记忆按会话合并的主键：优先取运行时 context，回退到 LangGraph configurable。
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        if not thread_id:
            logger.debug("No thread_id in context, skipping memory update")
            return None

        # 只从 LangGraph 状态读取消息；本中间件不直接访问前端或事件流。
        messages = state.get("messages", [])
        if not messages:
            logger.debug("No messages in state, skipping memory update")
            return None

        # 记忆只关心用户输入与最终助手回复，工具调用过程由过滤函数统一剔除。
        filtered_messages = filter_messages_for_memory(messages)

        # 至少要有一问一答才值得抽取长期记忆；单边消息没有稳定语义。
        user_messages = [m for m in filtered_messages if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered_messages if getattr(m, "type", None) == "ai"]

        if not user_messages or not assistant_messages:
            return None

        # 检测用户纠偏/正向反馈，作为 MemoryUpdater 调整事实置信度和类别的提示信号。
        correction_detected = detect_correction(filtered_messages)
        reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)
        # user_id 必须在入队时捕获：threading.Timer 会切到新线程，ContextVar 不会自动传播。
        user_id = get_effective_user_id()
        queue = get_memory_queue()
        queue.add(
            thread_id=thread_id,
            messages=filtered_messages,
            agent_name=self._agent_name,
            user_id=user_id,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
        )

        return None
