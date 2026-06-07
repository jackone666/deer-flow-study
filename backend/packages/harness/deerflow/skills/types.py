"""技能相关的数据类与分类枚举。"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

SKILL_MD_FILE = "SKILL.md"


class SkillCategory(StrEnum):
    """技能的来源分类。

    - ``PUBLIC``:平台内置的只读技能。
    - ``CUSTOM``:用户自建的可编辑/可删除技能。
    """

    PUBLIC = "public"
    CUSTOM = "custom"


@dataclass
class Skill:
    """代表一个技能,包含元数据与文件路径。"""

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path  # 相对于分类根目录到技能目录的相对路径
    category: SkillCategory  # 'public' 或 'custom'
    allowed_tools: list[str] | None = None
    enabled: bool = False  # 该技能是否启用

    @property
    def skill_path(self) -> str:
        """返回从分类根目录(``skills/{category}``)到该技能目录的相对路径。"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = "/mnt/skills") -> str:
        """获取该技能在容器中的完整目录路径。

        Args:
            container_base_path: 容器中 skills 的挂载根路径,默认 ``/mnt/skills``。

        Returns:
            容器中的完整目录路径。
        """
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(self, container_base_path: str = "/mnt/skills") -> str:
        """获取该技能主文件 SKILL.md 在容器中的完整路径。

        Args:
            container_base_path: 容器中 skills 的挂载根路径,默认 ``/mnt/skills``。

        Returns:
            容器中 SKILL.md 的完整路径。
        """
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        """返回对象的可读字符串表示。"""
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
