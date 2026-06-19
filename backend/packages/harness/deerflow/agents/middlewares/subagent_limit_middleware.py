"""限制每个模型响应中并发 subagent 工具调用数量的中间件。

比 Prompt 约束更可靠的硬限制。当 LLM 单次响应中产生超过 ``max_concurrent`` 个
``task`` 工具调用时，只保留前 N 个，其余静默丢弃。

示例（max_concurrent=3）：

```python
# 模型响应包含 5 个 task 调用
AIMessage(tool_calls=[
    {"name":"task", "args":{...}, "id":"t1"},  # ✅ 保留
    {"name":"task", "args":{...}, "id":"t2"},  # ✅ 保留
    {"name":"task", "args":{...}, "id":"t3"},  # ✅ 保留
    {"name":"task", "args":{...}, "id":"t4"},  # ❌ 丢弃
    {"name":"task", "args":{...}, "id":"t5"},  # ❌ 丢弃
])

# after_model 后：
AIMessage(tool_calls=[t1, t2, t3])  # 只剩前3个
# 日志：Truncated 2 excess task tool call(s) from model response (limit: 3)
```

非 ``task`` 类型的 tool_call 不参与计数、不会被截断。"""


import logging
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS

logger = logging.getLogger(__name__)

# Valid range for max_concurrent_subagents
MIN_SUBAGENT_LIMIT = 2
MAX_SUBAGENT_LIMIT = 4


def _clamp_subagent_limit(value: int) -> int:
    """将子代理上限裁剪到合法范围 ``[2, 4]``。"""
    return max(MIN_SUBAGENT_LIMIT, min(MAX_SUBAGENT_LIMIT, value))


class SubagentLimitMiddleware(AgentMiddleware[AgentState]):
    """截断单次模型响应中超出上限的 ``task`` 工具调用。

    当 LLM 在一次响应中生成超过 ``max_concurrent`` 个并行的 ``task`` 调用时，
    该中间件只保留前 ``max_concurrent`` 个、丢弃其余。这比基于 Prompt 的限制
    更加可靠。

    Args:
        max_concurrent: 允许的最大并发子代理调用数，默认
            ``MAX_CONCURRENT_SUBAGENTS``（3），会被裁剪到 ``[2, 4]``。
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SUBAGENTS):
        """初始化中间件，裁剪 *max_concurrent* 到合法范围。"""
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)

    def _truncate_task_calls(self, state: AgentState) -> dict | None:
        """截断最后一次 AIMessage 中超出上限的 ``task`` 调用。"""
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None

        # Count task tool calls
        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if len(task_indices) <= self.max_concurrent:
            return None

        # Build set of indices to drop (excess task calls beyond the limit)
        indices_to_drop = set(task_indices[self.max_concurrent :])
        truncated_tool_calls = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]

        dropped_count = len(indices_to_drop)
        logger.warning(f"Truncated {dropped_count} excess task tool call(s) from model response (limit: {self.max_concurrent})")

        # Replace the AIMessage with truncated tool_calls (same id triggers replacement)
        updated_msg = clone_ai_message_with_tool_calls(last_msg, truncated_tool_calls)
        return {"messages": [updated_msg]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """模型调用后同步钩子。"""
        return self._truncate_task_calls(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """模型调用后异步钩子。"""
        return self._truncate_task_calls(state)
