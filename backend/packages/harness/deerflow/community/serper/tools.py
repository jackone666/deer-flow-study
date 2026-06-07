"""Web 搜索工具:基于 Serper(Google Search API)进行搜索。

Serper 通过 JSON API 提供实时 Google 搜索结果,需要 API key,可到
https://serper.dev 注册。
"""

import json
import logging
import os

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

_SERPER_ENDPOINT = "https://google.serper.dev/search"
_api_key_warned = False


def _get_api_key() -> str | None:
    """从工具配置或环境变量中读取 Serper API key。"""
    config = get_app_config().get_tool_config("web_search")
    if config is not None:
        api_key = config.model_extra.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            return api_key
    return os.getenv("SERPER_API_KEY")


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str, max_results: int = 5) -> str:
    """通过 Serper 调用 Google Search 检索 Web 信息。

    Args:
        query: 描述待搜索内容的关键词,越具体效果越好。
        max_results: 返回的最大搜索结果数,默认 5。
    """
    global _api_key_warned

    config = get_app_config().get_tool_config("web_search")
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results", max_results)

    api_key = _get_api_key()
    if not api_key:
        if not _api_key_warned:
            _api_key_warned = True
            logger.warning("Serper API key is not set. Set SERPER_API_KEY in your environment or provide api_key in config.yaml. Sign up at https://serper.dev")
        return json.dumps(
            {"error": "SERPER_API_KEY is not configured", "query": query},
            ensure_ascii=False,
        )

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": max_results}

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(_SERPER_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Serper API returned HTTP {e.response.status_code}: {e.response.text}")
        return json.dumps(
            {"error": f"Serper API error: HTTP {e.response.status_code}", "query": query},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"Serper search failed: {type(e).__name__}: {e}")
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    organic = data.get("organic", [])
    if not organic:
        return json.dumps({"error": "No results found", "query": query}, ensure_ascii=False)

    normalized_results = [
        {
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "content": r.get("snippet", ""),
        }
        for r in organic[:max_results]
    ]

    output = {
        "query": query,
        "total_results": len(normalized_results),
        "results": normalized_results,
    }
    return json.dumps(output, indent=2, ensure_ascii=False)
