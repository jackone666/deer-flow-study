"""技能 allowed-tools 策略:聚合并按该策略过滤工具。"""

import logging
from typing import Protocol

from deerflow.skills.types import Skill

logger = logging.getLogger(__name__)


class NamedTool(Protocol):
    """只需要 ``name`` 属性的工具协议,便于泛型过滤。"""

    name: str


def allowed_tool_names_for_skills(skills: list[Skill]) -> set[str] | None:
    """聚合技能中显式声明的 allowed-tools 集合。

    Returns:
        - ``None`` 表示旧式"全允许"行为,只在没有任何技能声明 allowed-tools 时返回。
        - 一旦有任意技能声明该字段,未声明的技能不再"放宽"到全允许,聚合结果仅
          包含显式声明的工具名。
    """
    if not skills:
        return None

    allowed: set[str] = set()
    has_explicit_declaration = False
    for skill in skills:
        if skill.allowed_tools is None:
            continue
        has_explicit_declaration = True
        if not skill.allowed_tools:
            logger.info("Skill %s declared empty allowed-tools", skill.name)
        allowed.update(skill.allowed_tools)

    if not has_explicit_declaration:
        return None
    return allowed


def filter_tools_by_skill_allowed_tools[ToolT: NamedTool](tools: list[ToolT], skills: list[Skill]) -> list[ToolT]:
    """按聚合后的 allowed-tools 集合过滤工具列表。"""
    allowed = allowed_tool_names_for_skills(skills)
    if allowed is None:
        return tools

    return [tool for tool in tools if tool.name in allowed]
