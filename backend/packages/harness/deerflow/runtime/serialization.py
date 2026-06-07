"""LangChain / LangGraph 对象的规范化序列化。

提供将 LangChain 消息对象、Pydantic 模型与 LangGraph 状态字典转换为纯
JSON 可序列化 Python 结构的唯一来源。

消费者：``deerflow.runtime.runs.worker``（SSE 发布）与
``app.gateway.routers.threads``（REST 响应）。
"""

from __future__ import annotations

from typing import Any


def serialize_lc_object(obj: Any) -> Any:
    """递归地将 LangChain 对象序列化为 JSON 可序列化的字典。"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: serialize_lc_object(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_lc_object(item) for item in obj]
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # Pydantic v1 / older objects
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def serialize_channel_values(channel_values: dict[str, Any]) -> dict[str, Any]:
    """序列化 channel values，并剥离 LangGraph 内部键。

    会移除 ``__pregel_*`` 与 ``__interrupt__`` 等内部键，以与 LangGraph
    Platform API 的返回保持一致。
    """
    result: dict[str, Any] = {}
    for key, value in channel_values.items():
        if key.startswith("__pregel_") or key == "__interrupt__":
            continue
        result[key] = serialize_lc_object(value)
    return result


def serialize_messages_tuple(obj: Any) -> Any:
    """序列化 messages 模式的元组 ``(chunk, metadata)``。"""
    if isinstance(obj, tuple) and len(obj) == 2:
        chunk, metadata = obj
        return [serialize_lc_object(chunk), metadata if isinstance(metadata, dict) else {}]
    return serialize_lc_object(obj)


def serialize(obj: Any, *, mode: str = "") -> Any:
    """按模式对 LangChain 对象进行序列化。

    - ``messages`` —— ``obj`` 为 ``(message_chunk, metadata_dict)``；
    - ``values`` —— ``obj`` 为完整状态字典，会剥离 ``__pregel_*`` 键；
    - 其他 —— 回退到递归的 ``model_dump()`` / ``dict()``。
    """
    if mode == "messages":
        return serialize_messages_tuple(obj)
    if mode == "values":
        return serialize_channel_values(obj) if isinstance(obj, dict) else serialize_lc_object(obj)
    return serialize_lc_object(obj)
