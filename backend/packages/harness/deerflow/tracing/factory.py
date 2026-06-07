"""Tracing 回调工厂。

为每个显式启用的 tracing provider（LangSmith / Langfuse）构造对应的
LangChain 回调，统一返回 ``list[Any]`` 以便嵌入 ``RunnableConfig.callbacks``。
"""

from __future__ import annotations

from typing import Any

from deerflow.config import (
    get_enabled_tracing_providers,
    get_tracing_config,
    validate_enabled_tracing_providers,
)


def _create_langsmith_tracer(config) -> Any:
    """根据 tracing_config.langsmith 构造 LangSmith tracer。

    Args:
        config: ``tracing_config.langsmith`` 子配置，含 ``project`` 等字段。

    Returns:
        配置好项目名的 :class:`langchain_core.tracers.langchain.LangChainTracer`。
    """
    from langchain_core.tracers.langchain import LangChainTracer

    return LangChainTracer(project_name=config.project)


def _create_langfuse_handler(config) -> Any:
    """根据 tracing_config.langfuse 构造 Langfuse LangChain 回调。

    langfuse>=4 通过客户端单例管理项目级凭据，因此会先实例化一次
    :class:`langfuse.Langfuse`，再创建挂载到该客户端的 CallbackHandler。

    Args:
        config: ``tracing_config.langfuse`` 子配置，含 secret/public key 与 host。

    Returns:
        已绑定凭据的 Langfuse CallbackHandler。
    """
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

    # langfuse>=4 initializes project-specific credentials through the client
    # singleton; the LangChain callback then attaches to that configured client.
    Langfuse(
        secret_key=config.secret_key,
        public_key=config.public_key,
        host=config.host,
    )
    return LangfuseCallbackHandler(public_key=config.public_key)


def build_tracing_callbacks() -> list[Any]:
    """为所有显式启用的 tracing provider 构造回调。"""
    validate_enabled_tracing_providers()
    enabled_providers = get_enabled_tracing_providers()
    if not enabled_providers:
        return []

    tracing_config = get_tracing_config()
    callbacks: list[Any] = []

    for provider in enabled_providers:
        if provider == "langsmith":
            try:
                callbacks.append(_create_langsmith_tracer(tracing_config.langsmith))
            except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
                raise RuntimeError(f"LangSmith tracing initialization failed: {exc}") from exc
        elif provider == "langfuse":
            try:
                callbacks.append(_create_langfuse_handler(tracing_config.langfuse))
            except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
                raise RuntimeError(f"Langfuse tracing initialization failed: {exc}") from exc

    return callbacks
