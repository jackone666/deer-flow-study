"""Tavily Web 搜索/抓取工具子包。"""

import json

from langchain.tools import tool
from tavily import TavilyClient

from deerflow.config import get_app_config


def _get_tavily_client() -> TavilyClient:
    """根据工具配置构造 :class:`TavilyClient` 客户端。"""
    config = get_app_config().get_tool_config("web_search")
    api_key = None
    if config is not None and "api_key" in config.model_extra:
        api_key = config.model_extra.get("api_key")
    return TavilyClient(api_key=api_key)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """在 Web 上搜索信息。

    Args:
        query: 搜索查询字符串。
    """
    config = get_app_config().get_tool_config("web_search")
    max_results = 5
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results")

    client = _get_tavily_client()
    res = client.search(query, max_results=max_results)
    normalized_results = [
        {
            "title": result["title"],
            "url": result["url"],
            "snippet": result["content"],
        }
        for result in res["results"]
    ]
    json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
    return json_results


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
    client = _get_tavily_client()
    res = client.extract([url])
    if "failed_results" in res and len(res["failed_results"]) > 0:
        return f"Error: {res['failed_results'][0]['error']}"
    elif "results" in res and len(res["results"]) > 0:
        result = res["results"][0]
        return f"# {result['title']}\n\n{result['raw_content'][:4096]}"
    else:
        return "Error: No results found"
