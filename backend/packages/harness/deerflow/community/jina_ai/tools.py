"""基于 Jina Reader 的 Web 抓取工具。"""

import asyncio

from langchain.tools import tool

from deerflow.community.jina_ai.jina_client import JinaClient
from deerflow.config import get_app_config
from deerflow.utils.readability import ReadabilityExtractor

readability_extractor = ReadabilityExtractor()


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str) -> str:
    """抓取指定 URL 的网页内容。

    只抓取用户直接给出或由 ``web_search``/``web_fetch`` 工具返回的精确 URL;无法
    访问需要鉴权的内容(例如登录后的私有 Google Docs);不要为无 ``www.`` 的
    URL 强行添加;URL 必须包含协议头,例如 ``https://example.com`` 合法而
    ``example.com`` 不合法。

    Args:
        url: 待抓取的 URL。
    """
    jina_client = JinaClient()
    timeout = 10
    config = get_app_config().get_tool_config("web_fetch")
    if config is not None and "timeout" in config.model_extra:
        timeout = config.model_extra.get("timeout")
    html_content = await jina_client.crawl(url, return_format="html", timeout=timeout)
    if isinstance(html_content, str) and html_content.startswith("Error:"):
        return html_content
    article = await asyncio.to_thread(readability_extractor.extract_article, html_content)
    return article.to_markdown()[:4096]
