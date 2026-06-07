"""文件格式转换工具。

将常见文档（PDF、PPT、Excel、Word）转换为 Markdown。

PDF 转换策略（auto 模式）：
  1. 优先尝试 pymupdf4llm（若已安装）—— 大多数文件上具有更好的标题识别和速度。
  2. 若输出明显偏短（每页少于 ``_MIN_CHARS_PER_PAGE`` 字符，或在页数不可用时
     总字符数少于 200），视为图像型 PDF 并回退到 MarkItDown。
  3. 若 pymupdf4llm 未安装，则直接使用 MarkItDown（保持原有行为）。

大于 ``_ASYNC_THRESHOLD_BYTES`` 的大文件通过 ``asyncio.to_thread()`` 丢到
线程池中执行，避免阻塞事件循环（修复 #1569）。

本模块不依赖 FastAPI 或 HTTP，纯工具函数集合。
"""

import asyncio
import logging
import re
from pathlib import Path

from deerflow.config.app_config import get_app_config

logger = logging.getLogger(__name__)

# File extensions that should be converted to markdown
CONVERTIBLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
}

# Files larger than this threshold are converted in a background thread.
# Small files complete in < 1s synchronously; spawning a thread adds unnecessary
# scheduling overhead for them.
_ASYNC_THRESHOLD_BYTES = 1 * 1024 * 1024  # 1 MB

# If pymupdf4llm produces fewer characters *per page* than this threshold,
# the PDF is likely image-based or encrypted — fall back to MarkItDown.
# Rationale: normal text PDFs yield 200-2000 chars/page; image-based PDFs
# yield close to 0. 50 chars/page gives a wide safety margin.
# Falls back to absolute 200-char check when page count is unavailable.
_MIN_CHARS_PER_PAGE = 50


def _pymupdf_output_too_sparse(text: str, file_path: Path) -> bool:
    """判断 pymupdf4llm 的输出是否异常稀疏（疑似图像型 PDF）。

    使用「每页字符数」而非绝对字符阈值，从而同时正确处理短文档（页数少、
    字符少）和长文档（页数多、字符多）这两种场景。无法获取页数时回退到
    绝对 200 字符阈值。

    Args:
        text: pymupdf4llm 提取的 Markdown 文本。
        file_path: 原始 PDF 路径，仅用于在页数未知时回退读取。

    Returns:
        若输出过短（疑似图像型 PDF）则返回 ``True``。
    """
    chars = len(text.strip())
    doc = None
    pages: int | None = None
    try:
        import pymupdf

        doc = pymupdf.open(str(file_path))
        pages = len(doc)
    except Exception:
        pass
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    if pages is not None and pages > 0:
        return (chars / pages) < _MIN_CHARS_PER_PAGE
    # Fallback: absolute threshold when page count is unavailable
    return chars < 200


def _convert_pdf_with_pymupdf4llm(file_path: Path) -> str | None:
    """使用 pymupdf4llm 尝试转换 PDF。

    Args:
        file_path: 源 PDF 文件路径。

    Returns:
        转换得到的 Markdown 文本；若 pymupdf4llm 未安装或转换失败
        （例如加密/损坏的 PDF），则返回 ``None``。
    """
    try:
        import pymupdf4llm
    except ImportError:
        return None

    try:
        return pymupdf4llm.to_markdown(str(file_path))
    except Exception:
        logger.exception("pymupdf4llm failed to convert %s; falling back to MarkItDown", file_path.name)
        return None


def _convert_with_markitdown(file_path: Path) -> str:
    """使用 MarkItDown 将任意受支持文件转换为 Markdown 文本。

    Args:
        file_path: 源文件路径。

    Returns:
        转换得到的 Markdown 文本。
    """
    from markitdown import MarkItDown

    md = MarkItDown()
    return md.convert(str(file_path)).text_content


def _do_convert(file_path: Path, pdf_converter: str) -> str:
    """同步执行文档到 Markdown 的转换。

    既可被直接调用，也可通过 :func:`asyncio.to_thread` 在后台线程运行。
    内部根据 :attr:`pdf_converter` 的取值在 pymupdf4llm 与 MarkItDown 之间
    选择具体策略。

    Args:
        file_path: 待转换的文件路径。
        pdf_converter: PDF 转换策略，可选 ``"auto"`` / ``"pymupdf4llm"`` /
            ``"markitdown"``。
    """
    is_pdf = file_path.suffix.lower() == ".pdf"

    if is_pdf and pdf_converter != "markitdown":
        # Try pymupdf4llm first (auto or explicit)
        pymupdf_text = _convert_pdf_with_pymupdf4llm(file_path)

        if pymupdf_text is not None:
            # pymupdf4llm is installed
            if pdf_converter == "pymupdf4llm":
                # Explicit — use as-is regardless of output length
                return pymupdf_text
            # auto mode: fall back if output looks like a failed parse.
            # Use chars-per-page to distinguish image-based PDFs (near 0) from
            # legitimately short documents.
            if not _pymupdf_output_too_sparse(pymupdf_text, file_path):
                return pymupdf_text
            logger.warning(
                "pymupdf4llm produced only %d chars for %s (likely image-based PDF); falling back to MarkItDown",
                len(pymupdf_text.strip()),
                file_path.name,
            )
        # pymupdf4llm not installed or fallback triggered → use MarkItDown

    return _convert_with_markitdown(file_path)


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """将受支持的文档文件转换为 Markdown。

    PDF 文件采用两种转换器协同的策略（详见模块级 docstring）。
    大于 1 MB 的文件会被丢到线程池中执行，避免阻塞事件循环。

    Args:
        file_path: 待转换文件的路径。

    Returns:
        生成的 ``.md`` 文件路径；若转换失败则返回 ``None``。
    """
    try:
        pdf_converter = _get_pdf_converter()
        file_size = file_path.stat().st_size

        if file_size > _ASYNC_THRESHOLD_BYTES:
            text = await asyncio.to_thread(_do_convert, file_path, pdf_converter)
        else:
            text = _do_convert(file_path, pdf_converter)

        md_path = file_path.with_suffix(".md")
        md_path.write_text(text, encoding="utf-8")

        logger.info("Converted %s to markdown: %s (%d chars)", file_path.name, md_path.name, len(text))
        return md_path
    except Exception as e:
        logger.error("Failed to convert %s to markdown: %s", file_path.name, e)
        return None


# Regex for bold-only lines that look like section headings.
# Targets SEC filing structural headings that pymupdf4llm renders as **bold**
# rather than # Markdown headings (because they use same font size as body text,
# distinguished only by bold+caps formatting).
#
# Pattern requires ALL of:
#   1. Entire line is a single **...** block (no surrounding prose)
#   2. Starts with a recognised structural keyword:
#      - ITEM / PART / SECTION (with optional number/letter after)
#      - SCHEDULE, EXHIBIT, APPENDIX, ANNEX, CHAPTER
#      All-caps addresses, boilerplate ("CURRENT REPORT", "SIGNATURES",
#      "WASHINGTON, DC 20549") do NOT start with these keywords and are excluded.
#
# Chinese headings (第三节...) are already captured as standard # headings
# by pymupdf4llm, so they don't need this pattern.
_BOLD_HEADING_RE = re.compile(r"^\*\*((ITEM|PART|SECTION|SCHEDULE|EXHIBIT|APPENDIX|ANNEX|CHAPTER)\b[A-Z0-9 .,\-]*)\*\*\s*$")

# Regex for split-bold headings produced by pymupdf4llm when a heading spans
# multiple text spans in the PDF (e.g. section number and title are separate spans).
# Matches lines like:  **1** **Introduction**  or  **3.2** **Multi-Head Attention**
# Requirements:
#   1. Entire line consists only of **...** blocks separated by whitespace (no prose)
#   2. First block is a section number (digits and dots, e.g. "1", "3.2", "A.1")
#   3. Second block must not be purely numeric/punctuation — excludes financial table
#      headers like **2023** **2022** **2021** while allowing non-ASCII titles such as
#      **1** **概述** or accented words (negative lookahead instead of [A-Za-z])
#   4. At most two additional blocks (four total) with [^*]+ (no * inside) to keep
#      the regex linear and avoid ReDoS on attacker-controlled content
_SPLIT_BOLD_HEADING_RE = re.compile(r"^\*\*[\dA-Z][\d\.]*\*\*\s+\*\*(?!\d[\d\s.,\-–—/:()%]*\*\*)[^*]+\*\*(?:\s+\*\*[^*]+\*\*){0,2}\s*$")

# Maximum number of outline entries injected into the agent context.
# Keeps prompt size bounded even for very long documents.
MAX_OUTLINE_ENTRIES = 50

_ALLOWED_PDF_CONVERTERS = {"auto", "pymupdf4llm", "markitdown"}


def _clean_bold_title(raw: str) -> str:
    """规范化可能包含 pymupdf4llm 粗体残留的标题字符串。

    pymupdf4llm 偶尔会把相邻的粗体片段输出成 ``**A** **B**`` 的形式，而非
    单一的 ``**A B**``。本函数先把相邻粗体片段合并，再在最外层确实被
    ``**...`` 包裹时去掉包裹符号，让调用方得到纯文本标题。

    示例::

        "**Overview**"                       → "Overview"
        "**UNITED STATES** **SECURITIES**"   → "UNITED STATES SECURITIES"
        "plain text"                         → "plain text"  (unchanged)

    Args:
        raw: 原始标题字符串。

    Returns:
        规范化后的纯文本标题。
    """
    # Merge adjacent bold spans: "** **" → " "
    merged = re.sub(r"\*\*\s*\*\*", " ", raw).strip()
    # Strip outermost **...** if the whole string is wrapped
    if m := re.fullmatch(r"\*\*(.+?)\*\*", merged, re.DOTALL):
        return m.group(1).strip()
    return merged


def extract_outline(md_path: Path) -> list[dict]:
    """从 Markdown 文件中抽取文档大纲（标题列表）。

    能够识别 pymupdf4llm 产生的三种标题样式：

    1. **标准 Markdown 标题**：以一个或多个 ``#`` 开头的行。会清除内联的
       ``**...**`` 包裹以及相邻的粗体片段（``** **``），使最终标题为纯文本。
    2. **仅粗体的结构性标题**：如 ``**ITEM 1. BUSINESS**``、``**PART II**``。
       SEC 文件中这类段落使用粗体 + 大写且与正文同号字体，因此 pymupdf4llm
       无法提升为 ``#`` 标题。
    3. **拆分粗体标题**：如 ``**1** **Introduction**``、``**3.2** **Attention**``。
       当节编号与标题在原 PDF 中分属不同文本 span 时常见，多见于学术论文。

    Args:
        md_path: ``.md`` 文件路径。

    Returns:
        标题条目列表，每个条目包含 ``title``（str）与 ``line``（int, 1-based）。
        当抽取达到 :data:`MAX_OUTLINE_ENTRIES` 上限而被截断时，会在末尾追加
        一条 ``{"truncated": True}`` 哨兵条目，便于调用方渲染「仅展示前 N 条」
        提示而无需重新扫描文件。文件无法读取或无标题时返回空列表。
    """
    outline: list[dict] = []
    try:
        with md_path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue

                # Style 1: standard Markdown heading
                if stripped.startswith("#"):
                    title = _clean_bold_title(stripped.lstrip("#").strip())
                    if title:
                        outline.append({"title": title, "line": lineno})

                # Style 2: single bold block with SEC structural keyword
                elif m := _BOLD_HEADING_RE.match(stripped):
                    title = m.group(1).strip()
                    if title:
                        outline.append({"title": title, "line": lineno})

                # Style 3: split-bold heading — **<num>** **<title>**
                # Regex already enforces max 4 blocks and non-numeric second block.
                elif _SPLIT_BOLD_HEADING_RE.match(stripped):
                    title = " ".join(re.findall(r"\*\*([^*]+)\*\*", stripped))
                    if title:
                        outline.append({"title": title, "line": lineno})

                if len(outline) >= MAX_OUTLINE_ENTRIES:
                    outline.append({"truncated": True})
                    break
    except Exception:
        return []

    return outline


def _get_uploads_config_value(key: str, default: object) -> object:
    """从 uploads 配置中读取指定键值，同时兼容字典与对象属性两种形态。

    Args:
        key: 配置键名。
        default: 当键不存在时返回的默认值。

    Returns:
        配置项的值，或给定的默认值。
    """
    cfg = get_app_config()
    uploads_cfg = getattr(cfg, "uploads", None)
    if isinstance(uploads_cfg, dict):
        return uploads_cfg.get(key, default)
    return getattr(uploads_cfg, key, default)


def _get_pdf_converter() -> str:
    """从应用配置中读取 ``pdf_converter`` 选项，默认 ``'auto'``。

    将读取到的值统一转为小写并对照 :data:`_ALLOWED_PDF_CONVERTERS` 校验，
    防止 ``config.yaml`` 中形如 ``AUTO``、``MarkItDown`` 的大小写差异被
    静默忽略而产生意外行为。

    Returns:
        规范化后的 PDF 转换策略字符串，非法值会回退为 ``"auto"``。
    """
    try:
        raw = str(_get_uploads_config_value("pdf_converter", "auto")).strip().lower()
        if raw not in _ALLOWED_PDF_CONVERTERS:
            logger.warning("Invalid pdf_converter value %r; falling back to 'auto'", raw)
            return "auto"
        return raw
    except Exception:
        pass
    return "auto"
