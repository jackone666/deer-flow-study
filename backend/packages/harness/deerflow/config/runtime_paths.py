"""独立使用 harness 时的运行时路径解析。"""

import os
from pathlib import Path


def project_root() -> Path:
    """返回 runtime 所属文件所在的调用方项目根目录。

    优先读取环境变量 ``DEER_FLOW_PROJECT_ROOT``，否则使用当前工作目录。

    Returns:
        Path: 解析后的项目根目录绝对路径。

    Raises:
        ValueError: 当 ``DEER_FLOW_PROJECT_ROOT`` 指向一个不存在的路径或非目录时。
    """
    if env_root := os.getenv("DEER_FLOW_PROJECT_ROOT"):
        root = Path(env_root).resolve()
        if not root.exists():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT 设置为 '{env_root}'，但解析后的路径 '{root}' 不存在。")
        if not root.is_dir():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT 设置为 '{env_root}'，但解析后的路径 '{root}' 不是目录。")
        return root
    return Path.cwd().resolve()


def runtime_home() -> Path:
    """返回可写的 DeerFlow 状态目录。

    优先使用环境变量 ``DEER_FLOW_HOME``，否则返回 ``<project_root>/.deer-flow``。

    Returns:
        Path: 状态目录的绝对路径。
    """
    if env_home := os.getenv("DEER_FLOW_HOME"):
        return Path(env_home).resolve()
    return project_root() / ".deer-flow"


def resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    """将绝对路径原样解析、相对路径基于项目根解析。

    Args:
        value: 待解析的路径。
        base: 相对路径的解析基准；缺省时使用 :func:`project_root`。

    Returns:
        Path: 解析后的绝对路径。
    """
    path = Path(value)
    if not path.is_absolute():
        path = (base or project_root()) / path
    return path.resolve()


def existing_project_file(names: tuple[str, ...]) -> Path | None:
    """按顺序在项目根下查找名称匹配的文件，返回首个存在的那个。

    Args:
        names: 候选文件名元组，按顺序检查。

    Returns:
        Path | None: 首个存在的文件路径；若全部不存在则返回 ``None``。
    """
    root = project_root()
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
