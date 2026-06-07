"""用于自动生成线程标题的中间件。"""


import logging
import re
from typing import TYPE_CHECKING, Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder
from deerflow.config.title_config import get_title_config
from deerflow.models import create_chat_model

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig
    from deerflow.config.title_config import TitleConfig

logger = logging.getLogger(__name__)


class TitleMiddlewareState(AgentState):
    """与 ``ThreadState`` 模式兼容的状态类型。"""

    title: NotRequired[str | None]


class TitleMiddleware(AgentMiddleware[TitleMiddlewareState]):
    """在首轮用户消息之后自动生成线程标题。"""

    state_schema = TitleMiddlewareState

    def __init__(self, *, app_config: "AppConfig | None" = None, title_config: "TitleConfig | None" = None):
        """初始化标题中间件。

        Args:
            app_config: 可选的应用配置，便于测试或非默认配置注入。
            title_config: 可选的显式标题配置；提供时优先于 ``app_config.title``。
        """
        super().__init__()
        self._app_config = app_config
        self._title_config = title_config

    def _get_title_config(self):
        """解析当前生效的标题配置，按显式 > app > 全局单例顺序查找。"""
        if self._title_config is not None:
            return self._title_config
        if self._app_config is not None:
            return self._app_config.title
        return get_title_config()

    def _normalize_content(self, content: object) -> str:
        """将结构化消息内容归一化为纯文本字符串。"""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [self._normalize_content(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            text_value = content.get("text")
            if isinstance(text_value, str):
                return text_value

            nested_content = content.get("content")
            if nested_content is not None:
                return self._normalize_content(nested_content)

        return ""

    @staticmethod
    def _is_user_message_for_title(message: object) -> bool:
        """判断消息是否可作为标题生成所用的真实用户消息。"""
        return getattr(message, "type", None) == "human" and not is_dynamic_context_reminder(message)

    def _should_generate_title(self, state: TitleMiddlewareState) -> bool:
        """判断是否应当为当前线程生成标题。"""
        config = self._get_title_config()
        if not config.enabled:
            return False

        # Check if thread already has a title in state
        if state.get("title"):
            return False

        # Check if this is the first turn (has at least one user message and one assistant response)
        messages = state.get("messages", [])
        if len(messages) < 2:
            return False

        # Count user and assistant messages
        user_messages = [m for m in messages if self._is_user_message_for_title(m)]
        assistant_messages = [m for m in messages if m.type == "ai"]

        # Generate title after first complete exchange
        return len(user_messages) == 1 and len(assistant_messages) >= 1

    def _build_title_prompt(self, state: TitleMiddlewareState) -> tuple[str, str]:
        """提取用户/助手消息并构建标题生成提示。

        Returns:
            ``(prompt_string, user_msg)`` 元组，调用方可使用 ``user_msg``
            作为回退。
        """
        config = self._get_title_config()
        messages = state.get("messages", [])

        user_msg_content = next((m.content for m in messages if self._is_user_message_for_title(m)), "")
        assistant_msg_content = next((m.content for m in messages if m.type == "ai"), "")

        user_msg = self._normalize_content(user_msg_content)
        assistant_msg = self._strip_think_tags(self._normalize_content(assistant_msg_content))

        prompt = config.prompt_template.format(
            max_words=config.max_words,
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500],
        )
        return prompt, user_msg

    def _strip_think_tags(self, text: str) -> str:
        """移除推理模型（例如 minimax、DeepSeek-R1）输出的 ``<think>...</think>`` 块。"""
        return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

    def _parse_title(self, content: object) -> str:
        """将模型输出归一化为干净的标题字符串。"""
        config = self._get_title_config()
        title_content = self._normalize_content(content)
        title_content = self._strip_think_tags(title_content)
        title = title_content.strip().strip('"').strip("'")
        return title[: config.max_chars] if len(title) > config.max_chars else title

    def _fallback_title(self, user_msg: str) -> str:
        """执行赋值。"""
        config = self._get_title_config()
        fallback_chars = min(config.max_chars, 50)
        if len(user_msg) > fallback_chars:
            return user_msg[:fallback_chars].rstrip() + "..."
        return user_msg if user_msg else "New Conversation"

    def _get_runnable_config(self) -> dict[str, Any]:
        """继承父级 ``RunnableConfig`` 并附加中间件标签。

        确保 ``RunJournal`` 将本中间件的 LLM 调用识别为 ``middleware:title``
        而非 ``lead_agent``。
        """
        try:
            parent = get_config()
        except Exception:
            parent = {}
        config = {**parent}
        config["run_name"] = "title_agent"
        config["tags"] = [*(config.get("tags") or []), "middleware:title"]
        return config

    def _generate_title_result(self, state: TitleMiddlewareState) -> dict | None:
        """生成本地回退标题，避免阻塞在 LLM 调用上。"""
        if not self._should_generate_title(state):
            return None

        _, user_msg = self._build_title_prompt(state)
        return {"title": self._fallback_title(user_msg)}

    async def _agenerate_title_result(self, state: TitleMiddlewareState) -> dict | None:
        """异步生成标题，在失败时回退到本地生成的标题。"""
        if not self._should_generate_title(state):
            return None

        config = self._get_title_config()
        prompt, user_msg = self._build_title_prompt(state)

        try:
            # attach_tracing=False because ``_get_runnable_config()`` inherits
            # the graph-level RunnableConfig (set in ``_make_lead_agent``) whose
            # callbacks already carry tracing handlers; binding them again at
            # the model level would emit duplicate spans.
            model_kwargs = {"thinking_enabled": False, "attach_tracing": False}
            if self._app_config is not None:
                model_kwargs["app_config"] = self._app_config
            if config.model_name:
                model = create_chat_model(name=config.model_name, **model_kwargs)
            else:
                model = create_chat_model(**model_kwargs)
            response = await model.ainvoke(prompt, config=self._get_runnable_config())
            title = self._parse_title(response.content)
            if title:
                return {"title": title}
        except Exception:
            logger.debug("Failed to generate async title; falling back to local title", exc_info=True)
        return {"title": self._fallback_title(user_msg)}

    @override
    def after_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """模型调用后同步钩子。"""
        return self._generate_title_result(state)

    @override
    async def aafter_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """模型调用后异步钩子。"""
        return await self._agenerate_title_result(state)
