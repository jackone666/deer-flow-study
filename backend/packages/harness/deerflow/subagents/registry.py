"""子 Agent 注册表:管理可用子 Agent 的查询与覆盖。"""

import logging
from dataclasses import replace
from typing import Any

from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def _resolve_subagents_app_config(app_config: Any | None = None):
    """把传入的 ``app_config`` 统一规整为子 Agent 段配置对象。"""
    if app_config is None:
        from deerflow.config.subagents_config import get_subagents_app_config

        return get_subagents_app_config()
    return getattr(app_config, "subagents", app_config)


def _build_custom_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """基于 ``config.yaml`` 的 ``custom_agents`` 段构造 :class:`SubagentConfig`。

    Args:
        name: 自定义子 Agent 名。
        app_config: 可选 :class:`AppConfig` 或 :class:`SubagentsAppConfig`,用于解析覆盖。

    Returns:
        在 ``custom_agents`` 中找到时返回对应 :class:`SubagentConfig`,否则返回 None。
    """
    subagents_config = _resolve_subagents_app_config(app_config)
    custom = subagents_config.custom_agents.get(name)
    if custom is None:
        return None

    return SubagentConfig(
        name=name,
        description=custom.description,
        system_prompt=custom.system_prompt,
        tools=custom.tools,
        disallowed_tools=custom.disallowed_tools,
        skills=custom.skills,
        model=custom.model,
        max_turns=custom.max_turns,
        timeout_seconds=custom.timeout_seconds,
    )


def get_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """按名字获取子 Agent 配置,会应用 ``config.yaml`` 覆盖。

    解析顺序(与 Codex 的配置分层一致):
    1. 内置子 Agent(``general-purpose``、``bash``)
    2. ``config.yaml`` 中 ``custom_agents`` 段的自定义子 Agent
    3. ``config.yaml`` 中 ``agents`` 段的按名覆盖(timeout、max_turns、model、skills)

    Args:
        name: 子 Agent 名。
        app_config: 可选 :class:`AppConfig` 或 :class:`SubagentsAppConfig`。

    Returns:
        应用覆盖后的 :class:`SubagentConfig`;未找到时返回 None。
    """
    # Step 1: Look up built-in, then fall back to custom_agents
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)
    if config is None:
        return None

    # Step 2: Apply per-agent overrides from config.yaml agents section.
    # Only explicit per-agent overrides are applied here. Global defaults
    # (timeout_seconds, max_turns at the top level) apply to built-in agents
    # but must NOT override custom agents' own values — custom agents define
    # their own defaults in the custom_agents section.
    subagents_config = _resolve_subagents_app_config(app_config)
    is_builtin = name in BUILTIN_SUBAGENTS
    agent_override = subagents_config.agents.get(name)

    overrides = {}

    # Timeout: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.timeout_seconds is not None:
        if agent_override.timeout_seconds != config.timeout_seconds:
            logger.debug("Subagent '%s': timeout overridden (%ss -> %ss)", name, config.timeout_seconds, agent_override.timeout_seconds)
            overrides["timeout_seconds"] = agent_override.timeout_seconds
    elif is_builtin and subagents_config.timeout_seconds != config.timeout_seconds:
        logger.debug("Subagent '%s': timeout from global default (%ss -> %ss)", name, config.timeout_seconds, subagents_config.timeout_seconds)
        overrides["timeout_seconds"] = subagents_config.timeout_seconds

    # Max turns: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.max_turns is not None:
        if agent_override.max_turns != config.max_turns:
            logger.debug("Subagent '%s': max_turns overridden (%s -> %s)", name, config.max_turns, agent_override.max_turns)
            overrides["max_turns"] = agent_override.max_turns
    elif is_builtin and subagents_config.max_turns is not None and subagents_config.max_turns != config.max_turns:
        logger.debug("Subagent '%s': max_turns from global default (%s -> %s)", name, config.max_turns, subagents_config.max_turns)
        overrides["max_turns"] = subagents_config.max_turns

    # Model: per-agent override only (no global default for model)
    effective_model = subagents_config.get_model_for(name)
    if effective_model is not None and effective_model != config.model:
        logger.debug("Subagent '%s': model overridden (%s -> %s)", name, config.model, effective_model)
        overrides["model"] = effective_model

    # Skills: per-agent override only (no global default for skills)
    effective_skills = subagents_config.get_skills_for(name)
    if effective_skills is not None and effective_skills != config.skills:
        logger.debug("Subagent '%s': skills overridden (%s -> %s)", name, config.skills, effective_skills)
        overrides["skills"] = effective_skills

    if overrides:
        config = replace(config, **overrides)

    return config


def list_subagents(*, app_config: Any | None = None) -> list[SubagentConfig]:
    """列出全部可用子 Agent 配置(已应用 ``config.yaml`` 覆盖)。

    Args:
        app_config: 可选 :class:`AppConfig` 或 :class:`SubagentsAppConfig`。

    Returns:
        注册表中所有 :class:`SubagentConfig` 列表(内置 + 自定义)。
    """
    configs = []
    for name in get_subagent_names(app_config=app_config):
        config = get_subagent_config(name, app_config=app_config)
        if config is not None:
            configs.append(config)
    return configs


def get_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """获取全部可用子 Agent 名(内置 + 自定义)。

    Args:
        app_config: 可选 :class:`AppConfig` 或 :class:`SubagentsAppConfig`。

    Returns:
        子 Agent 名称列表。
    """
    names = list(BUILTIN_SUBAGENTS.keys())

    # Merge custom_agents from config.yaml
    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in subagents_config.custom_agents:
        if custom_name not in names:
            names.append(custom_name)

    return names


def get_available_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """返回当前运行时实际可暴露的子 Agent 名。

    在不允许主机 bash 的环境下会隐藏 ``bash`` 子 Agent。

    Args:
        app_config: 可选 :class:`AppConfig` 或 :class:`SubagentsAppConfig`。

    Returns:
        对当前沙箱配置可见的子 Agent 名称列表。
    """
    names = get_subagent_names(app_config=app_config)
    try:
        host_bash_allowed = is_host_bash_allowed(app_config) if hasattr(app_config, "sandbox") else is_host_bash_allowed()
    except Exception:
        logger.debug("Could not determine host bash availability; exposing all subagents")
        return names

    if not host_bash_allowed:
        names = [name for name in names if name != "bash"]
    return names
