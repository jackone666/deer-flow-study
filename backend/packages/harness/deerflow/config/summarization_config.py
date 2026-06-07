"""对话摘要（summarization）相关配置。"""

from typing import Literal

from pydantic import BaseModel, Field

ContextSizeType = Literal["fraction", "tokens", "messages"]


class ContextSize(BaseModel):
    """trigger 或 keep 参数使用的上下文大小描述。"""

    type: ContextSizeType = Field(description="上下文大小的类型")
    value: int | float = Field(description="上下文大小的数值")

    def to_tuple(self) -> tuple[ContextSizeType, int | float]:
        """转换为 :class:`SummarizationMiddleware` 期望的元组形式。

        Returns:
            tuple[ContextSizeType, int | float]: ``(type, value)`` 形式的元组。
        """
        return (self.type, self.value)


class SummarizationConfig(BaseModel):
    """自动对话摘要配置。"""

    enabled: bool = Field(
        default=False,
        description="是否启用自动对话摘要",
    )
    model_name: str | None = Field(
        default=None,
        description="用于摘要的模型名（None 表示使用轻量模型）",
    )
    trigger: ContextSize | list[ContextSize] | None = Field(
        default=None,
        description="触发摘要的一个或多个阈值，任一被命中即触发摘要。"
        "示例：{'type': 'messages', 'value': 50} 在 50 条消息时触发；"
        "{'type': 'tokens', 'value': 4000} 在 4000 token 时触发；"
        "{'type': 'fraction', 'value': 0.8} 在模型最大输入 token 的 80% 时触发。",
    )
    keep: ContextSize = Field(
        default_factory=lambda: ContextSize(type="messages", value=20),
        description="摘要后保留的上下文策略，指定保留多少历史。"
        "示例：{'type': 'messages', 'value': 20} 保留 20 条消息；"
        "{'type': 'tokens', 'value': 3000} 保留 3000 token；"
        "{'type': 'fraction', 'value': 0.3} 保留模型最大输入 token 的 30%。",
    )
    trim_tokens_to_summarize: int | None = Field(
        default=4000,
        description="准备摘要消息时保留的最大 token 数；传 null 跳过裁剪。",
    )
    summary_prompt: str | None = Field(
        default=None,
        description="用于生成摘要的自定义 prompt 模板；不提供时使用 LangChain 默认 prompt。",
    )
    preserve_recent_skill_count: int = Field(
        default=5,
        ge=0,
        description="摘要时豁免的最新加载的 skill 文件数；设为 0 禁用 skill 保留。",
    )
    preserve_recent_skill_tokens: int = Field(
        default=25000,
        ge=0,
        description="摘要时为最近加载的 skill 文件预留的总 token 预算。",
    )
    preserve_recent_skill_tokens_per_skill: int = Field(
        default=5000,
        ge=0,
        description="摘要时为单个 skill 文件保留的 token 上限；超过该大小的 skill 读取不会被保留。",
    )
    skill_file_read_tool_names: list[str] = Field(
        default_factory=lambda: ["read_file", "read", "view", "cat"],
        description="在摘要保留最近 skill 时，被视为 skill 文件读取的工具名列表。",
    )


# 全局配置实例
_summarization_config: SummarizationConfig = SummarizationConfig()


def get_summarization_config() -> SummarizationConfig:
    """获取当前摘要配置。

    Returns:
        SummarizationConfig: 进程级单例配置对象。
    """
    return _summarization_config


def set_summarization_config(config: SummarizationConfig) -> None:
    """设置摘要配置。

    Args:
        config: 新的配置对象。
    """
    global _summarization_config
    _summarization_config = config


def load_summarization_config_from_dict(config_dict: dict) -> None:
    """从字典加载摘要配置。

    Args:
        config_dict: 符合 :class:`SummarizationConfig` 字段的字典。
    """
    global _summarization_config
    _summarization_config = SummarizationConfig(**config_dict)
