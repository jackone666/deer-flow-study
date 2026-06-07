"""GuardrailMiddleware —— 在工具执行前通过 GuardrailProvider 评估授权。"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.guardrails.provider import GuardrailDecision, GuardrailProvider, GuardrailReason, GuardrailRequest

logger = logging.getLogger(__name__)


class GuardrailMiddleware(AgentMiddleware[AgentState]):
    """在工具执行前通过 GuardrailProvider 评估授权。

    被拒绝的工具调用会以错误 :class:`ToolMessage` 的形式返回，让 agent
    自行调整策略。若 Provider 自身抛出异常，行为由 :attr:`fail_closed`
    决定：

    - ``True``（默认）：拒绝该调用，避免「无授权就放行」；
    - ``False``：记录告警后放行。
    """

    def __init__(self, provider: GuardrailProvider, *, fail_closed: bool = True, passport: str | None = None):
        """初始化中间件。

        Args:
            provider: 实际执行授权决策的 Provider。
            fail_closed: Provider 抛错时是否拒绝调用（默认拒绝）。
            passport: 在生成的 :class:`GuardrailRequest` 中作为
                ``agent_id`` 上报，便于审计追溯。
        """
        self.provider = provider
        self.fail_closed = fail_closed
        self.passport = passport

    def _build_request(self, request: ToolCallRequest) -> GuardrailRequest:
        """从 LangGraph 的工具调用请求构造 :class:`GuardrailRequest`。

        Args:
            request: LangGraph 包装的工具调用请求。

        Returns:
            填充好工具名、入参、agent_id 与时间戳的 GuardrailRequest。
        """
        return GuardrailRequest(
            tool_name=str(request.tool_call.get("name", "")),
            tool_input=request.tool_call.get("args", {}),
            agent_id=self.passport,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _build_denied_message(self, request: ToolCallRequest, decision: GuardrailDecision) -> ToolMessage:
        """为被拒绝的调用构造错误 :class:`ToolMessage` 响应。

        Args:
            request: LangGraph 包装的工具调用请求。
            decision: Provider 输出的拒绝决策。

        Returns:
            带有拒绝原因、状态置为 ``error`` 的 :class:`ToolMessage`。
        """
        tool_name = str(request.tool_call.get("name", "unknown_tool"))
        tool_call_id = str(request.tool_call.get("id", "missing_id"))
        reason_text = decision.reasons[0].message if decision.reasons else "blocked by guardrail policy"
        reason_code = decision.reasons[0].code if decision.reasons else "oap.denied"
        return ToolMessage(
            content=f"Guardrail denied: tool '{tool_name}' was blocked ({reason_code}). Reason: {reason_text}. Choose an alternative approach.",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步路径：在调用实际工具前先经 Guardrail 评估。

        Args:
            request: LangGraph 包装的工具调用请求。
            handler: 真正执行工具调用的下游 handler。

        Returns:
            通过评估时透传 ``handler`` 的结果；被拒绝时返回错误
            :class:`ToolMessage`。
        """
        gr = self._build_request(request)
        try:
            decision = self.provider.evaluate(gr)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception:
            logger.exception("Guardrail provider error (sync)")
            if self.fail_closed:
                decision = GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.evaluator_error", message="guardrail provider error (fail-closed)")])
            else:
                return handler(request)
        if not decision.allow:
            logger.warning("Guardrail denied: tool=%s policy=%s code=%s", gr.tool_name, decision.policy_id, decision.reasons[0].code if decision.reasons else "unknown")
            return self._build_denied_message(request, decision)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步路径：与 :meth:`wrap_tool_call` 等价的协程版本。

        Args:
            request: LangGraph 包装的工具调用请求。
            handler: 真正执行工具调用的下游异步 handler。

        Returns:
            通过评估时透传 ``handler`` 的结果；被拒绝时返回错误
            :class:`ToolMessage`。
        """
        gr = self._build_request(request)
        try:
            decision = await self.provider.aevaluate(gr)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception:
            logger.exception("Guardrail provider error (async)")
            if self.fail_closed:
                decision = GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.evaluator_error", message="guardrail provider error (fail-closed)")])
            else:
                return await handler(request)
        if not decision.allow:
            logger.warning("Guardrail denied: tool=%s policy=%s code=%s", gr.tool_name, decision.policy_id, decision.reasons[0].code if decision.reasons else "unknown")
            return self._build_denied_message(request, decision)
        return await handler(request)
