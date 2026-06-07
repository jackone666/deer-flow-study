"""聊天模型工厂：基于配置构造 LangChain chat model。"""

import logging

from langchain.chat_models import BaseChatModel

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict | None, override: dict) -> dict:
    """递归合并两个 dict，不修改入参。

    Args:
        base: 基础字典，可为 ``None``。
        override: 覆盖字典；与 ``base`` 递归合并。

    Returns:
        dict: 合并后的新字典。
    """
    merged = dict(base or {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _vllm_disable_chat_template_kwargs(chat_template_kwargs: dict) -> dict:
    """构造用于关闭 vLLM/Qwen chat template kwargs 的负载。

    Args:
        chat_template_kwargs: 模型配置中的 chat template kwargs。

    Returns:
        dict: 用于关闭 thinking 的 kwargs 子集。
    """
    disable_kwargs: dict[str, bool] = {}
    if "thinking" in chat_template_kwargs:
        disable_kwargs["thinking"] = False
    if "enable_thinking" in chat_template_kwargs:
        disable_kwargs["enable_thinking"] = False
    return disable_kwargs


def _enable_stream_usage_by_default(model_use_path: str, model_settings_from_config: dict) -> None:
    """为 OpenAI 兼容模型默认开启 stream usage（除非显式配置）。

    LangChain 只在未配置自定义 base URL / client 时才会为 OpenAI 模型
    自动开启 ``stream_usage``。DeerFlow 经常使用 OpenAI 兼容网关，
    否则 token 用量追踪会留空，导致 :class:`TokenUsageMiddleware` 无可记。

    Args:
        model_use_path: 模型 ``use`` 字段（provider 类路径）。
        model_settings_from_config: 即将传给 provider 的设置字典（就地修改）。
    """
    if model_use_path != "langchain_openai:ChatOpenAI":
        return
    if "stream_usage" in model_settings_from_config:
        return
    if "base_url" in model_settings_from_config or "openai_api_base" in model_settings_from_config:
        model_settings_from_config["stream_usage"] = True


def create_chat_model(name: str | None = None, thinking_enabled: bool = False, *, app_config: AppConfig | None = None, attach_tracing: bool = True, **kwargs) -> BaseChatModel:
    """根据配置创建一个 chat model 实例。

    Args:
        name: 要创建的模型名；为 ``None`` 时取配置中第一个模型。
        thinking_enabled: 在模型支持时启用扩展 thinking 模式。
        app_config: 显式传入的应用配置；省略时回退到缓存的全局单例。
        attach_tracing: 默认为 ``True``，将 tracing 回调（Langfuse、LangSmith）
            直接挂到 model 实例。独立调用方（任何在 LangGraph run 之外
            调用模型的代码，如 :class:`MemoryUpdater`、ad-hoc 工具）应保留
            默认值，使 model 级别的回调仍能产生 trace。已经在 graph 根
            挂载 tracing 的调用方（``make_lead_agent``、图内的
            ``TitleMiddleware``）必须传 ``attach_tracing=False``；否则
            同一个 LLM 调用会产生重复 span（一个挂在 graph、一个挂在
            model），``session_id`` / ``user_id`` 元数据无法进入 trace，
            因为 model 会变成嵌套 observation，其 ``langfuse_*`` 字段
            会被剥掉。
        **kwargs: 透传给 provider 构造函数的额外参数。

    Returns:
        BaseChatModel: 构造完成的 chat model 实例。

    Raises:
        ValueError: 模型名在配置中找不到、模型不支持 thinking 但要求启用时。
    """
    config = app_config or get_app_config()
    if name is None:
        name = config.models[0].name
    model_config = config.get_model_config(name)
    if model_config is None:
        raise ValueError(f"Model {name} not found in config") from None
    model_class = resolve_class(model_config.use, BaseChatModel)
    model_settings_from_config = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "when_thinking_disabled",
            "thinking",
            "supports_vision",
        },
    )
    # 通过合并 `thinking` 简写字段计算有效的 when_thinking_enabled。
    # `thinking` 简写等价于设置 when_thinking_enabled["thinking"]。
    has_thinking_settings = (model_config.when_thinking_enabled is not None) or (model_config.thinking is not None)
    effective_wte: dict = dict(model_config.when_thinking_enabled) if model_config.when_thinking_enabled else {}
    if model_config.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **model_config.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}
    if thinking_enabled and has_thinking_settings:
        if not model_config.supports_thinking:
            raise ValueError(f"Model {name} does not support thinking. Set `supports_thinking` to true in the `config.yaml` to enable thinking.") from None
        if effective_wte:
            model_settings_from_config.update(effective_wte)
    if not thinking_enabled:
        if model_config.when_thinking_disabled is not None:
            # 用户提供的关闭设置拥有完全优先级
            model_settings_from_config.update(model_config.when_thinking_disabled)
        elif has_thinking_settings and effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            # OpenAI 兼容网关：thinking 嵌套在 extra_body 下
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"thinking": {"type": "disabled"}},
            )
            model_settings_from_config["reasoning_effort"] = "minimal"
        elif has_thinking_settings and (disable_chat_template_kwargs := _vllm_disable_chat_template_kwargs(effective_wte.get("extra_body", {}).get("chat_template_kwargs") or {})):
            # vLLM 使用 chat template kwargs 来切换 thinking 开关。
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"chat_template_kwargs": disable_chat_template_kwargs},
            )
        elif has_thinking_settings and effective_wte.get("thinking", {}).get("type"):
            # 原生 langchain_anthropic：thinking 是直接构造参数
            model_settings_from_config["thinking"] = {"type": "disabled"}
    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
        model_settings_from_config.pop("reasoning_effort", None)

    _enable_stream_usage_by_default(model_config.use, model_settings_from_config)

    # Codex Responses API 模型：把 thinking 模式映射到 reasoning_effort
    from deerflow.models.openai_codex_provider import CodexChatModel

    if issubclass(model_class, CodexChatModel):
        # ChatGPT Codex 端点当前拒绝 max_tokens/max_output_tokens。
        model_settings_from_config.pop("max_tokens", None)

        # 使用来自前端的显式 reasoning_effort（low/medium/high）
        explicit_effort = kwargs.pop("reasoning_effort", None)
        if not thinking_enabled:
            model_settings_from_config["reasoning_effort"] = "none"
        elif explicit_effort and explicit_effort in ("low", "medium", "high", "xhigh"):
            model_settings_from_config["reasoning_effort"] = explicit_effort
        elif "reasoning_effort" not in model_settings_from_config:
            model_settings_from_config["reasoning_effort"] = "medium"

    # MindIE 模型：强制使用保守的重试默认值。
    # 超时归一化由 MindIEChatModel 自身处理。
    if getattr(model_class, "__name__", "") == "MindIEChatModel":
        # 强制 max_retries 限制，避免超时级联。
        model_settings_from_config["max_retries"] = model_settings_from_config.get("max_retries", 1)

    # 显式确保 stream_usage 开启，使流式响应中能拿到 token 用量元数据。
    # LangChain 的 BaseChatOpenAI 仅在未设置自定义 base_url/api_base 时
    # 才默认 stream_usage=True，因此命中三方端点（如 doubao、deepseek）
    # 的模型会静默丢失 usage 数据。除非显式配置，否则这里默认开启。
    if "stream_usage" not in model_settings_from_config and "stream_usage" not in kwargs:
        if "stream_usage" in getattr(model_class, "model_fields", {}):
            model_settings_from_config["stream_usage"] = True

    model_instance = model_class(**kwargs, **model_settings_from_config)

    if attach_tracing:
        callbacks = build_tracing_callbacks()
        if callbacks:
            existing_callbacks = model_instance.callbacks or []
            model_instance.callbacks = [*existing_callbacks, *callbacks]
            logger.debug(f"Tracing attached to model '{name}' with providers={len(callbacks)}")
    return model_instance
