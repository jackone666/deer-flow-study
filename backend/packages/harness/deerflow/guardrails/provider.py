"""GuardrailProvider 协议及数据结构，用于工具调用前的授权。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GuardrailRequest:
    """每次工具调用都会传给 Provider 的上下文。

    Attributes:
        tool_name: 待调用的工具名称。
        tool_input: 工具调用的入参（关键字参数）。
        agent_id: 发起调用的 agent 标识。
        thread_id: LangGraph 线程 ID。
        is_subagent: 是否由 subagent 发起。
        timestamp: 评估发生时的 ISO 时间戳。
    """

    tool_name: str
    tool_input: dict[str, Any]
    agent_id: str | None = None
    thread_id: str | None = None
    is_subagent: bool = False
    timestamp: str = ""


@dataclass
class GuardrailReason:
    """allow/deny 决策的结构化原因（对应 OAP reason 对象）。

    Attributes:
        code: 原因编码，如 ``"oap.tool_not_allowed"``。
        message: 人类可读的详细描述。
    """

    code: str
    message: str = ""


@dataclass
class GuardrailDecision:
    """Provider 输出的 allow/deny 决策（与 OAP Decision 对象对齐）。

    Attributes:
        allow: 是否放行该工具调用。
        reasons: 决策所依据的原因列表，按评估顺序排列。
        policy_id: 命中的策略标识，便于审计追溯。
        metadata: 额外附加元数据，供上层日志/审计使用。
    """

    allow: bool
    reasons: list[GuardrailReason] = field(default_factory=list)
    policy_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GuardrailProvider(Protocol):
    """可插拔工具调用授权的协议。

    任何实现了下述方法的类都可作为 Provider——无需继承某个基类。Provider
    会通过类路径（``resolve_variable()``）按需加载，与 DeerFlow 加载模型、
    工具、sandbox 的机制保持一致。
    """

    name: str

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """同步评估该工具调用是否应被放行。

        Args:
            request: 工具调用上下文，参见 :class:`GuardrailRequest`。

        Returns:
            评估决策，参见 :class:`GuardrailDecision`。
        """
        ...

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """异步版本，与 :meth:`evaluate` 行为等价。

        Args:
            request: 工具调用上下文，参见 :class:`GuardrailRequest`。

        Returns:
            评估决策，参见 :class:`GuardrailDecision`。
        """
        ...
