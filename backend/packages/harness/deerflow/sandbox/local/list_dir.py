"""本地沙箱的目录遍历工具。

提供 :func:`list_dir`,在遍历过程中自动跳过 :data:`deerflow.sandbox.search.IGNORE_PATTERNS`
匹配的文件/目录,并避免越界跟随符号链接。
"""

from pathlib import Path

from deerflow.sandbox.search import should_ignore_name


def list_dir(path: str, max_depth: int = 2) -> list[str]:
    """递归列出指定深度内的文件与目录。

    Args:
        path: 根目录路径。
        max_depth: 最大递归深度,默认 2;1 表示仅直接子项,2 表示子项+孙项,依此类推。

    Returns:
        排序后的绝对路径列表(目录项末尾保留 ``/`` 标记);命中
        :data:`deerflow.sandbox.search.IGNORE_PATTERNS` 的项会被排除;当根路径
        不是目录时返回空列表。
    """
    result: list[str] = []
    root_path = Path(path).resolve()

    if not root_path.is_dir():
        return result

    def _is_within_root(candidate: Path) -> bool:
        """判断 ``candidate`` 是否位于 ``root_path`` 内(防止符号链接越界)。"""
        try:
            candidate.relative_to(root_path)
            return True
        except ValueError:
            return False

    def _traverse(current_path: Path, current_depth: int) -> None:
        """递归遍历目录直到 ``max_depth``。"""
        if current_depth > max_depth:
            return

        try:
            for item in current_path.iterdir():
                if should_ignore_name(item.name):
                    continue

                if item.is_symlink():
                    try:
                        item_resolved = item.resolve()
                        if not _is_within_root(item_resolved):
                            continue
                    except OSError:
                        continue
                    post_fix = "/" if item_resolved.is_dir() else ""
                    result.append(str(item_resolved) + post_fix)
                    continue

                item_resolved = item.resolve()
                if not _is_within_root(item_resolved):
                    continue

                post_fix = "/" if item.is_dir() else ""
                result.append(str(item_resolved) + post_fix)

                # Recurse into subdirectories if not at max depth
                if item.is_dir() and current_depth < max_depth:
                    _traverse(item, current_depth + 1)
        except PermissionError:
            pass

    _traverse(root_path, 1)

    return sorted(result)
