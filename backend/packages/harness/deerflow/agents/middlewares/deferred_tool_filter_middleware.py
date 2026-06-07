"""用于从模型绑定中过滤延迟 tool schema 的中间件。

    当启用 ``tool_search`` 时，MCP 工具仍会传递给 ToolNode 用于执行，
    但在模型通过 ``tool_search`` 发现它们之前，其 schema *不应* 通过
    ``bind_tools`` 发送给 LLM。该中间件会把仍处于延迟状态的 schema
    从模型绑定中移除，使模型只能在显式发现后才能调用它们。
"""


import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


class DeferredToolFilterMiddleware(AgentMiddleware[AgentState]):
    """在工具被提升前，对绑定到模型的 schema 进行隐藏。

    ``ToolNode`` 仍保留所有工具（含延迟工具）用于执行路由，但 LLM 仅能看到
    活跃工具的 schema 以及在当前目录哈希下已被提升的工具（记录于
    ``state["promoted"]``）。
    """

    def __init__(self, deferred_names: frozenset[str], catalog_hash: str | None):
        """初始化延迟工具过滤中间件。

        Args:
            deferred_names: 延迟工具名集合。
            catalog_hash: 当前工具目录哈希，用于作用域控制。
        """
        super().__init__()
        self._deferred = deferred_names
        self._catalog_hash = catalog_hash

    def _promoted(self, state) -> set[str]:
        """根据 state 解析当前已被提升的工具名集合。"""
        promoted = (state or {}).get("promoted")
        if promoted and promoted.get("catalog_hash") == self._catalog_hash:
            return set(promoted.get("names") or [])
        return set()

    def _hidden(self, state) -> set[str]:
        """返回当前仍需对模型隐藏的延迟工具名集合。"""
        return set(self._deferred) - self._promoted(state)

    def _filter_tools(self, request: ModelRequest) -> ModelRequest:
        """从请求中过滤掉需隐藏的延迟工具，再交给后续 handler。"""
        if not self._deferred:
            return request
        hide = self._hidden(request.state)
        if not hide:
            return request
        active = [t for t in request.tools if getattr(t, "name", None) not in hide]
        if len(active) < len(request.tools):
            logger.debug("Filtered %d deferred tool schema(s) from model binding", len(request.tools) - len(active))
        return request.override(tools=active)

    def _blocked_tool_message(self, request: ToolCallRequest) -> ToolMessage | None:
        """若工具调用指向未提升的延迟工具，则返回错误 ToolMessage。"""
        if not self._deferred:
            return None
        name = str(request.tool_call.get("name") or "")
        if not name or name not in self._hidden(request.state):
            return None
        tool_call_id = str(request.tool_call.get("id") or "missing_tool_call_id")
        return ToolMessage(
            content=(f"Error: Tool '{name}' is deferred and has not been promoted yet. Call tool_search first to expose and promote this tool's schema, then retry."),
            tool_call_id=tool_call_id,
            name=name,
            status="error",
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """同步入口：拦截模型调用，必要时修改 ``request`` 后调用 ``handler``。"""
        return handler(self._filter_tools(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步入口：拦截工具调用，按需修改 ``request`` 后调用 ``handler``。"""
        blocked = self._blocked_tool_message(request)
        if blocked is not None:
            return blocked
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """异步入口：拦截模型调用，必要时修改 ``request`` 后 ``await handler``。"""
        return await handler(self._filter_tools(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步入口：拦截工具调用，按需修改 ``request`` 后 ``await handler``。"""
        blocked = self._blocked_tool_message(request)
        if blocked is not None:
            return blocked
        return await handler(request)
