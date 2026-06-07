"""技能子包:把可复用的 SKILL.md 资源加载、校验、扫描、安装并提供给 Agent。

本子包封装了:
- 技能元数据的数据类与分类枚举
- YAML frontmatter 解析与校验
- 技能安装(公开/自定义目录)与文件锁
- 技能内容安全扫描
- 工具策略与权限调整
- 技能存储抽象与本地实现
"""

from __future__ import annotations

from .installer import SkillAlreadyExistsError, SkillSecurityScanError
from .storage import LocalSkillStorage, SkillStorage, get_or_new_skill_storage
from .types import Skill
from .validation import ALLOWED_FRONTMATTER_PROPERTIES, _validate_skill_frontmatter

__all__ = [
    "Skill",
    "ALLOWED_FRONTMATTER_PROPERTIES",
    "_validate_skill_frontmatter",
    "SkillAlreadyExistsError",
    "SkillSecurityScanError",
    "SkillStorage",
    "LocalSkillStorage",
    "get_or_new_skill_storage",
]
