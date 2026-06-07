"""Serper(Google Search API)Web 搜索工具子包。

提供通过 Serper.dev 代理的 Google 搜索能力(需要 ``SERPER_API_KEY``),
以 LangChain ``@tool`` 形式暴露 :func:`web_search_tool`。
"""

from .tools import web_search_tool

__all__ = ["web_search_tool"]
