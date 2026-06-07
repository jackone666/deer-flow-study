"""MCP servers 与 skills 的统一 extensions 配置。"""

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deerflow.config.runtime_paths import existing_project_file


class McpOAuthConfig(BaseModel):
    """MCP server（HTTP/SSE transport）的 OAuth 配置。"""

    enabled: bool = Field(default=True, description="是否启用 OAuth token 注入")
    token_url: str = Field(description="OAuth token 端点 URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(
        default="client_credentials",
        description="OAuth grant 类型",
    )
    client_id: str | None = Field(default=None, description="OAuth client ID")
    client_secret: str | None = Field(default=None, description="OAuth client secret")
    refresh_token: str | None = Field(default=None, description="OAuth refresh token（仅 refresh_token grant）")
    scope: str | None = Field(default=None, description="OAuth scope")
    audience: str | None = Field(default=None, description="OAuth audience（provider 相关）")
    token_field: str = Field(default="access_token", description="token 响应中包含 access token 的字段名")
    token_type_field: str = Field(default="token_type", description="token 响应中包含 token 类型的字段名")
    expires_in_field: str = Field(default="expires_in", description="token 响应中包含过期秒数的字段名")
    default_token_type: str = Field(default="Bearer", description="token 响应缺少 token_type 时使用的默认值")
    refresh_skew_seconds: int = Field(default=60, description="提前多少秒刷新即将过期的 token")
    extra_token_params: dict[str, str] = Field(default_factory=dict, description="额外发送到 token 端点的 form 参数")
    model_config = ConfigDict(extra="allow")


class McpServerConfig(BaseModel):
    """单个 MCP server 的配置。"""

    enabled: bool = Field(default=True, description="是否启用该 MCP server")
    type: str = Field(default="stdio", description="传输类型：'stdio'、'sse' 或 'http'")
    command: str | None = Field(default=None, description="启动 MCP server 的命令（stdio 类型）")
    args: list[str] = Field(default_factory=list, description="传递给命令的参数（stdio 类型）")
    env: dict[str, str] = Field(default_factory=dict, description="MCP server 的环境变量")
    url: str | None = Field(default=None, description="MCP server 的 URL（sse 或 http 类型）")
    headers: dict[str, str] = Field(default_factory=dict, description="发送的 HTTP headers（sse 或 http 类型）")
    oauth: McpOAuthConfig | None = Field(default=None, description="OAuth 配置（sse 或 http 类型）")
    description: str = Field(default="", description="MCP server 能力的人类可读描述")
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _accept_transport_alias(cls, data: Any) -> Any:
        """将 MCP 规范中的 ``transport`` 字段作为 ``type`` 的别名。

        官方 MCP 配置 schema 使用 ``transport`` 表示传输机制
        （``stdio``/``sse``/``http``）。本项目早期版本只识别 ``type``，导致
        仅配置 ``transport`` 的远程 SSE/HTTP server 被错误当作 ``stdio``
        （默认值）。本校验器对两种写法做归一化，``type`` 在同时存在时优先。
        """
        if isinstance(data, dict):
            transport = data.get("transport")
            if transport and not data.get("type"):
                data = {**data, "type": transport}
        return data


class SkillStateConfig(BaseModel):
    """单个 skill 的状态配置。"""

    enabled: bool = Field(default=True, description="是否启用该 skill")


class ExtensionsConfig(BaseModel):
    """MCP servers 与 skills 的统一配置。"""

    mcp_servers: dict[str, McpServerConfig] = Field(
        default_factory=dict,
        description="MCP server 名到配置的映射",
        alias="mcpServers",
    )
    skills: dict[str, SkillStateConfig] = Field(
        default_factory=dict,
        description="skill 名到状态配置的映射",
    )
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path | None:
        """解析 extensions 配置文件路径。

        优先级：
        1. 若传入了 ``config_path`` 参数，使用它。
        2. 若设置了 ``DEER_FLOW_EXTENSIONS_CONFIG_PATH`` 环境变量，使用它。
        3. 否则在调用方项目根下查找 ``extensions_config.json``，再找旧版 ``mcp_config.json``。
        4. 出于向后兼容，再查找 backend/仓库根的默认位置。
        5. 找不到则返回 ``None``（extensions 是可选的）。

        Args:
            config_path: 可选的 extensions 配置文件路径。

        Returns:
            Path | None: 找到则返回配置文件路径；都找不到则返回 ``None``。
        """
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"参数 `config_path` 指定的 extensions 配置文件在 {path} 找不到")
            return path
        elif os.getenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH"):
            path = Path(os.getenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH"))
            if not path.exists():
                raise FileNotFoundError(f"环境变量 `DEER_FLOW_EXTENSIONS_CONFIG_PATH` 指定的 extensions 配置文件在 {path} 找不到")
            return path
        else:
            project_config = existing_project_file(("extensions_config.json", "mcp_config.json"))
            if project_config is not None:
                return project_config

            backend_dir = Path(__file__).resolve().parents[4]
            repo_root = backend_dir.parent
            for path in (
                backend_dir / "extensions_config.json",
                repo_root / "extensions_config.json",
                backend_dir / "mcp_config.json",
                repo_root / "mcp_config.json",
            ):
                if path.exists():
                    return path

            # extensions 是可选的，找不到时返回 None
            return None

    @classmethod
    def from_file(cls, config_path: str | None = None) -> "ExtensionsConfig":
        """从 JSON 文件加载 extensions 配置。

        路径解析逻辑见 :meth:`resolve_config_path`。

        Args:
            config_path: 可选的 extensions 配置文件路径。

        Returns:
            ExtensionsConfig: 加载到的配置对象；找不到文件时返回空配置。

        Raises:
            ValueError: 配置文件存在但不是合法 JSON 时。
            RuntimeError: 其他加载失败时。
        """
        resolved_path = cls.resolve_config_path(config_path)
        if resolved_path is None:
            # 找不到 extensions 配置文件时返回空配置
            return cls(mcp_servers={}, skills={})

        try:
            with open(resolved_path, encoding="utf-8") as f:
                config_data = json.load(f)
            config_data = cls.resolve_env_variables(config_data)
            return cls.model_validate(config_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"{resolved_path} 处的 extensions 配置文件不是合法 JSON：{e}") from e
        except Exception as e:
            raise RuntimeError(f"从 {resolved_path} 加载 extensions 配置失败：{e}") from e

    @classmethod
    def resolve_env_variables(cls, config: Any) -> Any:
        """递归解析配置中的环境变量。

        通过 ``os.getenv`` 解析 ``$VAR`` 形式的占位符。示例：``$OPENAI_API_KEY``。

        Args:
            config: 任意配置结构（dict/list/tuple/str/其他）。

        Returns:
            Any: 解析环境变量后的等价结构。
        """
        if isinstance(config, str):
            if not config.startswith("$"):
                return config
            env_value = os.getenv(config[1:])
            if env_value is None:
                # 未解析的占位符：存为空字符串，避免下游消费者（如 MCP server）
                # 把字面量 "$VAR" 当作环境值使用。
                return ""
            return env_value

        if isinstance(config, dict):
            return {key: cls.resolve_env_variables(value) for key, value in config.items()}

        if isinstance(config, list):
            return [cls.resolve_env_variables(item) for item in config]

        if isinstance(config, tuple):
            return tuple(cls.resolve_env_variables(item) for item in config)

        return config

    def get_enabled_mcp_servers(self) -> dict[str, McpServerConfig]:
        """仅返回已启用的 MCP servers。

        Returns:
            dict[str, McpServerConfig]: 已启用的 MCP server 名字到配置的映射。
        """
        return {name: config for name, config in self.mcp_servers.items() if config.enabled}

    def is_skill_enabled(self, skill_name: str, skill_category: str) -> bool:
        """判断某 skill 是否处于启用状态。

        Args:
            skill_name: skill 名。
            skill_category: skill 分类。

        Returns:
            bool: 配置中显式声明时以配置为准；否则 ``public``/``custom`` 默认启用。
        """
        skill_config = self.skills.get(skill_name)
        if skill_config is None:
            # 对 public 与 custom 类 skill 默认启用
            return skill_category in ("public", "custom")
        return skill_config.enabled


_extensions_config: ExtensionsConfig | None = None


def get_extensions_config() -> ExtensionsConfig:
    """获取 extensions 配置实例。

    返回缓存的单例对象。可通过 :func:`reload_extensions_config` 从文件
    重新加载，或通过 :func:`reset_extensions_config` 清空缓存。

    Returns:
        ExtensionsConfig: 缓存中的 :class:`ExtensionsConfig` 实例。
    """
    global _extensions_config
    if _extensions_config is None:
        _extensions_config = ExtensionsConfig.from_file()
    return _extensions_config


def reload_extensions_config(config_path: str | None = None) -> ExtensionsConfig:
    """从文件重新加载 extensions 配置并更新缓存。

    当配置文件发生修改，希望不重启应用就拿到最新配置时使用。

    Args:
        config_path: 可选的 extensions 配置文件路径；不传则使用默认解析策略。

    Returns:
        ExtensionsConfig: 新加载的 :class:`ExtensionsConfig` 实例。
    """
    global _extensions_config
    _extensions_config = ExtensionsConfig.from_file(config_path)
    return _extensions_config


def reset_extensions_config() -> None:
    """重置缓存的 extensions 配置实例。

    清空单例缓存，使下一次调用 :func:`get_extensions_config` 重新从文件
    加载。常用于测试或在不同配置之间切换。
    """
    global _extensions_config
    _extensions_config = None


def set_extensions_config(config: ExtensionsConfig) -> None:
    """直接设置 extensions 配置实例。

    主要用于在测试中注入自定义或 mock 配置。

    Args:
        config: 目标 :class:`ExtensionsConfig` 实例。
    """
    global _extensions_config
    _extensions_config = config
