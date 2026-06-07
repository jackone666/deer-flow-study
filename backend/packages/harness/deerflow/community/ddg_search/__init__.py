"""DuckDuckGo Web 搜索工具子包。

提供基于 DuckDuckGo 的 Web 搜索能力(无需 API key),以 LangChain
``@tool`` 形式暴露 :func:`web_search_tool`。
"""

from .tools import web_search_tool

__all__ = ["web_search_tool"]
