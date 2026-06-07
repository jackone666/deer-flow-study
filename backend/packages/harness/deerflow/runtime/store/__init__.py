"""DeerFlow 运行时的 store 提供器。

    从统一命名空间再导出异步与同步提供器的公共 API：
    异步提供器用于长生命周期服务，同步提供器用于 CLI 工具与嵌入式客户端。
    异步场景（FastAPI、BackgroundTasks）优先使用 :func:`make_store`；
    同步场景（CLI、脚本）优先使用 :func:`get_store`。
"""


from .async_provider import make_store
from .provider import get_store, reset_store, store_context

__all__ = [
    # async
    "make_store",
    # sync
    "get_store",
    "reset_store",
    "store_context",
]
