"""Memory 机制的配置。"""

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """全局 memory 机制配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用 memory 机制",
    )
    storage_path: str = Field(
        default="",
        description=(
            "memory 数据存储路径。"
            "为空时默认为 per-user 存储 `{base_dir}/users/{user_id}/memory.json`。"
            "绝对路径按字面使用并放弃 per-user 隔离（所有用户共用同一文件）。"
            "相对路径以 `Paths.base_dir` 为基准解析（非 backend 工作目录）。"
            "注意：若之前设置为 `.deer-flow/memory.json`，"
            "该路径现在会按 `{base_dir}/.deer-flow/memory.json` 解析；"
            "若需保留旧位置，请迁移数据或改用绝对路径。"
        ),
    )
    storage_class: str = Field(
        default="deerflow.agents.memory.storage.FileMemoryStorage",
        description="memory 存储 provider 的类路径",
    )
    debounce_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="处理排队更新前的等待秒数（debounce）",
    )
    model_name: str | None = Field(
        default=None,
        description="用于 memory 更新的模型名（None 表示使用默认模型）",
    )
    max_facts: int = Field(
        default=100,
        ge=10,
        le=500,
        description="最多存储的事实数",
    )
    fact_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="存储事实的最低置信度阈值",
    )
    injection_enabled: bool = Field(
        default=True,
        description="是否将 memory 注入到系统提示中",
    )
    max_injection_tokens: int = Field(
        default=2000,
        ge=100,
        le=8000,
        description="memory 注入使用的最大 token 数",
    )


# 全局配置实例
_memory_config: MemoryConfig = MemoryConfig()


def get_memory_config() -> MemoryConfig:
    """获取当前 memory 配置。

    Returns:
        MemoryConfig: 进程级单例配置对象。
    """
    return _memory_config


def set_memory_config(config: MemoryConfig) -> None:
    """设置 memory 配置。

    Args:
        config: 新的配置对象。
    """
    global _memory_config
    _memory_config = config


def load_memory_config_from_dict(config_dict: dict) -> None:
    """从字典加载 memory 配置。

    Args:
        config_dict: 符合 :class:`MemoryConfig` 字段的字典。
    """
    global _memory_config
    _memory_config = MemoryConfig(**config_dict)
