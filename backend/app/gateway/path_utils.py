"""线程虚拟路径（例如 ``/mnt/user-data/outputs/...``）的共享解析工具。"""

from pathlib import Path

from fastapi import HTTPException

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:
    """将虚拟路径解析为线程用户数据目录下的实际文件系统路径。

    Args:
        thread_id: 线程 ID。
        virtual_path: 沙箱内部看到的虚拟路径（例如 ``/mnt/user-data/outputs/file.txt``）。

    Returns:
        解析后的文件系统路径。

    Raises:
        HTTPException: 路径非法或不在允许的目录范围内时抛出对应状态码的错误。
    """
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path, user_id=get_effective_user_id())
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
