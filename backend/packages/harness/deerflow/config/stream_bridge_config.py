"""Stream bridge 的配置。"""

from typing import Literal

from pydantic import BaseModel, Field

StreamBridgeType = Literal["memory", "redis"]


class StreamBridgeConfig(BaseModel):
    """连接 agent worker 与 SSE 端点的 stream bridge 配置。"""

    type: StreamBridgeType = Field(
        default="memory",
        description="stream bridge 后端类型。'memory' 使用进程内的 asyncio.Queue（仅限单进程）；'redis' 使用 Redis Streams（计划在 Phase 2 提供，尚未实现）。",
    )
    redis_url: str | None = Field(
        default=None,
        description="redis 后端的 Redis URL，例如 'redis://localhost:6379/0'。",
    )
    queue_maxsize: int = Field(
        default=256,
        description="memory bridge 下每个 run 最多缓冲的事件数。",
    )


# 全局配置实例 —— None 表示尚未配置 stream bridge（回退到默认 memory 行为）。
_stream_bridge_config: StreamBridgeConfig | None = None


def get_stream_bridge_config() -> StreamBridgeConfig | None:
    """获取当前 stream bridge 配置；未配置时返回 ``None``。

    Returns:
        StreamBridgeConfig | None: 当前配置对象，未配置时为 ``None``。
    """
    return _stream_bridge_config


def set_stream_bridge_config(config: StreamBridgeConfig | None) -> None:
    """设置 stream bridge 配置。

    Args:
        config: 新的配置对象；传 ``None`` 表示清除配置。
    """
    global _stream_bridge_config
    _stream_bridge_config = config


def load_stream_bridge_config_from_dict(config_dict: dict | None) -> None:
    """从字典加载 stream bridge 配置。

    Args:
        config_dict: 符合 :class:`StreamBridgeConfig` 字段的字典；``None`` 表示清除配置。
    """
    global _stream_bridge_config
    if config_dict is None:
        _stream_bridge_config = None
        return
    _stream_bridge_config = StreamBridgeConfig(**config_dict)
