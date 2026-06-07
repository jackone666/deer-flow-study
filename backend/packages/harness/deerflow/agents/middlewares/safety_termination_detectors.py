"""用于检测提供方侧安全终止信号的检测器。

    不同 LLM 提供方通过不同的字段与值来表达「我出于安全原因停止了这轮响应」。
    该模块定义了一个小型策略接口，并提供三个内置检测器，覆盖 DeerFlow
    当前支持的主要提供方。新的提供方（文心、混元、Bedrock 适配器、自研网关……）
    可通过实现 ``SafetyTerminationDetector`` 并通过
    ``config.yaml: safety_finish_reason.detectors`` 接入。

    消费这些检测器的中间件位于 ``safety_finish_reason_middleware.py``。
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage


@dataclass(frozen=True)
class SafetyTermination:
    """已检测到的安全相关终止信号。

    Attributes:
        detector: 触发该结果的检测器名称，用于可观测性，便于运维定位
            哪条提供方规则生效。
        reason_field: 承载信号的消息元数据字段名（如 ``finish_reason``、
            ``stop_reason``）。
        reason_value: 该字段的实际取值（如 ``content_filter``、``refusal``、
            ``SAFETY``）。
        extras: 提供方特定的元数据，可供下游消费者使用（如 Azure OpenAI
            的 ``content_filter_results``、Gemini 的 ``safety_ratings``）。
            检测器可自行选择填充或留空。
    """

    detector: str
    reason_field: str
    reason_value: str
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SafetyTerminationDetector(Protocol):
    """提供方安全终止检测的策略接口。"""


    name: str

    def detect(self, message: AIMessage) -> SafetyTermination | None:
        """若 *message* 表明提供方安全终止则返回 ``SafetyTermination``，否则返回 ``None``。

        实现必须无副作用并能容忍缺失或异常类型的元数据——检测器在每次
        模型响应时都会运行。
        """
        ...


def _get_metadata_value(message: AIMessage, field_name: str) -> str | None:
    """从 ``response_metadata`` 或 ``additional_kwargs`` 中读取字符串型值。

    LangChain 提供方适配器对“停止信号”字段的存放位置并不一致：现代适配器
    多用 ``response_metadata``，但部分老式/透传路径仍通过 ``additional_kwargs``
    暴露。此处按顺序检查两者，仅接受字符串——Pydantic 枚举或字典会被忽略，
    以避免在格式错误的输入上抛错。
    """
    for container_name in ("response_metadata", "additional_kwargs"):
        container = getattr(message, container_name, None) or {}
        if not isinstance(container, dict):
            continue
        value = container.get(field_name)
        if isinstance(value, str) and value:
            return value
    return None


class OpenAICompatibleContentFilterDetector:
    """OpenAI 兼容的 ``content_filter`` 信号。

    覆盖 OpenAI、Azure OpenAI、Moonshot/Kimi、DeepSeek、Mistral、vLLM、
    Qwen（OpenAI 兼容模式）以及任何遵循 OpenAI ``finish_reason`` 约定的适配器。

    部分中国厂商的 OpenAI 兼容网关使用 ``sensitive``、``violation`` 等
    自定义 token，可通过配置中的 ``finish_reasons`` 扩展集合。
    """

    name = "openai_compatible_content_filter"

    def __init__(self, finish_reasons: list[str] | tuple[str, ...] | None = None) -> None:
        """初始化 self。"""
        configured = finish_reasons if finish_reasons is not None else ("content_filter",)
        self._finish_reasons: frozenset[str] = frozenset(r.lower() for r in configured)

    def detect(self, message: AIMessage) -> SafetyTermination | None:
        """执行赋值。
        
                Args:
                    self: 参数说明。
                    message: AIMessage: 参数说明。
        
                Returns:
                    SafetyTermination | None。
        """
        value = _get_metadata_value(message, "finish_reason")
        if value is None or value.lower() not in self._finish_reasons:
            return None

        extras: dict[str, Any] = {}
        # Azure OpenAI ships a structured content_filter_results block; carry it
        # through so operators can see *what* was filtered without re-tracing.
        response_metadata = getattr(message, "response_metadata", None) or {}
        if isinstance(response_metadata, dict):
            filter_results = response_metadata.get("content_filter_results")
            if filter_results:
                extras["content_filter_results"] = filter_results

        return SafetyTermination(
            detector=self.name,
            reason_field="finish_reason",
            reason_value=value,
            extras=extras,
        )


class AnthropicRefusalDetector:
    """Anthropic ``stop_reason == "refusal"`` 信号。

    Anthropic 模型通过专用的 ``stop_reason`` 而非 ``finish_reason`` 表达
    安全拒绝。参考：
    https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/handle-streaming-refusals
    """

    name = "anthropic_refusal"

    def __init__(self, stop_reasons: list[str] | tuple[str, ...] | None = None) -> None:
        """初始化 self。"""
        configured = stop_reasons if stop_reasons is not None else ("refusal",)
        self._stop_reasons: frozenset[str] = frozenset(r.lower() for r in configured)

    def detect(self, message: AIMessage) -> SafetyTermination | None:
        """执行赋值。
        
                Args:
                    self: 参数说明。
                    message: AIMessage: 参数说明。
        
                Returns:
                    SafetyTermination | None。
        """
        value = _get_metadata_value(message, "stop_reason")
        if value is None or value.lower() not in self._stop_reasons:
            return None
        return SafetyTermination(
            detector=self.name,
            reason_field="stop_reason",
            reason_value=value,
        )


class GeminiSafetyDetector:
    """Gemini / Vertex AI 安全相关的 ``finish_reason`` 集合。

    Gemini 使用与 OpenAI 相同的 ``finish_reason`` 字段，但取值是枚举型大写。
    默认集合涵盖了所有“模型因触发安全/黑名单/复述/PII 过滤器而停止”的
    Gemini ``finish_reason``——即伴随返回的 ``tool_calls`` 可能被截断/不可信
    的情况。完整枚举参见：
    https://docs.cloud.google.com/python/docs/reference/aiplatform/latest/google.cloud.aiplatform_v1.types.Candidate.FinishReason

    默认集合中 **有意排除**：
    - ``STOP`` — 正常终止。
    - ``MAX_TOKENS`` — 输出长度截断而非安全过滤（根因与 ``content_filter``
                       类似，但 issue #3028 暂时未纳入；如需可单独暴露）。
    - ``LANGUAGE`` / ``NO_IMAGE`` — 能力不匹配，与安全无关；通常不会有
                                     ``tool_calls`` 伴随。
    - ``MALFORMED_FUNCTION_CALL`` /
      ``UNEXPECTED_TOOL_CALL`` — 工具调用协议错误。``tool_calls`` 在此
                                  也不可靠，但失败类别与安全过滤不同；
                                  应由专用检测器处理以保持可观测性记录清晰。
    - ``OTHER`` / ``IMAGE_OTHER`` /
      ``FINISH_REASON_UNSPECIFIED` — 含义过宽，默认不启用；若提供方
                                      滥用这些值，可通过 ``finish_reasons=``
                                      显式开启。
    """

    name = "gemini_safety"

    _DEFAULT_FINISH_REASONS = (
        # Text safety
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "RECITATION",
        # Image safety (multimodal generation)
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    )

    def __init__(self, finish_reasons: list[str] | tuple[str, ...] | None = None) -> None:
        """初始化 self。"""
        configured = finish_reasons if finish_reasons is not None else self._DEFAULT_FINISH_REASONS
        self._finish_reasons: frozenset[str] = frozenset(r.upper() for r in configured)

    def detect(self, message: AIMessage) -> SafetyTermination | None:
        """执行赋值。
        
                Args:
                    self: 参数说明。
                    message: AIMessage: 参数说明。
        
                Returns:
                    SafetyTermination | None。
        """
        value = _get_metadata_value(message, "finish_reason")
        if value is None or value.upper() not in self._finish_reasons:
            return None

        extras: dict[str, Any] = {}
        response_metadata = getattr(message, "response_metadata", None) or {}
        if isinstance(response_metadata, dict):
            # Gemini surfaces per-category scoring under safety_ratings.
            ratings = response_metadata.get("safety_ratings")
            if ratings:
                extras["safety_ratings"] = ratings

        return SafetyTermination(
            detector=self.name,
            reason_field="finish_reason",
            reason_value=value,
            extras=extras,
        )


def default_detectors() -> list[SafetyTerminationDetector]:
    """未配置自定义检测器时使用的内建检测器集合。"""
    return [
        OpenAICompatibleContentFilterDetector(),
        AnthropicRefusalDetector(),
        GeminiSafetyDetector(),
    ]


__all__ = [
    "AnthropicRefusalDetector",
    "GeminiSafetyDetector",
    "OpenAICompatibleContentFilterDetector",
    "SafetyTermination",
    "SafetyTerminationDetector",
    "default_detectors",
]
