"""将上传文件信息注入到 agent 上下文中的中间件。"""


import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.runnables import run_in_executor
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.file_conversion import extract_outline

logger = logging.getLogger(__name__)


_OUTLINE_PREVIEW_LINES = 5


def _extract_outline_for_file(file_path: Path) -> tuple[list[dict], list[str]]:
    """返回 *file_path* 对应文档的大纲与回退预览。

    查找由上传转换流程生成的同目录 ``<stem>.md`` 文件。

    Returns:
        ``(outline, preview)`` 元组：
        - ``outline``：``{title, line}`` 字典列表（可能含截断哨兵）。
          当没有找到标题或不存在 ``.md`` 时为空。
        - ``preview``：``outline`` 为空时取 ``.md`` 开头若干非空行作为
          内容锚点供模型参考；``outline`` 非空时为空（无需回退）。
    """
    md_path = file_path.with_suffix(".md")
    if not md_path.is_file():
        return [], []

    outline = extract_outline(md_path)
    if outline:
        logger.debug("Extracted %d outline entries from %s", len(outline), file_path.name)
        return outline, []

    # outline is empty — read the first few non-empty lines as a content preview
    preview: list[str] = []
    try:
        with md_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    preview.append(stripped)
                if len(preview) >= _OUTLINE_PREVIEW_LINES:
                    break
    except Exception:
        logger.debug("Failed to read preview lines from %s", md_path, exc_info=True)
    return [], preview


class UploadsMiddlewareState(AgentState):
    """Uploads 中间件对应的状态模式。"""

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """将已上传文件信息注入到 Agent 上下文的中间件。

    从当前消息的 ``additional_kwargs.files``（前端上传后写入）读取文件
    元数据，并在最后一条人类消息前添加 ``<uploaded_files>`` 块，让模型
    知晓可用文件。
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """初始化中间件。

        Args:
            base_dir: 线程数据根目录，缺省时使用 ``Paths`` 解析得到的路径。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _format_file_entry(self, file: dict, lines: list[str]) -> None:
        """向 *lines* 追加单个文件条目（名称、大小、路径、可选大纲）。"""
        size_kb = file["size"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        lines.append(f"- {file['filename']} ({size_str})")
        lines.append(f"  Path: {file['path']}")
        outline = file.get("outline") or []
        if outline:
            truncated = outline[-1].get("truncated", False)
            visible = [e for e in outline if not e.get("truncated")]
            lines.append("  Document outline (use `read_file` with line ranges to read sections):")
            for entry in visible:
                lines.append(f"    L{entry['line']}: {entry['title']}")
            if truncated:
                lines.append(f"    ... (showing first {len(visible)} headings; use `read_file` to explore further)")
        else:
            preview = file.get("outline_preview") or []
            if preview:
                lines.append("  No structural headings detected. Document begins with:")
                for text in preview:
                    lines.append(f"    > {text}")
            lines.append("  Use `grep` to search for keywords (e.g. `grep(pattern='keyword', path='/mnt/user-data/uploads/')`).")
        lines.append("")

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """创建一条列出上传文件的格式化消息。
        
                Args:
                    new_files: 当前消息中上传的文件。
                    historical_files: 历史消息中上传的文件。
                        每个文件 dict 可包含可选的 ``outline`` 键——
                        来自已转换 Markdown 文件的 ``{title, line}`` 字典列表。
        
                Returns:
                    包裹在 ``<uploaded_files>`` 标签内的格式化字符串。
        """

        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                self._format_file_entry(file, lines)
        else:
            lines.append("(empty)")
            lines.append("")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                self._format_file_entry(file, lines)

        lines.append("To work with these files:")
        lines.append("- Read from the file first — use the outline line numbers and `read_file` to locate relevant sections.")
        lines.append("- Use `grep` to search for keywords when you are not sure which section to look at")
        lines.append("  (e.g. `grep(pattern='revenue', path='/mnt/user-data/uploads/')`).")
        lines.append("- Use `glob` to find files by name pattern")
        lines.append("  (e.g. `glob(pattern='**/*.md', path='/mnt/user-data/uploads/')`).")
        lines.append("- Only fall back to web search if the file content is clearly insufficient to answer the question.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """从消息的 ``additional_kwargs.files`` 中提取文件信息。
        
            前端在上传成功后将文件元数据放在 ``additional_kwargs.files`` 中。
            每个条目包含：``filename``、``size``（字节）、``path``（虚拟路径）、``status``。
        
                Args:
                    message: 要检查的人类消息。
                    uploads_dir: 用于校验文件是否存在的物理 uploads 目录。
                        若提供，则对应文件已不存在的条目会被跳过。
        
                Returns:
                    包含虚拟路径的文件 dict 列表；若该字段缺失或为空则返回 ``None``。
        """

        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            files.append(
                {
                    "filename": filename,
                    "size": int(f.get("size") or 0),
                    "path": f"/mnt/user-data/uploads/{filename}",
                    "extension": Path(filename).suffix,
                }
            )
        return files if files else None

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """在 agent 执行前注入上传文件信息。
        
            新文件来自当前消息的 ``additional_kwargs.files``。历史文件从线程的 uploads 目录中扫描，
            排除新文件。会在最后一条人类消息内容前添加 ``<uploaded_files>`` 上下文。
            原始 ``additional_kwargs``（含 files 元数据）会保留在更新后的消息上，
            以便前端从流中读取。
        
                Args:
                    state: 当前 agent 状态。
                    runtime: 包含 ``thread_id`` 的运行时上下文。
        
                Returns:
                    包含上传文件列表的状态更新。
        """

        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # Resolve uploads directory for existence checks
        thread_id = (runtime.context or {}).get("thread_id")
        if thread_id is None:
            try:
                from langgraph.config import get_config

                thread_id = get_config().get("configurable", {}).get("thread_id")
            except RuntimeError:
                pass  # get_config() raises outside a runnable context (e.g. unit tests)
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id, user_id=get_effective_user_id()) if thread_id else None

        # Get newly uploaded files from the current message's additional_kwargs.files
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # Collect historical files from the uploads directory (all except the new ones)
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            for file_path in sorted(uploads_dir.iterdir()):
                if file_path.is_file() and file_path.name not in new_filenames:
                    stat = file_path.stat()
                    outline, preview = _extract_outline_for_file(file_path)
                    historical_files.append(
                        {
                            "filename": file_path.name,
                            "size": stat.st_size,
                            "path": f"/mnt/user-data/uploads/{file_path.name}",
                            "extension": file_path.suffix,
                            "outline": outline,
                            "outline_preview": preview,
                        }
                    )

        # Attach outlines to new files as well
        if uploads_dir:
            for file in new_files:
                phys_path = uploads_dir / file["filename"]
                outline, preview = _extract_outline_for_file(phys_path)
                file["outline"] = outline
                file["outline_preview"] = preview

        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # Create files message and prepend to the last human message content
        files_message = self._create_files_message(new_files, historical_files)

        # Extract original content - handle both string and list formats
        original_content = last_message.content
        if isinstance(original_content, str):
            # Simple case: string content, just prepend files message
            updated_content = f"{files_message}\n\n{original_content}"
        elif isinstance(original_content, list):
            # Complex case: list content (multimodal), preserve all blocks
            # Prepend files message as the first text block
            files_block = {"type": "text", "text": f"{files_message}\n\n"}
            # Keep all original blocks (including images)
            updated_content = [files_block, *original_content]
        else:
            # Other types, preserve as-is
            updated_content = original_content

        # Create new message with combined content.
        # Preserve additional_kwargs (including files metadata) so the frontend
        # can read structured file info from the streamed message.
        updated_message = HumanMessage(
            content=updated_content,
            id=last_message.id,
            name=last_message.name,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }

    @override
    async def abefore_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """将同步的 uploads 扫描移出事件循环的异步钩子。
        
            ``before_agent`` 会执行阻塞的文件系统 IO（目录枚举、``stat``、读取同级 ``.md`` 摘要）。
            当图以异步方式运行时，langgraph 默认会直接在事件循环上执行同步钩子，
            因此这里通过 ``run_in_executor`` 将其分派到工作线程。
            ``run_in_executor`` 会复制当前 context，因此 ``get_effective_user_id()`` 读取的
            ``user_id`` contextvar 会被保留。
        """

        return await run_in_executor(None, self.before_agent, state, runtime)
