"""沙箱供应的本地容器后端。

本模块基于本机的 Docker 或 Apple Container 管理沙箱容器,负责容器
生命周期(启动/停止)、端口分配,以及跨进程容器发现(以确定性
容器名为线索)。
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from datetime import datetime

from deerflow.utils.network import get_free_port, release_port

from .backend import SandboxBackend, wait_for_sandbox_ready
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def _parse_docker_timestamp(raw: str) -> float:
    """把 Docker 的 ISO 8601 时间戳解析为 Unix epoch 浮点数。

    Docker 返回的时间戳带纳秒精度和 ``Z`` 后缀(例如
    ``2026-04-08T01:22:50.123456789Z``)。Python 的 ``fromisoformat``
    最多只接受微秒精度,且在 3.11 之前不接受 ``Z``,因此在解析前
    需要先规范化字符串。空字符串或解析失败时返回 ``0.0``,方便
    调用方用 ``0.0`` 表示"年龄未知"。

    Args:
        raw: 原始 Docker 时间戳字符串。

    Returns:
        float: 解析出的 epoch 秒数;失败时为 ``0.0``。
    """
    if not raw:
        return 0.0
    try:
        s = raw.strip()
        if "." in s:
            dot_pos = s.index(".")
            tz_start = dot_pos + 1
            while tz_start < len(s) and s[tz_start].isdigit():
                tz_start += 1
            frac = s[dot_pos + 1 : tz_start][:6]  # truncate to microseconds
            tz_suffix = s[tz_start:]
            s = s[: dot_pos + 1] + frac + tz_suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not parse docker timestamp {raw!r}: {e}")
        return 0.0


def _extract_host_port(inspect_entry: dict, container_port: int) -> int | None:
    """从 ``docker inspect`` 结果中提取 ``container_port/tcp`` 对应的主机端口。

    Args:
        inspect_entry: 单个容器的 inspect JSON 字典。
        container_port: 容器内端口。

    Returns:
        匹配的主机端口;若该容器没有该端口的映射则返回 ``None``。
    """
    try:
        ports = (inspect_entry.get("NetworkSettings") or {}).get("Ports") or {}
        bindings = ports.get(f"{container_port}/tcp") or []
        if bindings:
            host_port = bindings[0].get("HostPort")
            if host_port:
                return int(host_port)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _format_container_mount(runtime: str, host_path: str, container_path: str, read_only: bool) -> list[str]:
    """为所选容器运行时格式化 bind mount 参数。

    Docker 的 ``-v host:container`` 语法在 Windows 盘符路径(例如
    ``D:/...``)上存在歧义,因为 ``:`` 既是盘符分隔符又是卷分隔符。
    因此在 Docker 上改用 ``--mount type=bind,...`` 以避免该歧义;
    Apple Container 仍使用 ``-v``。

    Args:
        runtime: ``"docker"`` 或 ``"container"``。
        host_path: 宿主机路径。
        container_path: 容器内路径。
        read_only: 是否以只读方式挂载。

    Returns:
        拼接到 ``docker run`` / ``container run`` 命令行中的参数列表。
    """
    if runtime == "docker":
        mount_spec = f"type=bind,src={host_path},dst={container_path}"
        if read_only:
            mount_spec += ",readonly"
        return ["--mount", mount_spec]

    mount_spec = f"{host_path}:{container_path}"
    if read_only:
        mount_spec += ":ro"
    return ["-v", mount_spec]


def _redact_container_command_for_log(cmd: list[str]) -> list[str]:
    """返回脱敏后的容器命令(环境变量值被替换为 ``<redacted>``)。"""
    redacted: list[str] = []
    redact_next_env = False

    for arg in cmd:
        if redact_next_env:
            if "=" in arg:
                key = arg.split("=", 1)[0]
                redacted.append(f"{key}=<redacted>" if key else "<redacted>")
            else:
                redacted.append(arg)
            redact_next_env = False
            continue

        if arg in {"-e", "--env"}:
            redacted.append(arg)
            redact_next_env = True
            continue

        if arg.startswith("--env="):
            value = arg.removeprefix("--env=")
            if "=" in value:
                key = value.split("=", 1)[0]
                redacted.append(f"--env={key}=<redacted>" if key else "--env=<redacted>")
            else:
                redacted.append(arg)
            continue

        redacted.append(arg)

    return redacted


def _format_container_command_for_log(cmd: list[str]) -> str:
    """把容器命令格式化为适合日志打印的字符串(Windows 使用 cmd 风格)。"""
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def _normalize_sandbox_host(host: str) -> str:
    """归一化沙箱主机名(去空白 + 小写)。"""
    return host.strip().lower()


def _is_ipv6_loopback_sandbox_host(host: str) -> bool:
    """判断主机名是否为 IPv6 回环。"""
    return _normalize_sandbox_host(host) in {"::1", "[::1]"}


def _is_loopback_sandbox_host(host: str) -> bool:
    """判断主机名是否为回环地址(空/localhost/IPv4/IPv6 回环)。"""
    return _normalize_sandbox_host(host) in {"", "localhost", "127.0.0.1", "::1", "[::1]"}


def _resolve_docker_bind_host(sandbox_host: str | None = None, bind_host: str | None = None) -> str:
    """为传统 Docker ``-p`` 沙箱发布选择宿主机绑定网卡。

    裸机/本地运行的沙箱通过 localhost 通信,不应把沙箱 HTTP API 暴露
    在所有网卡上。Docker-outside-of-Docker 部署中,其他容器通常通过
    ``host.docker.internal`` 访问,此时保留其传统的广泛绑定(除非
    运维通过 ``DEER_FLOW_SANDBOX_BIND_HOST`` 显式收窄);如果运维选择
    了 IPv6 回环主机名,则把 Docker 也绑定到 IPv6 回环,以保证对外
    公告的沙箱 URL 与发布的 socket 使用同一地址族。

    Args:
        sandbox_host: 对外公告的沙箱主机名,默认读取 ``DEER_FLOW_SANDBOX_HOST``。
        bind_host: 显式覆盖的绑定网卡;优先级最高。

    Returns:
        最终用于 Docker ``-p`` 绑定的网卡字符串。
    """
    explicit_bind = bind_host if bind_host is not None else os.environ.get("DEER_FLOW_SANDBOX_BIND_HOST")
    if explicit_bind is not None:
        explicit_bind = explicit_bind.strip()
        if explicit_bind:
            logger.debug("Docker sandbox bind: %s (explicit bind host override)", explicit_bind)
            return explicit_bind

    host = sandbox_host if sandbox_host is not None else os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
    if _is_ipv6_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: [::1] (IPv6 loopback sandbox host)")
        return "[::1]"
    if _is_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: 127.0.0.1 (loopback default)")
        return "127.0.0.1"

    logger.debug("Docker sandbox bind: 0.0.0.0 (non-loopback sandbox host compatibility)")
    return "0.0.0.0"


class LocalContainerBackend(SandboxBackend):
    """使用本机 Docker 或 Apple Container 管理沙箱容器的 :class:`SandboxBackend` 实现。

    在 macOS 上,自动优先使用 Apple Container(若可用),否则回退到 Docker;
    在其他平台上统一使用 Docker。

    特性:
    - 确定性容器名,支持跨进程发现
    - 通过线程安全工具函数分配端口
    - 容器生命周期管理(以 ``--rm`` 启动,停止即删除)
    - 支持卷挂载和容器环境变量
    """

    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        config_mounts: list,
        environment: dict[str, str],
    ):
        """初始化本地容器后端。

        Args:
            image: 使用的容器镜像。
            base_port: 端口搜索的起始基址。
            container_prefix: 容器名前缀,例如 ``"deer-flow-sandbox"``。
            config_mounts: 来自配置的卷挂载配置(``VolumeMountConfig`` 列表)。
            environment: 注入到容器的环境变量。
        """
        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._config_mounts = config_mounts
        self._environment = environment
        self._runtime = self._detect_runtime()

    @property
    def runtime(self) -> str:
        """检测到的容器运行时(``"docker"`` 或 ``"container"``)。"""
        return self._runtime

    def _detect_runtime(self) -> str:
        """检测要使用的容器运行时。

        macOS 上优先使用 Apple Container,不可用时回退到 Docker;
        其他平台统一使用 Docker。

        Returns:
            ``"container"`` 表示 Apple Container,``"docker"`` 表示 Docker。
        """
        import platform

        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["container", "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                logger.info(f"Detected Apple Container: {result.stdout.strip()}")
                return "container"
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.info("Apple Container not available, falling back to Docker")

        return "docker"

    # ── SandboxBackend interface ──────────────────────────────────────────

    def create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """启动一个新容器并返回其连接信息。

        内部包含一段端口重试循环:如果 Docker 拒绝当前端口(例如进程
        重启后旧容器仍持有绑定),就跳过该端口再试下一个。
        :func:`get_free_port` 已经用 socket-bind 模拟了 Docker 的
        ``0.0.0.0`` 绑定,但 Docker 释放端口有一定异步性,这里再做
        一次反应式回退以保证总能成功。

        Args:
            thread_id: 沙箱所属的线程 ID;按线程组织沙箱的后端可使用。
            sandbox_id: 确定性沙箱标识(用于容器名)。
            extra_mounts: 额外卷挂载,``(host_path, container_path, read_only)`` 三元组列表。

        Returns:
            SandboxInfo: 包含容器详情的元数据。

        Raises:
            RuntimeError: 容器启动失败(端口耗尽或 Docker 错误)。
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        # Retry loop: if Docker rejects the port (e.g. a stale container still
        # holds the binding after a process restart), skip that port and try the
        # next one.  The socket-bind check in get_free_port mirrors Docker's
        # 0.0.0.0 bind, but Docker's port-release can be slightly asynchronous,
        # so a reactive fallback here ensures we always make progress.
        _next_start = self._base_port
        container_id: str | None = None
        port: int = 0
        for _attempt in range(10):
            port = get_free_port(start_port=_next_start)
            try:
                container_id = self._start_container(container_name, port, extra_mounts)
                break
            except RuntimeError as exc:
                release_port(port)
                err = str(exc)
                err_lower = err.lower()
                # Port already bound: skip this port and retry with the next one.
                if "port is already allocated" in err or "address already in use" in err_lower:
                    logger.warning(f"Port {port} rejected by Docker (already allocated), retrying with next port")
                    _next_start = port + 1
                    continue
                # Container-name conflict: another process may have already started
                # the deterministic sandbox container for this sandbox_id. Try to
                # discover and adopt the existing container instead of failing.
                if "is already in use by container" in err_lower or "conflict. the container name" in err_lower:
                    logger.warning(f"Container name {container_name} already in use, attempting to discover existing sandbox instance")
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
                raise
        else:
            raise RuntimeError("Could not start sandbox container: all candidate ports are already allocated by Docker")

        # When running inside Docker (DooD), sandbox containers are reachable via
        # host.docker.internal rather than localhost (they run on the host daemon).
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{sandbox_host}:{port}",
            container_name=container_name,
            container_id=container_id,
        )

    def destroy(self, info: SandboxInfo) -> None:
        """停止容器并释放其占用的端口。"""
        # Prefer container_id, fall back to container_name (both accepted by docker stop).
        # This ensures containers discovered via list_running() (which only has the name)
        # can also be stopped.
        stop_target = info.container_id or info.container_name
        if stop_target:
            self._stop_container(stop_target)
        # Extract port from sandbox_url for release
        try:
            from urllib.parse import urlparse

            port = urlparse(info.sandbox_url).port
            if port:
                release_port(port)
        except Exception:
            pass

    def is_alive(self, info: SandboxInfo) -> bool:
        """轻量级检查容器是否仍在运行(不发起 HTTP 请求)。"""
        if info.container_name:
            return self._is_container_running(info.container_name)
        return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """按确定性容器名发现已存在的容器。

        检查同名容器是否在运行,获取其映射端口,并验证其健康端点
        是否能正常响应。

        Args:
            sandbox_id: 确定性沙箱 ID(决定容器名)。

        Returns:
            找到且健康时返回 :class:`SandboxInfo`,否则返回 ``None``。
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        if not self._is_container_running(container_name):
            return None

        port = self._get_container_port(container_name)
        if port is None:
            return None

        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            return None

        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
        )

    def list_running(self) -> list[SandboxInfo]:
        """枚举所有匹配配置前缀的运行中容器。

        通过一次 ``docker ps`` 拿到容器名列表,再通过一次批量
        ``docker inspect`` 拿到创建时间和端口映射。子进程调用
        次数为 2(相比每个容器调一次的朴素做法,降低到 2N+1 → 2)。

        注意:Docker 的 ``--filter name=`` 是 *子串* 匹配,因此需要
        在结果上做一次 ``startswith`` 二次过滤,只保留精确匹配
        前缀的容器。

        没有任何端口映射的容器也会被纳入(``sandbox_url`` 留空),
        以便启动协调时无论端口状态如何都能接管孤儿容器。

        Returns:
            匹配前缀的所有运行中沙箱的 :class:`SandboxInfo` 列表。
        """
        # Step 1: enumerate container names via docker ps
        try:
            result = subprocess.run(
                [
                    self._runtime,
                    "ps",
                    "--filter",
                    f"name={self._container_prefix}-",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(
                    "Failed to list running containers with %s ps (returncode=%s, stderr=%s)",
                    self._runtime,
                    result.returncode,
                    stderr or "<empty>",
                )
                return []
            if not result.stdout.strip():
                return []
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to list running containers: {e}")
            return []

        # Filter to names matching our exact prefix (docker filter is substring-based)
        container_names = [name.strip() for name in result.stdout.strip().splitlines() if name.strip().startswith(self._container_prefix + "-")]
        if not container_names:
            return []

        # Step 2: batched docker inspect — single subprocess call for all containers
        inspections = self._batch_inspect(container_names)

        infos: list[SandboxInfo] = []
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        for container_name in container_names:
            data = inspections.get(container_name)
            if data is None:
                # Container disappeared between ps and inspect, or inspect failed
                continue
            created_at, host_port = data
            sandbox_id = container_name[len(self._container_prefix) + 1 :]
            sandbox_url = f"http://{sandbox_host}:{host_port}" if host_port else ""

            infos.append(
                SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=sandbox_url,
                    container_name=container_name,
                    created_at=created_at,
                )
            )

        logger.info(f"Found {len(infos)} running sandbox container(s)")
        return infos

    def _batch_inspect(self, container_names: list[str]) -> dict[str, tuple[float, int | None]]:
        """一次子进程调用批量 inspect 多个容器。

        缺失的容器或解析失败会在结果中静默丢弃,不会抛错。

        Args:
            container_names: 待 inspect 的容器名列表。

        Returns:
            ``{container_name: (created_at, host_port)}`` 字典;其中
            ``created_at`` 为 epoch 秒,``host_port`` 可能为 ``None``。
        """
        if not container_names:
            return {}
        try:
            result = subprocess.run(
                [self._runtime, "inspect", *container_names],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to batch-inspect containers: {e}")
            return {}

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(
                "Failed to batch-inspect containers with %s inspect (returncode=%s, stderr=%s)",
                self._runtime,
                result.returncode,
                stderr or "<empty>",
            )
            return {}

        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse docker inspect output as JSON: {e}")
            return {}

        out: dict[str, tuple[float, int | None]] = {}
        for entry in payload:
            # ``Name`` is prefixed with ``/`` in the docker inspect response
            name = (entry.get("Name") or "").lstrip("/")
            if not name:
                continue
            created_at = _parse_docker_timestamp(entry.get("Created", ""))
            host_port = _extract_host_port(entry, 8080)
            out[name] = (created_at, host_port)
        return out

    # ── Container operations ─────────────────────────────────────────────

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        """启动一个新容器。

        启动参数按顺序拼装:运行时(``docker`` 或 ``container``) →
        端口映射 → 安全选项 → 环境变量 → 卷挂载 → 镜像名。
        命令中所有环境变量值都会在写入日志前脱敏。

        Args:
            container_name: 容器名。
            port: 宿主机端口,映射到容器内 8080。
            extra_mounts: 额外卷挂载(``(host_path, container_path, read_only)`` 元组列表)。

        Returns:
            启动成功时返回容器的 ID(由 ``docker run`` 输出)。

        Raises:
            RuntimeError: 容器启动失败(底层 ``CalledProcessError`` 包装)。
        """
        cmd = [self._runtime, "run"]

        # Docker-specific security options
        if self._runtime == "docker":
            cmd.extend(["--security-opt", "seccomp=unconfined"])

        if self._runtime == "docker":
            port_mapping = f"{_resolve_docker_bind_host()}:{port}:8080"
        else:
            port_mapping = f"{port}:8080"

        cmd.extend(
            [
                "--rm",
                "-d",
                "-p",
                port_mapping,
                "--name",
                container_name,
            ]
        )

        # Environment variables
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Config-level volume mounts
        for mount in self._config_mounts:
            cmd.extend(
                _format_container_mount(
                    self._runtime,
                    mount.host_path,
                    mount.container_path,
                    mount.read_only,
                )
            )

        # Extra mounts (thread-specific, skills, etc.)
        if extra_mounts:
            for host_path, container_path, read_only in extra_mounts:
                cmd.extend(
                    _format_container_mount(
                        self._runtime,
                        host_path,
                        container_path,
                        read_only,
                    )
                )

        cmd.append(self._image)

        log_cmd = _format_container_command_for_log(_redact_container_command_for_log(cmd))
        logger.info(f"Starting container using {self._runtime}: {log_cmd}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            logger.info(f"Started container {container_name} (ID: {container_id}) using {self._runtime}")
            return container_id
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start container using {self._runtime}: {e.stderr}")
            raise RuntimeError(f"Failed to start sandbox container: {e.stderr}")

    def _stop_container(self, container_id: str) -> None:
        """停止容器(``--rm`` 保证容器被自动删除)。"""
        try:
            subprocess.run(
                [self._runtime, "stop", container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop container {container_id}: {e.stderr}")

    def _is_container_running(self, container_name: str) -> bool:
        """检查指定名称的容器当前是否在运行。

        该能力是跨进程容器发现的关键:任何进程都可以通过确定性
        容器名,查到由其他进程启动的容器。
        """
        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _get_container_port(self, container_name: str) -> int | None:
        """获取运行中容器的宿主机端口。

        Args:
            container_name: 待 inspect 的容器名。

        Returns:
            映射到容器内 8080 端口的宿主机端口;无映射时返回 ``None``。
        """
        try:
            result = subprocess.run(
                [self._runtime, "port", container_name, "8080"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "0.0.0.0:PORT" or ":::PORT"
                port_str = result.stdout.strip().split(":")[-1]
                return int(port_str)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            pass
        return None


def _format_container_mount(runtime: str, host_path: str, container_path: str, read_only: bool) -> list[str]:
    """为所选运行时格式化 bind-mount 参数。

    Docker 的 ``-v host:container`` 语法在 Windows 盘符路径(如 ``D:/...``)
    上有歧义,因为 ``:`` 同时是盘符分隔符与卷分隔符。Docker 改用
    ``--mount type=bind,...`` 来避免这种解析歧义;Apple Container 继续
    使用 ``-v``。

    Args:
        runtime: 运行时名,``"docker"`` 或 ``"apple"``。
        host_path: 主机端源路径。
        container_path: 容器内目标路径。
        read_only: 是否只读挂载。

    Returns:
        对应运行时的命令行参数列表。
    """
    if runtime == "docker":
        mount_spec = f"type=bind,src={host_path},dst={container_path}"
        if read_only:
            mount_spec += ",readonly"
        return ["--mount", mount_spec]

    mount_spec = f"{host_path}:{container_path}"
    if read_only:
        mount_spec += ":ro"
    return ["-v", mount_spec]


def _redact_container_command_for_log(cmd: list[str]) -> list[str]:
    """返回一份环境变量值被脱敏后的 Docker/Container 命令(供日志使用)。"""
    redacted: list[str] = []
    redact_next_env = False

    for arg in cmd:
        if redact_next_env:
            if "=" in arg:
                key = arg.split("=", 1)[0]
                redacted.append(f"{key}=<redacted>" if key else "<redacted>")
            else:
                redacted.append(arg)
            redact_next_env = False
            continue

        if arg in {"-e", "--env"}:
            redacted.append(arg)
            redact_next_env = True
            continue

        if arg.startswith("--env="):
            value = arg.removeprefix("--env=")
            if "=" in value:
                key = value.split("=", 1)[0]
                redacted.append(f"--env={key}=<redacted>" if key else "--env=<redacted>")
            else:
                redacted.append(arg)
            continue

        redacted.append(arg)

    return redacted


def _format_container_command_for_log(cmd: list[str]) -> str:
    """内部辅助方法。"""
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def _normalize_sandbox_host(host: str) -> str:
    """返回值。"""
    return host.strip().lower()


def _is_ipv6_loopback_sandbox_host(host: str) -> bool:
    """返回值。"""
    return _normalize_sandbox_host(host) in {"::1", "[::1]"}


def _is_loopback_sandbox_host(host: str) -> bool:
    """返回值。"""
    return _normalize_sandbox_host(host) in {"", "localhost", "127.0.0.1", "::1", "[::1]"}


def _resolve_docker_bind_host(sandbox_host: str | None = None, bind_host: str | None = None) -> str:
    """为传统 Docker ``-p`` 沙箱发布选择主机接口。
    
            裸机/本地运行时通过 localhost 与沙箱通信，不应在每个主机接口上暴露沙箱 HTTP API。
            Docker-outside-of-Docker 部署通常会从另一个容器使用 ``host.docker.internal``；
            除非运维通过 ``DEER_FLOW_SANDBOX_BIND_HOST`` 选择更窄的绑定，否则保留其传统的
            宽绑定。当运维选择 IPv6 loopback 沙箱主机时，也将 Docker 绑定到 IPv6 loopback，
            以保证对外宣传的沙箱 URL 与发布的 socket 使用同一地址族。
    """

    explicit_bind = bind_host if bind_host is not None else os.environ.get("DEER_FLOW_SANDBOX_BIND_HOST")
    if explicit_bind is not None:
        explicit_bind = explicit_bind.strip()
        if explicit_bind:
            logger.debug("Docker sandbox bind: %s (explicit bind host override)", explicit_bind)
            return explicit_bind

    host = sandbox_host if sandbox_host is not None else os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
    if _is_ipv6_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: [::1] (IPv6 loopback sandbox host)")
        return "[::1]"
    if _is_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: 127.0.0.1 (loopback default)")
        return "127.0.0.1"

    logger.debug("Docker sandbox bind: 0.0.0.0 (non-loopback sandbox host compatibility)")
    return "0.0.0.0"


class LocalContainerBackend(SandboxBackend):
    """使用 Docker 或 Apple Container 在本地管理沙箱容器的后端。
    
            在 macOS 上，自动优先使用 Apple Container（若可用），否则回退到 Docker。
            在其他平台上，使用 Docker。
    
            特性：
            - 确定性容器命名，支持跨进程发现
            - 线程安全的端口分配工具
            - 容器生命周期管理（带 ``--rm`` 的启动/停止）
            - 支持卷挂载和环境变量
    """


    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        config_mounts: list,
        environment: dict[str, str],
    ):
        """初始化本地容器后端。
        
                    Args:
                        image: 使用的容器镜像。
                        base_port: 搜索空闲端口的起始基准端口。
                        container_prefix: 容器名前缀（例如 ``"deer-flow-sandbox"``）。
                        config_mounts: 来自配置的卷挂载配置（``VolumeMountConfig`` 列表）。
                        environment: 注入到容器中的环境变量。
        """

        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._config_mounts = config_mounts
        self._environment = environment
        self._runtime = self._detect_runtime()

    @property
    def runtime(self) -> str:
        """检测到的容器运行时（``docker`` 或 ``container``）。"""

        return self._runtime

    def _detect_runtime(self) -> str:
        """检测要使用的容器运行时。
        
                    在 macOS 上，优先使用 Apple Container（若可用），否则回退到 Docker。
                    在其他平台上，使用 Docker。
        
                    Returns:
                        Apple Container 返回 ``"container"``，Docker 返回 ``"docker"``。
        """

        import platform

        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["container", "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                logger.info(f"Detected Apple Container: {result.stdout.strip()}")
                return "container"
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.info("Apple Container not available, falling back to Docker")

        return "docker"

    # ── SandboxBackend interface ──────────────────────────────────────────

    def create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """启动一个新容器并返回其连接信息。
        
                    Args:
                        thread_id: 创建沙箱所用的线程 ID，方便后端按线程组织沙箱。
                        sandbox_id: 确定性沙箱标识（用于容器命名）。
                        extra_mounts: 额外的卷挂载，形式为 ``(host_path, container_path, read_only)``。
        
                    Returns:
                        包含容器详情的 ``SandboxInfo``。
        
                    Raises:
                        RuntimeError: 容器启动失败时抛出。
        """

        container_name = f"{self._container_prefix}-{sandbox_id}"

        # Retry loop: if Docker rejects the port (e.g. a stale container still
        # holds the binding after a process restart), skip that port and try the
        # next one.  The socket-bind check in get_free_port mirrors Docker's
        # 0.0.0.0 bind, but Docker's port-release can be slightly asynchronous,
        # so a reactive fallback here ensures we always make progress.
        _next_start = self._base_port
        container_id: str | None = None
        port: int = 0
        for _attempt in range(10):
            port = get_free_port(start_port=_next_start)
            try:
                container_id = self._start_container(container_name, port, extra_mounts)
                break
            except RuntimeError as exc:
                release_port(port)
                err = str(exc)
                err_lower = err.lower()
                # Port already bound: skip this port and retry with the next one.
                if "port is already allocated" in err or "address already in use" in err_lower:
                    logger.warning(f"Port {port} rejected by Docker (already allocated), retrying with next port")
                    _next_start = port + 1
                    continue
                # Container-name conflict: another process may have already started
                # the deterministic sandbox container for this sandbox_id. Try to
                # discover and adopt the existing container instead of failing.
                if "is already in use by container" in err_lower or "conflict. the container name" in err_lower:
                    logger.warning(f"Container name {container_name} already in use, attempting to discover existing sandbox instance")
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
                raise
        else:
            raise RuntimeError("Could not start sandbox container: all candidate ports are already allocated by Docker")

        # When running inside Docker (DooD), sandbox containers are reachable via
        # host.docker.internal rather than localhost (they run on the host daemon).
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{sandbox_host}:{port}",
            container_name=container_name,
            container_id=container_id,
        )

    def destroy(self, info: SandboxInfo) -> None:
        """停止容器并释放其端口。"""

        # Prefer container_id, fall back to container_name (both accepted by docker stop).
        # This ensures containers discovered via list_running() (which only has the name)
        # can also be stopped.
        stop_target = info.container_id or info.container_name
        if stop_target:
            self._stop_container(stop_target)
        # Extract port from sandbox_url for release
        try:
            from urllib.parse import urlparse

            port = urlparse(info.sandbox_url).port
            if port:
                release_port(port)
        except Exception:
            pass

    def is_alive(self, info: SandboxInfo) -> bool:
        """检查容器是否仍在运行（轻量级，无 HTTP）。"""

        if info.container_name:
            return self._is_container_running(info.container_name)
        return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """按确定性名称发现已存在的容器。
        
                    检查指定名称的容器是否在运行，获取其端口，并验证它能响应健康检查。
        
                    Args:
                        sandbox_id: 确定性沙箱 ID（决定容器名）。
        
                    Returns:
                        容器存在且健康时返回 ``SandboxInfo``，否则返回 ``None``。
        """

        container_name = f"{self._container_prefix}-{sandbox_id}"

        if not self._is_container_running(container_name):
            return None

        port = self._get_container_port(container_name)
        if port is None:
            return None

        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            return None

        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
        )

    def list_running(self) -> list[SandboxInfo]:
        """枚举所有与配置前缀匹配且正在运行的容器。
        
                    通过单次 ``docker ps`` 调用列出容器名，再通过单次批量的 ``docker inspect`` 调用
                    一次性获取所有容器的创建时间戳与端口映射。子进程调用总次数为 2
                    （原本逐容器的方式为 2N+1）。
        
                    注意：Docker 的 ``--filter name=`` 进行的是 *子串* 匹配，因此会再附加一个
                    ``startswith`` 检查，确保仅保留具有确切前缀的容器。
        
                    没有端口映射的容器仍会被包含（``sandbox_url`` 留空），以便启动协调可以
                    接管处于任意端口状态的孤立容器。
        """

        # Step 1: enumerate container names via docker ps
        try:
            result = subprocess.run(
                [
                    self._runtime,
                    "ps",
                    "--filter",
                    f"name={self._container_prefix}-",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(
                    "Failed to list running containers with %s ps (returncode=%s, stderr=%s)",
                    self._runtime,
                    result.returncode,
                    stderr or "<empty>",
                )
                return []
            if not result.stdout.strip():
                return []
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to list running containers: {e}")
            return []

        # Filter to names matching our exact prefix (docker filter is substring-based)
        container_names = [name.strip() for name in result.stdout.strip().splitlines() if name.strip().startswith(self._container_prefix + "-")]
        if not container_names:
            return []

        # Step 2: batched docker inspect — single subprocess call for all containers
        inspections = self._batch_inspect(container_names)

        infos: list[SandboxInfo] = []
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        for container_name in container_names:
            data = inspections.get(container_name)
            if data is None:
                # Container disappeared between ps and inspect, or inspect failed
                continue
            created_at, host_port = data
            sandbox_id = container_name[len(self._container_prefix) + 1 :]
            sandbox_url = f"http://{sandbox_host}:{host_port}" if host_port else ""

            infos.append(
                SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=sandbox_url,
                    container_name=container_name,
                    created_at=created_at,
                )
            )

        logger.info(f"Found {len(infos)} running sandbox container(s)")
        return infos

    def _batch_inspect(self, container_names: list[str]) -> dict[str, tuple[float, int | None]]:
        """在单次子进程调用中批量 inspect 容器。
        
                    返回 ``{container_name: (created_at, host_port)}`` 的映射。
                    找不到的容器或解析失败会从结果中静默剔除。
        """

        if not container_names:
            return {}
        try:
            result = subprocess.run(
                [self._runtime, "inspect", *container_names],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to batch-inspect containers: {e}")
            return {}

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(
                "Failed to batch-inspect containers with %s inspect (returncode=%s, stderr=%s)",
                self._runtime,
                result.returncode,
                stderr or "<empty>",
            )
            return {}

        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse docker inspect output as JSON: {e}")
            return {}

        out: dict[str, tuple[float, int | None]] = {}
        for entry in payload:
            # ``Name`` is prefixed with ``/`` in the docker inspect response
            name = (entry.get("Name") or "").lstrip("/")
            if not name:
                continue
            created_at = _parse_docker_timestamp(entry.get("Created", ""))
            host_port = _extract_host_port(entry, 8080)
            out[name] = (created_at, host_port)
        return out

    # ── Container operations ─────────────────────────────────────────────

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        """启动一个新容器。
        
                    Args:
                        container_name: 容器名。
                        port: 映射到容器 8080 端口的主机端口。
                        extra_mounts: 额外的卷挂载。
        
                    Returns:
                        容器 ID。
        
                    Raises:
                        RuntimeError: 容器启动失败时抛出。
        """

        cmd = [self._runtime, "run"]

        # Docker-specific security options
        if self._runtime == "docker":
            cmd.extend(["--security-opt", "seccomp=unconfined"])

        if self._runtime == "docker":
            port_mapping = f"{_resolve_docker_bind_host()}:{port}:8080"
        else:
            port_mapping = f"{port}:8080"

        cmd.extend(
            [
                "--rm",
                "-d",
                "-p",
                port_mapping,
                "--name",
                container_name,
            ]
        )

        # Environment variables
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Config-level volume mounts
        for mount in self._config_mounts:
            cmd.extend(
                _format_container_mount(
                    self._runtime,
                    mount.host_path,
                    mount.container_path,
                    mount.read_only,
                )
            )

        # Extra mounts (thread-specific, skills, etc.)
        if extra_mounts:
            for host_path, container_path, read_only in extra_mounts:
                cmd.extend(
                    _format_container_mount(
                        self._runtime,
                        host_path,
                        container_path,
                        read_only,
                    )
                )

        cmd.append(self._image)

        log_cmd = _format_container_command_for_log(_redact_container_command_for_log(cmd))
        logger.info(f"Starting container using {self._runtime}: {log_cmd}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            logger.info(f"Started container {container_name} (ID: {container_id}) using {self._runtime}")
            return container_id
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start container using {self._runtime}: {e.stderr}")
            raise RuntimeError(f"Failed to start sandbox container: {e.stderr}")

    def _stop_container(self, container_id: str) -> None:
        """停止容器（``--rm`` 确保自动删除）。"""

        try:
            subprocess.run(
                [self._runtime, "stop", container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop container {container_id}: {e.stderr}")

    def _is_container_running(self, container_name: str) -> bool:
        """检查指定名称的容器当前是否在运行。
        
                    这支持跨进程的容器发现——任意进程都可以通过确定性容器名发现
                    由其他进程启动的容器。
        """

        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _get_container_port(self, container_name: str) -> int | None:
        """获取运行中容器的主机端口。
        
                    Args:
                        container_name: 要 inspect 的容器名。
        
                    Returns:
                        映射到容器 8080 端口的主机端口；找不到时返回 ``None``。
        """

        try:
            result = subprocess.run(
                [self._runtime, "port", container_name, "8080"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "0.0.0.0:PORT" or ":::PORT"
                port_str = result.stdout.strip().split(":")[-1]
                return int(port_str)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            pass
        return None
