"""Subagent 系统配置，从 config.yaml 加载。"""

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SubagentOverrideConfig(BaseModel):
    """针对单个 agent 的配置覆盖。"""

    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="该 subagent 的超时秒数（None 表示使用全局默认）",
    )
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description="该 subagent 的最大轮次（None 表示使用全局或内置默认）",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="该 subagent 的模型名（None 表示继承自父 agent）",
    )
    skills: list[str] | None = Field(
        default=None,
        description="该 subagent 的技能白名单（None 表示继承所有启用的技能，[] 表示不使用任何技能）",
    )


class CustomSubagentConfig(BaseModel):
    """在 config.yaml 中声明的用户自定义 subagent 类型。"""

    description: str = Field(
        description="lead agent 应在何种情况下委派给该 subagent",
    )
    system_prompt: str = Field(
        description="指导 subagent 行为的系统提示",
    )
    tools: list[str] | None = Field(
        default=None,
        description="工具白名单（None 表示继承父 agent 的全部工具）",
    )
    disallowed_tools: list[str] | None = Field(
        default_factory=lambda: ["task", "ask_clarification", "present_files"],
        description="禁用工具列表",
    )
    skills: list[str] | None = Field(
        default=None,
        description="技能白名单（None 表示继承所有启用的技能，[] 表示不使用任何技能）",
    )
    model: str = Field(
        default="inherit",
        description="使用的模型，'inherit' 表示继承父 agent 的模型",
    )
    max_turns: int = Field(
        default=50,
        ge=1,
        description="停止前允许的最大 agent 轮次",
    )
    timeout_seconds: int = Field(
        default=900,
        ge=1,
        description="最大执行时长（秒）",
    )


class SubagentsAppConfig(BaseModel):
    """Subagent 系统配置。"""

    timeout_seconds: int = Field(
        default=900,
        ge=1,
        description="所有 subagent 的默认超时秒数（默认 900 = 15 分钟）",
    )
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description="可选：所有 subagent 的默认最大轮次（None 表示保留内置默认）",
    )
    agents: dict[str, SubagentOverrideConfig] = Field(
        default_factory=dict,
        description="按 agent 名分组的 per-agent 覆盖配置",
    )
    custom_agents: dict[str, CustomSubagentConfig] = Field(
        default_factory=dict,
        description="用户自定义的 subagent 类型，按 agent 名索引",
    )

    def get_timeout_for(self, agent_name: str) -> int:
        """获取指定 agent 的有效超时。

        Args:
            agent_name: subagent 名称。

        Returns:
            int: 超时秒数；若 per-agent 覆盖设置了则使用覆盖值，否则使用全局默认。
        """
        override = self.agents.get(agent_name)
        if override is not None and override.timeout_seconds is not None:
            return override.timeout_seconds
        return self.timeout_seconds

    def get_model_for(self, agent_name: str) -> str | None:
        """获取指定 agent 的模型覆盖。

        Args:
            agent_name: subagent 名称。

        Returns:
            str | None: 覆盖的模型名；未设置覆盖时返回 ``None``（subagent 继承父 agent 的模型）。
        """
        override = self.agents.get(agent_name)
        if override is not None and override.model is not None:
            return override.model
        return None

    def get_max_turns_for(self, agent_name: str, builtin_default: int) -> int:
        """获取指定 agent 的有效最大轮次。

        Args:
            agent_name: subagent 名称。
            builtin_default: 没有任何覆盖时使用的内置默认值。

        Returns:
            int: 优先 per-agent 覆盖，其次全局默认，最后回退到 ``builtin_default``。
        """
        override = self.agents.get(agent_name)
        if override is not None and override.max_turns is not None:
            return override.max_turns
        if self.max_turns is not None:
            return self.max_turns
        return builtin_default

    def get_skills_for(self, agent_name: str) -> list[str] | None:
        """获取指定 agent 的技能覆盖。

        Args:
            agent_name: subagent 名称。

        Returns:
            list[str] | None: 覆盖的技能白名单；未设置时返回 ``None``（subagent 继承所有启用的技能）。
        """
        override = self.agents.get(agent_name)
        if override is not None and override.skills is not None:
            return override.skills
        return None


_subagents_config: SubagentsAppConfig = SubagentsAppConfig()


def get_subagents_app_config() -> SubagentsAppConfig:
    """获取当前 subagent 配置。

    Returns:
        SubagentsAppConfig: 进程级单例配置对象。
    """
    return _subagents_config


def load_subagents_config_from_dict(config_dict: dict) -> None:
    """从字典加载 subagent 配置。

    Args:
        config_dict: 符合 :class:`SubagentsAppConfig` 字段的字典。
    """
    global _subagents_config
    _subagents_config = SubagentsAppConfig(**config_dict)

    overrides_summary = {}
    for name, override in _subagents_config.agents.items():
        parts = []
        if override.timeout_seconds is not None:
            parts.append(f"timeout={override.timeout_seconds}s")
        if override.max_turns is not None:
            parts.append(f"max_turns={override.max_turns}")
        if override.model is not None:
            parts.append(f"model={override.model}")
        if override.skills is not None:
            parts.append(f"skills={override.skills}")
        if parts:
            overrides_summary[name] = ", ".join(parts)

    custom_agents_names = list(_subagents_config.custom_agents.keys())

    if overrides_summary or custom_agents_names:
        logger.info(
            "Subagent 配置已加载：默认 timeout=%ss，默认 max_turns=%s，per-agent 覆盖=%s，custom_agents=%s",
            _subagents_config.timeout_seconds,
            _subagents_config.max_turns,
            overrides_summary or "无",
            custom_agents_names or "无",
        )
