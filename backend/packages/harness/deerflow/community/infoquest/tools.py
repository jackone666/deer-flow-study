"""InfoQuest Web 搜索/抓取/图片搜索工具子包。"""

from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.utils.readability import ReadabilityExtractor

from .infoquest_client import InfoQuestClient

readability_extractor = ReadabilityExtractor()


def _get_infoquest_client() -> InfoQuestClient:
    """根据应用配置构造 :class:`InfoQuestClient` 客户端。"""
    search_config = get_app_config().get_tool_config("web_search")
    search_time_range = -1
    if search_config is not None and "search_time_range" in search_config.model_extra:
        search_time_range = search_config.model_extra.get("search_time_range")

    fetch_config = get_app_config().get_tool_config("web_fetch")
    fetch_time = -1
    if fetch_config is not None and "fetch_time" in fetch_config.model_extra:
        fetch_time = fetch_config.model_extra.get("fetch_time")
    fetch_timeout = -1
    if fetch_config is not None and "timeout" in fetch_config.model_extra:
        fetch_timeout = fetch_config.model_extra.get("timeout")
    navigation_timeout = -1
    if fetch_config is not None and "navigation_timeout" in fetch_config.model_extra:
        navigation_timeout = fetch_config.model_extra.get("navigation_timeout")

    image_search_config = get_app_config().get_tool_config("image_search")
    image_search_time_range = -1
    if image_search_config is not None and "image_search_time_range" in image_search_config.model_extra:
        image_search_time_range = image_search_config.model_extra.get("image_search_time_range")
    image_size = "i"
    if image_search_config is not None and "image_size" in image_search_config.model_extra:
        image_size = image_search_config.model_extra.get("image_size")

    return InfoQuestClient(
        search_time_range=search_time_range,
        fetch_timeout=fetch_timeout,
        fetch_navigation_timeout=navigation_timeout,
        fetch_time=fetch_time,
        image_search_time_range=image_search_time_range,
        image_size=image_size,
    )


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """在 Web 上搜索信息。

    Args:
        query: 搜索查询字符串。
    """

    client = _get_infoquest_client()
    return client.web_search(query)


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
    client = _get_infoquest_client()
    result = client.fetch(url)
    if result.startswith("Error: "):
        return result
    article = readability_extractor.extract_article(result)
    return article.to_markdown()[:4096]


@tool("image_search", parse_docstring=True)
def image_search_tool(query: str) -> str:
    """在线搜索图片。请在图像生成之前使用本工具,查找人物、肖像、物体、场景等需要视觉准确性的参考图。

    **使用时机:**
    - 生成人物/肖像图像前:搜索相似姿态、表情、风格
    - 生成特定物体/产品前:搜索准确的视觉参考
    - 生成场景/地点前:搜索建筑或环境参考
    - 生成服装/配饰前:搜索风格与细节参考

    返回的图片 URL 可作为图像生成的参考图,显著提高质量。

    Args:
        query: 待搜索图片的查询字符串。
    """
    client = _get_infoquest_client()
    return client.image_search(query)
