"""Langfuse trace 属性元数据构建器。

Langfuse v4 的 ``langchain.CallbackHandler`` 会从 ``RunnableConfig.metadata``
中提取一组固定保留键并挂到根 trace 上：

- ``langfuse_session_id`` → 把 trace 归入同一个 Session（LangGraph thread → Langfuse Session）
- ``langfuse_user_id``    → trace 的 user_id（驱动 Users 页面）
- ``langfuse_trace_name`` → 人类可读的 trace 名称
- ``langfuse_tags``       → trace 的标签

具体契约参见 ``langfuse/langchain/CallbackHandler.py::_parse_langfuse_trace_attributes``
以及 https://langfuse.com/docs/observability/features/sessions 。本模块的
builder 让 Gateway / run worker 能够注入正确的元数据，而无需在调用方
泄漏 Langfuse 的内部细节。
"""

from __future__ import annotations

from typing import Any

from deerflow.config import get_enabled_tracing_providers

# Lazy-imported below to avoid a circular import: ``deerflow.runtime`` eagerly
# imports the run worker, which in turn needs ``deerflow.tracing``.
_DEFAULT_TRACE_NAME = "lead-agent"


def build_langfuse_trace_metadata(
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """为 ``RunnableConfig.metadata`` 构造 Langfuse trace 属性元数据。

    当 Langfuse 未在已启用的 tracing providers 中时返回 ``{}``，调用方
    可无脑合并而不会影响 LangSmith 等其他 tracer。

    Args:
        thread_id: LangGraph 线程 ID，映射到 ``langfuse_session_id``。
        user_id: 实际生效的用户 ID；为 ``None`` 时回退到 ``DEFAULT_USER_ID``，
            以保证 Langfuse Users 页面在无鉴权模式下也能正常工作。
        assistant_id: 可选的 agent 标识；缺省为 ``"lead-agent"``。
        model_name: 模型名称，会以 ``model:<name>`` 的形式写入 ``langfuse_tags``。
        environment: 部署环境（如 ``"production"``），会以 ``env:<value>``
            的形式写入 ``langfuse_tags``。

    Returns:
        包含 Langfuse 保留键的元数据字典；若 Langfuse 未启用则为空字典。
    """
    if "langfuse" not in get_enabled_tracing_providers():
        return {}

    from deerflow.runtime.user_context import DEFAULT_USER_ID

    metadata: dict[str, Any] = {
        "langfuse_session_id": thread_id,
        "langfuse_user_id": user_id or DEFAULT_USER_ID,
        "langfuse_trace_name": assistant_id or _DEFAULT_TRACE_NAME,
    }

    tags: list[str] = []
    if environment:
        tags.append(f"env:{environment}")
    if model_name:
        tags.append(f"model:{model_name}")
    if tags:
        metadata["langfuse_tags"] = tags

    return metadata


def inject_langfuse_metadata(
    config: dict,
    *,
    thread_id: str | None,
    user_id: str | None = None,
    assistant_id: str | None = None,
    model_name: str | None = None,
    environment: str | None = None,
) -> None:
    """将 Langfuse trace 属性元数据合并到 ``config["metadata"]`` 中。

    Gateway worker（``runtime/runs/worker.py``）与嵌入式 client
    （``client.py``）共用该函数，以保证两条链路不会因元数据差异而漂移。

    通过 ``setdefault`` 实现「调用方先到先得」——例如前端已经写入的
    ``langfuse_session_id`` 不会被覆盖。``config`` 字典会被原地修改；
    当 Langfuse 未在已启用的 tracing providers 中时该调用是 no-op。

    Args:
        config: 待修改的 ``RunnableConfig`` 字典。
        thread_id: LangGraph 线程 ID，映射到 ``langfuse_session_id``。
        user_id: 实际生效的用户 ID；为 ``None`` 时回退到 ``DEFAULT_USER_ID``。
        assistant_id: 可选的 agent 标识；缺省为 ``"lead-agent"``。
        model_name: 模型名称，会以 ``model:<name>`` 写入 ``langfuse_tags``。
        environment: 部署环境，会以 ``env:<value>`` 写入 ``langfuse_tags``。
    """
    langfuse_metadata = build_langfuse_trace_metadata(
        thread_id=thread_id,
        user_id=user_id,
        assistant_id=assistant_id,
        model_name=model_name,
        environment=environment,
    )
    if not langfuse_metadata:
        return

    merged_metadata = dict(config.get("metadata") or {})
    for key, value in langfuse_metadata.items():
        merged_metadata.setdefault(key, value)
    config["metadata"] = merged_metadata
