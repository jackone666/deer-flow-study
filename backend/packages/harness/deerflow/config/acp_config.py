"""ACP（Agent Client Protocol）agent 配置，从 config.yaml 加载。"""

import logging
from collections.abc import Mapping

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ACPAgentConfig(BaseModel):
    """单个 ACP 兼容 agent 的配置。"""

    command: str = Field(description="启动 ACP agent 子进程的命令")
    args: list[str] = Field(default_factory=list, description="附加的命令行参数")
    env: dict[str, str] = Field(default_factory=dict, description="注入到 agent 子进程的环境变量；以 $ 开头的值会从宿主机环境变量解析。")
    description: str = Field(description="agent 能力的描述（会展示在 tool description 中）")
    model: str | None = Field(default=None, description="传递给 agent 的模型提示（可选）")
    auto_approve_permissions: bool = Field(
        default=False,
        description=(
            "为 True 时，DeerFlow 自动批准该 agent 发出的所有 ACP 权限请求"
            "（优先 allow_once 而非 allow_always）。默认为 False 时，所有权限请求都会被拒绝"
            "——此时 agent 必须配置为不发起权限请求即可工作。"
        ),
    )


_acp_agents: dict[str, ACPAgentConfig] = {}


def get_acp_agents() -> dict[str, ACPAgentConfig]:
    """获取当前配置的 ACP agents。

    Returns:
        dict[str, ACPAgentConfig]: agent 名到 :class:`ACPAgentConfig` 的映射；未配置时为空字典。
    """
    return _acp_agents


def load_acp_config_from_dict(config_dict: Mapping[str, Mapping[str, object]] | None) -> None:
    """从字典（通常来自 config.yaml）加载 ACP agent 配置。

    Args:
        config_dict: agent 名到配置字段的映射；``None`` 视为空。
    """
    global _acp_agents
    if config_dict is None:
        config_dict = {}
    _acp_agents = {name: ACPAgentConfig(**cfg) for name, cfg in config_dict.items()}
    logger.info("ACP 配置已加载：%d 个 agent：%s", len(_acp_agents), list(_acp_agents.keys()))
