"""通过 tool_search 进行延迟工具加载的配置。"""

from pydantic import BaseModel, Field


class ToolSearchConfig(BaseModel):
    """通过 tool_search 进行延迟工具加载的配置。

    启用后，MCP 工具不会被直接加载进 agent 上下文，而是以名称列表的形式
    出现在系统提示中，由 agent 在运行时通过 tool_search 工具按需发现并调用。
    """

    enabled: bool = Field(
        default=False,
        description="是否延迟加载工具并启用 tool_search。",
    )


_tool_search_config: ToolSearchConfig | None = None


def get_tool_search_config() -> ToolSearchConfig:
    """获取 tool search 配置；必要时进行惰性初始化。

    Returns:
        ToolSearchConfig: 进程级单例配置对象。
    """
    global _tool_search_config
    if _tool_search_config is None:
        _tool_search_config = ToolSearchConfig()
    return _tool_search_config


def load_tool_search_config_from_dict(data: dict) -> ToolSearchConfig:
    """从字典加载 tool search 配置（在 AppConfig 加载阶段被调用）。

    Args:
        data: 符合 :class:`ToolSearchConfig` 字段的字典。

    Returns:
        ToolSearchConfig: 加载后写入并返回的配置对象。
    """
    global _tool_search_config
    _tool_search_config = ToolSearchConfig.model_validate(data)
    return _tool_search_config
