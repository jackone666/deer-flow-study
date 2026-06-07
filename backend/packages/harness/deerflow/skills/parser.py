"""SKILL.md 文件的解析逻辑。"""

import logging
import re
from pathlib import Path

import yaml

from .types import SKILL_MD_FILE, Skill, SkillCategory

logger = logging.getLogger(__name__)


def _format_yaml_error(skill_file: Path, exc: yaml.YAMLError, source: str) -> str:
    """生成对开发者友好的 YAML front-matter 错误说明。"""

    lines = [f"Invalid YAML front-matter in {skill_file}: {exc}"]

    mark = getattr(exc, "problem_mark", None)
    source_lines = source.splitlines()
    if mark is not None and 0 <= mark.line < len(source_lines):
        offending = source_lines[mark.line]

        # mark.line is 0-based within the front-matter body; +1 makes it
        # 1-based, +1 more accounts for the leading `---` fence that the
        # front-matter regex strips before yaml.safe_load sees it. The
        # result matches the line number an author sees in their editor.
        file_line_number = mark.line + 2
        lines.append(f"  line {file_line_number}: {offending}")

        # Targeted hint for the most common authoring mistake: an unquoted
        # scalar value whose body contains ``: ``. We only surface the hint
        # when we are confident it applies, to avoid misleading authors who
        # hit unrelated YAML errors.
        if getattr(exc, "problem", "") == "mapping values are not allowed here" and ":" in offending:
            key, _, value = offending.partition(":")
            value = value.strip()
            if value and value[0] not in {'"', "'", "|", ">", "[", "{"}:
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'  hint: values containing ":" must be quoted, e.g. {key}: "{escaped}"')

    return "\n".join(lines)


def parse_allowed_tools(raw: object, skill_file: Path) -> list[str] | None:
    """解析可选的 ``allowed-tools`` frontmatter 字段。

    Args:
        raw: YAML 中读取到的原始值。
        skill_file: 当前 SKILL.md 路径,用于错误提示。

    Returns:
        字段缺失时返回 None;为字符串列表时返回该列表(可为空列表,表示
        显式声明无可用工具)。

    Raises:
        ValueError: 字段值不是字符串列表或包含空字符串。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"allowed-tools in {skill_file} must be a list of strings")

    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"allowed-tools in {skill_file} must contain only strings")
        tool_name = item.strip()
        if not tool_name:
            raise ValueError(f"allowed-tools in {skill_file} cannot contain empty tool names")
        allowed_tools.append(tool_name)
    return allowed_tools


def parse_skill_file(skill_file: Path, category: SkillCategory, relative_path: Path | None = None) -> Skill | None:
    """解析 SKILL.md 文件并提取元数据。

    Args:
        skill_file: SKILL.md 文件路径。
        category: 技能所属分类。
        relative_path: 从分类根目录到技能目录的相对路径,缺省时使用技能目录名。

    Returns:
        解析成功时返回 :class:`Skill`;失败时返回 None。
    """
    if not skill_file.exists() or skill_file.name != SKILL_MD_FILE:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")

        # Extract YAML front-matter block between leading ``---`` fences.
        front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not front_matter_match:
            return None

        front_matter_text = front_matter_match.group(1)

        try:
            metadata = yaml.safe_load(front_matter_text)
        except yaml.YAMLError as exc:
            logger.error("%s", _format_yaml_error(skill_file, exc, front_matter_text))
            return None

        if not isinstance(metadata, dict):
            logger.error("Front-matter in %s is not a YAML mapping", skill_file)
            return None

        # Extract required fields.  Both must be non-empty strings.
        name = metadata.get("name")
        description = metadata.get("description")

        if not name or not isinstance(name, str):
            return None
        if not description or not isinstance(description, str):
            return None

        # Normalise: strip surrounding whitespace that YAML may preserve.
        name = name.strip()
        description = description.strip()

        if not name or not description:
            return None

        license_text = metadata.get("license")
        if license_text is not None:
            license_text = str(license_text).strip() or None

        try:
            allowed_tools = parse_allowed_tools(metadata.get("allowed-tools"), skill_file)
        except ValueError as exc:
            logger.error("Invalid allowed-tools in %s: %s", skill_file, exc)
            return None

        return Skill(
            name=name,
            description=description,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            allowed_tools=allowed_tools,
            enabled=True,  # Actual state comes from the extensions config file.
        )

    except Exception:
        logger.exception("Unexpected error parsing skill file %s", skill_file)
        return None
