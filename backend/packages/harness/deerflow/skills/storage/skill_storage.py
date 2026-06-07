"""抽象 :class:`SkillStorage` 基类,提供模板方法流程。"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from deerflow.skills.types import SKILL_MD_FILE, Skill, SkillCategory  # noqa: F401

logger = logging.getLogger(__name__)

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillStorage(ABC):
    """技能存储后端的抽象基类。

    子类实现一组与存储介质相关的原子操作;本基类提供模板方法流程
    (load_skills、历史记录序列化、路径辅助、校验)以组合协议层辅助函数。
    """

    def __init__(self, container_path: str = "/mnt/skills") -> None:
        """初始化存储基类。

        Args:
            container_path: 容器中 skills 的挂载根路径,默认 ``/mnt/skills``。
        """
        self._container_root = container_path

    # ------------------------------------------------------------------
    # Static protocol helpers (not storage-specific)
    # ------------------------------------------------------------------

    @staticmethod
    def validate_skill_name(name: str) -> str:
        """校验并规范化技能名,返回规范化后的形式。

        Raises:
            ValueError: 名称格式不合法或超过 64 字符。
        """
        normalized = name.strip()
        if not _SKILL_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("Skill name must be hyphen-case using lowercase letters, digits, and hyphens only.")
        if len(normalized) > 64:
            raise ValueError("Skill name must be 64 characters or fewer.")
        return normalized

    @staticmethod
    def validate_relative_path(relative_path: str, base_dir: Path) -> Path:
        """校验 ``relative_path`` 相对于 ``base_dir`` 的安全位置,返回解析后的目标。

        Args:
            relative_path: 相对路径字符串,必须非空。
            base_dir: 基目录,目标须落在其下。

        Returns:
            解析后的绝对路径。

        Raises:
            ValueError: 路径为空或解析后不在 ``base_dir`` 之内。
        """
        if not relative_path:
            raise ValueError("relative_path must not be empty.")
        resolved_base = base_dir.resolve()
        target = (resolved_base / relative_path).resolve()
        try:
            target.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("relative_path must resolve within the skill directory.") from exc
        return target

    @staticmethod
    def validate_skill_markdown_content(name: str, content: str) -> None:
        """校验 SKILL.md 内容:解析 frontmatter 并确认 name 字段一致。

        Args:
            name: 期望的技能名。
            content: 完整的 SKILL.md 内容。

        Raises:
            ValueError: 解析失败或 frontmatter 中的 name 与 ``name`` 不一致。
        """
        import tempfile

        from deerflow.skills.validation import _validate_skill_frontmatter

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_skill_dir = Path(tmp_dir) / SkillStorage.validate_skill_name(name)
            temp_skill_dir.mkdir(parents=True, exist_ok=True)
            (temp_skill_dir / SKILL_MD_FILE).write_text(content, encoding="utf-8")
            is_valid, message, parsed_name = _validate_skill_frontmatter(temp_skill_dir)
            if not is_valid:
                raise ValueError(message)
            if parsed_name != name:
                raise ValueError(f"Frontmatter name '{parsed_name}' must match requested skill name '{name}'.")

    def ensure_safe_support_path(self, name: str, relative_path: str) -> Path:
        """校验并返回技能支持文件的解析后绝对路径。

        Args:
            name: 技能名(连字符形式)。
            relative_path: 相对支持文件路径。

        Returns:
            已校验的绝对路径。

        Raises:
            ValueError: 路径为空、绝对、含穿越段或越出允许的支持子目录。
        """
        _ALLOWED_SUPPORT_SUBDIRS = {"references", "templates", "scripts", "assets"}
        skill_dir = self.get_custom_skill_dir(self.validate_skill_name(name)).resolve()
        if not relative_path or relative_path.endswith("/"):
            raise ValueError("Supporting file path must include a filename.")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("Supporting file path must be relative.")
        if any(part in {"..", ""} for part in relative.parts):
            raise ValueError("Supporting file path must not contain parent-directory traversal.")
        top_level = relative.parts[0] if relative.parts else ""
        if top_level not in _ALLOWED_SUPPORT_SUBDIRS:
            raise ValueError(f"Supporting files must live under one of: {', '.join(sorted(_ALLOWED_SUPPORT_SUBDIRS))}.")
        target = (skill_dir / relative).resolve()
        allowed_root = (skill_dir / top_level).resolve()
        try:
            target.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("Supporting file path must stay within the selected support directory.") from exc
        return target

    # ------------------------------------------------------------------
    # Abstract atomic operations (storage-medium specific)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_skills_root_path(self) -> Path:
        """技能根目录的主机绝对路径,用于沙箱挂载。"""

    @abstractmethod
    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        """为每个 SKILL.md 产出 ``(分类, 分类根目录, SKILL.md 路径)`` 三元组。"""

    @abstractmethod
    def read_custom_skill(self, name: str) -> str:
        """读取自定义技能 SKILL.md 的内容。"""

    @abstractmethod
    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        """原子地把文本写入 ``custom/<name>/<relative_path>``。"""

    @abstractmethod
    async def ainstall_skill_from_archive(self, archive_path: str | Path) -> dict:
        """从 ``.skill`` ZIP 压缩包异步安装一个技能。"""

    def install_skill_from_archive(self, archive_path: str | Path) -> dict:
        """同步包装,内部委托给 :meth:`ainstall_skill_from_archive`。"""
        from deerflow.skills.installer import _run_async_install

        return _run_async_install(self.ainstall_skill_from_archive(archive_path))

    @abstractmethod
    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        """删除自定义技能(校验 + 可选历史 + 目录清理)。"""

    @abstractmethod
    def custom_skill_exists(self, name: str) -> bool:
        """判断自定义技能是否存在。"""

    @abstractmethod
    def public_skill_exists(self, name: str) -> bool:
        """判断公开(内置)技能是否存在。"""

    @abstractmethod
    def append_history(self, name: str, record: dict) -> None:
        """为 ``name`` 追加一条 JSONL 历史记录。"""

    @abstractmethod
    def read_history(self, name: str) -> list[dict]:
        """按时间顺序返回 ``name`` 的全部历史记录。"""

    # ------------------------------------------------------------------
    # Concrete path helpers (layout is part of the SKILL.md protocol)
    # ------------------------------------------------------------------

    def get_container_root(self) -> str:
        """返回容器中 skills 挂载根路径。"""
        return self._container_root

    def get_custom_skill_dir(self, name: str) -> Path:
        """``custom/<name>`` 的路径,不会自动创建目录。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / normalized_name

    def get_custom_skill_file(self, name: str) -> Path:
        """``custom/<name>/SKILL.md`` 的路径。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_custom_skill_dir(normalized_name) / SKILL_MD_FILE

    def get_skill_history_file(self, name: str) -> Path:
        """``custom/.history/<name>.jsonl`` 的路径,不会自动创建父目录。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / ".history" / f"{normalized_name}.jsonl"

    # ------------------------------------------------------------------
    # Final template-method flows
    # ------------------------------------------------------------------

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        """发现所有技能,合并启用状态,排序并按需过滤。

        Args:
            enabled_only: 为 True 时仅保留启用的技能。

        Returns:
            排序后的 :class:`Skill` 列表。
        """
        from deerflow.skills.parser import parse_skill_file

        skills_by_name: dict[str, Skill] = {}
        for category, category_root, md_path in self._iter_skill_files():
            skill = parse_skill_file(
                md_path,
                category=category,
                relative_path=md_path.parent.relative_to(category_root),
            )
            if skill:
                skills_by_name[skill.name] = skill

        skills = list(skills_by_name.values())

        # Merge enabled state from extensions config (re-read every call so
        # changes made by another process are picked up immediately).
        try:
            from deerflow.config.extensions_config import ExtensionsConfig

            extensions_config = ExtensionsConfig.from_file()
            for skill in skills:
                skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
        except Exception as e:
            logger.warning("Failed to load extensions config: %s", e)

        if enabled_only:
            skills = [s for s in skills if s.enabled]

        skills.sort(key=lambda s: s.name)
        return skills

    def ensure_custom_skill_is_editable(self, name: str) -> None:
        """确保 ``name`` 指向一个可编辑的自定义技能,否则抛出。"""
        if self.custom_skill_exists(name):
            return
        if self.public_skill_exists(name):
            raise ValueError(f"'{name}' is a built-in skill. To customise it, create a new skill with the same name under skills/custom/.")
        raise FileNotFoundError(f"Custom skill '{name}' not found.")
