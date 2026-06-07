"""DuckDuckGo 图片搜索工具子包。

提供基于 DuckDuckGo 的图片搜索能力(无需 API key),以 LangChain
``@tool`` 形式暴露 :func:`image_search_tool`,供图像生成前的
参考图检索场景使用。
"""

from .tools import image_search_tool

__all__ = ["image_search_tool"]
