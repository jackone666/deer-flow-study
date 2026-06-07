"""自动 thread 标题生成相关配置。"""

from pydantic import BaseModel, Field


class TitleConfig(BaseModel):
    """自动 thread 标题生成配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用自动标题生成",
    )
    max_words: int = Field(
        default=6,
        ge=1,
        le=20,
        description="生成标题的最大单词数",
    )
    max_chars: int = Field(
        default=60,
        ge=10,
        le=200,
        description="生成标题的最大字符数",
    )
    model_name: str | None = Field(
        default=None,
        description="用于标题生成的模型名（None 表示使用默认模型）",
    )
    prompt_template: str = Field(
        default=("Generate a concise title (max {max_words} words) for this conversation.\nUser: {user_msg}\nAssistant: {assistant_msg}\n\nReturn ONLY the title, no quotes, no explanation."),
        description="用于标题生成的 prompt 模板",
    )


# 全局配置实例
_title_config: TitleConfig = TitleConfig()


def get_title_config() -> TitleConfig:
    """获取当前标题配置。

    Returns:
        TitleConfig: 进程级单例配置对象。
    """
    return _title_config


def set_title_config(config: TitleConfig) -> None:
    """设置标题配置。

    Args:
        config: 新的配置对象。
    """
    global _title_config
    _title_config = config


def load_title_config_from_dict(config_dict: dict) -> None:
    """从字典加载标题配置。

    Args:
        config_dict: 符合 :class:`TitleConfig` 字段的字典。
    """
    global _title_config
    _title_config = TitleConfig(**config_dict)


def reset_title_config() -> None:
    """将标题配置恢复为 ``TitleConfig()`` 默认值。

    对外暴露的测试 API：测试用例无需直接访问私有模块属性 ``_title_config``。
    ``AppConfig.from_file()`` 会调用 :func:`load_title_config_from_dict` 永久
    修改单例；需要在用例间获得干净状态的测试应调用本函数。
    """
    global _title_config
    _title_config = TitleConfig()
