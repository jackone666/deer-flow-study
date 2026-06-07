"""present_files 工具:把生成的文件暴露给前端用户进行查看与渲染。"""

from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tools.types import Runtime

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"


def _get_thread_id(runtime: Runtime) -> str | None:
    """从 runtime context 或 RunnableConfig 解析当前 thread_id。"""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id

    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None


def _normalize_presented_filepath(
    runtime: Runtime,
    filepath: str,
) -> str:
    """把呈现文件路径归一化为 ``/mnt/user-data/outputs/*`` 形式。

    支持以下两种入参:
    - 虚拟沙箱路径,如 ``/mnt/user-data/outputs/report.md``
    - 主机端线程输出目录路径,如
      ``/app/backend/.deer-flow/threads/<thread>/user-data/outputs/report.md``

    Args:
        runtime: 工具运行时,用于解析当前线程的输出目录。
        filepath: 用户提供的文件路径。

    Returns:
        归一化后的虚拟路径。

    Raises:
        ValueError: runtime 元数据缺失或路径不在当前线程的 outputs 目录内。
    """
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")

    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    thread_data = runtime.state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    outputs_dir = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        try:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath, user_id=get_effective_user_id())
        except TypeError:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath)
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


@tool("present_files", parse_docstring=True)
def present_file_tool(
    runtime: Runtime,
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """把文件展示给用户,在客户端界面查看与渲染。

    使用时机:

    - 把文件提供给用户查看、下载或交互
    - 一次性呈现多个相关文件
    - 在生成应当展示给用户的文件后

    不应使用本工具的情况:
    - 只是为了自己处理而读取文件内容
    - 临时或中间文件,不需要让用户看到

    注意:
    - 在创建文件并移动到 ``/mnt/user-data/outputs`` 后调用本工具。
    - 本工具可与其他工具安全并发调用;状态更新由 reducer 合并去重。

    Args:
        filepaths: 待呈现给用户的文件绝对路径列表,**只接受** ``/mnt/user-data/outputs`` 下的文件。
    """
    try:
        normalized_paths = [_normalize_presented_filepath(runtime, filepath) for filepath in filepaths]
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )

    # The merge_artifacts reducer will handle merging and deduplication
    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
        },
    )
