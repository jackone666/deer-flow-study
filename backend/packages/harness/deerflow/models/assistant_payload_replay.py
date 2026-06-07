"""用于回放 provider 专属 assistant 消息字段的辅助函数。

多个 provider 适配器需要保留 LangChain 在原始 :class:`AIMessage` 上存储、
但在序列化请求负载时被丢弃的字段。本模块保持 assistant 消息匹配逻辑共享，
同时让每个 provider 自行决定要恢复哪些字段。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

AssistantPayloadRestorer = Callable[[dict[str, Any], AIMessage], None]


def restore_assistant_payloads(
    payload_messages: Sequence[dict[str, Any]],
    original_messages: Sequence[BaseMessage],
    restore: AssistantPayloadRestorer,
) -> None:
    """将 provider 专属字段恢复到序列化后的 assistant 负载。

    Args:
        payload_messages: 序列化后的消息字典序列（按位置）。
        original_messages: 原始 LangChain :class:`BaseMessage` 序列。
        restore: 用于在 ``(payload_msg, orig_msg)`` 上恢复字段的回调。
    """
    if len(payload_messages) == len(original_messages):
        for payload_msg, orig_msg in zip(payload_messages, original_messages):
            if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                restore(payload_msg, orig_msg)
        return

    ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
    assistant_payloads = [m for m in payload_messages if m.get("role") == "assistant"]
    used_ai_indexes: set[int] = set()

    for ordinal, payload_msg in enumerate(assistant_payloads):
        ai_msg = _match_ai_message(payload_msg, ai_messages, used_ai_indexes, ordinal)
        if ai_msg is not None:
            restore(payload_msg, ai_msg)


def restore_additional_kwargs_field(payload_msg: dict[str, Any], orig_msg: AIMessage, field_name: str) -> None:
    """把 provider 专属 ``additional_kwargs`` 字段复制到负载消息中。

    Args:
        payload_msg: 序列化后的 assistant 消息字典（原地修改）。
        orig_msg: 原始 :class:`AIMessage`。
        field_name: 要复制的字段名。
    """
    value = orig_msg.additional_kwargs.get(field_name)
    if value is not None:
        payload_msg[field_name] = value


def restore_reasoning_content(payload_msg: dict[str, Any], orig_msg: AIMessage) -> None:
    """把 provider 推理内容复制到序列化后的 assistant 负载。"""
    restore_additional_kwargs_field(payload_msg, orig_msg, "reasoning_content")


def _match_ai_message(
    payload_msg: dict[str, Any],
    ai_messages: Sequence[AIMessage],
    used_ai_indexes: set[int],
    fallback_ordinal: int,
) -> AIMessage | None:
    """按内容+tool_call 签名匹配最佳 :class:`AIMessage`，失败时回退到位置序。"""
    payload_key = _assistant_signature(payload_msg)
    if payload_key is not None:
        matches = [index for index, ai_msg in enumerate(ai_messages) if index not in used_ai_indexes and _ai_signature(ai_msg) == payload_key]
        if len(matches) == 1:
            used_ai_indexes.add(matches[0])
            return ai_messages[matches[0]]

    fallback_index = _next_unused_index_at_or_after(len(ai_messages), used_ai_indexes, fallback_ordinal)
    if fallback_index is not None:
        used_ai_indexes.add(fallback_index)
        return ai_messages[fallback_index]

    return None


def _next_unused_index_at_or_after(count: int, used_ai_indexes: set[int], start: int) -> int | None:
    """返回 ``start`` 之后（含）第一个未使用的 AI 索引。

    从负载的 ordinal 向前扫描保留了旧行为的位置偏好，同时在序列化
    丢消息或重排导致精确 ordinal 已被占用时仍能恢复。不会回卷到
    更早的索引，因为那些消息可能对应已经被丢弃的负载条目。

    Args:
        count: AI 消息总数。
        used_ai_indexes: 已使用的索引集合。
        start: 扫描起点。

    Returns:
        int | None: 命中的索引；找不到则返回 ``None``。
    """
    if count == 0 or start >= count:
        return None
    for index in range(start, count):
        if index not in used_ai_indexes:
            return index
    return None


def _assistant_signature(payload_msg: dict[str, Any]) -> tuple[str, str] | None:
    """构造 assistant 负载的签名（内容 + tool_call id 列表）。"""
    return _signature(
        payload_msg.get("content"),
        _tool_call_ids(payload_msg.get("tool_calls") or []),
    )


def _ai_signature(message: AIMessage) -> tuple[str, str] | None:
    """构造 :class:`AIMessage` 的签名（内容 + tool_call id 列表）。"""
    tool_calls = message.tool_calls or message.additional_kwargs.get("tool_calls") or []
    return _signature(message.content, _tool_call_ids(tool_calls))


def _signature(content: Any, tool_call_ids: tuple[str, ...]) -> tuple[str, str] | None:
    """构造 ``(content_repr, "|"-joined ids)`` 签名；内容与 id 都为空时返回 ``None``。"""
    if content in (None, "") and not tool_call_ids:
        return None
    return (_stable_repr(content), "|".join(tool_call_ids))


def _stable_repr(value: Any) -> str:
    """得到 ``value`` 的稳定字符串表示（JSON 失败时回退到 ``repr``）。"""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return repr(value)


def _tool_call_ids(tool_calls: Sequence[Any]) -> tuple[str, ...]:
    """从 tool_call 序列中抽取所有非空字符串 id。"""
    ids: list[str] = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            call_id = tool_call.get("id")
            if isinstance(call_id, str) and call_id:
                ids.append(call_id)
    return tuple(ids)
