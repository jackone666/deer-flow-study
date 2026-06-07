"""图片搜索工具:基于 DuckDuckGo 检索参考图,供图像生成场景使用。"""

import json
import logging

from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)


def _search_images(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    size: str | None = None,
    color: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
    license_image: str | None = None,
) -> list[dict]:
    """使用 DuckDuckGo 执行图片搜索。

    Args:
        query: 搜索关键词。
        max_results: 最大结果数。
        region: 搜索区域。
        safesearch: 安全搜索级别。
        size: 图片尺寸(``Small``/``Medium``/``Large``/``Wallpaper``)。
        color: 颜色过滤器。
        type_image: 图片类型(``photo``/``clipart``/``gif``/``transparent``/``line``)。
        layout: 布局(``Square``/``Tall``/``Wide``)。
        license_image: 授权过滤器。

    Returns:
        搜索结果列表;库未安装或失败时为空列表。
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.error("ddgs library not installed. Run: pip install ddgs")
        return []

    ddgs = DDGS(timeout=30)

    try:
        kwargs = {
            "region": region,
            "safesearch": safesearch,
            "max_results": max_results,
        }

        if size:
            kwargs["size"] = size
        if color:
            kwargs["color"] = color
        if type_image:
            kwargs["type_image"] = type_image
        if layout:
            kwargs["layout"] = layout
        if license_image:
            kwargs["license_image"] = license_image

        results = ddgs.images(query, **kwargs)
        return list(results) if results else []

    except Exception as e:
        logger.error(f"Failed to search images: {e}")
        return []


@tool("image_search", parse_docstring=True)
def image_search_tool(
    query: str,
    max_results: int = 5,
    size: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
) -> str:
    """在线搜索图片。请在图像生成之前使用本工具,查找人物、肖像、物体、场景等需要视觉准确性的参考图。

    **使用时机:**
    - 生成人物/肖像图像前:搜索相似姿态、表情、风格
    - 生成特定物体/产品前:搜索准确的视觉参考
    - 生成场景/地点前:搜索建筑或环境参考
    - 生成服装/配饰前:搜索风格与细节参考

    返回的图片 URL 可作为图像生成的参考图,显著提高质量。

    Args:
        query: 描述待搜索图片的关键词,越具体效果越好(例如使用
            ``"Japanese woman street photography 1990s"`` 而不是仅 ``"woman"``)。
        max_results: 返回的最大图片数,默认 5。
        size: 图片尺寸过滤器,可选 ``"Small"``/``"Medium"``/``"Large"``/``"Wallpaper"``;
            参考图建议使用 ``"Large"``。
        type_image: 图片类型过滤器,可选 ``"photo"``/``"clipart"``/``"gif"``/``"transparent"``/``"line"``;
            写实参考建议使用 ``"photo"``。
        layout: 布局过滤器,可选 ``"Square"``/``"Tall"``/``"Wide"``,根据生成需要选取。
    """
    config = get_app_config().get_tool_config("image_search")

    # Override max_results from config if set
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results", max_results)

    results = _search_images(
        query=query,
        max_results=max_results,
        size=size,
        type_image=type_image,
        layout=layout,
    )

    if not results:
        return json.dumps({"error": "No images found", "query": query}, ensure_ascii=False)

    normalized_results = [
        {
            "title": r.get("title", ""),
            "image_url": r.get("thumbnail", ""),
            "thumbnail_url": r.get("thumbnail", ""),
        }
        for r in results
    ]

    output = {
        "query": query,
        "total_results": len(normalized_results),
        "results": normalized_results,
        "usage_hint": "Use the 'image_url' values as reference images in image generation. Download them first if needed.",
    }

    return json.dumps(output, indent=2, ensure_ascii=False)
