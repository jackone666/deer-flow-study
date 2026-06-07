"""Tracing 配置，支持 LangSmith、Langfuse 等 provider。"""

import os
import threading

from pydantic import BaseModel, Field

_config_lock = threading.Lock()


class LangSmithTracingConfig(BaseModel):
    """LangSmith tracing 配置。"""

    enabled: bool = Field(...)
    api_key: str | None = Field(...)
    project: str = Field(...)
    endpoint: str = Field(...)

    @property
    def is_configured(self) -> bool:
        """``enabled`` 且 ``api_key`` 非空时返回 ``True``。"""
        return self.enabled and bool(self.api_key)

    def validate(self) -> None:
        """校验启用时已配置 ``api_key``。

        Raises:
            ValueError: 启用但缺少 ``LANGSMITH_API_KEY``/``LANGCHAIN_API_KEY`` 时。
        """
        if self.enabled and not self.api_key:
            raise ValueError("LangSmith tracing 已启用，但未设置 LANGSMITH_API_KEY（或 LANGCHAIN_API_KEY）。")


class LangfuseTracingConfig(BaseModel):
    """Langfuse tracing 配置。"""

    enabled: bool = Field(...)
    public_key: str | None = Field(...)
    secret_key: str | None = Field(...)
    host: str = Field(...)

    @property
    def is_configured(self) -> bool:
        """``enabled`` 且 ``public_key``/``secret_key`` 都不为空时返回 ``True``。"""
        return self.enabled and bool(self.public_key) and bool(self.secret_key)

    def validate(self) -> None:
        """校验启用时已配置所需字段。

        Raises:
            ValueError: 启用但缺少 ``LANGFUSE_PUBLIC_KEY`` 或 ``LANGFUSE_SECRET_KEY`` 时。
        """
        if not self.enabled:
            return
        missing: list[str] = []
        if not self.public_key:
            missing.append("LANGFUSE_PUBLIC_KEY")
        if not self.secret_key:
            missing.append("LANGFUSE_SECRET_KEY")
        if missing:
            raise ValueError(f"Langfuse tracing 已启用，但缺少必需设置：{', '.join(missing)}")


class TracingConfig(BaseModel):
    """支持 provider 的 tracing 配置。"""

    langsmith: LangSmithTracingConfig = Field(...)
    langfuse: LangfuseTracingConfig = Field(...)

    @property
    def is_configured(self) -> bool:
        """当存在已启用的 provider 时返回 ``True``。"""
        return bool(self.enabled_providers)

    @property
    def explicitly_enabled_providers(self) -> list[str]:
        """返回配置中显式启用的 provider 名列表（即便不完整）。"""
        enabled: list[str] = []
        if self.langsmith.enabled:
            enabled.append("langsmith")
        if self.langfuse.enabled:
            enabled.append("langfuse")
        return enabled

    @property
    def enabled_providers(self) -> list[str]:
        """返回已启用且配置完整的 provider 名列表。"""
        enabled: list[str] = []
        if self.langsmith.is_configured:
            enabled.append("langsmith")
        if self.langfuse.is_configured:
            enabled.append("langfuse")
        return enabled

    def validate_enabled(self) -> None:
        """校验所有显式启用的 provider 配置是否完整。"""
        self.langsmith.validate()
        self.langfuse.validate()


_tracing_config: TracingConfig | None = None


_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _env_flag_preferred(*names: str) -> bool:
    """返回首个存在且非空的环境变量对应的布尔值。

    Args:
        names: 按优先级排列的环境变量名列表。

    Returns:
        bool: 首个环境变量存在且非空时按 ``_TRUTHY_VALUES`` 解析；否则返回 ``False``。
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip().lower() in _TRUTHY_VALUES
    return False


def _first_env_value(*names: str) -> str | None:
    """从候选环境变量名中返回首个非空值（已去首尾空白）。

    Args:
        names: 按优先级排列的环境变量名列表。

    Returns:
        str | None: 首个非空值；都不存在时返回 ``None``。
    """
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def get_tracing_config() -> TracingConfig:
    """从环境变量获取当前 tracing 配置（惰性 + 线程安全单例）。

    Returns:
        TracingConfig: 进程级单例配置对象。
    """
    global _tracing_config
    if _tracing_config is not None:
        return _tracing_config
    with _config_lock:
        if _tracing_config is not None:
            return _tracing_config
        _tracing_config = TracingConfig(
            langsmith=LangSmithTracingConfig(
                enabled=_env_flag_preferred("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"),
                api_key=_first_env_value("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
                project=_first_env_value("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT") or "deer-flow",
                endpoint=_first_env_value("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT") or "https://api.smith.langchain.com",
            ),
            langfuse=LangfuseTracingConfig(
                enabled=_env_flag_preferred("LANGFUSE_TRACING"),
                public_key=_first_env_value("LANGFUSE_PUBLIC_KEY"),
                secret_key=_first_env_value("LANGFUSE_SECRET_KEY"),
                host=_first_env_value("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com",
            ),
        )
        return _tracing_config


def get_enabled_tracing_providers() -> list[str]:
    """返回已启用且配置完整的 tracing provider 列表。

    Returns:
        list[str]: provider 名列表。
    """
    return get_tracing_config().enabled_providers


def get_explicitly_enabled_tracing_providers() -> list[str]:
    """返回配置中显式启用的 provider 列表（即便不完整）。

    Returns:
        list[str]: provider 名列表。
    """
    return get_tracing_config().explicitly_enabled_providers


def validate_enabled_tracing_providers() -> None:
    """校验所有显式启用的 provider 是否配置完整。"""
    get_tracing_config().validate_enabled()


def is_tracing_enabled() -> bool:
    """判断是否存在已启用且配置完整的 tracing provider。

    Returns:
        bool: 至少一个 provider 完整启用时为 ``True``。
    """
    return get_tracing_config().is_configured


def reset_tracing_config() -> None:
    """丢弃缓存的 :class:`TracingConfig`，下次访问时重建。

    对外暴露的测试 API，避免测试用例直接修改私有模块属性
    ``_tracing_config``；未来若发生内部重命名也不会被破坏。
    """
    global _tracing_config
    with _config_lock:
        _tracing_config = None
