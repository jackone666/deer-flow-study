"""Exa Web 搜索/抓取工具子包。"""

import json

from exa_py import Exa
from langchain.tools import tool

from deerflow.config import get_app_config


def _get_exa_client(tool_name: str = "web_search") -> Exa:
    """根据工具配置构造 :class:`Exa` 客户端。"""
    config = get_app_config().get_tool_config(tool_name)
    api_key = None
    if config is not None and "api_key" in config.model_extra:
        api_key = config.model_extra.get("api_key")
    return Exa(api_key=api_key)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """在 Web 上搜索信息。

    Args:
        query: 搜索查询字符串。
    """
    try:
        config = get_app_config().get_tool_config("web_search")
        max_results = 5
        search_type = "auto"
        contents_max_characters = 1000
        if config is not None:
            max_results = config.model_extra.get("max_results", max_results)
            search_type = config.model_extra.get("search_type", search_type)
            contents_max_characters = config.model_extra.get("contents_max_characters", contents_max_characters)

        client = _get_exa_client()
        res = client.search(
            query,
            type=search_type,
            num_results=max_results,
            contents={"highlights": {"max_characters": contents_max_characters}},
        )

        normalized_results = [
            {
                "title": result.title or "",
                "url": result.url or "",
                "snippet": "\n".join(result.highlights) if result.highlights else "",
            }
            for result in res.results
        ]
        json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
        return json_results
    except Exception as e:
        return f"Error: {str(e)}"


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """抓取指定 URL 的网页内容。

    只抓取用户直接给出或由 ``web_search``/``web_fetch`` 工具返回的精确 URL;无法
    访问需要鉴权的内容(例如登录后的私有 Google Docs);不要为无 ``www.`` 的
    URL 强行添加;URL 必须包含协议头,例如 ``https://example.com`` 合法而
    ``example.com`` 不合法。

    Args:
        url: 待抓取的 URL。
    """
    try:
        client = _get_exa_client("web_fetch")
        res = client.get_contents([url], text={"max_characters": 4096})

        if res.results:
            result = res.results[0]
            title = result.title or "Untitled"
            text = result.text or ""
            return f"# {title}\n\n{text[:4096]}"
        else:
            return "Error: No results found"
    except Exception as e:
        return f"Error: {str(e)}"
