"""模型（Model）相关配置。"""

from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """单个模型的配置段。

    Attributes:
        name: 模型唯一名称。
        display_name: 展示用名称（可选）。
        description: 模型描述（可选）。
        use: 模型 provider 的类路径，如 ``langchain_openai.ChatOpenAI``。
        model: 实际模型名。
        use_responses_api: 是否将 OpenAI ChatOpenAI 调用路由到 ``/v1/responses`` API。
        output_version: OpenAI 响应内容的结构化输出版本，如 ``responses/v1``。
        supports_thinking: 模型是否支持 thinking。
        supports_reasoning_effort: 模型是否支持 reasoning effort。
        when_thinking_enabled: thinking 启用时附加传给模型的参数。
        when_thinking_disabled: thinking 关闭时附加传给模型的参数。
        supports_vision: 模型是否支持视觉/图像输入。
        thinking: thinking 设置的简写；与 ``when_thinking_enabled`` 同时存在时会被合并。
    """

    name: str = Field(..., description="模型的唯一名称")
    display_name: str | None = Field(..., default_factory=lambda: None, description="模型的展示名称")
    description: str | None = Field(..., default_factory=lambda: None, description="模型的描述")
    use: str = Field(
        ...,
        description="模型 provider 的类路径（如 ``langchain_openai.ChatOpenAI``）。",
    )
    model: str = Field(..., description="模型名")
    model_config = ConfigDict(extra="allow")
    use_responses_api: bool | None = Field(
        default=None,
        description="是否将 OpenAI ChatOpenAI 调用路由到 /v1/responses API。",
    )
    output_version: str | None = Field(
        default=None,
        description="OpenAI 响应内容的结构化输出版本，如 responses/v1。",
    )
    supports_thinking: bool = Field(default_factory=lambda: False, description="模型是否支持 thinking")
    supports_reasoning_effort: bool = Field(default_factory=lambda: False, description="模型是否支持 reasoning effort")
    when_thinking_enabled: dict | None = Field(
        default_factory=lambda: None,
        description="thinking 启用时附加传给模型的设置。",
    )
    when_thinking_disabled: dict | None = Field(
        default_factory=lambda: None,
        description="thinking 关闭时附加传给模型的设置。",
    )
    supports_vision: bool = Field(default_factory=lambda: False, description="模型是否支持视觉/图像输入")
    thinking: dict | None = Field(
        default_factory=lambda: None,
        description=(
            "thinking 设置；如提供，将在 thinking 启用时传给模型。"
            "这是 when_thinking_enabled 的简写形式；二者同时存在时会被合并。"
        ),
    )
