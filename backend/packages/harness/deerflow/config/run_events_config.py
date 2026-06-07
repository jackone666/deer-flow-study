"""Run 事件存储配置。

控制 run 事件（消息 + 执行轨迹）的持久化位置与策略。

后端选项：
- memory：内存存储，重启后数据丢失。适合开发与测试。
- db：通过 SQLAlchemy ORM 写入关系数据库，支持完整查询能力。适合生产部署。
- jsonl：仅追加的 JSONL 文件，介于内存与数据库之间的轻量持久化方案。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RunEventsConfig(BaseModel):
    """Run 事件存储配置。

    Attributes:
        backend: 存储后端类型，取值 ``memory`` / ``db`` / ``jsonl``。
        max_trace_content: 截断前的最大 trace 内容字节数（仅 db 后端生效）。
        track_token_usage: RunJournal 是否累计 token 用量到 RunRow。
    """

    backend: Literal["memory", "db", "jsonl"] = Field(
        default="memory",
        description="run 事件的存储后端。'memory' 用于开发（无持久化），'db' 用于生产（支持 SQL 查询），'jsonl' 用于轻量级单节点持久化。",
    )
    max_trace_content: int = Field(
        default=10240,
        description="trace 内容在被截断前的最大字节数（仅 db 后端生效）。",
    )
    track_token_usage: bool = Field(
        default=True,
        description="RunJournal 是否将 token 用量累计到 RunRow。",
    )
