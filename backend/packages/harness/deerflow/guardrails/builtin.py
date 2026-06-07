"""DeerFlow 自带的内置 Guardrail Provider。

目前只包含 :class:`AllowlistProvider`，一个不依赖任何外部库的轻量级
允许/拒绝名单实现，可作为自定义 Provider 的参考示例。
"""

from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest


class AllowlistProvider:
    """简单的允许名单/拒绝名单 Provider，不依赖任何外部库。

    - 当传入 ``allowed_tools`` 时，只有集合内的工具会被放行；
    - 当传入 ``denied_tools`` 时，其中的工具会一律被拒绝。
    - 两类名单同时存在时，先校验 allowlist，再校验 denylist。
    """

    name = "allowlist"

    def __init__(self, *, allowed_tools: list[str] | None = None, denied_tools: list[str] | None = None):
        """初始化 Provider。

        Args:
            allowed_tools: 允许调用的工具名列表；为 ``None`` 时不限制。
            denied_tools: 拒绝调用的工具名列表；为 ``None`` 时视为空集。
        """
        self._allowed = set(allowed_tools) if allowed_tools else None
        self._denied = set(denied_tools) if denied_tools else set()

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """同步评估工具调用是否放行。

        Args:
            request: 工具调用上下文。

        Returns:
            允许名单/拒绝名单比对后的决策。
        """
        if self._allowed is not None and request.tool_name not in self._allowed:
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' not in allowlist")])
        if request.tool_name in self._denied:
            return GuardrailDecision(allow=False, reasons=[GuardrailReason(code="oap.tool_not_allowed", message=f"tool '{request.tool_name}' is denied")])
        return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.allowed")])

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """异步评估工具调用是否放行。

        直接复用同步逻辑。

        Args:
            request: 工具调用上下文。

        Returns:
            与 :meth:`evaluate` 相同的决策对象。
        """
        return self.evaluate(request)
