"""MCP 工具元数据标签的唯一定义点。

当一个工具携带 ``deerflow_mcp`` 元数据标志时即视为"MCP 来源"。该标签在 MCP 工
具加载处(:mod:`tools`)写入,在延迟工具装配(:mod:`tool_search`)与 Agent 构建
处(:mod:`agent`)读取。把键、写入函数与判断函数集中到本模块,可以让该魔数字
符串只存在一处,读取方也只需导入公共谓词,而非跨模块的私有辅助。

本模块被有意设计为叶子模块:只依赖 :class:`BaseTool`,任何模块(尤其是工具加载
器)都能导入而不会引入循环引用。
"""

from __future__ import annotations

from langchain.tools import BaseTool

MCP_TOOL_METADATA_KEY = "deerflow_mcp"


def tag_mcp_tool(tool: BaseTool) -> BaseTool:
    """将工具标记为 MCP 来源。原地修改 ``tool.metadata`` 并返回以便链式调用。

    Args:
        tool: 需要打标的 LangChain 工具。

    Returns:
        已打标的同一工具实例。
    """
    tool.metadata = {**(tool.metadata or {}), MCP_TOOL_METADATA_KEY: True}
    return tool


def is_mcp_tool(tool: BaseTool) -> bool:
    """判断工具是否带有由 :func:`tag_mcp_tool` 写入的 MCP 来源标签。

    Args:
        tool: 待检测的 LangChain 工具。

    Returns:
        元数据中存在 ``deerflow_mcp=True`` 时返回 True。
    """
    return (getattr(tool, "metadata", None) or {}).get(MCP_TOOL_METADATA_KEY) is True
