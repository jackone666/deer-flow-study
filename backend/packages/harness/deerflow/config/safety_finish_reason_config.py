"""SafetyFinishReasonMiddleware 的配置。

结构与 GuardrailsConfig 镜像：detector 通过 ``deerflow.reflection.resolve_variable``
按类路径加载（与 ``guardrails.provider`` 配置使用同一加载器），从而用户
可以在不修改核心代码的前提下插入自定义 provider detector。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SafetyDetectorConfig(BaseModel):
    """``safety_finish_reason.detectors`` 下的一项 detector 配置。"""

    use: str = Field(
        description=("SafetyTerminationDetector 实现的类路径，如 'deerflow.agents.middlewares.safety_termination_detectors:OpenAICompatibleContentFilterDetector'。"),
    )
    config: dict = Field(
        default_factory=dict,
        description="传给 detector 类的构造参数。",
    )


class SafetyFinishReasonConfig(BaseModel):
    """SafetyFinishReasonMiddleware 的配置。

    该中间件用于拦截 provider 因安全原因（如 OpenAI ``finish_reason='content_filter'``）
    中断 AIMessage 但仍返回了 tool call 的情况，并抑制这些 tool call，
    避免半截的参数被实际执行。

    Attributes:
        enabled: 是否启用该中间件。
        detectors: 自定义 detector 列表；为 ``None`` 时使用内置集合。
    """

    enabled: bool = Field(
        default=True,
        description="SafetyFinishReasonMiddleware 的总开关。",
    )
    detectors: list[SafetyDetectorConfig] | None = Field(
        default=None,
        description=(
            "自定义 detector 列表。留空 (None) 时使用内置集合（覆盖 OpenAI 兼容的 content_filter、"
            "Anthropic refusal 以及 Gemini 的 SAFETY/BLOCKLIST/PROHIBITED_CONTENT/SPII/RECITATION）。"
            "提供非空列表将完全覆盖默认集合。"
        ),
    )
