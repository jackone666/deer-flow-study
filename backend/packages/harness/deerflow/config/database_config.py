"""统一的数据库后端配置。

同时控制 LangGraph checkpointer 与 DeerFlow 应用持久化层
（runs、threads metadata、users 等）。用户只配置一个后端，
系统负责底层的物理分离细节。

SQLite 模式：checkpointer 与 app 共享同一个 .db 文件
（{sqlite_dir}/deerflow.db），并对每个连接启用 WAL 日志模式。
WAL 允许多个读和一个写并发而互不阻塞，因此单一文件对两套负载
都是安全的。争抢写锁的写者会按 sqlite3 默认的 5 秒 busy_timeout
等待，而不是立即失败。

Postgres 模式：两者共用同一个数据库 URL，但维护各自独立、
生命周期不同的连接池。

Memory 模式：checkpointer 使用 MemorySaver，app 使用内存存储，
不会初始化任何数据库。

敏感字段（如 ``postgres_url``）应在 config.yaml 中使用 ``$VAR``
语法引用 .env 中的环境变量：

    database:
      backend: postgres
      postgres_url: $DATABASE_URL

``$VAR`` 的解析由 ``AppConfig.resolve_env_variables()`` 在本配置
被实例化之前完成，``DatabaseConfig`` 自身不需要做任何环境变量处理。
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    """统一数据库后端配置。

    Attributes:
        backend: checkpointer 与应用数据共用的存储后端。
        sqlite_dir: SQLite 数据库文件所在目录。
        postgres_url: PostgreSQL 连接 URL，checkpointer 与 app 共用。
        echo_sql: 是否回显所有 SQL 语句到日志（仅调试）。
        pool_size: app ORM 引擎的连接池大小（仅 postgres）。
    """

    backend: Literal["memory", "sqlite", "postgres"] = Field(
        default="memory",
        description=("checkpointer 与应用数据共用的存储后端。'memory' 用于开发（重启后无持久化），'sqlite' 用于单节点部署，'postgres' 用于生产多节点部署。"),
    )
    sqlite_dir: str = Field(
        default=".deer-flow/data",
        description=("SQLite 数据库文件所在目录。checkpointer 与应用数据共用 {sqlite_dir}/deerflow.db。"),
    )
    postgres_url: str = Field(
        default="",
        description=(
            "checkpointer 与 app 共用的 PostgreSQL 连接 URL。"
            "在 config.yaml 中使用 $DATABASE_URL 引用 .env。"
            "示例：postgresql://user:pass@host:5432/deerflow "
            "（+asyncpg 驱动后缀会在需要时自动补齐）。"
        ),
    )
    echo_sql: bool = Field(
        default=False,
        description="是否将所有 SQL 语句回显到日志（仅用于调试）。",
    )
    pool_size: int = Field(
        default=5,
        description="app ORM 引擎的连接池大小（仅 postgres）。",
    )

    # -- 派生辅助方法（不对用户开放配置） --

    @property
    def _resolved_sqlite_dir(self) -> str:
        """将 ``sqlite_dir`` 解析为绝对路径（相对 CWD）。"""
        from pathlib import Path

        return str(Path(self.sqlite_dir).resolve())

    @property
    def sqlite_path(self) -> str:
        """checkpointer 与 app 共用的 SQLite 文件路径。"""
        return os.path.join(self._resolved_sqlite_dir, "deerflow.db")

    # 向后兼容的别名
    @property
    def checkpointer_sqlite_path(self) -> str:
        """LangGraph checkpointer 的 SQLite 文件路径（``sqlite_path`` 的别名）。"""
        return self.sqlite_path

    @property
    def app_sqlite_path(self) -> str:
        """应用 ORM 数据的 SQLite 文件路径（``sqlite_path`` 的别名）。"""
        return self.sqlite_path

    @property
    def app_sqlalchemy_url(self) -> str:
        """应用 ORM 引擎的 SQLAlchemy 异步 URL。

        Raises:
            ValueError: 当 ``backend`` 不是 ``sqlite`` 或 ``postgres`` 时。
        """
        if self.backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        if self.backend == "postgres":
            url = self.postgres_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        raise ValueError(f"backend={self.backend!r} 没有对应的 SQLAlchemy URL")
