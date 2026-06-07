"""store 与 checkpointer 提供器共享的 SQLite 连接工具。"""


from __future__ import annotations

import pathlib

from deerflow.config.paths import resolve_path


def resolve_sqlite_conn_str(raw: str) -> str:
    """返回 store/checkpointer 后端可直接使用的 SQLite 连接字符串。
    
        SQLite 特殊字符串（``":memory:"`` 和 ``file:`` URI）会原样返回。
        普通文件系统路径（相对或绝对）会被解析为绝对路径，并创建其父目录。
    """

    if raw == ":memory:" or raw.startswith("file:"):
        return raw
    return str(resolve_path(raw))


def ensure_sqlite_parent_dir(conn_str: str) -> None:
    """为 SQLite 文件系统路径创建父目录。
    
        对内存数据库（``":memory:"``）和 ``file:`` URI 是空操作。
    """

    if conn_str != ":memory:" and not conn_str.startswith("file:"):
        pathlib.Path(conn_str).parent.mkdir(parents=True, exist_ok=True)
