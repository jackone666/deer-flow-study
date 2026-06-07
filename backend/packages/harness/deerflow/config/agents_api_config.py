"""自定义 Agent 管理 API 的配置。"""

from pydantic import BaseModel, Field


class AgentsApiConfig(BaseModel):
    """自定义 Agent 与用户档案管理路由的配置。"""

    enabled: bool = Field(
        default=False,
        description=("是否在 HTTP 上开放自定义 Agent 管理 API。禁用时，gateway 会拒绝读取/写入自定义 Agent 的 SOUL.md、config 以及 USER.md 等 prompt 管理路由。"),
    )


_agents_api_config: AgentsApiConfig = AgentsApiConfig()


def get_agents_api_config() -> AgentsApiConfig:
    """获取当前的 agents API 配置。

    Returns:
        AgentsApiConfig: 进程级单例的当前配置对象。
    """
    return _agents_api_config


def set_agents_api_config(config: AgentsApiConfig) -> None:
    """设置 agents API 配置（覆盖进程级单例）。

    Args:
        config: 新的配置对象。
    """
    global _agents_api_config
    _agents_api_config = config


def load_agents_api_config_from_dict(config_dict: dict) -> None:
    """从字典加载 agents API 配置。

    Args:
        config_dict: 符合 :class:`AgentsApiConfig` 字段定义的字典。
    """
    global _agents_api_config
    _agents_api_config = AgentsApiConfig(**config_dict)
