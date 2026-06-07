"""技能（Skills）系统配置。"""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from deerflow.config.runtime_paths import project_root, resolve_path


def _legacy_skills_candidates() -> tuple[Path, ...]:
    """为 monorepo 兼容而返回源码树中的 skills 候选位置。"""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (repo_root / "skills",)


class SkillsConfig(BaseModel):
    """技能系统配置。"""

    use: str = Field(
        default="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        description="SkillStorage 实现的类路径。",
    )
    path: str | None = Field(
        default=None,
        description=("技能目录路径。如未指定，默认为调用方项目根下的 `skills` 目录，并在 monorepo 场景下回退到源码树根的旧位置。"),
    )
    container_path: str = Field(
        default="/mnt/skills",
        description="sandbox 容器中挂载 skills 的路径",
    )

    def get_skills_path(self) -> Path:
        """获取解析后的 skills 目录路径。

        解析顺序：
            1. 显式 ``path`` 字段
            2. ``DEER_FLOW_SKILLS_PATH`` 环境变量
            3. 调用方项目根下的 ``skills`` 目录（``project_root()``）
            4. 为 monorepo 兼容而存在的源码树根目录候选（``_legacy_skills_candidates``）

        当 (3)(4) 在磁盘上都不存在时，仍返回项目根默认路径，
        以便调用方在没有 skills 时也能拿到一个稳定的占位路径而不会抛错。

        Returns:
            Path: 解析后的 skills 目录绝对路径。
        """
        if self.path:
            # 使用显式配置的路径（绝对路径或相对于项目根）
            return resolve_path(self.path)
        if env_path := os.getenv("DEER_FLOW_SKILLS_PATH"):
            return resolve_path(env_path)

        project_default = project_root() / "skills"
        if project_default.is_dir():
            return project_default

        for candidate in _legacy_skills_candidates():
            if candidate.is_dir():
                return candidate

        return project_default

    def get_skill_container_path(self, skill_name: str, category: str = "public") -> str:
        """获取指定 skill 在容器中的完整路径。

        Args:
            skill_name: skill 名称（目录名）。
            category: skill 分类（public 或 custom）。

        Returns:
            str: 容器内指向该 skill 的完整路径。
        """
        return f"{self.container_path}/{category}/{skill_name}"
