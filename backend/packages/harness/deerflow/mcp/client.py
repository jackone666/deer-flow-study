"""基于 langchain-mcp-adapters 的 MCP 客户端构造工具。"""

import logging
from typing import Any

from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)


def build_server_params(server_name: str, config: McpServerConfig) -> dict[str, Any]:
    """为 :class:`MultiServerMCPClient` 构造单个服务器的连接参数。

    Args:
        server_name: MCP 服务器名。
        config: 该服务器的 :class:`McpServerConfig`。

    Returns:
        适用于 langchain-mcp-adapters 的参数字典。

    Raises:
        ValueError: 必填字段缺失或传输类型不支持。
    """
    transport_type = config.type or "stdio"
    params: dict[str, Any] = {"transport": transport_type}

    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{server_name}' with stdio transport requires 'command' field")
        params["command"] = config.command
        params["args"] = config.args
        # Add environment variables if present
        if config.env:
            params["env"] = config.env
    elif transport_type in ("sse", "http"):
        if not config.url:
            raise ValueError(f"MCP server '{server_name}' with {transport_type} transport requires 'url' field")
        params["url"] = config.url
        # Add headers if present
        if config.headers:
            params["headers"] = config.headers
    else:
        raise ValueError(f"MCP server '{server_name}' has unsupported transport type: {transport_type}")

    return params


def build_servers_config(extensions_config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """为 :class:`MultiServerMCPClient` 构造全部已启用 MCP 服务器的配置。

    Args:
        extensions_config: 包含所有 MCP 服务器的扩展配置。

    Returns:
        ``server_name -> 参数`` 的映射;无启用服务器时为空字典。
    """
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    if not enabled_servers:
        logger.info("No enabled MCP servers found")
        return {}

    servers_config = {}
    for server_name, server_config in enabled_servers.items():
        try:
            servers_config[server_name] = build_server_params(server_name, server_config)
            logger.info(f"Configured MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to configure MCP server '{server_name}': {e}")

    return servers_config
