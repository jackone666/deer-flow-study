"""子 Agent 的配置数据类与模型解析函数。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig


@dataclass
class SubagentConfig:
    """一个子 Agent 的配置。

    Attributes:
        name: 子 Agent 唯一标识。
        description: 何时应将任务委派给该子 Agent。
        system_prompt: 指导子 Agent 行为的系统提示。
        tools: 可选允许的工具名列表;None 时继承父 Agent 的全部工具。
        disallowed_tools: 可选禁用工具名列表。
        skills: 可选加载的技能名列表;None 时继承所有已启用技能,
            空列表表示不加载任何技能。
        model: 使用的模型;``"inherit"`` 表示沿用父 Agent 模型。
        max_turns: 最大 agent 轮转数。
        timeout_seconds: 最大执行时间(秒),默认 900(15 分钟)。
    """

    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    skills: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900


def _default_model_name(app_config: "AppConfig") -> str:
    """当未指定模型时,返回应用配置中的第一个模型名。"""
    if not app_config.models:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")
    return app_config.models[0].name


def resolve_subagent_model_name(config: SubagentConfig, parent_model: str | None, *, app_config: "AppConfig | None" = None) -> str:
    """解析子 Agent 实际应使用的模型名。

    解析顺序:
    1. ``config.model`` 显式非 ``"inherit"`` 时直接使用;
    2. 否则使用 ``parent_model``;
    3. 最后回退到 :func:`_default_model_name` 读取应用配置中的第一个模型。

    Args:
        config: 子 Agent 配置。
        parent_model: 父 Agent 使用的模型名,可能为 None。
        app_config: 可选应用配置,缺省时通过 :func:`get_app_config` 读取。

    Returns:
        解析后的模型名。

    Raises:
        ValueError: 当回退到默认模型时,应用配置中没有任何模型。
    """
    if config.model != "inherit":
        return config.model

    if parent_model is not None:
        return parent_model

    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()
    return _default_model_name(app_config)
