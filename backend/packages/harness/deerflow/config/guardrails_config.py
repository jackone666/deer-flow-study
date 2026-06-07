"""工具调用前的授权（guardrail）相关配置。"""

from pydantic import BaseModel, Field


class GuardrailProviderConfig(BaseModel):
    """单个 guardrail provider 的配置。"""

    use: str = Field(description="类路径，如 'deerflow.guardrails.builtin:AllowlistProvider'")
    config: dict = Field(default_factory=dict, description="以 kwargs 形式传入的 provider 专属设置")


class GuardrailsConfig(BaseModel):
    """工具调用前的授权（guardrail）配置。

    启用后，每次 tool call 都会先经过配置的 provider 才执行。
    Provider 接收工具名、参数以及 agent 的 passport 引用，并返回 allow/deny 决策。

    Attributes:
        enabled: 是否启用 guardrail 中间件。
        fail_closed: provider 异常时是否阻断工具调用。
        passport: OAP passport 路径或托管 agent ID。
        provider: guardrail provider 配置。
    """

    enabled: bool = Field(default=False, description="是否启用 guardrail 中间件")
    fail_closed: bool = Field(default=True, description="provider 报错时是否阻断工具调用")
    passport: str | None = Field(default=None, description="OAP passport 路径或托管 agent ID")
    provider: GuardrailProviderConfig | None = Field(default=None, description="guardrail provider 配置")


_guardrails_config: GuardrailsConfig | None = None


def get_guardrails_config() -> GuardrailsConfig:
    """获取 guardrails 配置；未加载时返回默认值。

    Returns:
        GuardrailsConfig: 进程级单例配置对象。
    """
    global _guardrails_config
    if _guardrails_config is None:
        _guardrails_config = GuardrailsConfig()
    return _guardrails_config


def load_guardrails_config_from_dict(data: dict) -> GuardrailsConfig:
    """从字典加载 guardrails 配置（在 AppConfig 加载阶段被调用）。

    Args:
        data: 符合 :class:`GuardrailsConfig` 字段的字典。

    Returns:
        GuardrailsConfig: 加载并写入单例后的配置对象。
    """
    global _guardrails_config
    _guardrails_config = GuardrailsConfig.model_validate(data)
    return _guardrails_config


def reset_guardrails_config() -> None:
    """重置缓存的配置实例。供测试使用，避免单例在不同用例间泄漏。"""
    global _guardrails_config
    _guardrails_config = None
