"""网页正文抽取与 Markdown 转换工具。

封装了 Readability.js（通过 readabilipy 调用）作为主抽取器，并保留
``markdownify`` 退化为纯 Python 处理。``Article`` 表示抽取后的文章，
``ReadabilityExtractor`` 提供 ``extract_article`` 这一高层接口。
"""

import logging
import re
import subprocess
from urllib.parse import urljoin

from markdownify import markdownify as md
from readabilipy import simple_json_from_html_string

logger = logging.getLogger(__name__)


class Article:
    """已抽取的网页文章模型，承载标题、原始 HTML 以及所属源 URL。

    Attributes:
        url: 源网页 URL，由抽取方在创建 ``Article`` 时赋值，供
            :meth:`to_message` 中拼接图片相对地址使用。
    """

    url: str

    def __init__(self, title: str, html_content: str):
        """初始化文章对象。

        Args:
            title: 文章标题。
            html_content: 抽取得到的 HTML 正文内容。
        """
        self.title = title
        self.html_content = html_content

    def to_markdown(self, including_title: bool = True) -> str:
        """将文章转换为 Markdown 文本。

        Args:
            including_title: 是否在结果开头包含一级标题，默认 ``True``。

        Returns:
            完整的 Markdown 文本；若 HTML 内容为空，则使用占位提示
            ``*No content available*``。
        """
        markdown = ""
        if including_title:
            markdown += f"# {self.title}\n\n"

        if self.html_content is None or not str(self.html_content).strip():
            markdown += "*No content available*\n"
        else:
            markdown += md(self.html_content)

        return markdown

    def to_message(self) -> list[dict]:
        """将文章拆分为适合多模态大模型消费的消息块列表。

        解析 Markdown 中的 ``![](url)`` 图片语法，将正文与图片交替放入
        结果列表。若文章本身为空或解析后仍无内容，则返回单条占位文本。

        Returns:
            形如 ``[{"type": "text"|"image_url", ...}]`` 的消息块列表。
        """
        image_pattern = r"!\[.*?\]\((.*?)\)"

        content: list[dict[str, str]] = []
        markdown = self.to_markdown()

        if not markdown or not markdown.strip():
            return [{"type": "text", "text": "No content available"}]

        parts = re.split(image_pattern, markdown)

        for i, part in enumerate(parts):
            if i % 2 == 1:
                image_url = urljoin(self.url, part.strip())
                content.append({"type": "image_url", "image_url": {"url": image_url}})
            else:
                text_part = part.strip()
                if text_part:
                    content.append({"type": "text", "text": text_part})

        # If after processing all parts, content is still empty, provide a fallback message.
        if not content:
            content = [{"type": "text", "text": "No content available"}]

        return content


class ReadabilityExtractor:
    """使用 Readability.js 抽取网页正文的封装。

    当 Readability.js 调用失败时，自动回退到 readabilipy 的纯 Python 抽取。
    """

    def extract_article(self, html: str) -> Article:
        """从 HTML 字符串中抽取文章标题与正文。

        主流程调用 ``use_readability=True`` 触发 Readability.js；若
        ``readabilipy`` 报告子进程错误（``CalledProcessError`` 或
        ``FileNotFoundError``，通常表示 Node.js/Readability.js 不可用），
        则降级为 ``use_readability=False`` 走纯 Python 路径。最终若
        标题或正文仍为空，会写入占位字符串以保证 ``Article`` 始终有可渲染
        内容。

        Args:
            html: 待抽取的原始 HTML 字符串。

        Returns:
            包含标题与 HTML 正文的 :class:`Article` 实例。
        """
        try:
            article = simple_json_from_html_string(html, use_readability=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = getattr(exc, "stderr", None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            stderr_info = f"; stderr={stderr.strip()}" if isinstance(stderr, str) and stderr.strip() else ""
            logger.warning(
                "Readability.js extraction failed with %s%s; falling back to pure-Python extraction",
                type(exc).__name__,
                stderr_info,
                exc_info=True,
            )
            article = simple_json_from_html_string(html, use_readability=False)

        html_content = article.get("content")
        if not html_content or not str(html_content).strip():
            html_content = "No content could be extracted from this page"

        title = article.get("title")
        if not title or not str(title).strip():
            title = "Untitled"

        return Article(title=title, html_content=html_content)
