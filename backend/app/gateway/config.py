"""Gateway 服务的配置管理。

从环境变量读取 ``GATEWAY_HOST``、``GATEWAY_PORT``、``GATEWAY_ENABLE_DOCS`` 等，
构造并缓存全局 ``GatewayConfig`` 单例。
"""

import os

from pydantic import BaseModel, Field


class GatewayConfig(BaseModel):
    """Gateway 服务的配置。"""

    host: str = Field(default="0.0.0.0", description="Host to bind the gateway server")
    port: int = Field(default=8001, description="Port to bind the gateway server")
    enable_docs: bool = Field(default=True, description="Enable Swagger/ReDoc/OpenAPI endpoints")


_gateway_config: GatewayConfig | None = None


def get_gateway_config() -> GatewayConfig:
    """获取全局 ``GatewayConfig`` 单例，首次调用时从环境变量解析。"""
    global _gateway_config
    if _gateway_config is None:
        _gateway_config = GatewayConfig(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8001")),
            enable_docs=os.getenv("GATEWAY_ENABLE_DOCS", "true").lower() == "true",
        )
    return _gateway_config
