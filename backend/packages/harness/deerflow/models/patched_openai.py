"""在 Gemini thinking 模型上保留 ``thought_signature`` 的 ``ChatOpenAI`` 补丁。

当通过 OpenAI 兼容网关（Vertex AI、Google AI Studio 或任何代理）
使用启用 thinking 的 Gemini 时，API 要求 tool-call 对象上的
``thought_signature`` 字段在每个后续请求中原样回传。

OpenAI 兼容网关会把包含 ``thought_signature`` 的原始 tool-call 字典
存放在 ``additional_kwargs["tool_calls"]`` 中，但标准
``langchain_openai.ChatOpenAI`` 在序列化出站负载时只保留
``id``/``type``/``function`` 等标准字段，会静默丢掉签名，
导致 HTTP 400 ``INVALID_ARGUMENT`` 错误：

    Unable to submit request because function call `<tool>` in the N. content
    block is missing a `thought_signature`.

本模块通过重写 ``_get_request_payload``，在出站负载中对携带
``thought_signature`` 的 assistant 消息重新注入签名来解决该问题。
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from deerflow.models.assistant_payload_replay import restore_assistant_payloads


class PatchedChatOpenAI(ChatOpenAI):
    """为通过 OpenAI 网关使用 Gemini thinking 的场景保留 ``thought_signature`` 的 ``ChatOpenAI``。

    通过 OpenAI 兼容网关使用启用 thinking 的 Gemini 时，API 期望
    多轮对话的 tool-call 对象上存在 ``thought_signature``。本补丁类
    在请求负载发送前，会从 ``AIMessage.additional_kwargs["tool_calls"]``
    中恢复这些签名。

    在 ``config.yaml`` 中的用法示例：::

        - name: gemini-2.5-pro-thinking
          display_name: Gemini 2.5 Pro (Thinking)
          use: deerflow.models.patched_openai:PatchedChatOpenAI
          model: google/gemini-2.5-pro-preview
          api_key: $GEMINI_API_KEY
          base_url: https://<your-openai-compat-gateway>/v1
          max_tokens: 16384
          supports_thinking: true
          supports_vision: true
          when_thinking_enabled:
            extra_body:
              thinking:
                type: enabled
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """构造请求负载，并保留 tool-call 对象上的 ``thought_signature``。

        重写父类方法，把 LangChain 存放在 ``additional_kwargs["tool_calls"]``
        中、但序列化时被丢弃的 ``thought_signature`` 字段重新注入。

        Args:
            input_: LangChain 模型输入。
            stop: 可选的停止词列表。
            **kwargs: 透传给父方法的额外参数。

        Returns:
            dict: 准备发送给 provider 的请求负载。
        """
        # 在转换前先捕获原始 LangChain 消息，以便访问序列化时可能丢失的字段。
        original_messages = self._convert_input(input_).to_messages()

        # 由父实现得到基础负载。
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        restore_assistant_payloads(payload.get("messages", []), original_messages, _restore_tool_call_signatures)

        return payload


def _restore_tool_call_signatures(payload_msg: dict, orig_msg: AIMessage) -> None:
    """把 ``thought_signature`` 重新注入 ``payload_msg`` 的 tool-call 对象。

    当 Gemini 的 OpenAI 兼容网关返回包含函数调用的响应时，每个
    tool-call 对象可能携带一个 ``thought_signature``。LangChain 会
    把原始的 tool-call 字典存放在 ``additional_kwargs["tool_calls"]``，
    但只在序列化出站负载时保留标准字段（``id``/``type``/``function``），
    静默丢弃签名。

    本函数按 ``id`` 匹配原始 tool-call（无法匹配时回退到位置序），
    并把签名复制回序列化后的负载条目。

    Args:
        payload_msg: 序列化后的 assistant 消息字典（原地修改）。
        orig_msg: 原始 :class:`AIMessage`。
    """
    raw_tool_calls: list[dict] = orig_msg.additional_kwargs.get("tool_calls") or []
    payload_tool_calls: list[dict] = payload_msg.get("tool_calls") or []

    if not raw_tool_calls or not payload_tool_calls:
        return

    # 构造 id → raw_tc 映射以加速匹配。
    raw_by_id: dict[str, dict] = {}
    for raw_tc in raw_tool_calls:
        tc_id = raw_tc.get("id")
        if tc_id:
            raw_by_id[tc_id] = raw_tc

    for idx, payload_tc in enumerate(payload_tool_calls):
        # 先按 id 匹配，再回退到位置序。
        raw_tc = raw_by_id.get(payload_tc.get("id", ""))
        if raw_tc is None and idx < len(raw_tool_calls):
            raw_tc = raw_tool_calls[idx]

        if raw_tc is None:
            continue

        # 网关可能使用 snake_case 或 camelCase 字段名。
        sig = raw_tc.get("thought_signature") or raw_tc.get("thoughtSignature")
        if sig:
            payload_tc["thought_signature"] = sig
