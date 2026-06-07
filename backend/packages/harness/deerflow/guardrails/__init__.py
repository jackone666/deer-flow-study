"""工具调用前的授权中间件。

该子包提供「Guardrail」（护栏）抽象：在 agent 真正执行某个工具调用之前，
先经过可插拔的 :class:`GuardrailProvider` 评估，得到 allow/deny 决策；
:class:`GuardrailMiddleware` 把这一评估嵌入到 LangChain 工具调用链中。
内置实现 :class:`AllowlistProvider` 提供了最简单的允许/拒绝名单。
"""

from deerflow.guardrails.builtin import AllowlistProvider
from deerflow.guardrails.middleware import GuardrailMiddleware
from deerflow.guardrails.provider import GuardrailDecision, GuardrailProvider, GuardrailReason, GuardrailRequest

__all__ = [
    "AllowlistProvider",
    "GuardrailDecision",
    "GuardrailMiddleware",
    "GuardrailProvider",
    "GuardrailReason",
    "GuardrailRequest",
]
