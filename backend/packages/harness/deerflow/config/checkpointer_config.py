"""LangGraph checkpointer 的配置。"""

from typing import Literal

from pydantic import BaseModel, Field

CheckpointerType = Literal["memory", "sqlite", "postgres"]


class CheckpointerConfig(BaseModel):
    """LangGraph 状态持久化 checkpointer 的配置。

    Attributes:
        type: checkpointer 后端类型。
        connection_string: sqlite（文件路径）或 postgres（DSN）的连接串。
    """

    type: CheckpointerType = Field(
        description="checkpointer 后端类型。"
        "'memory' 仅在进程内存储（重启后丢失）；"
        "'sqlite' 持久化到本地文件（需要 langgraph-checkpoint-sqlite）；"
        "'postgres' 持久化到 PostgreSQL（需安装 deerflow-harness[postgres]）。"
    )
    connection_string: str | None = Field(
        default=None,
        description="sqlite（文件路径）或 postgres（DSN）的连接串。"
        "对 sqlite 可选，省略时默认为 'store.db'；"
        "对 postgres 必填。"
        "sqlite 可使用文件路径（如 '.deer-flow/checkpoints.db'）或 ':memory:'；"
        "postgres 可使用 DSN，如 'postgresql://user:pass@localhost:5432/db'。",
    )


# 全局配置实例 —— None 表示尚未配置 checkpointer。
_checkpointer_config: CheckpointerConfig | None = None


def get_checkpointer_config() -> CheckpointerConfig | None:
    """获取当前 checkpointer 配置，未配置时返回 ``None``。

    Returns:
        CheckpointerConfig | None: 当前配置对象，未配置时为 ``None``。
    """
    return _checkpointer_config


def set_checkpointer_config(config: CheckpointerConfig | None) -> None:
    """设置 checkpointer 配置。

    Args:
        config: 新的配置对象；传 ``None`` 表示清除配置。
    """
    global _checkpointer_config
    _checkpointer_config = config


def load_checkpointer_config_from_dict(config_dict: dict | None) -> None:
    """从字典加载 checkpointer 配置。

    Args:
        config_dict: 符合 :class:`CheckpointerConfig` 字段的字典；``None`` 表示清除配置。
    """
    global _checkpointer_config
    if config_dict is None:
        _checkpointer_config = None
        return
    _checkpointer_config = CheckpointerConfig(**config_dict)
