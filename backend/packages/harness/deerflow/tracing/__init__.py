"""Tracing 子包。

聚合 Langfuse / LangSmith 等 tracing 集成的元数据构建与回调工厂，
供 agent 运行入口（嵌入式 client、Gateway worker）复用。
"""

from .factory import build_tracing_callbacks
from .metadata import build_langfuse_trace_metadata, inject_langfuse_metadata

__all__ = [
    "build_langfuse_trace_metadata",
    "build_tracing_callbacks",
    "inject_langfuse_metadata",
]
