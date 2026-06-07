"""``app.gateway`` 子包：DeerFlow 的 FastAPI Gateway。

提供 8001 端口对外暴露的 REST API，并嵌入 LangGraph 兼容运行时。包含
路由、鉴权、中间件、CORS/CSRF、配置、依赖注入等模块。
"""

from .app import app, create_app
from .config import GatewayConfig, get_gateway_config

__all__ = ["app", "create_app", "GatewayConfig", "get_gateway_config"]
