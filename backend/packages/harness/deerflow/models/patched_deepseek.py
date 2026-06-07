"""在多轮对话中保留 ``reasoning_content`` 的 ``ChatDeepSeek`` 补丁。

本模块提供 :class:`ChatDeepSeek` 的补丁版本，使其在向 API 发送消息时
能正确处理 ``reasoning_content``。原实现把 ``reasoning_content`` 存放在
``additional_kwargs`` 中，但发起后续 API 调用时不会带上该字段，导致
那些在 thinking 启用时要求所有 assistant 消息都必须包含
``reasoning_content`` 的接口报错。
"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_deepseek import ChatDeepSeek

from deerflow.models.assistant_payload_replay import restore_assistant_payloads, restore_reasoning_content


class PatchedChatDeepSeek(ChatDeepSeek):
    """``reasoning_content`` 被正确保留的 :class:`ChatDeepSeek`。

    使用启用 thinking/reasoning 的模型时，API 要求多轮对话中所有
    assistant 消息都包含 ``reasoning_content``。本补丁版本确保
    ``additional_kwargs`` 中的 ``reasoning_content`` 被包含进请求负载。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """声明本类可被 LangChain 序列化。"""
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """声明需要从环境变量读取的密钥字段。"""
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """获取保留 ``reasoning_content`` 的请求负载。

        重写父类方法，把 ``additional_kwargs`` 中的 ``reasoning_content``
        注入到负载中的 assistant 消息。

        Args:
            input_: LangChain 模型输入。
            stop: 可选的停止词列表。
            **kwargs: 透传给父方法的额外参数。

        Returns:
            dict: 准备发送给 provider 的请求负载。
        """
        # 在转换前获取原始消息
        original_messages = self._convert_input(input_).to_messages()

        # 调用父方法得到基础负载
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        restore_assistant_payloads(
            payload.get("messages", []),
            original_messages,
            restore_reasoning_content,
        )

        return payload
