"""Runtime — 核心运行时上下文。

Runtime 是 DeerFlow 中贯穿整个请求生命周期的上下文对象。
它持有状态、配置、元数据和工具注册信息，供主智能体和各中间件使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Runtime:
    """核心运行时上下文。

    提供对请求级状态、上下文和配置的统一访问。
    主智能体的 LLM 调用和工具函数通过 Runtime 读写共享数据。

    Attributes:
        state: 请求级可变状态，包含 sandbox、thread_data 等。
               sandbox 和 thread_data 以引用方式传递给子智能体。
        context: 请求级只读上下文，包含 thread_id、trace_id 等。
        metadata: 元数据，包含 model_name 等。
        app_config: 应用配置字典。
    """

    state: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    app_config: dict[str, Any] = field(default_factory=dict)

    @property
    def thread_id(self) -> str | None:
        """获取当前线程 ID。"""
        return self.context.get("thread_id")

    @property
    def trace_id(self) -> str | None:
        """获取追踪 ID。"""
        return self.context.get("trace_id")

    @property
    def model_name(self) -> str | None:
        """获取模型名称。"""
        return self.metadata.get("model_name")

    @property
    def sandbox(self) -> dict[str, Any] | None:
        """获取沙盒状态。"""
        return self.state.get("sandbox")

    @property
    def thread_data(self) -> dict[str, Any] | None:
        """获取线程数据。"""
        return self.state.get("thread_data")


def create_agent_runtime(
    model_name: str,
    middlewares: list | None = None,
) -> Runtime:
    """创建一个新的 Agent Runtime 实例。

    Args:
        model_name: 使用的模型名称。
        middlewares: 可选的中间件列表。

    Returns:
        配置好的 Runtime 实例。
    """
    return Runtime(
        metadata={"model_name": model_name},
        state={},
        context={},
        app_config={},
    )
