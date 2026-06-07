"""本地沙箱实现:把本机文件系统与进程视作沙箱。

:class:`LocalSandbox` 是 :class:`deerflow.sandbox.sandbox.Sandbox` 的具体实现,
通过 :class:`PathMapping` 把容器路径映射到本地目录,并在执行命令/读写文件时
自动进行双向路径转换,使 Agent 在使用 ``/mnt/...`` 等虚拟路径时无需感知底层
主机目录布局。
"""

import errno
import logging
import ntpath
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.local.list_dir import list_dir
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.search import GrepMatch, find_glob_matches, find_grep_matches

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathMapping:
    """从容器路径到本地路径的映射,带可选只读标志。

    Attributes:
        container_path: 容器内的虚拟路径。
        local_path: 对应的主机文件系统路径。
        read_only: 标记该挂载点是否只读,默认 False。
    """

    container_path: str
    local_path: str
    read_only: bool = False


class ResolvedPath(NamedTuple):
    """路径解析结果。

    Attributes:
        path: 解析后的主机路径。
        mapping: 命中的路径映射,未命中时为 None。
    """

    path: str
    mapping: PathMapping | None


class LocalSandbox(Sandbox):
    """基于本机文件系统与子进程的沙箱实现。"""

    @staticmethod
    def _shell_name(shell: str) -> str:
        """返回 shell 路径或命令对应的可执行文件名(小写)。"""
        return shell.replace("\\", "/").rsplit("/", 1)[-1].lower()

    @staticmethod
    def _is_powershell(shell: str) -> bool:
        """判断给定的 shell 是否为 PowerShell 可执行文件。"""
        return LocalSandbox._shell_name(shell) in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}

    @staticmethod
    def _is_cmd_shell(shell: str) -> bool:
        """判断给定的 shell 是否为 cmd.exe。"""
        return LocalSandbox._shell_name(shell) in {"cmd", "cmd.exe"}

    @staticmethod
    def _is_msys_shell(shell: str) -> bool:
        """判断给定的 shell 是否为 Git Bash / MSYS shell。"""
        normalized = shell.replace("\\", "/").lower()
        shell_name = LocalSandbox._shell_name(shell)
        return shell_name in {"sh.exe", "bash.exe"} and any(part in normalized for part in ("/git/", "/mingw", "/msys"))

    @staticmethod
    def _find_first_available_shell(candidates: tuple[str, ...]) -> str | None:
        """从候选列表中找到第一个可用的 shell 路径或命令。

        Args:
            candidates: 按优先级排列的候选 shell 路径或命令。

        Returns:
            第一个存在且可执行的文件绝对路径,未找到时为 None。
        """
        for shell in candidates:
            if os.path.isabs(shell):
                if os.path.isfile(shell) and os.access(shell, os.X_OK):
                    return shell
                continue

            shell_from_path = shutil.which(shell)
            if shell_from_path is not None:
                return shell_from_path

        return None

    def __init__(self, id: str, path_mappings: list[PathMapping] | None = None):
        """初始化本地沙箱。

        Args:
            id: 沙箱标识。
            path_mappings: 容器路径到本地路径的映射列表,默认空(只允许本地路径);
                skills 目录默认只读。
        """
        super().__init__(id)
        self.path_mappings = path_mappings or []
        # Track files written through write_file so read_file only
        # reverse-resolves paths in agent-authored content.
        self._agent_written_paths: set[str] = set()

    def _is_read_only_path(self, resolved_path: str) -> bool:
        """判断已解析路径是否位于某个只读挂载点下。

        当多个挂载点命中(嵌套挂载)时,优先选择最具体的挂载点(``local_path``
        是已解析路径最长前缀的那一项),与 :meth:`_resolve_path` 的语义保持一致。
        """
        resolved = str(Path(resolved_path).resolve())

        best_mapping: PathMapping | None = None
        best_prefix_len = -1

        for mapping in self.path_mappings:
            local_resolved = str(Path(mapping.local_path).resolve())
            if resolved == local_resolved or resolved.startswith(local_resolved + os.sep):
                prefix_len = len(local_resolved)
                if prefix_len > best_prefix_len:
                    best_prefix_len = prefix_len
                    best_mapping = mapping

        if best_mapping is None:
            return False

        return best_mapping.read_only

    def _find_path_mapping(self, path: str) -> tuple[PathMapping, str] | None:
        """在挂载列表中查找首个与 ``path`` 匹配的 :class:`PathMapping`。

        Args:
            path: 待匹配的容器路径。

        Returns:
            匹配项 ``(映射, 相对路径)``,未命中时为 None。
        """
        path_str = str(path)

        for mapping in sorted(self.path_mappings, key=lambda m: len(m.container_path.rstrip("/") or "/"), reverse=True):
            container_path = mapping.container_path.rstrip("/") or "/"
            if container_path == "/":
                if path_str.startswith("/"):
                    return mapping, path_str.lstrip("/")
                continue

            if path_str == container_path or path_str.startswith(container_path + "/"):
                relative = path_str[len(container_path) :].lstrip("/")
                return mapping, relative

        return None

    def _resolve_path_with_mapping(self, path: str) -> ResolvedPath:
        """使用挂载映射将容器路径解析为本地路径。

        Args:
            path: 可能是容器路径的字符串。

        Returns:
            包含解析结果与对应 :class:`PathMapping` 的 :class:`ResolvedPath`。
        """
        path_str = str(path)

        mapping_match = self._find_path_mapping(path_str)
        if mapping_match is None:
            return ResolvedPath(path_str, None)

        mapping, relative = mapping_match
        local_root = Path(mapping.local_path).resolve()
        resolved_path = (local_root / relative).resolve() if relative else local_root

        try:
            resolved_path.relative_to(local_root)
        except ValueError as exc:
            raise PermissionError(errno.EACCES, "Access denied: path escapes mounted directory", path_str) from exc

        return ResolvedPath(str(resolved_path), mapping)

    def _resolve_path(self, path: str) -> str:
        """仅返回 :meth:`_resolve_path_with_mapping` 的解析后路径字符串。"""
        return self._resolve_path_with_mapping(path).path

    def _is_resolved_path_read_only(self, resolved: ResolvedPath) -> bool:
        """判断已解析的 :class:`ResolvedPath` 是否指向只读挂载点。"""
        return bool(resolved.mapping and resolved.mapping.read_only) or self._is_read_only_path(resolved.path)

    def _reverse_resolve_path(self, path: str) -> str:
        """把本地路径反向解析为容器路径(若存在匹配挂载)。

        Args:
            path: 本地路径。

        Returns:
            命中挂载时返回对应容器路径,否则返回原始路径。
        """
        normalized_path = path.replace("\\", "/")
        path_str = str(Path(normalized_path).resolve())

        # Try each mapping (longest local path first for more specific matches)
        for mapping in sorted(self.path_mappings, key=lambda m: len(m.local_path), reverse=True):
            local_path_resolved = str(Path(mapping.local_path).resolve())
            if path_str == local_path_resolved or path_str.startswith(local_path_resolved + "/"):
                # Replace the local path prefix with container path
                relative = path_str[len(local_path_resolved) :].lstrip("/")
                resolved = f"{mapping.container_path}/{relative}" if relative else mapping.container_path
                return resolved

        # No mapping found, return original path
        return path_str

    def _reverse_resolve_paths_in_output(self, output: str) -> str:
        """把输出字符串中的本地路径反向解析为容器路径。

        Args:
            output: 包含本地路径的输出字符串。

        Returns:
            路径已被替换为对应容器路径的字符串。
        """
        import re

        # Sort mappings by local path length (longest first) for correct prefix matching
        sorted_mappings = sorted(self.path_mappings, key=lambda m: len(m.local_path), reverse=True)

        if not sorted_mappings:
            return output

        # Create pattern that matches absolute paths
        # Match paths like /Users/... or other absolute paths
        result = output
        for mapping in sorted_mappings:
            # Escape the local path for use in regex
            escaped_local = re.escape(str(Path(mapping.local_path).resolve()))
            # Match the local path followed by optional path components with either separator
            pattern = re.compile(escaped_local + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_match(match: re.Match) -> str:
                """执行赋值。
                
                        Args:
                            match: re.Match: 参数说明。
                
                        Returns:
                            str。
                """
                matched_path = match.group(0)
                return self._reverse_resolve_path(matched_path)

            result = pattern.sub(replace_match, result)

        return result

    def _resolve_paths_in_command(self, command: str) -> str:
        """把命令字符串中的容器路径替换为本地路径。

        Args:
            command: 包含容器路径的命令字符串。

        Returns:
            容器路径已被替换为本地路径的命令字符串。
        """
        import re

        # Sort mappings by length (longest first) for correct prefix matching
        sorted_mappings = sorted(self.path_mappings, key=lambda m: len(m.container_path), reverse=True)

        # Build regex pattern to match all container paths
        # Match container path followed by optional path components
        if not sorted_mappings:
            return command

        # Create pattern that matches any of the container paths.
        # The lookahead (?=/|$|...) ensures we only match at a path-segment boundary,
        # preventing /mnt/skills from matching inside /mnt/skills-extra.
        patterns = [re.escape(m.container_path) + r"(?=/|$|[\s\"';&|<>()])(?:/[^\s\"';&|<>()]*)?" for m in sorted_mappings]
        pattern = re.compile("|".join(f"({p})" for p in patterns))

        def replace_match(match: re.Match) -> str:
            """执行赋值。
            
                    Args:
                        match: re.Match: 参数说明。
            
                    Returns:
                        str。
            """
            matched_path = match.group(0)
            return self._resolve_path(matched_path)

        return pattern.sub(replace_match, command)

    def _resolve_paths_in_content(self, content: str) -> str:
        """把任意文本中的容器路径解析为本地路径。

        与 :meth:`_resolve_paths_in_command` 不同,本方法把内容视作纯文本,会
        解析所有出现的容器路径前缀;解析结果统一为正斜杠,以避免 Windows 上
        的反斜杠转义问题(例如 ``C:\\Users\\..`` 在 Python 字符串字面量中
        带来的破坏)。

        Args:
            content: 包含容器路径的文本。

        Returns:
            容器路径已替换为本地路径(正斜杠)的文本。
        """
        import re

        sorted_mappings = sorted(self.path_mappings, key=lambda m: len(m.container_path), reverse=True)
        if not sorted_mappings:
            return content

        patterns = [re.escape(m.container_path) + r"(?=/|$|[^\w./-])(?:/[^\s\"';&|<>()]*)?" for m in sorted_mappings]
        pattern = re.compile("|".join(f"({p})" for p in patterns))

        def replace_match(match: re.Match) -> str:
            """执行赋值。
            
                    Args:
                        match: re.Match: 参数说明。
            
                    Returns:
                        str。
            """
            matched_path = match.group(0)
            resolved = self._resolve_path(matched_path)
            # Normalize to forward slashes so that Windows backslash paths
            # don't create invalid escape sequences in source files.
            return resolved.replace("\\", "/")

        return pattern.sub(replace_match, content)

    @staticmethod
    def _get_shell() -> str:
        """探测当前系统上可用的 shell 可执行文件,按优先级回退。

        Returns:
            可用 shell 的绝对路径字符串。

        Raises:
            RuntimeError: 在 UNIX 上找不到任何 shell,或在 Windows 上找不到
                PowerShell/cmd.exe 时抛出。
        """
        shell = LocalSandbox._find_first_available_shell(("/bin/zsh", "/bin/bash", "/bin/sh", "sh"))
        if shell is not None:
            return shell

        if os.name == "nt":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            shell = LocalSandbox._find_first_available_shell(
                (
                    "pwsh",
                    "pwsh.exe",
                    "powershell",
                    "powershell.exe",
                    ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
                    "cmd.exe",
                )
            )
            if shell is not None:
                return shell

            raise RuntimeError("No suitable shell executable found. Tried /bin/zsh, /bin/bash, /bin/sh, `sh` on PATH, then PowerShell and cmd.exe fallbacks for Windows.")

        raise RuntimeError("No suitable shell executable found. Tried /bin/zsh, /bin/bash, /bin/sh, and `sh` on PATH.")

    def execute_command(self, command: str) -> str:
        """在本地 shell 中执行命令并返回合并后的输出文本。

        Args:
            command: 待执行的命令字符串,可能含容器路径。

        Returns:
            已把本地路径反向解析为容器路径的命令输出。
        """
        # Resolve container paths in command before execution
        resolved_command = self._resolve_paths_in_command(command)
        shell = self._get_shell()

        if os.name == "nt":
            env = None
            if self._is_powershell(shell):
                args = [shell, "-NoProfile", "-Command", resolved_command]
            elif self._is_cmd_shell(shell):
                args = [shell, "/c", resolved_command]
            else:
                args = [shell, "-c", resolved_command]
                if self._is_msys_shell(shell):
                    env = {
                        **os.environ,
                        "MSYS_NO_PATHCONV": "1",
                        "MSYS2_ARG_CONV_EXCL": "*",
                    }

            result = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
        else:
            args = [shell, "-c", resolved_command]
            result = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=600,
            )
        output = result.stdout
        if result.stderr:
            output += f"\nStd Error:\n{result.stderr}" if output else result.stderr
        if result.returncode != 0:
            output += f"\nExit Code: {result.returncode}"

        final_output = output if output else "(no output)"
        # Reverse resolve local paths back to container paths in output
        return self._reverse_resolve_paths_in_output(final_output)

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        """列出目录内容,返回容器路径形式的条目列表。"""
        resolved_path = self._resolve_path(path)
        entries = list_dir(resolved_path, max_depth)
        # Reverse resolve local paths back to container paths and preserve
        # list_dir's trailing "/" marker for directories.
        result: list[str] = []
        for entry in entries:
            is_dir = entry.endswith(("/", "\\"))
            reversed_entry = self._reverse_resolve_path(entry.rstrip("/\\")) if is_dir else self._reverse_resolve_path(entry)
            result.append(f"{reversed_entry}/" if is_dir and not reversed_entry.endswith("/") else reversed_entry)
        return result

    def read_file(self, path: str) -> str:
        """读取文件内容,必要时对 Agent 写入文件做反向路径解析。

        Args:
            path: 待读取的(容器)路径。

        Returns:
            文件文本内容。

        Raises:
            OSError: 文件不存在或无法读取,异常中 ``filename`` 字段为原始
                容器路径,内部解析后的主机路径不会暴露。
        """
        resolved_path = self._resolve_path(path)
        try:
            with open(resolved_path, encoding="utf-8") as f:
                content = f.read()
            # Only reverse-resolve paths in files that were previously written
            # by write_file (agent-authored content). User-uploaded files,
            # external tool output, and other non-agent content should not be
            # silently rewritten — see discussion on PR #1935.
            if resolved_path in self._agent_written_paths:
                content = self._reverse_resolve_paths_in_output(content)
            return content
        except OSError as e:
            # Re-raise with the original path for clearer error messages, hiding internal resolved paths
            raise type(e)(e.errno, e.strerror, path) from None

    def download_file(self, path: str) -> bytes:
        """读取沙箱内文件的二进制内容(限制大小 100MB)。

        Args:
            path: 待下载文件的容器路径,必须位于 :data:`VIRTUAL_PATH_PREFIX` 下。

        Returns:
            文件原始字节内容。

        Raises:
            PermissionError: 路径不在允许的前缀内时抛出。
            OSError: 文件不存在、不可读或超过 100MB 大小限制时抛出。
        """
        normalised = path.replace("\\", "/")
        stripped_path = normalised.lstrip("/")
        allowed_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
        if stripped_path != allowed_prefix and not stripped_path.startswith(f"{allowed_prefix}/"):
            logger.error("Refused download outside allowed directory: path=%s, allowed_prefix=%s", path, VIRTUAL_PATH_PREFIX)
            raise PermissionError(errno.EACCES, f"Access denied: path must be under '{VIRTUAL_PATH_PREFIX}'", path)

        resolved_path = self._resolve_path(path)
        max_download_size = 100 * 1024 * 1024
        try:
            file_size = os.path.getsize(resolved_path)
            if file_size > max_download_size:
                raise OSError(errno.EFBIG, f"File exceeds maximum download size of {max_download_size} bytes", path)
            # TOCTOU note: the file could grow between getsize() and read(); accepted
            # tradeoff since this is a controlled sandbox environment.
            with open(resolved_path, "rb") as f:
                return f.read()
        except OSError as e:
            # Re-raise with the original path for clearer error messages, hiding internal resolved paths
            raise type(e)(e.errno, e.strerror, path) from None

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """把文本内容写入文件(自动解析内容中的容器路径)。

        Args:
            path: 目标(容器)路径。
            content: 待写入内容。
            append: 是否追加模式,默认覆盖写入。

        Raises:
            OSError: 目标只读或写入失败时抛出,``filename`` 字段保留原始容器路径。
        """
        resolved = self._resolve_path_with_mapping(path)
        resolved_path = resolved.path
        if self._is_resolved_path_read_only(resolved):
            raise OSError(errno.EROFS, "Read-only file system", path)
        try:
            dir_path = os.path.dirname(resolved_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            # Resolve container paths in content to local paths
            # using the content-specific resolver (forward-slash safe)
            resolved_content = self._resolve_paths_in_content(content)
            mode = "a" if append else "w"
            with open(resolved_path, mode, encoding="utf-8") as f:
                f.write(resolved_content)
            # Track this path so read_file knows to reverse-resolve on read.
            # Only agent-written files get reverse-resolved; user uploads and
            # external tool output are left untouched.
            self._agent_written_paths.add(resolved_path)
        except OSError as e:
            # Re-raise with the original path for clearer error messages, hiding internal resolved paths
            raise type(e)(e.errno, e.strerror, path) from None

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """在本地沙箱中按 glob 模式查找路径。"""
        resolved_path = Path(self._resolve_path(path))
        matches, truncated = find_glob_matches(resolved_path, pattern, include_dirs=include_dirs, max_results=max_results)
        return [self._reverse_resolve_path(match) for match in matches], truncated

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        """在本地沙箱内执行文本搜索。"""
        resolved_path = Path(self._resolve_path(path))
        matches, truncated = find_grep_matches(
            resolved_path,
            pattern,
            glob_pattern=glob,
            literal=literal,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        return [
            GrepMatch(
                path=self._reverse_resolve_path(match.path),
                line_number=match.line_number,
                line=match.line,
            )
            for match in matches
        ], truncated

    def update_file(self, path: str, content: bytes) -> None:
        """以二进制内容更新文件(用于非文本内容)。"""
        resolved = self._resolve_path_with_mapping(path)
        resolved_path = resolved.path
        if self._is_resolved_path_read_only(resolved):
            raise OSError(errno.EROFS, "Read-only file system", path)
        try:
            dir_path = os.path.dirname(resolved_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(resolved_path, "wb") as f:
                f.write(content)
        except OSError as e:
            # Re-raise with the original path for clearer error messages, hiding internal resolved paths
            raise type(e)(e.errno, e.strerror, path) from None
