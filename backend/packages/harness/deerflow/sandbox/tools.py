"""沙箱工具集合:为 Agent 提供 bash / 文件读写 / glob / grep 等能力。

本模块负责:
- 路径虚拟化与解析(用户数据、技能、ACP 工作区、自定义挂载点)
- 路径穿越防御与本地沙箱命令合法性校验
- 沙箱懒加载获取与生命周期
- 面向 Agent 的 LangChain 工具函数(bash/ls/glob/grep/read_file/write_file/str_replace)
"""

import asyncio
import posixpath
import re
import shlex
from collections.abc import Callable
from pathlib import Path

from langchain.tools import tool

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.exceptions import (
    SandboxError,
    SandboxNotFoundError,
    SandboxRuntimeError,
)
from deerflow.sandbox.file_operation_lock import get_file_operation_lock
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.sandbox.search import GrepMatch
from deerflow.sandbox.security import LOCAL_HOST_BASH_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.tools.types import Runtime

_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![:\w])(?<!:/)/(?:[^\s\"'`;&|<>()]+)")
_FILE_URL_PATTERN = re.compile(r"\bfile://\S+", re.IGNORECASE)
_URL_WITH_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_URL_IN_COMMAND_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'`;&|<>()]+", re.IGNORECASE)
_DOTDOT_PATH_SEGMENT_PATTERN = re.compile(r"(?:^|[/\\=])\.\.(?:$|[/\\])")
_LOCAL_BASH_SYSTEM_PATH_PREFIXES = (
    "/bin/",
    "/usr/bin/",
    "/usr/sbin/",
    "/sbin/",
    "/opt/homebrew/bin/",
    "/dev/",
)

_DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"
_ACP_WORKSPACE_VIRTUAL_PATH = "/mnt/acp-workspace"
_DEFAULT_GLOB_MAX_RESULTS = 200
_MAX_GLOB_MAX_RESULTS = 1000
_DEFAULT_GREP_MAX_RESULTS = 100
_MAX_GREP_MAX_RESULTS = 500
_DEFAULT_WRITE_FILE_ERROR_MAX_CHARS = 2000
_LOCAL_BASH_CWD_COMMANDS = {"cd", "pushd"}
_LOCAL_BASH_COMMAND_WRAPPERS = {"command", "builtin"}
_LOCAL_BASH_COMMAND_PREFIX_KEYWORDS = {"!", "{", "case", "do", "elif", "else", "for", "if", "select", "then", "time", "until", "while"}
_LOCAL_BASH_COMMAND_END_KEYWORDS = {"}", "done", "esac", "fi"}
_LOCAL_BASH_ROOT_PATH_COMMANDS = {
    "awk",
    "cat",
    "cp",
    "du",
    "find",
    "grep",
    "head",
    "less",
    "ln",
    "ls",
    "more",
    "mv",
    "rm",
    "sed",
    "tail",
    "tar",
}
_SHELL_COMMAND_SEPARATORS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
_SHELL_REDIRECTION_OPERATORS = {
    "<",
    ">",
    "<<",
    ">>",
    "<<<",
    "<>",
    ">&",
    "<&",
    "&>",
    "&>>",
    ">|",
}


def _get_skills_container_path() -> str:
    """从配置中读取 skills 容器路径,失败时回退到默认值。

    第一次成功读取后会被缓存;配置读取失败时不会缓存,以便后续调用在配置可用时
    重新拿到真实值。

    Returns:
        容器内的 skills 路径字符串。
    """
    cached = getattr(_get_skills_container_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config import get_app_config

        value = get_app_config().skills.container_path
        _get_skills_container_path._cached = value  # type: ignore[attr-defined]
        return value
    except Exception:
        return _DEFAULT_SKILLS_CONTAINER_PATH


def _get_skills_host_path() -> str | None:
    """从配置中读取 skills 所在的主机文件系统路径。

    目录不存在或配置不可用时返回 None;仅缓存成功的查找结果,失败会在下次重试,
    避免一次性不可用导致 skills 访问被永久禁用。

    Returns:
        主机文件系统中的 skills 目录绝对路径字符串,失败时为 None。
    """
    cached = getattr(_get_skills_host_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config import get_app_config

        config = get_app_config()
        skills_path = config.skills.get_skills_path()
        if skills_path.exists():
            value = str(skills_path)
            _get_skills_host_path._cached = value  # type: ignore[attr-defined]
            return value
    except Exception:
        pass
    return None


def _is_skills_path(path: str) -> bool:
    """判断给定路径是否位于 skills 容器路径下。

    Args:
        path: 待检查的路径。

    Returns:
        是 skills 容器路径或位于其下时返回 True。
    """
    skills_prefix = _get_skills_container_path()
    return path == skills_prefix or path.startswith(f"{skills_prefix}/")


def _resolve_skills_path(path: str) -> str:
    """将虚拟 skills 路径解析为主机文件系统路径。

    Args:
        path: 虚拟 skills 路径,例如 ``/mnt/skills/public/bootstrap/SKILL.md``。

    Returns:
        解析后的主机路径字符串。

    Raises:
        FileNotFoundError: skills 目录未配置或不存在时抛出。
    """
    skills_container = _get_skills_container_path()
    skills_host = _get_skills_host_path()
    if skills_host is None:
        raise FileNotFoundError(f"Skills directory not available for path: {path}")

    if path == skills_container:
        return skills_host

    relative = path[len(skills_container) :].lstrip("/")
    return _join_path_preserving_style(skills_host, relative)


def _is_acp_workspace_path(path: str) -> bool:
    """判断路径是否位于 ACP 工作区虚拟路径下。"""
    return path == _ACP_WORKSPACE_VIRTUAL_PATH or path.startswith(f"{_ACP_WORKSPACE_VIRTUAL_PATH}/")


def _get_custom_mounts():
    """从沙箱配置中读取自定义卷挂载信息。

    第一次成功读取后会被缓存;配置读取失败时返回空列表且不缓存,以便后续
    调用在配置可用时拿到真实值。

    Returns:
        自定义挂载配置列表;配置不可用时为空列表。
    """
    cached = getattr(_get_custom_mounts, "_cached", None)
    if cached is not None:
        return cached
    try:
        from pathlib import Path

        from deerflow.config import get_app_config

        config = get_app_config()
        mounts = []
        if config.sandbox and config.sandbox.mounts:
            # Only include mounts whose host_path exists, consistent with
            # LocalSandboxProvider._setup_path_mappings() which also filters
            # by host_path.exists().
            mounts = [m for m in config.sandbox.mounts if Path(m.host_path).exists()]
        _get_custom_mounts._cached = mounts  # type: ignore[attr-defined]
        return mounts
    except Exception:
        # If config loading fails, return an empty list without caching so that
        # a later call can retry once the config is available.
        return []


def _is_custom_mount_path(path: str) -> bool:
    """判断路径是否落在某个自定义挂载点的 container_path 下。"""
    for mount in _get_custom_mounts():
        if path == mount.container_path or path.startswith(f"{mount.container_path}/"):
            return True
    return False


def _get_custom_mount_for_path(path: str):
    """获取与路径匹配的自定义挂载配置(优先匹配最长前缀)。"""
    best = None
    for mount in _get_custom_mounts():
        if path == mount.container_path or path.startswith(f"{mount.container_path}/"):
            if best is None or len(mount.container_path) > len(best.container_path):
                best = mount
    return best


def _extract_thread_id_from_thread_data(thread_data: "ThreadDataState | None") -> str | None:
    """从 thread_data 的 ``workspace_path`` 中提取 thread_id。

    ``workspace_path`` 形如 ``{base_dir}/threads/{thread_id}/user-data/workspace``,
    因此 ``Path(workspace_path).parent.parent.name`` 即为 thread_id。

    Args:
        thread_data: 当前线程的运行时数据,可为 None。

    Returns:
        解析出的 thread_id,失败时为 None。
    """
    if thread_data is None:
        return None
    workspace_path = thread_data.get("workspace_path")
    if not workspace_path:
        return None
    try:
        # {base_dir}/threads/{thread_id}/user-data/workspace → parent.parent = threads/{thread_id}
        return Path(workspace_path).parent.parent.name
    except Exception:
        return None


def _get_acp_workspace_host_path(thread_id: str | None = None) -> str | None:
    """获取 ACP 工作区在主机文件系统中的路径。

    - 当 ``thread_id`` 不为空时,返回该线程专属工作区
      ``{base_dir}/threads/{thread_id}/acp-workspace/``(不缓存,该目录由
      ``invoke_acp_agent_tool`` 按需创建)。
    - 当 ``thread_id`` 为 None 时,回退到全局 ``{base_dir}/acp-workspace/``,
      并在首次成功解析后缓存。
    - 目录不存在时返回 None。

    Args:
        thread_id: 可选的线程 ID。

    Returns:
        主机文件系统路径字符串;不可用时为 None。
    """
    if thread_id is not None:
        try:
            from deerflow.config.paths import get_paths
            from deerflow.runtime.user_context import get_effective_user_id

            host_path = get_paths().acp_workspace_dir(thread_id, user_id=get_effective_user_id())
            if host_path.exists():
                return str(host_path)
        except Exception:
            pass
        return None

    cached = getattr(_get_acp_workspace_host_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config.paths import get_paths

        host_path = get_paths().base_dir / "acp-workspace"
        if host_path.exists():
            value = str(host_path)
            _get_acp_workspace_host_path._cached = value  # type: ignore[attr-defined]
            return value
    except Exception:
        pass
    return None


def _resolve_acp_workspace_path(path: str, thread_id: str | None = None) -> str:
    """将虚拟 ACP 工作区路径解析为主机文件系统路径。

    Args:
        path: 虚拟路径,例如 ``/mnt/acp-workspace/hello_world.py``。
        thread_id: 当前线程 ID,用于解析线程专属工作区;为 None 时回退到全局工作区。

    Returns:
        解析后的主机路径字符串。

    Raises:
        FileNotFoundError: ACP 工作区目录不可用时抛出。
        PermissionError: 检测到路径穿越时抛出。
    """
    _reject_path_traversal(path)

    host_path = _get_acp_workspace_host_path(thread_id)
    if host_path is None:
        raise FileNotFoundError(f"ACP workspace directory not available for path: {path}")

    if path == _ACP_WORKSPACE_VIRTUAL_PATH:
        return host_path

    relative = path[len(_ACP_WORKSPACE_VIRTUAL_PATH) :].lstrip("/")
    resolved = _join_path_preserving_style(host_path, relative)

    if "/" in host_path and "\\" not in host_path:
        base_path = posixpath.normpath(host_path)
        candidate_path = posixpath.normpath(resolved)
        try:
            if posixpath.commonpath([base_path, candidate_path]) != base_path:
                raise PermissionError("Access denied: path traversal detected")
        except ValueError:
            raise PermissionError("Access denied: path traversal detected") from None
        return resolved

    resolved_path = Path(resolved).resolve()
    try:
        resolved_path.relative_to(Path(host_path).resolve())
    except ValueError:
        raise PermissionError("Access denied: path traversal detected")

    return str(resolved_path)


def _get_mcp_allowed_paths() -> list[str]:
    """从 MCP 配置中读取 filesystem server 允许访问的目录列表。

    Returns:
        形如 ``["/path/"]`` 的允许路径列表(末尾带 ``/``);配置不可用或
        未启用 filesystem server 时为空列表。
    """
    allowed_paths = []
    try:
        from deerflow.config.extensions_config import get_extensions_config

        extensions_config = get_extensions_config()

        for _, server in extensions_config.mcp_servers.items():
            if not server.enabled:
                continue

            # Only check the filesystem server
            args = server.args or []
            # Check if args has server-filesystem package
            has_filesystem = any("server-filesystem" in arg for arg in args)
            if not has_filesystem:
                continue
            # Unpack the allowed file system paths in config
            for arg in args:
                if not arg.startswith("-") and arg.startswith("/"):
                    allowed_paths.append(arg.rstrip("/") + "/")

    except Exception:
        pass

    return allowed_paths


def _get_tool_config_int(name: str, key: str, default: int) -> int:
    """从应用配置中按工具名读取整型字段值,失败时返回默认值。

    Args:
        name: 工具名称。
        key: 字段键名。
        default: 当字段不存在或类型不符时的回退值。

    Returns:
        配置中的整型值,或 ``default``。
    """
    try:
        tool_config = get_app_config().get_tool_config(name)
        if tool_config is not None and key in tool_config.model_extra:
            value = tool_config.model_extra.get(key)
            if isinstance(value, int):
                return value
    except Exception:
        pass
    return default


def _clamp_max_results(value: int, *, default: int, upper_bound: int) -> int:
    """把 ``value`` 限制在 ``[1, upper_bound]``;非法值回退到 ``default``。

    Args:
        value: 用户请求的 max_results。
        default: ``value <= 0`` 时使用的默认上限。
        upper_bound: 允许的最大值。

    Returns:
        限制后的整数值。
    """
    if value <= 0:
        return default
    return min(value, upper_bound)


def _resolve_max_results(name: str, requested: int, *, default: int, upper_bound: int) -> int:
    """在用户请求与配置上限之间取较小值,作为最终 max_results。

    Args:
        name: 工具名称(用于查找配置上限)。
        requested: 用户请求的 max_results。
        default: 缺省上限。
        upper_bound: 全局最大上限。

    Returns:
        实际生效的 max_results。
    """
    requested_max_results = _clamp_max_results(requested, default=default, upper_bound=upper_bound)
    configured_max_results = _clamp_max_results(
        _get_tool_config_int(name, "max_results", default),
        default=default,
        upper_bound=upper_bound,
    )
    return min(requested_max_results, configured_max_results)


def _resolve_local_read_path(path: str, thread_data: ThreadDataState) -> str:
    """在本地沙箱模式下解析只读路径(skills/ACP/user-data)。"""
    validate_local_tool_path(path, thread_data, read_only=True)
    if _is_skills_path(path):
        return _resolve_skills_path(path)
    if _is_acp_workspace_path(path):
        return _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
    return _resolve_and_validate_user_data_path(path, thread_data)


def _format_glob_results(root_path: str, matches: list[str], truncated: bool) -> str:
    """把 glob 命中结果格式化为可读文本。

    Args:
        root_path: 搜索根目录。
        matches: 命中的路径列表。
        truncated: 结果是否被截断。

    Returns:
        格式化的多行字符串。
    """
    if not matches:
        return f"No files matched under {root_path}"

    lines = [f"Found {len(matches)} paths under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{index}. {path}" for index, path in enumerate(matches, start=1))
    if truncated:
        lines.append("Results truncated. Narrow the path or pattern to see fewer matches.")
    return "\n".join(lines)


def _format_grep_results(root_path: str, matches: list[GrepMatch], truncated: bool) -> str:
    """把 grep 命中结果格式化为可读文本。

    Args:
        root_path: 搜索根目录。
        matches: 命中的 :class:`GrepMatch` 列表。
        truncated: 结果是否被截断。

    Returns:
        格式化的多行字符串。
    """
    if not matches:
        return f"No matches found under {root_path}"

    lines = [f"Found {len(matches)} matches under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{match.path}:{match.line_number}: {match.line}" for match in matches)
    if truncated:
        lines.append("Results truncated. Narrow the path or add a glob filter.")
    return "\n".join(lines)


def _path_variants(path: str) -> set[str]:
    """生成路径在 ``\\`` 与 ``/`` 两种分隔符下的等价形式。"""
    return {path, path.replace("\\", "/"), path.replace("/", "\\")}


def _path_separator_for_style(path: str) -> str:
    """根据路径中分隔符出现情况推断使用 ``/`` 还是 ``\\``。"""
    return "\\" if "\\" in path and "/" not in path else "/"


def _join_path_preserving_style(base: str, relative: str) -> str:
    """以 ``base`` 的分隔符风格拼接相对路径。

    Args:
        base: 基路径,决定使用的分隔符。
        relative: 相对路径,允许使用任意风格的分隔符。

    Returns:
        拼接后的完整路径字符串。
    """
    if not relative:
        return base
    separator = _path_separator_for_style(base)
    normalized_relative = relative.replace("\\" if separator == "/" else "/", separator).lstrip("/\\")
    stripped_base = base.rstrip("/\\")
    return f"{stripped_base}{separator}{normalized_relative}"


def _sanitize_error(error: Exception, runtime: Runtime | None = None) -> str:
    """清洗错误信息,避免泄露主机文件系统路径。

    本地沙箱模式下,错误字符串中解析出的主机路径会被替换为对应的虚拟路径,以
    避免用户可见的输出暴露主机目录布局。

    Args:
        error: 待处理的异常实例。
        runtime: 工具运行时,用于判断是否本地沙箱。

    Returns:
        清洗后的错误字符串。
    """
    msg = f"{type(error).__name__}: {error}"
    if runtime is not None and is_local_sandbox(runtime):
        thread_data = get_thread_data(runtime)
        msg = mask_local_paths_in_output(msg, thread_data)
    return msg


def _truncate_write_file_error_detail(detail: str, max_chars: int) -> str:
    """对 write_file 错误详情做中间截断,保留首尾。

    Args:
        detail: 错误详情原文。
        max_chars: 输出最大字符数(包含截断标记)。

    Returns:
        截断后的字符串。
    """
    if max_chars == 0:
        return detail
    if len(detail) <= max_chars:
        return detail
    total = len(detail)
    marker_max_len = len(f"\n... [write_file error truncated: {total} chars skipped] ...\n")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return detail[:max_chars]
    head_len = kept // 2
    tail_len = kept - head_len
    skipped = total - kept
    marker = f"\n... [write_file error truncated: {skipped} chars skipped] ...\n"
    return f"{detail[:head_len]}{marker}{detail[-tail_len:] if tail_len > 0 else ''}"


def _format_write_file_error(
    requested_path: str,
    error: Exception,
    runtime: Runtime | None = None,
    *,
    max_chars: int = _DEFAULT_WRITE_FILE_ERROR_MAX_CHARS,
) -> str:
    """生成有限长度、已清洗路径的 write_file 错误字符串。

    Args:
        requested_path: 触发错误的用户请求路径。
        error: 原始异常。
        runtime: 工具运行时。
        max_chars: 整条错误字符串的最大字符数。

    Returns:
        格式化后的错误描述。
    """
    header = f"Error: Failed to write file '{requested_path}'"
    detail = _sanitize_error(error, runtime)
    if max_chars == 0:
        return f"{header}: {detail}"
    detail_budget = max_chars - len(header) - 2
    if detail_budget <= 0:
        return _truncate_write_file_error_detail(f"{header}: {detail}", max_chars)
    return f"{header}: {_truncate_write_file_error_detail(detail, detail_budget)}"


def replace_virtual_path(path: str, thread_data: ThreadDataState | None) -> str:
    """把 ``/mnt/user-data`` 虚拟路径替换为当前线程对应的实际路径。

    映射规则:
        - ``/mnt/user-data/workspace/*`` → ``thread_data['workspace_path']/*``
        - ``/mnt/user-data/uploads/*`` → ``thread_data['uploads_path']/*``
        - ``/mnt/user-data/outputs/*`` → ``thread_data['outputs_path']/*``

    Args:
        path: 可能含虚拟路径前缀的字符串。
        thread_data: 当前线程的运行时数据,可为 None。

    Returns:
        替换后的实际路径;若未命中或无 thread_data 则原样返回。
    """
    if thread_data is None:
        return path

    mappings = _thread_virtual_to_actual_mappings(thread_data)
    if not mappings:
        return path

    # Longest-prefix-first replacement with segment-boundary checks.
    for virtual_base, actual_base in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
        if path == virtual_base:
            return actual_base
        if path.startswith(f"{virtual_base}/"):
            rest = path[len(virtual_base) :].lstrip("/")
            result = _join_path_preserving_style(actual_base, rest)
            if path.endswith("/") and not result.endswith(("/", "\\")):
                result += _path_separator_for_style(actual_base)
            return result

    return path


def _thread_virtual_to_actual_mappings(thread_data: ThreadDataState) -> dict[str, str]:
    """构造当前线程的虚拟路径到实际路径映射表。"""
    mappings: dict[str, str] = {}

    workspace = thread_data.get("workspace_path")
    uploads = thread_data.get("uploads_path")
    outputs = thread_data.get("outputs_path")

    if workspace:
        mappings[f"{VIRTUAL_PATH_PREFIX}/workspace"] = workspace
    if uploads:
        mappings[f"{VIRTUAL_PATH_PREFIX}/uploads"] = uploads
    if outputs:
        mappings[f"{VIRTUAL_PATH_PREFIX}/outputs"] = outputs

    # Also map the virtual root when all known dirs share the same parent.
    actual_dirs = [Path(p) for p in (workspace, uploads, outputs) if p]
    if actual_dirs:
        common_parent = str(Path(actual_dirs[0]).parent)
        if all(str(path.parent) == common_parent for path in actual_dirs):
            mappings[VIRTUAL_PATH_PREFIX] = common_parent

    return mappings


def _thread_actual_to_virtual_mappings(thread_data: ThreadDataState) -> dict[str, str]:
    """构造当前线程的实际路径到虚拟路径反向映射(用于输出脱敏)。"""
    return {actual: virtual for virtual, actual in _thread_virtual_to_actual_mappings(thread_data).items()}


def mask_local_paths_in_output(output: str, thread_data: ThreadDataState | None) -> str:
    """把本地沙箱输出中的主机绝对路径替换为对应的虚拟路径。

    同时处理 user-data(按线程)、skills、ACP 工作区等虚拟路径族。

    Args:
        output: 待脱敏的原始输出字符串。
        thread_data: 当前线程的运行时数据。

    Returns:
        脱敏后的输出。
    """
    result = output

    # Mask skills host paths
    skills_host = _get_skills_host_path()
    skills_container = _get_skills_container_path()
    if skills_host:
        raw_base = str(Path(skills_host))
        resolved_base = str(Path(skills_host).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_skills(match: re.Match, _base: str = base) -> str:
                """执行赋值。
                
                        Args:
                            match: re.Match: 参数说明。
                            _base: str: 参数说明。
                
                        Returns:
                            str。
                """
                matched_path = match.group(0)
                if matched_path == _base:
                    return skills_container
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{skills_container}/{relative}" if relative else skills_container

            result = pattern.sub(replace_skills, result)

    # Mask ACP workspace host paths
    _thread_id = _extract_thread_id_from_thread_data(thread_data)
    acp_host = _get_acp_workspace_host_path(_thread_id)
    if acp_host:
        raw_base = str(Path(acp_host))
        resolved_base = str(Path(acp_host).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_acp(match: re.Match, _base: str = base) -> str:
                """执行赋值。
                
                        Args:
                            match: re.Match: 参数说明。
                            _base: str: 参数说明。
                
                        Returns:
                            str。
                """
                matched_path = match.group(0)
                if matched_path == _base:
                    return _ACP_WORKSPACE_VIRTUAL_PATH
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{_ACP_WORKSPACE_VIRTUAL_PATH}/{relative}" if relative else _ACP_WORKSPACE_VIRTUAL_PATH

            result = pattern.sub(replace_acp, result)

    # Custom mount host paths are masked by LocalSandbox._reverse_resolve_paths_in_output()

    # Mask user-data host paths
    if thread_data is None:
        return result

    mappings = _thread_actual_to_virtual_mappings(thread_data)
    if not mappings:
        return result

    for actual_base, virtual_base in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
        raw_base = str(Path(actual_base))
        resolved_base = str(Path(actual_base).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped_actual = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped_actual + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_match(match: re.Match, _base: str = base, _virtual: str = virtual_base) -> str:
                """执行赋值。
                
                        Args:
                            match: re.Match: 参数说明。
                            _base: str: 参数说明。
                            _virtual: str: 参数说明。
                
                        Returns:
                            str。
                """
                matched_path = match.group(0)
                if matched_path == _base:
                    return _virtual
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{_virtual}/{relative}" if relative else _virtual

            result = pattern.sub(replace_match, result)

    return result


def _reject_path_traversal(path: str) -> None:
    """拒绝包含 ``..`` 段以防止目录穿越。"""
    # Normalise to forward slashes, then check for '..' segments.
    normalised = path.replace("\\", "/")
    for segment in normalised.split("/"):
        if segment == "..":
            raise PermissionError("Access denied: path traversal detected")


def validate_local_tool_path(path: str, thread_data: ThreadDataState | None, *, read_only: bool = False) -> None:
    """校验虚拟路径是否允许在本地沙箱中被访问。

    这是安全关卡函数,只判断是否允许访问,不会把虚拟路径解析为主机路径,
    解析需要由调用方通过 :func:`resolve_and_validate_user_data_path` 或
    :func:`_resolve_skills_path` 完成。

    允许的虚拟路径族:
      - ``/mnt/user-data/*`` — 始终允许(读 + 写)
      - ``/mnt/skills/*`` — 仅当 ``read_only`` 为 True 时允许
      - ``/mnt/acp-workspace/*`` — 仅当 ``read_only`` 为 True 时允许
      - 自定义挂载路径(来自 config.yaml) — 受每条挂载的 ``read_only`` 控制

    Args:
        path: 待校验的虚拟路径。
        thread_data: 线程运行时数据(本地沙箱必须存在)。
        read_only: 为 True 时允许访问 skills 与 ACP 工作区。

    Raises:
        SandboxRuntimeError: 缺少线程数据时抛出。
        PermissionError: 路径不允许或包含穿越段时抛出。
    """
    if thread_data is None:
        raise SandboxRuntimeError("Thread data not available for local sandbox")

    _reject_path_traversal(path)

    # Skills paths — read-only access only
    if _is_skills_path(path):
        if not read_only:
            raise PermissionError(f"Write access to skills path is not allowed: {path}")
        return

    # ACP workspace paths — read-only access only
    if _is_acp_workspace_path(path):
        if not read_only:
            raise PermissionError(f"Write access to ACP workspace is not allowed: {path}")
        return

    # User-data paths
    if path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
        return

    # Custom mount paths — respect read_only config
    if _is_custom_mount_path(path):
        mount = _get_custom_mount_for_path(path)
        if mount and mount.read_only and not read_only:
            raise PermissionError(f"Write access to read-only mount is not allowed: {path}")
        return

    raise PermissionError(f"Only paths under {VIRTUAL_PATH_PREFIX}/, {_get_skills_container_path()}/, {_ACP_WORKSPACE_VIRTUAL_PATH}/, or configured mount paths are allowed")


def _validate_resolved_user_data_path(resolved: Path, thread_data: ThreadDataState) -> None:
    """校验解析后的主机路径是否落在当前线程允许的根目录内。

    Args:
        resolved: 解析并 resolve 后的主机路径。
        thread_data: 当前线程运行时数据。

    Raises:
        SandboxRuntimeError: 线程未配置任何允许的根目录。
        PermissionError: 路径越界时抛出。
    """
    allowed_roots = [
        Path(p).resolve()
        for p in (
            thread_data.get("workspace_path"),
            thread_data.get("uploads_path"),
            thread_data.get("outputs_path"),
        )
        if p is not None
    ]

    if not allowed_roots:
        raise SandboxRuntimeError("No allowed local sandbox directories configured")

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue

    raise PermissionError("Access denied: path traversal detected")


def _resolve_and_validate_user_data_path(path: str, thread_data: ThreadDataState) -> str:
    """解析 ``/mnt/user-data`` 虚拟路径并校验不越界,返回主机路径字符串。"""
    resolved_str = replace_virtual_path(path, thread_data)
    resolved = Path(resolved_str).resolve()
    _validate_resolved_user_data_path(resolved, thread_data)
    return str(resolved)


def _is_non_file_url_token(token: str) -> bool:
    """判断一个 token 是否为不应被当作路径的 URL。"""
    values = [token]
    if "=" in token:
        values.append(token.split("=", 1)[1])

    for value in values:
        match = _URL_WITH_SCHEME_PATTERN.match(value)
        if match and not value.lower().startswith("file://"):
            return True
    return False


def _non_file_url_spans(command: str) -> list[tuple[int, int]]:
    """返回命令字符串中所有非 file:// URL 的 ``(start, end)`` 位置列表。"""
    spans = []
    for match in _URL_IN_COMMAND_PATTERN.finditer(command):
        if not match.group().lower().startswith("file://"):
            spans.append(match.span())
    return spans


def _is_in_spans(position: int, spans: list[tuple[int, int]]) -> bool:
    """判断 ``position`` 是否落在任一 ``(start, end)`` 区间内。"""
    return any(start <= position < end for start, end in spans)


def _has_dotdot_path_segment(token: str) -> bool:
    """判断 token 是否包含 ``..`` 路径段(且不是 URL)。"""
    if _is_non_file_url_token(token):
        return False
    return bool(_DOTDOT_PATH_SEGMENT_PATTERN.search(token))


def _split_shell_tokens(command: str) -> list[str]:
    """使用 POSIX shlex 把 shell 命令拆分为 token 列表。"""
    try:
        normalized = command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ; ")
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        # The shell will reject malformed quoting later; keep validation
        # best-effort instead of turning syntax errors into security messages.
        return command.split()


def _is_shell_command_separator(token: str) -> bool:
    """判断 token 是否为 shell 命令分隔符(``;``、``&&``、``|`` 等)。"""
    return token in _SHELL_COMMAND_SEPARATORS


def _is_shell_redirection_operator(token: str) -> bool:
    """判断 token 是否为 shell 重定向操作符(``>``、``<``、``>>`` 等)。"""
    return token in _SHELL_REDIRECTION_OPERATORS


def _is_shell_assignment(token: str) -> bool:
    """判断 token 是否为合法的 shell 变量赋值语句(``NAME=value``)。"""
    name, separator, _ = token.partition("=")
    if not separator or not name:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _is_allowed_local_bash_absolute_path(path: str, allowed_paths: list[str], *, allow_system_paths: bool) -> bool:
    """判断绝对路径是否在本地 bash 允许的白名单内。"""
    # Check for MCP filesystem server allowed paths
    if any(path.startswith(allowed_path) or path == allowed_path.rstrip("/") for allowed_path in allowed_paths):
        _reject_path_traversal(path)
        return True

    if path == VIRTUAL_PATH_PREFIX or path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
        _reject_path_traversal(path)
        return True

    # Allow skills container path (resolved by tools.py before passing to sandbox)
    if _is_skills_path(path):
        _reject_path_traversal(path)
        return True

    # Allow ACP workspace path (path-traversal check only)
    if _is_acp_workspace_path(path):
        _reject_path_traversal(path)
        return True

    # Allow custom mount container paths
    if _is_custom_mount_path(path):
        _reject_path_traversal(path)
        return True

    if allow_system_paths and any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in _LOCAL_BASH_SYSTEM_PATH_PREFIXES):
        return True

    return False


def _next_cd_target(tokens: list[str], start_index: int) -> tuple[str | None, int]:
    """从 ``tokens[start_index]`` 开始解析 cd 的目标参数。

    Args:
        tokens: 已拆分的 shell token 列表。
        start_index: 扫描起点。

    Returns:
        ``(目标路径或 None, 下一个未消费索引)``。
    """
    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if _is_shell_command_separator(token):
            return None, index
        if _is_shell_redirection_operator(token):
            index += 2
            continue
        if token == "--":
            index += 1
            continue
        if token in {"-L", "-P", "-e", "-@"}:
            index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        return token, index + 1
    return None, index


def _validate_local_bash_cwd_target(command_name: str, target: str | None, allowed_paths: list[str]) -> None:
    """校验 cd 类命令的目标目录是否在本地 bash 允许的白名单内。"""
    if target is None or target == "-":
        raise PermissionError(f"Unsafe working directory change in command: {command_name}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith(("$", "`")):
        raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith("~"):
        raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith("/"):
        _reject_path_traversal(target)
        if not _is_allowed_local_bash_absolute_path(target, allowed_paths, allow_system_paths=False):
            raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")


def _looks_like_unsafe_cwd_target(target: str | None) -> bool:
    """粗略判断一个目录切换目标是否危险(以 ``-``/``$``/``/``/``..`` 开头等)。"""
    if target is None:
        return False
    return target == "-" or target.startswith(("$", "`", "~", "/", "..")) or _has_dotdot_path_segment(target)


def _validate_local_bash_root_path_args(command_name: str, tokens: list[str], start_index: int) -> None:
    """对常见根路径访问命令的参数进行白名单校验。"""
    if command_name not in _LOCAL_BASH_ROOT_PATH_COMMANDS:
        return

    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if _is_shell_command_separator(token):
            return
        if _is_shell_redirection_operator(token):
            index += 2
            continue
        if token == "/" and not _is_non_file_url_token(token):
            raise PermissionError(f"Unsafe absolute paths in command: /. Use paths under {VIRTUAL_PATH_PREFIX}")
        index += 1


def _validate_local_bash_shell_tokens(command: str, allowed_paths: list[str]) -> None:
    """通过 token 流保守地拒绝绝对路径扫描漏掉的相对路径穿越。"""
    if re.search(r"\$\([^)]*\b(?:cd|pushd)\b", command):
        raise PermissionError(f"Unsafe working directory change in command substitution. Use paths under {VIRTUAL_PATH_PREFIX}")

    tokens = _split_shell_tokens(command)

    for token in tokens:
        if _is_shell_command_separator(token) or _is_shell_redirection_operator(token):
            continue
        if _has_dotdot_path_segment(token):
            raise PermissionError("Access denied: path traversal detected")

    at_command_start = True
    index = 0
    while index < len(tokens):
        token = tokens[index]

        if _is_shell_command_separator(token):
            at_command_start = True
            index += 1
            continue

        if _is_shell_redirection_operator(token):
            index += 1
            continue

        if at_command_start and _is_shell_assignment(token):
            index += 1
            continue

        command_name = token.rsplit("/", 1)[-1]
        if at_command_start and command_name in _LOCAL_BASH_COMMAND_PREFIX_KEYWORDS | _LOCAL_BASH_COMMAND_END_KEYWORDS:
            index += 1
            continue

        if not at_command_start:
            index += 1
            continue

        at_command_start = False
        if command_name in _LOCAL_BASH_COMMAND_WRAPPERS and index + 1 < len(tokens):
            wrapped_name = tokens[index + 1].rsplit("/", 1)[-1]
            if wrapped_name in _LOCAL_BASH_CWD_COMMANDS:
                target, next_index = _next_cd_target(tokens, index + 2)
                _validate_local_bash_cwd_target(wrapped_name, target, allowed_paths)
                index = next_index
                continue
            _validate_local_bash_root_path_args(wrapped_name, tokens, index + 2)

        if command_name not in _LOCAL_BASH_CWD_COMMANDS:
            _validate_local_bash_root_path_args(command_name, tokens, index + 1)
            index += 1
            continue

        target, next_index = _next_cd_target(tokens, index + 1)
        _validate_local_bash_cwd_target(command_name, target, allowed_paths)
        index = next_index


def resolve_and_validate_user_data_path(path: str, thread_data: ThreadDataState) -> str:
    """解析 ``/mnt/user-data`` 虚拟路径并校验不越界。"""
    return _resolve_and_validate_user_data_path(path, thread_data)


def validate_local_bash_command_paths(command: str, thread_data: ThreadDataState | None) -> None:
    """校验本地沙箱 bash 命令中的绝对路径。

    本校验只是 ``sandbox.allow_host_bash: true`` 显式开启后的兜底防护,并不是一个
    安全的沙箱边界,不可视作与主机文件系统的隔离。

    本地模式下,用户数据访问必须使用 ``/mnt/user-data`` 下的虚拟路径。允许
    ``/mnt/skills``、``/mnt/acp-workspace``、以及 config.yaml 中配置的自定义挂载
    容器路径(仅做路径穿越检查,不在此处做写保护)。另保留少量系统路径前缀白名单
    以兼容可执行文件与设备文件引用(例如 ``/bin/sh``、``/dev/null``)。

    Args:
        command: 待校验的 bash 命令字符串。
        thread_data: 当前线程的运行时数据。

    Raises:
        SandboxRuntimeError: 线程数据不可用时抛出。
        PermissionError: 命令中出现不安全路径(``file://``、越界绝对路径、``..`` 段)时抛出。
    """
    if thread_data is None:
        raise SandboxRuntimeError("Thread data not available for local sandbox")

    # Block file:// URLs which bypass the absolute-path regex but allow local file exfiltration
    file_url_match = _FILE_URL_PATTERN.search(command)
    if file_url_match:
        raise PermissionError(f"Unsafe file:// URL in command: {file_url_match.group()}. Use paths under {VIRTUAL_PATH_PREFIX}")

    unsafe_paths: list[str] = []
    allowed_paths = _get_mcp_allowed_paths()
    _validate_local_bash_shell_tokens(command, allowed_paths)
    url_spans = _non_file_url_spans(command)

    for match in _ABSOLUTE_PATH_PATTERN.finditer(command):
        if _is_in_spans(match.start(), url_spans):
            continue
        absolute_path = match.group()
        if _is_allowed_local_bash_absolute_path(absolute_path, allowed_paths, allow_system_paths=True):
            continue

        unsafe_paths.append(absolute_path)

    if unsafe_paths:
        unsafe = ", ".join(sorted(dict.fromkeys(unsafe_paths)))
        raise PermissionError(f"Unsafe absolute paths in command: {unsafe}. Use paths under {VIRTUAL_PATH_PREFIX}")


def replace_virtual_paths_in_command(command: str, thread_data: ThreadDataState | None) -> str:
    """把命令字符串中所有虚拟路径(``/mnt/user-data``、``/mnt/skills``、``/mnt/acp-workspace``)替换为实际路径。

    Args:
        command: 可能含虚拟路径前缀的命令字符串。
        thread_data: 当前线程的运行时数据。

    Returns:
        替换完成后的命令字符串。
    """
    result = command

    # Replace skills paths
    skills_container = _get_skills_container_path()
    skills_host = _get_skills_host_path()
    if skills_host and skills_container in result:
        skills_pattern = re.compile(rf"{re.escape(skills_container)}(/[^\s\"';&|<>()]*)?")

        def replace_skills_match(match: re.Match) -> str:
            """返回值。
            
                    Args:
                        match: re.Match: 参数说明。
            
                    Returns:
                        str。
            """
            return _resolve_skills_path(match.group(0))

        result = skills_pattern.sub(replace_skills_match, result)

    # Replace ACP workspace paths
    _thread_id = _extract_thread_id_from_thread_data(thread_data)
    acp_host = _get_acp_workspace_host_path(_thread_id)
    if acp_host and _ACP_WORKSPACE_VIRTUAL_PATH in result:
        acp_pattern = re.compile(rf"{re.escape(_ACP_WORKSPACE_VIRTUAL_PATH)}(/[^\s\"';&|<>()]*)?")

        def replace_acp_match(match: re.Match, _tid: str | None = _thread_id) -> str:
            """返回值。
            
                    Args:
                        match: re.Match: 参数说明。
                        _tid: str | None: 参数说明。
            
                    Returns:
                        str。
            """
            return _resolve_acp_workspace_path(match.group(0), _tid)

        result = acp_pattern.sub(replace_acp_match, result)

    # Custom mount paths are resolved by LocalSandbox._resolve_paths_in_command()

    # Replace user-data paths
    if VIRTUAL_PATH_PREFIX in result and thread_data is not None:
        pattern = re.compile(rf"{re.escape(VIRTUAL_PATH_PREFIX)}(/[^\s\"';&|<>()]*)?")

        def replace_user_data_match(match: re.Match) -> str:
            """返回值。
            
                    Args:
                        match: re.Match: 参数说明。
            
                    Returns:
                        str。
            """
            return replace_virtual_path(match.group(0), thread_data)

        result = pattern.sub(replace_user_data_match, result)

    return result


def _apply_cwd_prefix(command: str, thread_data: ThreadDataState | None) -> str:
    """在命令前添加 ``cd <workspace> &&`` 前缀,让相对路径锚定到线程工作区。

    Args:
        command: 待执行的 bash 命令。
        thread_data: 当前线程运行时数据。

    Returns:
        若 ``workspace_path`` 可用则返回带前缀的命令;否则原样返回。
    """
    if thread_data and (workspace := thread_data.get("workspace_path")):
        return f"cd {shlex.quote(workspace)} && {command}"
    return command


def get_thread_data(runtime: Runtime | None) -> ThreadDataState | None:
    """从工具运行时 state 中读取 thread_data。"""
    if runtime is None:
        return None
    if runtime.state is None:
        return None
    return runtime.state.get("thread_data")


def is_local_sandbox(runtime: Runtime | None) -> bool:
    """判断当前沙箱是否为本地沙箱。

    同时接受老式泛化 ID ``"local"``(无 thread 上下文时获取的)与按线程的 ID
    形式 ``"local:{thread_id}"``(:meth:`LocalSandboxProvider.acquire` 在有
    thread 上下文时产生的)。

    Args:
        runtime: 工具运行时。

    Returns:
        当前沙箱为本地沙箱时返回 True。
    """
    if runtime is None:
        return False
    if runtime.state is None:
        return False
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        return False
    sandbox_id = sandbox_state.get("sandbox_id")
    if not isinstance(sandbox_id, str):
        return False
    return sandbox_id == "local" or sandbox_id.startswith("local:")


def sandbox_from_runtime(runtime: Runtime | None = None) -> Sandbox:
    """从工具运行时中提取沙箱实例。

    已废弃:推荐使用 :func:`ensure_sandbox_initialized` 走懒加载路径;本函数
    假定沙箱已经初始化,否则会抛错。

    Args:
        runtime: 工具运行时。

    Returns:
        当前沙箱实例。

    Raises:
        SandboxRuntimeError: runtime 不可用或 state 中缺少沙箱信息。
        SandboxNotFoundError: 找不到指定 ID 的沙箱。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")
    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        raise SandboxRuntimeError("Sandbox state not initialized in runtime")
    sandbox_id = sandbox_state.get("sandbox_id")
    if sandbox_id is None:
        raise SandboxRuntimeError("Sandbox ID not found in state")
    sandbox = get_sandbox_provider().get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError(f"Sandbox with ID '{sandbox_id}' not found", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for downstream use
    return sandbox


def ensure_sandbox_initialized(runtime: Runtime | None = None) -> Sandbox:
    """确保沙箱已初始化,必要时进行懒加载获取。

    首次调用会从提供者获取沙箱并写入 runtime state;后续调用直接返回已存在的沙箱。
    线程安全由提供者内部锁机制保证。

    Args:
        runtime: 含 state 和 context 的工具运行时。

    Returns:
        已初始化的沙箱实例。

    Raises:
        SandboxRuntimeError: runtime 不可用或缺少 thread_id。
        SandboxNotFoundError: 沙箱获取失败时抛出。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")

    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")

    # 先复用 state 中已有的 sandbox，避免同一线程内重复创建执行环境。
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is not None:
        sandbox_id = sandbox_state.get("sandbox_id")
        if sandbox_id is not None:
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is not None:
                if runtime.context is not None:
                    runtime.context["sandbox_id"] = sandbox_id  # 写入 context，便于 after_agent/release 与日志关联。
                return sandbox
            # 旧 sandbox 已被释放，继续走懒加载申请新实例。

    # 懒加载申请：只有首次真实工具调用时才根据 thread_id 获取 sandbox。
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        thread_id = runtime.config.get("configurable", {}).get("thread_id") if runtime.config else None
    if thread_id is None:
        raise SandboxRuntimeError("Thread ID not available in runtime context")

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire(thread_id)

    # 写回 runtime state，使后续工具调用能复用同一个 sandbox_id。
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}

    # 取回 sandbox 实例并返回给具体工具执行。
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # 写入 context，便于 after_agent/release 与日志关联。
    return sandbox


async def ensure_sandbox_initialized_async(runtime: Runtime | None = None) -> Sandbox:
    """异步版 :func:`ensure_sandbox_initialized`,用于异步工具运行时。

    保持懒加载沙箱获取走异步提供者钩子,避免 AIO 沙箱启动与就绪轮询在异步工具
    执行期间回退到同步的 ``provider.acquire()``。

    Args:
        runtime: 含 state 和 context 的工具运行时。

    Returns:
        已初始化的沙箱实例。

    Raises:
        SandboxRuntimeError: runtime 不可用或缺少 thread_id。
        SandboxNotFoundError: 沙箱获取失败。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")

    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")

    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is not None:
        sandbox_id = sandbox_state.get("sandbox_id")
        if sandbox_id is not None:
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is not None:
                if runtime.context is not None:
                    runtime.context["sandbox_id"] = sandbox_id
                return sandbox

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        thread_id = runtime.config.get("configurable", {}).get("thread_id") if runtime.config else None
    if thread_id is None:
        raise SandboxRuntimeError("Thread ID not available in runtime context")

    provider = get_sandbox_provider()
    sandbox_id = await provider.acquire_async(thread_id)

    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}

    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id
    return sandbox


async def _run_sync_tool_after_async_sandbox_init(
    func: Callable[..., str] | None,
    runtime: Runtime,
    *args: object,
) -> str:
    """异步初始化沙箱,然后把同步工具体放到工作线程中执行。"""
    try:
        await ensure_sandbox_initialized_async(runtime)
    except SandboxError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error initializing sandbox: {_sanitize_error(e, runtime)}"

    if func is None:
        return "Error: Tool implementation not available"

    return await asyncio.to_thread(func, runtime, *args)


def ensure_thread_directories_exist(runtime: Runtime | None) -> None:
    """确保线程数据目录(workspace/uploads/outputs)存在。

    在首次使用任意沙箱工具时延迟调用。本地沙箱会在主机文件系统上创建这些目录;
    其它沙箱(如 aio)目录已经在容器中挂载,无需处理。

    Args:
        runtime: 工具运行时。
    """
    if runtime is None:
        return

    # Only create directories for local sandbox
    if not is_local_sandbox(runtime):
        return

    thread_data = get_thread_data(runtime)
    if thread_data is None:
        return

    # Check if directories have already been created
    if runtime.state.get("thread_directories_created"):
        return

    # Create the three directories
    import os

    for key in ["workspace_path", "uploads_path", "outputs_path"]:
        path = thread_data.get(key)
        if path:
            os.makedirs(path, exist_ok=True)

    # Mark as created to avoid redundant operations
    runtime.state["thread_directories_created"] = True


def _truncate_bash_output(output: str, max_chars: int) -> str:
    """对 bash 输出做中间截断,保留头尾(50/50 对半分)。

    bash 输出可能在头尾任意位置出现错误(stderr/stdout 顺序不确定),因此两端同等保留。

    返回的字符串(含截断标记)长度不超过 ``max_chars``。``max_chars=0`` 表示
    禁用截断,直接返回原文。

    Args:
        output: 原始输出。
        max_chars: 最大字符数。

    Returns:
        截断后的输出。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total_len = len(output)
    # Compute the exact worst-case marker length: skipped chars is at most
    # total_len, so this is a tight upper bound.
    marker_max_len = len(f"\n... [middle truncated: {total_len} chars skipped] ...\n")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    head_len = kept // 2
    tail_len = kept - head_len
    skipped = total_len - kept
    marker = f"\n... [middle truncated: {skipped} chars skipped] ...\n"
    return f"{output[:head_len]}{marker}{output[-tail_len:] if tail_len > 0 else ''}"


def _truncate_read_file_output(output: str, max_chars: int) -> str:
    """对 read_file 输出做头部截断,保留文件开头。

    源码和文档自上而下阅读,头部包含最多上下文(import、类定义、函数签名等)。

    返回的字符串(含截断标记)长度不超过 ``max_chars``。``max_chars=0`` 表示
    禁用截断,直接返回原文。

    Args:
        output: 原始文件内容。
        max_chars: 最大字符数。

    Returns:
        截断后的内容。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    # Compute the exact worst-case marker length: both numeric fields are at
    # their maximum (total chars), so this is a tight upper bound.
    marker_max_len = len(f"\n... [truncated: showing first {total} of {total} chars. Use start_line/end_line to read a specific range] ...")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    marker = f"\n... [truncated: showing first {kept} of {total} chars. Use start_line/end_line to read a specific range] ..."
    return f"{output[:kept]}{marker}"


def _truncate_ls_output(output: str, max_chars: int) -> str:
    """对 ls 输出做头部截断,保留列表开头。

    目录列表自上而下阅读,头部展示最相关的目录结构。

    返回的字符串(含截断标记)长度不超过 ``max_chars``。``max_chars=0`` 表示
    禁用截断,直接返回原文。

    Args:
        output: 原始列表内容。
        max_chars: 最大字符数。

    Returns:
        截断后的内容。
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    marker_max_len = len(f"\n... [truncated: showing first {total} of {total} chars. Use a more specific path to see fewer results] ...")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    marker = f"\n... [truncated: showing first {kept} of {total} chars. Use a more specific path to see fewer results] ..."
    return f"{output[:kept]}{marker}"


@tool("bash", parse_docstring=True)
def bash_tool(runtime: Runtime, description: str, command: str) -> str:
    """在 Linux 环境中执行 bash 命令。


    - 使用 `python` 运行 Python 代码。
    - 优先使用 `/mnt/user-data/workspace/.venv` 中的线程级虚拟环境。
    - 在虚拟环境中使用 `python -m pip` 安装 Python 包。

    Args:
        description: 简短说明运行此命令的目的。请始终作为第一个参数提供。
        command: 待执行的 bash 命令,文件和目录请始终使用绝对路径。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        if is_local_sandbox(runtime):
            if not is_host_bash_allowed():
                return f"Error: {LOCAL_HOST_BASH_DISABLED_MESSAGE}"
            ensure_thread_directories_exist(runtime)
            thread_data = get_thread_data(runtime)
            validate_local_bash_command_paths(command, thread_data)
            command = replace_virtual_paths_in_command(command, thread_data)
            command = _apply_cwd_prefix(command, thread_data)
            output = sandbox.execute_command(command)
            try:
                from deerflow.config.app_config import get_app_config

                sandbox_cfg = get_app_config().sandbox
                max_chars = sandbox_cfg.bash_output_max_chars if sandbox_cfg else 20000
            except Exception:
                max_chars = 20000
            return _truncate_bash_output(mask_local_paths_in_output(output, thread_data), max_chars)
        ensure_thread_directories_exist(runtime)
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.bash_output_max_chars if sandbox_cfg else 20000
        except Exception:
            max_chars = 20000
        return _truncate_bash_output(sandbox.execute_command(command), max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error executing command: {_sanitize_error(e, runtime)}"


async def _bash_tool_async(runtime: Runtime, description: str, command: str) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(bash_tool.func, runtime, description, command)


bash_tool.coroutine = _bash_tool_async


@tool("ls", parse_docstring=True)
def ls_tool(runtime: Runtime, description: str, path: str) -> str:
    """以树形格式列出目录内容(最多递归 2 层)。

    Args:
        description: 简短说明列举该目录的目的。请始终作为第一个参数提供。
        path: 待列举目录的 **绝对** 路径。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data, read_only=True)
            if _is_skills_path(path):
                path = _resolve_skills_path(path)
            elif _is_acp_workspace_path(path):
                path = _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
            elif not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        children = sandbox.list_dir(path)
        if not children:
            return "(empty)"
        output = "\n".join(children)
        if thread_data is not None:
            output = mask_local_paths_in_output(output, thread_data)
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.ls_output_max_chars if sandbox_cfg else 20000
        except Exception:
            max_chars = 20000
        return _truncate_ls_output(output, max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error listing directory: {_sanitize_error(e, runtime)}"


async def _ls_tool_async(runtime: Runtime, description: str, path: str) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(ls_tool.func, runtime, description, path)


ls_tool.coroutine = _ls_tool_async


@tool("glob", parse_docstring=True)
def glob_tool(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    include_dirs: bool = False,
    max_results: int = _DEFAULT_GLOB_MAX_RESULTS,
) -> str:
    """在指定根目录下按 glob 模式查找匹配的文件或目录。

    Args:
        description: 简短说明搜索这些路径的目的。请始终作为第一个参数提供。
        pattern: 相对根目录的 glob 模式,例如 `**/*.py`。
        path: 搜索的 **绝对** 根目录。
        include_dirs: 是否同时返回匹配的目录,默认 False。
        max_results: 返回的最大路径数,默认 200。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "glob",
            max_results,
            default=_DEFAULT_GLOB_MAX_RESULTS,
            upper_bound=_MAX_GLOB_MAX_RESULTS,
        )
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            if thread_data is None:
                raise SandboxRuntimeError("Thread data not available for local sandbox")
            path = _resolve_local_read_path(path, thread_data)
        matches, truncated = sandbox.glob(path, pattern, include_dirs=include_dirs, max_results=effective_max_results)
        if thread_data is not None:
            matches = [mask_local_paths_in_output(match, thread_data) for match in matches]
        return _format_glob_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error searching paths: {_sanitize_error(e, runtime)}"


async def _glob_tool_async(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    include_dirs: bool = False,
    max_results: int = _DEFAULT_GLOB_MAX_RESULTS,
) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(
        glob_tool.func,
        runtime,
        description,
        pattern,
        path,
        include_dirs,
        max_results,
    )


glob_tool.coroutine = _glob_tool_async


@tool("grep", parse_docstring=True)
def grep_tool(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = _DEFAULT_GREP_MAX_RESULTS,
) -> str:
    """在指定根目录下的文本文件中搜索匹配行。

    Args:
        description: 简短说明搜索文件内容的目的。请始终作为第一个参数提供。
        pattern: 待搜索的字符串或正则表达式。
        path: 搜索的 **绝对** 根目录。
        glob: 可选 glob 过滤,例如 `**/*.py`。
        literal: 是否按字面量处理 ``pattern``,默认 False。
        case_sensitive: 是否区分大小写,默认 False。
        max_results: 返回的最大匹配行数,默认 100。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "grep",
            max_results,
            default=_DEFAULT_GREP_MAX_RESULTS,
            upper_bound=_MAX_GREP_MAX_RESULTS,
        )
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            if thread_data is None:
                raise SandboxRuntimeError("Thread data not available for local sandbox")
            path = _resolve_local_read_path(path, thread_data)
        matches, truncated = sandbox.grep(
            path,
            pattern,
            glob=glob,
            literal=literal,
            case_sensitive=case_sensitive,
            max_results=effective_max_results,
        )
        if thread_data is not None:
            matches = [
                GrepMatch(
                    path=mask_local_paths_in_output(match.path, thread_data),
                    line_number=match.line_number,
                    line=match.line,
                )
                for match in matches
            ]
        return _format_grep_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error searching file contents: {_sanitize_error(e, runtime)}"


async def _grep_tool_async(
    runtime: Runtime,
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = _DEFAULT_GREP_MAX_RESULTS,
) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(
        grep_tool.func,
        runtime,
        description,
        pattern,
        path,
        glob,
        literal,
        case_sensitive,
        max_results,
    )


grep_tool.coroutine = _grep_tool_async


@tool("read_file", parse_docstring=True)
def read_file_tool(
    runtime: Runtime,
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """读取文本文件的内容。用于查看源代码、配置文件、日志或其它文本文件。

    Args:
        description: 简短说明读取此文件的目的。请始终作为第一个参数提供。
        path: 待读取文件的 **绝对** 路径。
        start_line: 可选起始行号(从 1 开始,含),与 ``end_line`` 一起用于读取指定区间。
        end_line: 可选结束行号(从 1 开始,含),与 ``start_line`` 一起用于读取指定区间。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data, read_only=True)
            if _is_skills_path(path):
                path = _resolve_skills_path(path)
            elif _is_acp_workspace_path(path):
                path = _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
            elif not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        content = sandbox.read_file(path)
        if not content:
            return "(empty)"
        if start_line is not None and end_line is not None:
            content = "\n".join(content.splitlines()[start_line - 1 : end_line])
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.read_file_output_max_chars if sandbox_cfg else 50000
        except Exception:
            max_chars = 50000
        return _truncate_read_file_output(content, max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied reading file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error reading file: {_sanitize_error(e, runtime)}"


async def _read_file_tool_async(
    runtime: Runtime,
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(read_file_tool.func, runtime, description, path, start_line, end_line)


read_file_tool.coroutine = _read_file_tool_async


@tool("write_file", parse_docstring=True)
def write_file_tool(
    runtime: Runtime,
    description: str,
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """将文本内容写入文件。默认会覆盖目标文件;将 ``append`` 设为 true 可在末尾追加而不覆盖已有内容。

    Args:
        description: 简短说明写入此文件的目的。请始终作为第一个参数提供。
        path: 待写入文件的 **绝对** 路径。请始终作为第二个参数提供。
        content: 待写入的内容。请始终作为第三个参数提供。
        append: 是否以追加方式写入(而非覆盖),默认 False。
    """
    try:
        requested_path = path
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data)
            if not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        with get_file_operation_lock(sandbox, path):
            sandbox.write_file(path, content, append)
        return "OK"
    except SandboxError as e:
        return _format_write_file_error(requested_path, e, runtime)
    except PermissionError:
        return _truncate_write_file_error_detail(
            f"Error: Permission denied writing to file: {requested_path}",
            _DEFAULT_WRITE_FILE_ERROR_MAX_CHARS,
        )
    except IsADirectoryError:
        return _truncate_write_file_error_detail(
            f"Error: Path is a directory, not a file: {requested_path}",
            _DEFAULT_WRITE_FILE_ERROR_MAX_CHARS,
        )
    except OSError as e:
        return _format_write_file_error(requested_path, e, runtime)
    except Exception as e:
        return _format_write_file_error(requested_path, e, runtime)


async def _write_file_tool_async(
    runtime: Runtime,
    description: str,
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(write_file_tool.func, runtime, description, path, content, append)


write_file_tool.coroutine = _write_file_tool_async


@tool("str_replace", parse_docstring=True)
def str_replace_tool(
    runtime: Runtime,
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """将文件中的某段子串替换为另一段子串。
    当 ``replace_all`` 为 False(默认)时,被替换的子串必须在文件中出现且 **仅出现一次**。

    Args:
        description: 简短说明替换子串的目的。请始终作为第一个参数提供。
        path: 待替换文件的 **绝对** 路径。请始终作为第二个参数提供。
        old_str: 被替换的子串。请始终作为第三个参数提供。
        new_str: 用于替换的新子串。请始终作为第四个参数提供。
        replace_all: 是否替换所有出现,默认 False 时仅替换第一个匹配。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data)
            if not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        with get_file_operation_lock(sandbox, path):
            content = sandbox.read_file(path)
            if not content:
                return "OK"
            if old_str not in content:
                return f"Error: String to replace not found in file: {requested_path}"
            if replace_all:
                content = content.replace(old_str, new_str)
            else:
                content = content.replace(old_str, new_str, 1)
            sandbox.write_file(path, content)
        return "OK"
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied accessing file: {requested_path}"
    except Exception as e:
        return f"Error: Unexpected error replacing string: {_sanitize_error(e, runtime)}"


async def _str_replace_tool_async(
    runtime: Runtime,
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """返回值。"""
    return await _run_sync_tool_after_async_sandbox_init(
        str_replace_tool.func,
        runtime,
        description,
        path,
        old_str,
        new_str,
        replace_all,
    )


str_replace_tool.coroutine = _str_replace_tool_async
