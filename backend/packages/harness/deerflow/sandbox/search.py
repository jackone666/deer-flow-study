"""沙箱内的 glob/grep 搜索能力实现。

提供 :class:`GrepMatch` 数据结构与一组 :func:`should_ignore_name` 等辅助函数,
以及 :func:`find_glob_matches`、:func:`find_grep_matches` 两个核心搜索函数,
被本地与远程沙箱实现复用。
"""

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IGNORE_PATTERNS = [
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".nox",
    ".eggs",
    "*.egg-info",
    "site-packages",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    ".turbo",
    "target",
    "out",
    ".idea",
    ".vscode",
    "*.swp",
    "*.swo",
    "*~",
    ".project",
    ".classpath",
    ".settings",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.lnk",
    "*.log",
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.cache",
    ".cache",
    "logs",
    ".coverage",
    "coverage",
    ".nyc_output",
    "htmlcov",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000
DEFAULT_LINE_SUMMARY_LENGTH = 200


@dataclass(frozen=True)
class GrepMatch:
    """单次 grep 匹配结果。

    Attributes:
        path: 匹配所在文件路径。
        line_number: 匹配所在行号(从 1 开始)。
        line: 匹配行的截断内容。
    """

    path: str
    line_number: int
    line: str


def should_ignore_name(name: str) -> bool:
    """判断文件名或目录名是否应当被忽略。

    Args:
        name: 文件名或目录名。

    Returns:
        若匹配到 ``IGNORE_PATTERNS`` 中任一模式则返回 True,否则返回 False。
    """
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def should_ignore_path(path: str) -> bool:
    """判断路径(任意一段)是否包含需要忽略的目录或文件。

    Args:
        path: 相对或绝对路径。

    Returns:
        路径任意一段命中 :func:`should_ignore_name` 时返回 True。
    """
    return any(should_ignore_name(segment) for segment in path.replace("\\", "/").split("/") if segment)


def path_matches(pattern: str, rel_path: str) -> bool:
    """检查相对路径是否匹配给定 glob 模式。

    除常规 :meth:`PurePosixPath.match` 之外,还额外支持 ``**/`` 前缀模式,以便
    表达"匹配任意层级目录"的语义。

    Args:
        pattern: glob 模式字符串。
        rel_path: 待检查的相对路径(使用 POSIX 风格分隔符)。

    Returns:
        匹配成功时返回 True,否则返回 False。
    """
    path = PurePosixPath(rel_path)
    if path.match(pattern):
        return True
    if pattern.startswith("**/"):
        return path.match(pattern[3:])
    return False


def truncate_line(line: str, max_chars: int = DEFAULT_LINE_SUMMARY_LENGTH) -> str:
    """截断单行内容,末尾附加省略号。

    Args:
        line: 待处理的原始行内容。
        max_chars: 截断后最大字符数,默认 :data:`DEFAULT_LINE_SUMMARY_LENGTH`。

    Returns:
        截断后的字符串;若未超长则原样返回。
    """
    line = line.rstrip("\n\r")
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 3] + "..."


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    """通过采样前 N 字节判断文件是否为二进制文件。

    若采样中出现 NUL 字节即视为二进制;读取失败时同样返回 True 以避免误判为文本。

    Args:
        path: 待检测的文件路径。
        sample_size: 采样字节数,默认 8192。

    Returns:
        是二进制文件(包含读取失败)时返回 True。
    """
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(sample_size)
    except OSError:
        return True


def find_glob_matches(root: Path, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
    """在指定根目录下按 glob 模式查找匹配路径。

    会自动跳过 :data:`IGNORE_PATTERNS` 中的目录与文件,以避免无意义的扫描。

    Args:
        root: 搜索根目录。
        pattern: glob 模式字符串。
        include_dirs: 是否将目录纳入匹配结果,默认 False。
        max_results: 命中上限,超过时立刻返回并把 truncated 置为 True。

    Returns:
        二元组 ``(匹配路径列表, 是否被截断)``。

    Raises:
        FileNotFoundError: 根目录不存在。
        NotADirectoryError: 根路径不是目录。
    """
    matches: list[str] = []
    truncated = False
    root = root.resolve()

    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    for current_root, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not should_ignore_name(name)]
        # root is already resolved; os.walk builds current_root by joining under root,
        # so relative_to() works without an extra stat()/resolve() per directory.
        rel_dir = Path(current_root).relative_to(root)

        if include_dirs:
            for name in dirs:
                rel_path = (rel_dir / name).as_posix()
                if path_matches(pattern, rel_path):
                    matches.append(str(Path(current_root) / name))
                    if len(matches) >= max_results:
                        truncated = True
                        return matches, truncated

        for name in files:
            if should_ignore_name(name):
                continue
            rel_path = (rel_dir / name).as_posix()
            if path_matches(pattern, rel_path):
                matches.append(str(Path(current_root) / name))
                if len(matches) >= max_results:
                    truncated = True
                return matches, truncated

    return matches, truncated


def find_grep_matches(
    root: Path,
    pattern: str,
    *,
    glob_pattern: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    line_summary_length: int = DEFAULT_LINE_SUMMARY_LENGTH,
) -> tuple[list[GrepMatch], bool]:
    """在目录下递归搜索文本文件中的模式匹配。

    会自动跳过过大、二进制、符号链接等不合适处理的文件;并对超长行做截断以避免
    在压缩/无换行文件上触发 ReDoS。

    Args:
        root: 搜索根目录。
        pattern: 搜索模式;为 ``literal=True`` 时视为字面量,否则视为正则表达式。
        glob_pattern: 可选的额外 glob 过滤模式。
        literal: 是否按字面量处理 ``pattern``。
        case_sensitive: 是否区分大小写,默认不区分。
        max_results: 命中上限,超过时立即返回并把 truncated 置为 True。
        max_file_size: 超过该字节数的文件会被跳过。
        line_summary_length: 单行摘要的最大长度,用于控制 :class:`GrepMatch.line` 大小。

    Returns:
        二元组 ``(匹配结果列表, 是否被截断)``。

    Raises:
        FileNotFoundError: 根目录不存在。
        NotADirectoryError: 根路径不是目录。
    """
    matches: list[GrepMatch] = []
    truncated = False
    root = root.resolve()

    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    regex_source = re.escape(pattern) if literal else pattern
    flags = 0 if case_sensitive else re.IGNORECASE
    regex = re.compile(regex_source, flags)

    # Skip lines longer than this to prevent ReDoS on minified / no-newline files.
    _max_line_chars = line_summary_length * 10

    for current_root, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not should_ignore_name(name)]
        rel_dir = Path(current_root).relative_to(root)

        for name in files:
            if should_ignore_name(name):
                continue

            candidate_path = Path(current_root) / name
            rel_path = (rel_dir / name).as_posix()

            if glob_pattern is not None and not path_matches(glob_pattern, rel_path):
                continue

            try:
                if candidate_path.is_symlink():
                    continue
                file_path = candidate_path.resolve()
                if not file_path.is_relative_to(root):
                    continue
                if file_path.stat().st_size > max_file_size or is_binary_file(file_path):
                    continue
                with file_path.open(encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if len(line) > _max_line_chars:
                            continue
                        if regex.search(line):
                            matches.append(
                                GrepMatch(
                                    path=str(file_path),
                                    line_number=line_number,
                                    line=truncate_line(line, line_summary_length),
                                )
                            )
                            if len(matches) >= max_results:
                                truncated = True
                                return matches, truncated
            except OSError:
                continue

    return matches, truncated
