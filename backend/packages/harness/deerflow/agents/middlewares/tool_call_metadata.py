"""用于保持 AIMessage 工具调用元数据一致的辅助函数。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def _raw_tool_call_id(raw_tool_call: Any) -> str | None:
    """内部辅助方法。"""
    if not isinstance(raw_tool_call, dict):
        return None

    raw_id = raw_tool_call.get("id")
    return raw_id if isinstance(raw_id, str) and raw_id else None


def clone_ai_message_with_tool_calls(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """克隆 AIMessage 并保持原始提供方工具调用元数据同步。

    当中间件需要修改 AIMessage 的 ``tool_calls`` 时（如截断、剥离），
    简单的 ``model_copy(update={"tool_calls": [...]})`` 会导致
    ``additional_kwargs.tool_calls``（原始提供方 payload）与结构化
    ``tool_calls`` 不一致，从而引发 LLM API 校验失败。

    本函数同步处理三处：
    1. ``tool_calls`` — 结构化列表（LangChain 标准字段）
    2. ``additional_kwargs.tool_calls`` — 原始提供方 payload（按 id 过滤）
    3. ``response_metadata.finish_reason`` — tool_calls 为空时设为 "stop"

    使用示例：
    ```python
    # 截断：只保留前两个 tool_call
    truncated = clone_ai_message_with_tool_calls(msg, msg.tool_calls[:2])
    # → 返回新 AIMessage，additional_kwargs 自动同步

    # 清空 + 改 content
    cleared = clone_ai_message_with_tool_calls(msg, [], content="已处理")
    # → tool_calls=[], finish_reason="stop", content="已处理"
    ```

    Args:
        message: 原始 AIMessage。
        tool_calls: 新的 tool_calls 列表（LangChain 结构化格式）。
        content: 可选的新 content（None 表示保留原值）。

    Returns:
        同步了所有元数据字段的新 AIMessage。
    """
    kept_ids = {tc["id"] for tc in tool_calls if isinstance(tc.get("id"), str) and tc["id"]}

    update: dict[str, Any] = {"tool_calls": tool_calls}
    if content is not None:
        update["content"] = content

    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        synced_raw_tool_calls = [raw_tc for raw_tc in raw_tool_calls if _raw_tool_call_id(raw_tc) in kept_ids]
        if synced_raw_tool_calls:
            additional_kwargs["tool_calls"] = synced_raw_tool_calls
        else:
            additional_kwargs.pop("tool_calls", None)

    if not tool_calls:
        additional_kwargs.pop("function_call", None)

    update["additional_kwargs"] = additional_kwargs

    response_metadata = dict(getattr(message, "response_metadata", {}) or {})
    if not tool_calls and response_metadata.get("finish_reason") == "tool_calls":
        response_metadata["finish_reason"] = "stop"
    update["response_metadata"] = response_metadata

    return message.model_copy(update=update)
