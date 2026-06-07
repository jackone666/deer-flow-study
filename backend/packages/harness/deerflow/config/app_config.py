"""DeerFlow 顶层应用配置。"""

import logging
import os
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Self

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from deerflow.config.acp_config import ACPAgentConfig, load_acp_config_from_dict
from deerflow.config.agents_api_config import AgentsApiConfig, load_agents_api_config_from_dict
from deerflow.config.checkpointer_config import CheckpointerConfig, load_checkpointer_config_from_dict
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.guardrails_config import GuardrailsConfig, load_guardrails_config_from_dict
from deerflow.config.loop_detection_config import LoopDetectionConfig
from deerflow.config.memory_config import MemoryConfig, load_memory_config_from_dict
from deerflow.config.model_config import ModelConfig
from deerflow.config.run_events_config import RunEventsConfig
from deerflow.config.runtime_paths import existing_project_file
from deerflow.config.safety_finish_reason_config import SafetyFinishReasonConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.skill_evolution_config import SkillEvolutionConfig
from deerflow.config.skills_config import SkillsConfig
from deerflow.config.stream_bridge_config import StreamBridgeConfig, load_stream_bridge_config_from_dict
from deerflow.config.subagents_config import SubagentsAppConfig, load_subagents_config_from_dict
from deerflow.config.summarization_config import SummarizationConfig, load_summarization_config_from_dict
from deerflow.config.title_config import TitleConfig, load_title_config_from_dict
from deerflow.config.token_usage_config import TokenUsageConfig
from deerflow.config.tool_config import ToolConfig, ToolGroupConfig
from deerflow.config.tool_output_config import ToolOutputConfig
from deerflow.config.tool_search_config import ToolSearchConfig, load_tool_search_config_from_dict

load_dotenv()

logger = logging.getLogger(__name__)


CONFIG_FILE_DATABASE_DEFAULTS = {
    "backend": "sqlite",
    "sqlite_dir": ".deer-flow/data",
}


class CircuitBreakerConfig(BaseModel):
    """LLM Circuit Breaker 配置。"""

    failure_threshold: int = Field(default=5, description="熔断器跳闸前允许的连续失败次数")
    recovery_timeout_sec: int = Field(default=60, description="尝试恢复熔断器前的等待秒数")


def _legacy_config_candidates() -> tuple[Path, ...]:
    """为 monorepo 兼容而返回源码树中的 config.yaml 候选位置。"""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (backend_dir / "config.yaml", repo_root / "config.yaml")


def logging_level_from_config(name: str | None) -> int:
    """将 ``config.yaml`` 中的 ``log_level`` 字符串映射为 :mod:`logging` 级别常量。

    Args:
        name: 配置中的 ``log_level`` 字符串；为空时按 ``info`` 处理。

    Returns:
        int: 对应的 logging 级别常量。
    """
    mapping = logging.getLevelNamesMapping()
    return mapping.get((name or "info").strip().upper(), logging.INFO)


def apply_logging_level(name: str | None) -> None:
    """将 ``name`` 解析为 logging 级别，并应用到 ``deerflow``/``app`` logger 层级。

    只调整 ``deerflow`` 与 ``app`` 的 logger 级别，不影响第三方库
    （如 uvicorn、sqlalchemy）的详细程度。根 handler 的级别只会被调低
    （不会被调高），以便被配置 logger 的消息可以原样传播，同时保留
    第三方日志输出可能被刻意收紧的 handler 阈值。
    """
    level = logging_level_from_config(name)
    for logger_name in ("deerflow", "app"):
        logging.getLogger(logger_name).setLevel(level)
    for handler in logging.root.handlers:
        if level < handler.level:
            handler.setLevel(level)


class AppConfig(BaseModel):
    """DeerFlow 应用配置。"""

    log_level: str = Field(default="info", description="deerflow 与 app 模块的日志级别（debug/info/warning/error），不影响第三方库")
    token_usage: TokenUsageConfig = Field(default_factory=TokenUsageConfig, description="Token 用量追踪配置")
    models: list[ModelConfig] = Field(default_factory=list, description="可用模型列表")
    sandbox: SandboxConfig = Field(description="沙箱配置")
    tools: list[ToolConfig] = Field(default_factory=list, description="可用工具列表")
    tool_groups: list[ToolGroupConfig] = Field(default_factory=list, description="可用工具组列表")
    skills: SkillsConfig = Field(default_factory=SkillsConfig, description="Skills 配置")
    skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig, description="Agent 管理的 skill 演化配置")
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig, description="extensions 配置（MCP servers 与 skills 状态）")
    tool_output: ToolOutputConfig = Field(default_factory=ToolOutputConfig, description="工具输出预算保护配置")
    tool_search: ToolSearchConfig = Field(default_factory=ToolSearchConfig, description="tool search / 延迟加载配置")
    title: TitleConfig = Field(default_factory=TitleConfig, description="自动标题生成配置")
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig, description="对话摘要配置")
    memory: MemoryConfig = Field(default_factory=MemoryConfig, description="memory 子系统配置")
    agents_api: AgentsApiConfig = Field(default_factory=AgentsApiConfig, description="自定义 agent 管理 API 配置")
    acp_agents: dict[str, ACPAgentConfig] = Field(default_factory=dict, description="ACP 兼容 agent 配置")
    subagents: SubagentsAppConfig = Field(default_factory=SubagentsAppConfig, description="subagent 运行时配置")
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig, description="guardrail 中间件配置")
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig, description="LLM 熔断器配置")
    loop_detection: LoopDetectionConfig = Field(default_factory=LoopDetectionConfig, description="循环检测中间件配置")
    safety_finish_reason: SafetyFinishReasonConfig = Field(default_factory=SafetyFinishReasonConfig, description="provider safety-filter finish_reason 拦截中间件配置")
    model_config = ConfigDict(extra="allow")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig, description="统一数据库后端配置")
    run_events: RunEventsConfig = Field(default_factory=RunEventsConfig, description="run 事件存储配置")
    checkpointer: CheckpointerConfig | None = Field(default=None, description="checkpointer 配置")
    stream_bridge: StreamBridgeConfig | None = Field(default=None, description="stream bridge 配置")

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path:
        """解析配置文件路径。

        优先级：
        1. 显式传入的 ``config_path`` 参数。
        2. ``DEER_FLOW_CONFIG_PATH`` 环境变量。
        3. 调用方项目根下查找。
        4. 出于 monorepo 兼容，回退到 backend/仓库根的旧位置。

        Returns:
            Path: 解析得到的配置文件绝对路径。

        Raises:
            FileNotFoundError: 显式参数、环境变量、查找路径下均未找到文件。
        """
        if config_path:
            path = Path(config_path)
            if not Path.exists(path):
                raise FileNotFoundError(f"参数 `config_path` 指定的配置文件在 {path} 找不到")
            return path
        elif os.getenv("DEER_FLOW_CONFIG_PATH"):
            path = Path(os.getenv("DEER_FLOW_CONFIG_PATH"))
            if not Path.exists(path):
                raise FileNotFoundError(f"环境变量 `DEER_FLOW_CONFIG_PATH` 指定的配置文件在 {path} 找不到")
            return path
        else:
            project_config = existing_project_file(("config.yaml",))
            if project_config is not None:
                return project_config

            for path in _legacy_config_candidates():
                if path.exists():
                    return path
            raise FileNotFoundError("在项目根或 backend/仓库根等旧位置均未找到 `config.yaml` 文件")

    @classmethod
    def from_file(cls, config_path: str | None = None) -> Self:
        """从 YAML 文件加载配置。

        路径解析细节见 :meth:`resolve_config_path`。

        Args:
            config_path: 可选的配置文件路径。

        Returns:
            AppConfig: 加载到的配置对象。
        """
        resolved_path = cls.resolve_config_path(config_path)
        with open(resolved_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

        # 处理前先检查 config 版本
        cls._check_config_version(config_data, resolved_path)

        config_data = cls.resolve_env_variables(config_data)
        cls._apply_database_defaults(config_data)

        # 加载 circuit_breaker 配置（如有）
        if "circuit_breaker" in config_data:
            config_data["circuit_breaker"] = config_data["circuit_breaker"]

        # extensions 配置单独从另一份文件加载
        extensions_config = ExtensionsConfig.from_file()
        config_data["extensions"] = extensions_config.model_dump()

        result = cls.model_validate(config_data)
        acp_agents = cls._validate_acp_agents(config_data.get("acp_agents", {}))
        cls._apply_singleton_configs(result, acp_agents)
        return result

    @classmethod
    def _validate_acp_agents(
        cls,
        config_data: Mapping[str, Mapping[str, object]] | None,
    ) -> dict[str, ACPAgentConfig]:
        """校验并构造 ``acp_agents`` 段。"""
        if config_data is None:
            config_data = {}
        return {name: ACPAgentConfig(**cfg) for name, cfg in config_data.items()}

    @classmethod
    def _apply_singleton_configs(cls, config: Self, acp_agents: dict[str, ACPAgentConfig]) -> None:
        """把配置中的各子系统配置写入对应的全局单例。"""
        from deerflow.config.checkpointer_config import get_checkpointer_config

        previous_checkpointer_config = get_checkpointer_config()

        load_title_config_from_dict(config.title.model_dump())
        load_summarization_config_from_dict(config.summarization.model_dump())
        load_memory_config_from_dict(config.memory.model_dump())
        load_agents_api_config_from_dict(config.agents_api.model_dump())
        load_subagents_config_from_dict(config.subagents.model_dump())
        load_tool_search_config_from_dict(config.tool_search.model_dump())
        load_guardrails_config_from_dict(config.guardrails.model_dump())
        load_checkpointer_config_from_dict(config.checkpointer.model_dump() if config.checkpointer is not None else None)
        load_stream_bridge_config_from_dict(config.stream_bridge.model_dump() if config.stream_bridge is not None else None)
        load_acp_config_from_dict({name: agent.model_dump() for name, agent in acp_agents.items()})

        if previous_checkpointer_config != config.checkpointer:
            # 这些 runtime 单例的后端派生自 checkpointer 配置。
            # 局部导入以避免循环：两个 provider 都会导入 get_app_config。
            from deerflow.runtime.checkpointer import reset_checkpointer
            from deerflow.runtime.store import reset_store

            reset_checkpointer()
            reset_store()

    @classmethod
    def _apply_database_defaults(cls, config_data: dict[str, Any]) -> None:
        """当配置中缺少 ``database`` 段时应用 config.yaml 默认值。"""
        database_config = config_data.get("database")
        if database_config is None:
            database_config = {}
            config_data["database"] = database_config
        if not isinstance(database_config, dict):
            return
        for key, value in CONFIG_FILE_DATABASE_DEFAULTS.items():
            database_config.setdefault(key, value)

    @classmethod
    def _check_config_version(cls, config_data: dict, config_path: Path) -> None:
        """检查用户 config.yaml 是否相对 config.example.yaml 已经过时。

        当用户 config_version 低于示例版本时发出告警；缺失
        ``config_version`` 视为版本 0（未启用版本化之前）。
        """
        try:
            user_version = int(config_data.get("config_version", 0))
        except (TypeError, ValueError):
            user_version = 0

        # 从 config.yaml 所在目录开始，向上查找 config.example.yaml
        example_path = None
        search_dir = config_path.parent
        for _ in range(5):  # 最多向上 5 层
            candidate = search_dir / "config.example.yaml"
            if candidate.exists():
                example_path = candidate
                break
            parent = search_dir.parent
            if parent == search_dir:
                break
            search_dir = parent
        if example_path is None:
            return

        try:
            with open(example_path, encoding="utf-8") as f:
                example_data = yaml.safe_load(f)
            raw = example_data.get("config_version", 0) if example_data else 0
            try:
                example_version = int(raw)
            except (TypeError, ValueError):
                example_version = 0
        except Exception:
            return

        if user_version < example_version:
            logger.warning(
                "你的 config.yaml（版本 %d）已过期，最新版本为 %d。运行 `make config-upgrade` 合并新字段。",
                user_version,
                example_version,
            )

    @classmethod
    def resolve_env_variables(cls, config: Any) -> Any:
        """递归解析配置中的环境变量。

        通过 ``os.getenv`` 解析 ``$VAR`` 形式的占位符。示例：``$OPENAI_API_KEY``。

        Args:
            config: 任意配置结构（dict/list/str/其他）。

        Returns:
            Any: 解析环境变量后的等价结构。

        Raises:
            ValueError: 占位符指向未设置的环境变量时。
        """
        if isinstance(config, str):
            if config.startswith("$"):
                env_value = os.getenv(config[1:])
                if env_value is None:
                    raise ValueError(f"环境变量 {config[1:]} 未找到，无法解析配置值 {config}")
                return env_value
            return config
        elif isinstance(config, dict):
            return {k: cls.resolve_env_variables(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [cls.resolve_env_variables(item) for item in config]
        return config

    def get_model_config(self, name: str) -> ModelConfig | None:
        """按名称查找模型配置。

        Args:
            name: 要查找的模型名。

        Returns:
            ModelConfig | None: 命中则返回对应配置；未找到返回 ``None``。
        """
        return next((model for model in self.models if model.name == name), None)

    def get_tool_config(self, name: str) -> ToolConfig | None:
        """按名称查找工具配置。

        Args:
            name: 要查找的工具名。

        Returns:
            ToolConfig | None: 命中则返回对应配置；未找到返回 ``None``。
        """
        return next((tool for tool in self.tools if tool.name == name), None)

    def get_tool_group_config(self, name: str) -> ToolGroupConfig | None:
        """按名称查找工具组配置。

        Args:
            name: 要查找的工具组名。

        Returns:
            ToolGroupConfig | None: 命中则返回对应配置；未找到返回 ``None``。
        """
        return next((group for group in self.tool_groups if group.name == name), None)


# 兼容旧路径的单例层。尚未迁移到显式 ``AppConfig`` 透传的代码路径仍依赖该单例。
# 新的 composition root 应优先构造一次 ``AppConfig`` 并显式向下传递。
_app_config: AppConfig | None = None
_app_config_path: Path | None = None
_app_config_mtime: float | None = None
_app_config_is_custom = False
_current_app_config: ContextVar[AppConfig | None] = ContextVar("deerflow_current_app_config", default=None)
_current_app_config_stack: ContextVar[tuple[AppConfig | None, ...]] = ContextVar("deerflow_current_app_config_stack", default=())


def _get_config_mtime(config_path: Path) -> float | None:
    """获取配置文件的修改时间（若存在）。"""
    try:
        return config_path.stat().st_mtime
    except OSError:
        return None


def _load_and_cache_app_config(config_path: str | None = None) -> AppConfig:
    """从磁盘加载配置并刷新缓存元信息。"""
    global _app_config, _app_config_path, _app_config_mtime, _app_config_is_custom

    resolved_path = AppConfig.resolve_config_path(config_path)
    _app_config = AppConfig.from_file(str(resolved_path))
    _app_config_path = resolved_path
    _app_config_mtime = _get_config_mtime(resolved_path)
    _app_config_is_custom = False
    return _app_config


def get_app_config() -> AppConfig:
    """获取 DeerFlow 配置实例。

    返回缓存的单例实例，并在底层配置文件的路径或修改时间变化时自动
    重新加载。可以通过 :func:`reload_app_config` 强制重载，或
    :func:`reset_app_config` 清空缓存。

    Returns:
        AppConfig: 当前的 :class:`AppConfig` 实例。
    """
    global _app_config, _app_config_path, _app_config_mtime

    runtime_override = _current_app_config.get()
    if runtime_override is not None:
        return runtime_override

    if _app_config is not None and _app_config_is_custom:
        return _app_config

    resolved_path = AppConfig.resolve_config_path()
    current_mtime = _get_config_mtime(resolved_path)

    should_reload = _app_config is None or _app_config_path != resolved_path or _app_config_mtime != current_mtime
    if should_reload:
        if _app_config_path == resolved_path and _app_config_mtime is not None and current_mtime is not None and _app_config_mtime != current_mtime:
            logger.info(
                "配置文件已修改（mtime: %s -> %s），重新加载 AppConfig",
                _app_config_mtime,
                current_mtime,
            )
        _load_and_cache_app_config(str(resolved_path))
    return _app_config


def reload_app_config(config_path: str | None = None) -> AppConfig:
    """从文件重新加载配置并更新缓存实例。

    当配置文件发生修改，希望不重启应用就拿到最新配置时使用。

    Args:
        config_path: 可选的配置文件路径；不传则使用默认解析策略。

    Returns:
        AppConfig: 新加载的 :class:`AppConfig` 实例。
    """
    return _load_and_cache_app_config(config_path)


def reset_app_config() -> None:
    """重置缓存的配置实例。

    清空单例缓存，使下一次调用 :func:`get_app_config` 重新从文件加载。
    常用于测试或在不同配置之间切换。
    """
    global _app_config, _app_config_path, _app_config_mtime, _app_config_is_custom
    _app_config = None
    _app_config_path = None
    _app_config_mtime = None
    _app_config_is_custom = False


def set_app_config(config: AppConfig) -> None:
    """直接设置一个自定义配置实例。

    主要用于在测试中注入自定义或 mock 配置。

    Args:
        config: 目标 :class:`AppConfig` 实例。
    """
    global _app_config, _app_config_path, _app_config_mtime, _app_config_is_custom
    _app_config = config
    _app_config_path = None
    _app_config_mtime = None
    _app_config_is_custom = True


def peek_current_app_config() -> AppConfig | None:
    """返回当前执行上下文中正在使用的 :class:`AppConfig` 覆盖（若存在）。

    Returns:
        AppConfig | None: 运行时 override 存在时返回对应实例，否则 ``None``。
    """
    return _current_app_config.get()


def push_current_app_config(config: AppConfig) -> None:
    """为当前执行上下文压入一个运行时 :class:`AppConfig` 覆盖。"""
    stack = _current_app_config_stack.get()
    _current_app_config_stack.set(stack + (_current_app_config.get(),))
    _current_app_config.set(config)


def pop_current_app_config() -> None:
    """弹出当前执行上下文最近压入的运行时 :class:`AppConfig` 覆盖。"""
    stack = _current_app_config_stack.get()
    if not stack:
        _current_app_config.set(None)
        return
    previous = stack[-1]
    _current_app_config_stack.set(stack[:-1])
    _current_app_config.set(previous)
