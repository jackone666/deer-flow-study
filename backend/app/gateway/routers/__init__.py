"""``app.gateway.routers`` 子包：FastAPI 路由模块集合。

通过 ``app.gateway.app`` 中的 ``include_router`` 加载；保持 ``__all__`` 与
``import`` 列表同步，便于显式暴露子模块。
"""

from . import artifacts, assistants_compat, mcp, models, skills, suggestions, thread_runs, threads, uploads

__all__ = ["artifacts", "assistants_compat", "mcp", "models", "skills", "suggestions", "threads", "thread_runs", "uploads"]
