"""AIO 沙箱实现:通过 HTTP API 与运行中的 agent-infra/sandbox Docker 容器通信。

该沙箱通过 HTTP API 与 AIO 沙箱容器相连;使用 :class:`threading.Lock` 把
shell 命令串行化,避免并发请求污染容器内的单一持久 shell 会话(参见 #1433)。
"""

"""AIO 沙箱客户端实现,提供基于 agent-infra/sandbox 容器的 :class:`Sandbox` 适配。

该模块把 DeerFlow 的 :class:`~deerflow.sandbox.sandbox.Sandbox` 抽象映射到
agent-infra/sandbox 容器暴露的 HTTP API,并在容器维持单一持久 shell 会话
的前提下,通过 ``threading.Lock`` 序列化所有命令调用,以避免并发请求
污染会话状态(见 #1433)。
"""

import base64
import errno
import logging
import shlex
import threading
import uuid

from agent_sandbox import Sandbox as AioSandboxClient

from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.search import GrepMatch, path_matches, should_ignore_path, truncate_line

logger = logging.getLogger(__name__)

_MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

_ERROR_OBSERVATION_SIGNATURE = "'ErrorObservation' object has no attribute 'exit_code'"


class AioSandbox(Sandbox):
    """基于 agent-infra/sandbox Docker 容器的 :class:`Sandbox` 实现。

    该沙箱通过 HTTP API 连接到正在运行的 AIO 沙箱容器。所有 shell 命令都
    通过 ``threading.Lock`` 串行化执行,以避免并发请求破坏容器内唯一的
    持久 shell 会话(见 #1433)。
    """

    def __init__(self, id: str, base_url: str, home_dir: str | None = None):
        """初始化 AIO 沙箱实例。

        Args:
            id: 本沙箱实例的唯一标识。
            base_url: 沙箱 HTTP API 的 URL,例如 ``http://localhost:8080``。
            home_dir: 沙箱内部的主目录。若为 ``None``,会在首次访问
                :attr:`home_dir` 时从沙箱拉取。
        """
        super().__init__(id)
        self._base_url = base_url
        self._client = AioSandboxClient(base_url=base_url, timeout=600)
        self._home_dir = home_dir
        self._lock = threading.Lock()
        self._closed = False

    @property
    def base_url(self) -> str:
        """沙箱 HTTP API 的基址(只读)。"""
        return self._base_url

    def close(self) -> None:
        """尽力关闭本沙箱持有的宿主端 HTTP 客户端。

        ``agent_sandbox`` SDK 由 Fern 生成,没有暴露 ``close()`` 或
        ``__exit__``,因此需要沿着属性链一路下钻到真正持有 socket
        的 ``httpx.Client``::

            Sandbox._client_wrapper        -> SyncClientWrapper
                .httpx_client              -> Fern HttpClient (一个包装,不是 httpx.Client)
                    .httpx_client          -> httpx.Client     <- 真正的 socket 持有者

        关闭它可以释放池化的 socket,避免长生命周期的 provider 不断累积
        宿主端未回收的资源(#2872)。

        解析按"最具体优先"顺序回退,具备良好的向后兼容:如果未来 SDK
        在顶层增加 ``Sandbox.close()``,本方法会自动选用而无需改代码。
        整体上是幂等、线程安全且非致命的:关闭过程中发生的任何错误都
        会被记录并吞掉,不会阻塞 provider / backend 的清理流程。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            # Drop the reference under the lock for use-after-close safety: any
            # later command on this instance fails loudly instead of reusing a
            # half-closed client.
            self._client = None

        if client is None:
            return

        # Walk from the real httpx.Client up to the top-level client, picking the
        # first object that actually exposes close().
        wrapper = getattr(client, "_client_wrapper", None)
        fern_http = getattr(wrapper, "httpx_client", None)
        real_httpx = getattr(fern_http, "httpx_client", None)
        target = next(
            (c for c in (real_httpx, fern_http, client) if c is not None and hasattr(c, "close")),
            None,
        )
        if target is None:
            logger.debug("AioSandbox %s: no closable client found, nothing to release", self.id)
            return

        try:
            target.close()
        except Exception as e:
            logger.warning(f"Error closing AioSandbox client for {self.id}: {e}")

    @property
    def home_dir(self) -> str:
        """沙箱内部的主目录。

        首次访问时会调用沙箱的 ``sandbox.get_context()`` 拉取并缓存。
        """
        if self._home_dir is None:
            context = self._client.sandbox.get_context()
            self._home_dir = context.home_dir
        return self._home_dir

    # Default no_change_timeout for exec_command (seconds).  Matches the
    # client-level timeout so that long-running commands which produce no
    # output are not prematurely terminated by the sandbox's built-in 120 s
    # default.
    _DEFAULT_NO_CHANGE_TIMEOUT = 600

    def execute_command(self, command: str) -> str:
        """在沙箱内执行 shell 命令。

        所有命令都在 ``self._lock`` 保护下串行执行。AIO 沙箱容器维持着
        一个持久的 shell 会话,并发调用 ``exec_command`` 会让该会话
        返回 ``ErrorObservation`` 而不是真实输出。即便在锁保护下,如果
        检测到这种污染迹象(例如多个进程共享同一个沙箱),会自动用
        新的 session id 重试一次。

        Args:
            command: 要执行的 shell 命令。

        Returns:
            命令的标准输出;若容器未产生输出,则返回 ``"(no output)"``;
            发生异常时返回以 ``"Error: "`` 开头的错误信息字符串。
        """
        with self._lock:
            try:
                result = self._client.shell.exec_command(command=command, no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                output = result.data.output if result.data else ""

                if output and _ERROR_OBSERVATION_SIGNATURE in output:
                    logger.warning("ErrorObservation detected in sandbox output, retrying with a fresh session")
                    fresh_id = str(uuid.uuid4())
                    result = self._client.shell.exec_command(command=command, id=fresh_id, no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                    output = result.data.output if result.data else ""

                return output if output else "(no output)"
            except Exception as e:
                logger.error(f"Failed to execute command in sandbox: {e}")
                return f"Error: {e}"

    def read_file(self, path: str) -> str:
        """读取沙箱中某个文件的文本内容。

        Args:
            path: 待读取文件的绝对路径。

        Returns:
            文件内容字符串。读取失败时返回以 ``"Error: "`` 开头的错误信息。
        """
        try:
            result = self._client.file.read_file(file=path)
            return result.data.content if result.data else ""
        except Exception as e:
            logger.error(f"Failed to read file in sandbox: {e}")
            return f"Error: {e}"

    def download_file(self, path: str) -> bytes:
        """以字节流形式从沙箱下载文件。

        为了避免任意路径下载带来的越权风险,本方法在转发到容器 API 之前
        会先在本地做路径白名单校验,要求路径必须位于
        :data:`~deerflow.config.paths.VIRTUAL_PATH_PREFIX` 之下,且不
        包含 ``..`` 路径穿越片段。

        Args:
            path: 待下载文件的绝对路径(沙箱内视角)。

        Returns:
            文件的原始字节内容。

        Raises:
            PermissionError: 路径包含 ``..`` 片段或不在允许的前缀下。
            OSError: 文件下载失败,或文件大小超过 :data:`_MAX_DOWNLOAD_SIZE`。
        """
        # Reject path traversal before sending to the container API.
        # LocalSandbox gets this implicitly via _resolve_path;
        # here the path is forwarded verbatim so we must check explicitly.
        normalised = path.replace("\\", "/")
        for segment in normalised.split("/"):
            if segment == "..":
                logger.error(f"Refused download due to path traversal: {path}")
                raise PermissionError(f"Access denied: path traversal detected in '{path}'")

        stripped_path = normalised.lstrip("/")
        allowed_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
        if stripped_path != allowed_prefix and not stripped_path.startswith(f"{allowed_prefix}/"):
            logger.error("Refused download outside allowed directory: path=%s, allowed_prefix=%s", path, VIRTUAL_PATH_PREFIX)
            raise PermissionError(f"Access denied: path must be under '{VIRTUAL_PATH_PREFIX}': '{path}'")

        with self._lock:
            try:
                chunks: list[bytes] = []
                total = 0
                for chunk in self._client.file.download_file(path=path):
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_SIZE:
                        raise OSError(
                            errno.EFBIG,
                            f"File exceeds maximum download size of {_MAX_DOWNLOAD_SIZE} bytes",
                            path,
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
            except OSError:
                raise
            except Exception as e:
                logger.error(f"Failed to download file in sandbox: {e}")
                raise OSError(f"Failed to download file '{path}' from sandbox: {e}") from e

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """在沙箱内递归列出目录内容。

        通过 ``find -maxdepth`` 在容器内执行,默认深度 2,并在结果中
        截断到前 500 行。

        Args:
            path: 待列出目录的绝对路径。
            max_depth: 最大递归深度,默认 2。

        Returns:
            包含所有匹配项绝对路径的字符串列表;失败时返回空列表。
        """
        with self._lock:
            try:
                result = self._client.shell.exec_command(command=f"find {shlex.quote(path)} -maxdepth {max_depth} -type f -o -type d 2>/dev/null | head -500", no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                output = result.data.output if result.data else ""
                if output:
                    return [line.strip() for line in output.strip().split("\n") if line.strip()]
                return []
            except Exception as e:
                logger.error(f"Failed to list directory in sandbox: {e}")
                return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """向沙箱内的文件写入文本内容。

        当 ``append=True`` 时,会先读出已有内容并把新内容追加在末尾。
        注意:在追加场景下若原文件读取失败,会以 ``Error: ...`` 开头,
        此时会跳过追加、直接覆盖写入(避免把错误信息混入文件)。

        Args:
            path: 待写入文件的绝对路径。
            content: 要写入的文本内容。
            append: 是否以追加模式写入。

        Raises:
            Exception: 沙箱写入失败时,透传底层异常。
        """
        with self._lock:
            try:
                if append:
                    existing = self.read_file(path)
                    if not existing.startswith("Error:"):
                        content = existing + content
                self._client.file.write_file(file=path, content=content)
            except Exception as e:
                logger.error(f"Failed to write file in sandbox: {e}")
                raise

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """在沙箱内按 glob 模式匹配文件(可选包含目录)。

        当 ``include_dirs=False`` 时直接走容器提供的 ``find_files`` 路径;
        当 ``include_dirs=True`` 时,改用 ``list_path`` 递归列出后由本地
        ``path_matches`` 过滤,以便把目录也纳入匹配。

        Args:
            path: 匹配根目录的绝对路径。
            pattern: glob 模式字符串(例如 ``*.py``)。
            include_dirs: 是否把目录也视为匹配目标。
            max_results: 返回结果的最大数量。

        Returns:
            长度为 2 的元组 ``(matches, truncated)``:
            ``matches`` 是匹配到的绝对路径列表(至多 ``max_results`` 条);
            ``truncated`` 表示是否因达到上限而被截断。
        """
        if not include_dirs:
            result = self._client.file.find_files(path=path, glob=pattern)
            files = result.data.files if result.data and result.data.files else []
            filtered = [file_path for file_path in files if not should_ignore_path(file_path)]
            truncated = len(filtered) > max_results
            return filtered[:max_results], truncated

        result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
        entries = result.data.files if result.data and result.data.files else []
        matches: list[str] = []
        root_path = path.rstrip("/") or "/"
        root_prefix = root_path if root_path == "/" else f"{root_path}/"
        for entry in entries:
            if entry.path != root_path and not entry.path.startswith(root_prefix):
                continue
            if should_ignore_path(entry.path):
                continue
            rel_path = entry.path[len(root_path) :].lstrip("/")
            if path_matches(pattern, rel_path):
                matches.append(entry.path)
                if len(matches) >= max_results:
                    return matches, True
        return matches, False

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
        """在沙箱内对文件执行正则匹配,返回匹配行列表。

        实现上先把候选文件集合收缩为 ``glob`` 命中或 ``list_path`` 返回
        的路径列表,然后对每个文件调用容器 ``search_in_file`` 拿到匹配
        行号和文本,并在本地用 :func:`truncate_line` 做截断。
        ``literal=True`` 时会在发送前对 ``pattern`` 做 ``re.escape``;
        同时会在本地用 :func:`re.compile` 预校验,使无效正则抛出
        ``re.error``(由 ``grep_tool`` 的 ``except re.error`` 接住),而不是
        退化为通用的远端 API 错误。

        Args:
            path: 检索根目录的绝对路径。
            pattern: 待匹配的正则表达式;``literal=True`` 时按字面量处理。
            glob: 可选的候选文件 glob 过滤。
            literal: 是否按字面量匹配(自动 ``re.escape``)。
            case_sensitive: 是否区分大小写,默认 ``False``(不区分)。
            max_results: 返回的最大匹配数,默认 100。

        Returns:
            ``(matches, truncated)`` 元组:``matches`` 是 :class:`GrepMatch`
            列表,``truncated`` 表示是否因达到上限而被截断。
        """
        import re as _re

        regex_source = _re.escape(pattern) if literal else pattern
        # Validate the pattern locally so an invalid regex raises re.error
        # (caught by grep_tool's except re.error handler) rather than a
        # generic remote API error.
        _re.compile(regex_source, 0 if case_sensitive else _re.IGNORECASE)
        regex = regex_source if case_sensitive else f"(?i){regex_source}"

        if glob is not None:
            find_result = self._client.file.find_files(path=path, glob=glob)
            candidate_paths = find_result.data.files if find_result.data and find_result.data.files else []
        else:
            list_result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
            entries = list_result.data.files if list_result.data and list_result.data.files else []
            candidate_paths = [entry.path for entry in entries if not entry.is_directory]

        matches: list[GrepMatch] = []
        truncated = False

        for file_path in candidate_paths:
            if should_ignore_path(file_path):
                continue

            search_result = self._client.file.search_in_file(file=file_path, regex=regex)
            data = search_result.data
            if data is None:
                continue

            line_numbers = data.line_numbers or []
            matched_lines = data.matches or []
            for line_number, line in zip(line_numbers, matched_lines):
                matches.append(
                    GrepMatch(
                        path=file_path,
                        line_number=line_number if isinstance(line_number, int) else 0,
                        line=truncate_line(line),
                    )
                )
                if len(matches) >= max_results:
                    truncated = True
                    return matches, truncated

        return matches, truncated

    def update_file(self, path: str, content: bytes) -> None:
        """以二进制内容更新沙箱中的文件。

        内部把 ``content`` 用 base64 编码后调用 ``write_file(encoding="base64")``,
        这样可以无损地写入非 UTF-8 文本或任意二进制数据。

        Args:
            path: 待更新文件的绝对路径。
            content: 二进制内容。

        Raises:
            Exception: 沙箱写入失败时,透传底层异常。
        """
        with self._lock:
            try:
                base64_content = base64.b64encode(content).decode("utf-8")
                self._client.file.write_file(file=path, content=base64_content, encoding="base64")
            except Exception as e:
                logger.error(f"Failed to update file in sandbox: {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to download file in sandbox: {e}")
                raise OSError(f"Failed to download file '{path}' from sandbox: {e}") from e

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """列出沙箱内指定目录的内容(最多 500 行,深度由 ``max_depth`` 控制)。

        Args:
            path: 待列举目录的绝对路径。
            max_depth: 最大递归深度,默认 2。

        Returns:
            目录内文件/目录路径列表(每行一条),过滤掉空行。
        """
        with self._lock:
            try:
                result = self._client.shell.exec_command(command=f"find {shlex.quote(path)} -maxdepth {max_depth} -type f -o -type d 2>/dev/null | head -500", no_change_timeout=self._DEFAULT_NO_CHANGE_TIMEOUT)
                output = result.data.output if result.data else ""
                if output:
                    return [line.strip() for line in output.strip().split("\n") if line.strip()]
                return []
            except Exception as e:
                logger.error(f"Failed to list directory in sandbox: {e}")
                return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """把文本内容写入沙箱内文件。

        Args:
            path: 目标绝对路径。
            content: 待写入的文本内容。
            append: 是否以追加模式写入;为 True 时会在已有内容后拼接。

        Raises:
            Exception: 透传沙箱 SDK 抛出的任何错误。
        """
        with self._lock:
            try:
                if append:
                    existing = self.read_file(path)
                    if not existing.startswith("Error:"):
                        content = existing + content
                self._client.file.write_file(file=path, content=content)
            except Exception as e:
                logger.error(f"Failed to write file in sandbox: {e}")
                raise

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """按 glob 模式在沙箱内搜索路径。"""
        if not include_dirs:
            result = self._client.file.find_files(path=path, glob=pattern)
            files = result.data.files if result.data and result.data.files else []
            filtered = [file_path for file_path in files if not should_ignore_path(file_path)]
            truncated = len(filtered) > max_results
            return filtered[:max_results], truncated

        result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
        entries = result.data.files if result.data and result.data.files else []
        matches: list[str] = []
        root_path = path.rstrip("/") or "/"
        root_prefix = root_path if root_path == "/" else f"{root_path}/"
        for entry in entries:
            if entry.path != root_path and not entry.path.startswith(root_prefix):
                continue
            if should_ignore_path(entry.path):
                continue
            rel_path = entry.path[len(root_path) :].lstrip("/")
            if path_matches(pattern, rel_path):
                matches.append(entry.path)
                if len(matches) >= max_results:
                    return matches, True
        return matches, False

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
        """在沙箱内的文件中搜索匹配行。"""
        import re as _re

        regex_source = _re.escape(pattern) if literal else pattern
        # Validate the pattern locally so an invalid regex raises re.error
        # (caught by grep_tool's except re.error handler) rather than a
        # generic remote API error.
        _re.compile(regex_source, 0 if case_sensitive else _re.IGNORECASE)
        regex = regex_source if case_sensitive else f"(?i){regex_source}"

        if glob is not None:
            find_result = self._client.file.find_files(path=path, glob=glob)
            candidate_paths = find_result.data.files if find_result.data and find_result.data.files else []
        else:
            list_result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
            entries = list_result.data.files if list_result.data and list_result.data.files else []
            candidate_paths = [entry.path for entry in entries if not entry.is_directory]

        matches: list[GrepMatch] = []
        truncated = False

        for file_path in candidate_paths:
            if should_ignore_path(file_path):
                continue

            search_result = self._client.file.search_in_file(file=file_path, regex=regex)
            data = search_result.data
            if data is None:
                continue

            line_numbers = data.line_numbers or []
            matched_lines = data.matches or []
            for line_number, line in zip(line_numbers, matched_lines):
                matches.append(
                    GrepMatch(
                        path=file_path,
                        line_number=line_number if isinstance(line_number, int) else 0,
                        line=truncate_line(line),
                    )
                )
                if len(matches) >= max_results:
                    truncated = True
                    return matches, truncated

        return matches, truncated

    def update_file(self, path: str, content: bytes) -> None:
        """以二进制内容更新沙箱内文件。

        Args:
            path: 目标文件的绝对路径。
            content: 待写入的二进制内容。

        Raises:
            Exception: 透传沙箱 SDK 抛出的任何错误。
        """
        with self._lock:
            try:
                base64_content = base64.b64encode(content).decode("utf-8")
                self._client.file.write_file(file=path, content=base64_content, encoding="base64")
            except Exception as e:
                logger.error(f"Failed to update file in sandbox: {e}")
                raise
