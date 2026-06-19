"""AIO 沙箱提供者:用可插拔后端编排沙箱生命周期。

本提供者组合一个 :class:`SandboxBackend`(决定沙箱如何被供应),并自行负责:
- 进程内缓存,加速重复访问
- 空闲超时管理
- 优雅关闭与信号处理
- 卷挂载计算(线程级、skills)
"""

import asyncio
import atexit
import hashlib
import logging
import os
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider

from .aio_sandbox import AioSandbox
from .backend import SandboxBackend, wait_for_sandbox_ready, wait_for_sandbox_ready_async
from .local_backend import LocalContainerBackend
from .remote_backend import RemoteSandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_IMAGE = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
DEFAULT_PORT = 8080
DEFAULT_CONTAINER_PREFIX = "deer-flow-sandbox"
DEFAULT_IDLE_TIMEOUT = 600  # 10 minutes in seconds
DEFAULT_REPLICAS = 3  # Maximum concurrent sandbox containers
IDLE_CHECK_INTERVAL = 60  # Check every 60 seconds
THREAD_LOCK_EXECUTOR_WORKERS = min(32, (os.cpu_count() or 1) + 4)
_THREAD_LOCK_EXECUTOR = ThreadPoolExecutor(max_workers=THREAD_LOCK_EXECUTOR_WORKERS, thread_name_prefix="sandbox-lock-wait")
atexit.register(_THREAD_LOCK_EXECUTOR.shutdown, wait=False, cancel_futures=True)


def _lock_file_exclusive(lock_file) -> None:
    """内部辅助方法。"""
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_file(lock_file) -> None:
    """内部辅助方法。"""
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _open_lock_file(lock_path):
    """返回值。"""
    return open(lock_path, "a", encoding="utf-8")


async def _acquire_thread_lock_async(lock: threading.Lock) -> None:
    """在不使用轮询或默认执行器的前提下获取 ``threading.Lock``。"""

    loop = asyncio.get_running_loop()
    acquire_future = loop.run_in_executor(_THREAD_LOCK_EXECUTOR, lock.acquire, True)

    try:
        acquired = await asyncio.shield(acquire_future)
    except asyncio.CancelledError:
        acquire_future.add_done_callback(lambda task: _release_cancelled_lock_acquire(lock, task))
        raise

    if not acquired:
        raise RuntimeError("Failed to acquire sandbox thread lock")


def _release_cancelled_lock_acquire(lock: threading.Lock, task: asyncio.Future[bool]) -> None:
    """释放由已被取消的协程在等待时取得的锁。"""

    if task.cancelled():
        return

    try:
        acquired = task.result()
    except Exception as e:
        logger.warning(f"Cancelled sandbox lock acquisition finished with error: {e}")
        return

    if acquired:
        lock.release()


class AioSandboxProvider(SandboxProvider):
    """管理运行 AIO 沙箱的容器生命周期的沙箱提供者。

    架构:
        本提供者组合一个 :class:`SandboxBackend`(决定沙箱如何被供应),从而支持:
        - 本地 Docker/Apple Container 模式(自动启动容器)
        - 远程/K8s 模式(连接到已存在的沙箱 URL)

    在 ``config.yaml`` 的 ``sandbox`` 段下支持的配置项::

        use: deerflow.community.aio_sandbox:AioSandboxProvider
        image: <container image>
        port: 8080                      # 本地容器的基础端口
        container_prefix: deer-flow-sandbox
        idle_timeout: 600               # 空闲超时秒数(0 表示禁用)
        replicas: 3                     # 最大并发沙箱数(超出按 LRU 淘汰)
        mounts:                         # 本地容器的卷挂载
          - host_path: /path/on/host
            container_path: /path/in/container
            read_only: false
        environment:                    # 容器环境变量
          NODE_ENV: production
          API_KEY: $MY_API_KEY
    """

    def __init__(self):
        """初始化提供者,加载配置、创建后端、注册信号处理并启动空闲检查。"""
        # 进程级互斥锁：保护沙箱池的并发访问
        self._lock = threading.Lock()
        self._sandboxes: dict[str, AioSandbox] = {}  # sandbox_id -> AioSandbox instance
        self._sandbox_infos: dict[str, SandboxInfo] = {}  # sandbox_id -> SandboxInfo (for destroy)
        self._thread_sandboxes: dict[str, str] = {}  # thread_id -> sandbox_id
        self._thread_locks: dict[str, threading.Lock] = {}  # thread_id -> in-process lock
        self._last_activity: dict[str, float] = {}  # sandbox_id -> last activity timestamp
        # Warm pool: 已释放但容器仍在运行的沙箱。
        # 映射 sandbox_id -> (SandboxInfo, release_timestamp)。
        # 池中的容器可被快速回收（无冷启动），或在超出副本容量时被销毁。
        self._warm_pool: dict[str, tuple[SandboxInfo, float]] = {}
        self._shutdown_called = False
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

        self._config = self._load_config()
        self._backend: SandboxBackend = self._create_backend()

        # Register shutdown handler
        atexit.register(self.shutdown)
        self._register_signal_handlers()

        # Reconcile orphaned containers from previous process lifecycles
        self._reconcile_orphans()

        # Start idle checker if enabled
        if self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT) > 0:
            self._start_idle_checker()

    @property
    def uses_thread_data_mounts(self) -> bool:
        """线程 workspace/uploads/outputs 是否通过挂载点直接对沙箱可见。

        本地容器后端把线程数据目录 bind-mount 进容器,因此 gateway 写入的文件在
        沙箱启动时即可见;远程后端可能需要显式同步。
        """
        return isinstance(self._backend, LocalContainerBackend)

    # ── Factory methods ──────────────────────────────────────────────────

    def _create_backend(self) -> SandboxBackend:
        """根据配置创建对应的后端。

        选择逻辑(按顺序检查):
        1. 配置了 ``provisioner_url`` → :class:`RemoteSandboxBackend`(provisioner 模式);
              provisioner 在 k3s 中动态创建 Pod + Service。
        2. 默认 → :class:`LocalContainerBackend`(本地模式);
              本地提供者直接管理容器生命周期(启动/停止)。
        """
        provisioner_url = self._config.get("provisioner_url")
        if provisioner_url:
            logger.info(f"Using remote sandbox backend with provisioner at {provisioner_url}")
            return RemoteSandboxBackend(provisioner_url=provisioner_url)

        logger.info("Using local container sandbox backend")
        return LocalContainerBackend(
            image=self._config["image"],
            base_port=self._config["port"],
            container_prefix=self._config["container_prefix"],
            config_mounts=self._config["mounts"],
            environment=self._config["environment"],
        )

    # ── Configuration ────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """从应用配置中读取沙箱相关设置并补齐默认值。"""
        config = get_app_config()
        sandbox_config = config.sandbox

        idle_timeout = getattr(sandbox_config, "idle_timeout", None)
        replicas = getattr(sandbox_config, "replicas", None)

        return {
            "image": sandbox_config.image or DEFAULT_IMAGE,
            "port": sandbox_config.port or DEFAULT_PORT,
            "container_prefix": sandbox_config.container_prefix or DEFAULT_CONTAINER_PREFIX,
            "idle_timeout": idle_timeout if idle_timeout is not None else DEFAULT_IDLE_TIMEOUT,
            "replicas": replicas if replicas is not None else DEFAULT_REPLICAS,
            "mounts": sandbox_config.mounts or [],
            "environment": self._resolve_env_vars(sandbox_config.environment or {}),
            # provisioner URL for dynamic pod management (e.g. http://provisioner:8002)
            "provisioner_url": getattr(sandbox_config, "provisioner_url", None) or "",
        }

    @staticmethod
    def _resolve_env_vars(env_config: dict[str, str]) -> dict[str, str]:
        """解析以 ``$`` 开头的环境变量引用,从进程环境取值。"""
        resolved = {}
        for key, value in env_config.items():
            if isinstance(value, str) and value.startswith("$"):
                env_name = value[1:]
                resolved[key] = os.environ.get(env_name, "")
            else:
                resolved[key] = str(value)
        return resolved

    # ── Startup reconciliation ────────────────────────────────────────────

    def _reconcile_orphans(self) -> None:
        """协调被旧进程生命周期遗留的容器。

        启动时枚举所有匹配容器名前缀的运行中容器并全部纳入热池;若没有线程
        在 ``idle_timeout`` 内复用,空闲检查线程会负责回收。无法仅凭"年龄"
        区分"孤儿"与"被其他进程在用",``idle_timeout`` 衡量的是不活跃时长而非
        运行时长;将其纳入热池、再让空闲检查线程决定,既可以避免误杀另一个
        进程仍在使用的容器,也堵住了"进程重启/崩溃/SIGKILL 留下 Docker 容
        器一直运行"这一根本性漏洞。
        """
        try:
            running = self._backend.list_running()
        except Exception as e:
            logger.warning(f"Failed to enumerate running containers during startup reconciliation: {e}")
            return

        if not running:
            return

        current_time = time.time()
        adopted = 0

        for info in running:
            age = current_time - info.created_at if info.created_at > 0 else float("inf")
            # Single lock acquisition per container: atomic check-and-insert.
            # Avoids a TOCTOU window between the "already tracked?" check and
            # the warm-pool insert.
            with self._lock:
                if info.sandbox_id in self._sandboxes or info.sandbox_id in self._warm_pool:
                    continue
                self._warm_pool[info.sandbox_id] = (info, current_time)
            adopted += 1
            logger.info(f"Adopted container {info.sandbox_id} into warm pool (age: {age:.0f}s)")

        logger.info(f"Startup reconciliation complete: {adopted} adopted into warm pool, {len(running)} total found")

    # ── Deterministic ID ─────────────────────────────────────────────────

    @staticmethod
    def _deterministic_sandbox_id(thread_id: str) -> str:
        """基于 ``thread_id`` 派生确定性沙箱 ID。

        保证所有进程对同一 ``thread_id`` 派生相同的 ``sandbox_id``,使跨进程
        沙箱发现无需共享内存也能工作。
        """
        return hashlib.sha256(thread_id.encode()).hexdigest()[:8]

    # ── Mount helpers ────────────────────────────────────────────────────

    def _get_extra_mounts(self, thread_id: str | None) -> list[tuple[str, str, bool]]:
        """收集该沙箱所需的全部额外挂载(线程级 + skills)。"""
        mounts: list[tuple[str, str, bool]] = []

        if thread_id:
            mounts.extend(self._get_thread_mounts(thread_id))
            logger.info(f"Adding thread mounts for thread {thread_id}: {mounts}")

        skills_mount = self._get_skills_mount()
        if skills_mount:
            mounts.append(skills_mount)
            logger.info(f"Adding skills mount: {skills_mount}")

        return mounts

    @staticmethod
    def _get_thread_mounts(thread_id: str) -> list[tuple[str, str, bool]]:
        """为线程的数据目录生成卷挂载配置。

        必要时惰性创建目录;挂载源使用 host_base_dir,以便当使用 Docker-in-Docker
        (DooD) 挂载 Docker socket 时,主机 Docker 守护进程能解析这些路径。
        """
        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)

        return [
            (paths.host_sandbox_work_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/workspace", False),
            (paths.host_sandbox_uploads_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/uploads", False),
            (paths.host_sandbox_outputs_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/outputs", False),
            # ACP workspace: read-only inside the sandbox (lead agent reads results;
            # the ACP subprocess writes from the host side, not from within the container).
            (paths.host_acp_workspace_dir(thread_id, user_id=user_id), "/mnt/acp-workspace", True),
        ]

    @staticmethod
    def _get_skills_mount() -> tuple[str, str, bool] | None:
        """获取 skills 目录的挂载配置。

        在 Docker-in-Docker (DooD) 场景下使用 ``DEER_FLOW_HOST_SKILLS_PATH``
        以让主机 Docker 守护进程能够解析路径。
        """
        try:
            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            if skills_path.exists():
                # When running inside Docker with DooD, use host-side skills path.
                host_skills = os.environ.get("DEER_FLOW_HOST_SKILLS_PATH") or str(skills_path)
                return (host_skills, container_path, True)  # Read-only for security
        except Exception as e:
            logger.warning(f"Could not setup skills mount: {e}")
        return None

    # ── Idle timeout management ──────────────────────────────────────────

    def _start_idle_checker(self) -> None:
        """启动后台线程,定期清理空闲沙箱。"""
        self._idle_checker_thread = threading.Thread(
            target=self._idle_checker_loop,
            name="sandbox-idle-checker",
            daemon=True,
        )
        self._idle_checker_thread.start()
        logger.info(f"Started idle checker thread (timeout: {self._config.get('idle_timeout', DEFAULT_IDLE_TIMEOUT)}s)")

    def _idle_checker_loop(self) -> None:
        """空闲检查线程主循环,周期性调用 :meth:`_cleanup_idle_sandboxes`。"""
        idle_timeout = self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT)
        while not self._idle_checker_stop.wait(timeout=IDLE_CHECK_INTERVAL):
            try:
                self._cleanup_idle_sandboxes(idle_timeout)
            except Exception as e:
                logger.error(f"Error in idle checker loop: {e}")

    def _cleanup_idle_sandboxes(self, idle_timeout: float) -> None:
        """扫描活动与热池,销毁空闲超时的沙箱。"""
        current_time = time.time()
        active_to_destroy = []
        warm_to_destroy: list[tuple[str, SandboxInfo]] = []

        with self._lock:
            # Active sandboxes: tracked via _last_activity
            for sandbox_id, last_activity in self._last_activity.items():
                idle_duration = current_time - last_activity
                if idle_duration > idle_timeout:
                    active_to_destroy.append(sandbox_id)
                    logger.info(f"Sandbox {sandbox_id} idle for {idle_duration:.1f}s, marking for destroy")

            # Warm pool: tracked via release_timestamp stored in _warm_pool
            for sandbox_id, (info, release_ts) in list(self._warm_pool.items()):
                warm_duration = current_time - release_ts
                if warm_duration > idle_timeout:
                    warm_to_destroy.append((sandbox_id, info))
                    del self._warm_pool[sandbox_id]
                    logger.info(f"Warm-pool sandbox {sandbox_id} idle for {warm_duration:.1f}s, marking for destroy")

        # Destroy active sandboxes (re-verify still idle before acting)
        for sandbox_id in active_to_destroy:
            try:
                # Re-verify the sandbox is still idle under the lock before destroying.
                # Between the snapshot above and here, the sandbox may have been
                # re-acquired (last_activity updated) or already released/destroyed.
                with self._lock:
                    last_activity = self._last_activity.get(sandbox_id)
                    if last_activity is None:
                        # Already released or destroyed by another path — skip.
                        logger.info(f"Sandbox {sandbox_id} already gone before idle destroy, skipping")
                        continue
                    if (time.time() - last_activity) < idle_timeout:
                        # Re-acquired (activity updated) since the snapshot — skip.
                        logger.info(f"Sandbox {sandbox_id} was re-acquired before idle destroy, skipping")
                        continue
                logger.info(f"Destroying idle sandbox {sandbox_id}")
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy idle sandbox {sandbox_id}: {e}")

        # Destroy warm-pool sandboxes (already removed from _warm_pool under lock above)
        for sandbox_id, info in warm_to_destroy:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed idle warm-pool sandbox {sandbox_id}")
            except Exception as e:
                logger.error(f"Failed to destroy idle warm-pool sandbox {sandbox_id}: {e}")

    # ── Signal handling ──────────────────────────────────────────────────

    def _register_signal_handlers(self) -> None:
        """注册信号处理器实现优雅关闭,覆盖 SIGTERM / SIGINT / SIGHUP(终端关闭)。"""
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sighup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None

        def signal_handler(signum, frame):
            """执行相应操作。
            
                    Args:
                        signum: 参数说明。
                        frame: 参数说明。
            """
            self.shutdown()
            if signum == signal.SIGTERM:
                original = self._original_sigterm
            elif hasattr(signal, "SIGHUP") and signum == signal.SIGHUP:
                original = self._original_sighup
            else:
                original = self._original_sigint
            if callable(original):
                original(signum, frame)
            elif original == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, signal_handler)
        except ValueError:
            logger.debug("Could not register signal handlers (not main thread)")

    # ── Thread locking (in-process) ──────────────────────────────────────

    def _get_thread_lock(self, thread_id: str) -> threading.Lock:
        """按 ``thread_id`` 获取或创建进程内锁。"""
        with self._lock:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = threading.Lock()
            return self._thread_locks[thread_id]

    def _sandbox_id_for_thread(self, thread_id: str | None) -> str:
        """线程沙箱返回确定性 ID,匿名沙箱返回随机 ID。"""
        return self._deterministic_sandbox_id(thread_id) if thread_id else str(uuid.uuid4())[:8]

    def _reuse_in_process_sandbox(self, thread_id: str | None, *, post_lock: bool = False) -> str | None:
        """若线程仍跟踪着活动沙箱,直接复用。"""
        if thread_id is None:
            return None

        with self._lock:
            if thread_id not in self._thread_sandboxes:
                return None

            existing_id = self._thread_sandboxes[thread_id]
            if existing_id in self._sandboxes:
                suffix = " (post-lock check)" if post_lock else ""
                logger.info(f"Reusing in-process sandbox {existing_id} for thread {thread_id}{suffix}")
                self._last_activity[existing_id] = time.time()
                return existing_id

            del self._thread_sandboxes[thread_id]
            return None

    def _reclaim_warm_pool_sandbox(self, thread_id: str | None, sandbox_id: str, *, post_lock: bool = False) -> str | None:
        """把热池中的沙箱提升回活动状态(如可用)。"""
        if thread_id is None:
            return None

        with self._lock:
            if sandbox_id not in self._warm_pool:
                return None

            info, _ = self._warm_pool.pop(sandbox_id)
            sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_infos[sandbox_id] = info
            self._last_activity[sandbox_id] = time.time()
            self._thread_sandboxes[thread_id] = sandbox_id

        suffix = " (post-lock check)" if post_lock else f" at {info.sandbox_url}"
        logger.info(f"Reclaimed warm-pool sandbox {sandbox_id} for thread {thread_id}{suffix}")
        return sandbox_id

    def _recheck_cached_sandbox(self, thread_id: str, sandbox_id: str) -> str | None:
        """在跨进程文件锁拿到后重新检查内存缓存。"""
        return self._reuse_in_process_sandbox(thread_id, post_lock=True) or self._reclaim_warm_pool_sandbox(thread_id, sandbox_id, post_lock=True)

    def _register_discovered_sandbox(self, thread_id: str, info: SandboxInfo) -> str:
        """把后端发现的沙箱纳入活动跟踪表。"""
        sandbox = AioSandbox(id=info.sandbox_id, base_url=info.sandbox_url)
        with self._lock:
            self._sandboxes[info.sandbox_id] = sandbox
            self._sandbox_infos[info.sandbox_id] = info
            self._last_activity[info.sandbox_id] = time.time()
            self._thread_sandboxes[thread_id] = info.sandbox_id

        logger.info(f"Discovered existing sandbox {info.sandbox_id} for thread {thread_id} at {info.sandbox_url}")
        return info.sandbox_id

    def _register_created_sandbox(self, thread_id: str | None, sandbox_id: str, info: SandboxInfo) -> str:
        """把刚创建的沙箱纳入活动映射。"""
        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
        with self._lock:
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_infos[sandbox_id] = info
            self._last_activity[sandbox_id] = time.time()
            if thread_id:
                self._thread_sandboxes[thread_id] = sandbox_id

        logger.info(f"Created sandbox {sandbox_id} for thread {thread_id} at {info.sandbox_url}")
        return sandbox_id

    def _replica_count(self) -> tuple[int, int]:
        """返回配置的副本数与当前被跟踪的沙箱总数。"""
        replicas = self._config.get("replicas", DEFAULT_REPLICAS)
        with self._lock:
            total = len(self._sandboxes) + len(self._warm_pool)
        return replicas, total

    def _log_replicas_soft_cap(self, replicas: int, sandbox_id: str, evicted: str | None) -> None:
        """记录执行 warm-pool 副本预算后的结果。"""
        if evicted:
            logger.info(f"Evicted warm-pool sandbox {evicted} to stay within replicas={replicas}")
            return

        # All slots are occupied by active sandboxes — proceed anyway and log.
        # The replicas limit is a soft cap; we never forcibly stop a container
        # that is actively serving a thread.
        logger.warning(f"All {replicas} replica slots are in active use; creating sandbox {sandbox_id} beyond the soft limit")

    # ── Core: acquire / get / release / shutdown ─────────────────────────

    def acquire(self, thread_id: str | None = None) -> str:
        """获取一个沙箱环境并返回其 ID。

        对同一 ``thread_id``,本方法在多次轮转、多进程,以及(配合共享存储)
        多 Pod 场景下都会返回相同的 ``sandbox_id``。同时使用进程内锁与跨进程
        锁保证线程安全。

        Args:
            thread_id: 可选线程 ID,用于线程级配置。

        Returns:
            已获取的沙箱环境 ID。
        """
        if thread_id:
            thread_lock = self._get_thread_lock(thread_id)
            with thread_lock:
                return self._acquire_internal(thread_id)
        else:
            return self._acquire_internal(thread_id)

    async def acquire_async(self, thread_id: str | None = None) -> str:
        """异步获取沙箱,避免阻塞事件循环。

        与 :meth:`acquire` 行为一致,但把阻塞的后端操作移出事件循环,并在新建沙
        箱时使用原生异步的就绪轮询。

        Args:
            thread_id: 可选线程 ID。

        Returns:
            已获取的沙箱环境 ID。
        """
        if thread_id:
            thread_lock = self._get_thread_lock(thread_id)
            await _acquire_thread_lock_async(thread_lock)
            try:
                return await self._acquire_internal_async(thread_id)
            finally:
                thread_lock.release()

        return await self._acquire_internal_async(thread_id)

    def _acquire_internal(self, thread_id: str | None) -> str:
        """两层一致性的内部沙箱获取。

        第一层:进程内缓存(最快,覆盖同进程重复访问)。
        第二层:后端发现(覆盖其他进程已启动的容器;``sandbox_id`` 由
        ``thread_id`` 确定性派生,无需共享状态文件,任何进程都能派生
        同一个容器名)。
        """
        cached_id = self._reuse_in_process_sandbox(thread_id)
        if cached_id is not None:
            return cached_id

        # Deterministic ID for thread-specific, random for anonymous
        sandbox_id = self._sandbox_id_for_thread(thread_id)

        # ── Layer 1.5: Warm pool (container still running, no cold-start) ──
        reclaimed_id = self._reclaim_warm_pool_sandbox(thread_id, sandbox_id)
        if reclaimed_id is not None:
            return reclaimed_id

        # ── Layer 2: Backend discovery + create (protected by cross-process lock) ──
        # Use a file lock so that two processes racing to create the same sandbox
        # for the same thread_id serialize here: the second process will discover
        # the container started by the first instead of hitting a name-conflict.
        if thread_id:
            return self._discover_or_create_with_lock(thread_id, sandbox_id)

        return self._create_sandbox(thread_id, sandbox_id)

    async def _acquire_internal_async(self, thread_id: str | None) -> str:
        """异步版 :meth:`_acquire_internal`。"""
        cached_id = self._reuse_in_process_sandbox(thread_id)
        if cached_id is not None:
            return cached_id

        # Deterministic ID for thread-specific, random for anonymous
        sandbox_id = self._sandbox_id_for_thread(thread_id)

        # ── Layer 1.5: Warm pool (container still running, no cold-start) ──
        reclaimed_id = self._reclaim_warm_pool_sandbox(thread_id, sandbox_id)
        if reclaimed_id is not None:
            return reclaimed_id

        # ── Layer 2: Backend discovery + create (protected by cross-process lock) ──
        if thread_id:
            return await self._discover_or_create_with_lock_async(thread_id, sandbox_id)

        return await self._create_sandbox_async(thread_id, sandbox_id)

    def _discover_or_create_with_lock(self, thread_id: str, sandbox_id: str) -> str:
        """在跨进程文件锁保护下发现或创建沙箱。

        文件锁将同一 ``thread_id`` 跨进程的并发沙箱创建串行化,避免容器名冲突。

        Args:
            thread_id: 线程 ID。
            sandbox_id: 确定性沙箱 ID。

        Returns:
            获取到的沙箱 ID。
        """
        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)
        lock_path = paths.thread_dir(thread_id, user_id=user_id) / f"{sandbox_id}.lock"

        with open(lock_path, "a", encoding="utf-8") as lock_file:
            locked = False
            try:
                _lock_file_exclusive(lock_file)
                locked = True
                # Re-check in-process caches under the file lock in case another
                # thread in this process won the race while we were waiting.
                cached_id = self._recheck_cached_sandbox(thread_id, sandbox_id)
                if cached_id is not None:
                    return cached_id

                # Backend discovery: another process may have created the container.
                discovered = self._backend.discover(sandbox_id)
                if discovered is not None:
                    return self._register_discovered_sandbox(thread_id, discovered)

                return self._create_sandbox(thread_id, sandbox_id)
            finally:
                if locked:
                    _unlock_file(lock_file)

    async def _discover_or_create_with_lock_async(self, thread_id: str, sandbox_id: str) -> str:
        """异步版 :meth:`_discover_or_create_with_lock`。"""
        paths = get_paths()
        user_id = get_effective_user_id()
        await asyncio.to_thread(paths.ensure_thread_dirs, thread_id, user_id=user_id)
        lock_path = paths.thread_dir(thread_id, user_id=user_id) / f"{sandbox_id}.lock"

        lock_file = await asyncio.to_thread(_open_lock_file, lock_path)
        locked = False
        try:
            await asyncio.to_thread(_lock_file_exclusive, lock_file)
            locked = True
            # Re-check in-process caches under the file lock in case another
            # thread in this process won the race while we were waiting.
            cached_id = self._recheck_cached_sandbox(thread_id, sandbox_id)
            if cached_id is not None:
                return cached_id

            # Backend discovery is sync because local discovery may inspect
            # Docker and perform a health check; keep it off the event loop.
            discovered = await asyncio.to_thread(self._backend.discover, sandbox_id)
            if discovered is not None:
                return self._register_discovered_sandbox(thread_id, discovered)

            return await self._create_sandbox_async(thread_id, sandbox_id)
        finally:
            if locked:
                await asyncio.to_thread(_unlock_file, lock_file)
            await asyncio.to_thread(lock_file.close)

    def _evict_oldest_warm(self) -> str | None:
        """销毁热池中最早的容器以腾出容量。

        Returns:
            被淘汰的沙箱 ID;热池为空时返回 None。
        """
        with self._lock:
            if not self._warm_pool:
                return None
            oldest_id = min(self._warm_pool, key=lambda sid: self._warm_pool[sid][1])
            info, _ = self._warm_pool.pop(oldest_id)

        try:
            self._backend.destroy(info)
            logger.info(f"Destroyed warm-pool sandbox {oldest_id}")
        except Exception as e:
            logger.error(f"Failed to destroy warm-pool sandbox {oldest_id}: {e}")
            return None
        return oldest_id

    def _create_sandbox(self, thread_id: str | None, sandbox_id: str) -> str:
        """通过后端创建一个新沙箱。

        Args:
            thread_id: 可选线程 ID。
            sandbox_id: 待使用的沙箱 ID。

        Returns:
            创建后的沙箱 ID。

        Raises:
            RuntimeError: 沙箱创建失败或在就绪等待超时时间内未就绪。
        """
        extra_mounts = self._get_extra_mounts(thread_id)

        # Enforce replicas: only warm-pool containers count toward eviction budget.
        # Active sandboxes are in use by live threads and must not be forcibly stopped.
        replicas, total = self._replica_count()
        if total >= replicas:
            evicted = self._evict_oldest_warm()
            self._log_replicas_soft_cap(replicas, sandbox_id, evicted)

        info = self._backend.create(thread_id, sandbox_id, extra_mounts=extra_mounts or None)

        # Wait for sandbox to be ready
        if not wait_for_sandbox_ready(info.sandbox_url, timeout=60):
            self._backend.destroy(info)
            raise RuntimeError(f"Sandbox {sandbox_id} failed to become ready within timeout at {info.sandbox_url}")

        return self._register_created_sandbox(thread_id, sandbox_id, info)

    async def _create_sandbox_async(self, thread_id: str | None, sandbox_id: str) -> str:
        """异步版 :meth:`_create_sandbox`。"""
        extra_mounts = await asyncio.to_thread(self._get_extra_mounts, thread_id)

        # Enforce replicas: only warm-pool containers count toward eviction budget.
        # Active sandboxes are in use by live threads and must not be forcibly stopped.
        replicas, total = self._replica_count()
        if total >= replicas:
            evicted = await asyncio.to_thread(self._evict_oldest_warm)
            self._log_replicas_soft_cap(replicas, sandbox_id, evicted)

        info = await asyncio.to_thread(self._backend.create, thread_id, sandbox_id, extra_mounts=extra_mounts or None)

        # Wait for sandbox to be ready without blocking the event loop.
        if not await wait_for_sandbox_ready_async(info.sandbox_url, timeout=60):
            await asyncio.to_thread(self._backend.destroy, info)
            raise RuntimeError(f"Sandbox {sandbox_id} failed to become ready within timeout at {info.sandbox_url}")

        return self._register_created_sandbox(thread_id, sandbox_id, info)

    def get(self, sandbox_id: str) -> Sandbox | None:
        """按 ID 获取沙箱并更新最后活跃时间。

        Args:
            sandbox_id: 沙箱 ID。

        Returns:
            找到的沙箱实例,未找到时为 None。
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None:
                self._last_activity[sandbox_id] = time.time()
            return sandbox

    def release(self, sandbox_id: str) -> None:
        """把沙箱从活动状态释放到热池,容器继续运行以便后续复用。

        与 :meth:`destroy` 不同,本方法不会停止容器——这样同线程的下一轮
        复用可以免去冷启动开销。容器仅在 ``replicas`` 上限触发淘汰或
        :meth:`shutdown` 时才被停止。

        释放时会先关闭缓存 :class:`AioSandbox` 实例持有的主机端 HTTP 客户端
        (参见 #2872)。热池只保存 :class:`SandboxInfo`,如后续回收,会重建
        新的 :class:`AioSandbox` 与客户端。

        Args:
            sandbox_id: 待释放的沙箱 ID。
        """
        info = None
        sandbox = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # Park in warm pool — container keeps running
            if info and sandbox_id not in self._warm_pool:
                self._warm_pool[sandbox_id] = (info, time.time())

        if sandbox is not None:
            # Defense-in-depth: close() already swallows its own errors; this
            # guard only protects against a future close() that misbehaves, so
            # host-side client cleanup can never block parking in the warm pool.
            try:
                sandbox.close()
            except Exception as e:
                logger.warning(f"Error closing sandbox {sandbox_id} during release: {e}")

        logger.info(f"Released sandbox {sandbox_id} to warm pool (container still running)")

    def destroy(self, sandbox_id: str) -> None:
        """销毁沙箱:停止容器并释放所有资源。

        与 :meth:`release` 不同,本方法会真正停止容器。用于显式清理、容量
        触发的淘汰或 :meth:`shutdown`。

        同时关闭缓存 :class:`AioSandbox` 实例持有的主机端 HTTP 客户端,
        避免客户端/套接字资源泄漏(参见 #2872)。

        Args:
            sandbox_id: 待销毁的沙箱 ID。
        """
        info = None
        sandbox = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # Also pull from warm pool if it was parked there
            if info is None and sandbox_id in self._warm_pool:
                info, _ = self._warm_pool.pop(sandbox_id)
            else:
                self._warm_pool.pop(sandbox_id, None)

        if sandbox is not None:
            # Defense-in-depth: close() already swallows its own errors; this
            # guard only protects against a future close() that misbehaves, so
            # host-side client cleanup can never block container destruction.
            try:
                sandbox.close()
            except Exception as e:
                logger.warning(f"Error closing sandbox {sandbox_id} during destroy: {e}")

        if info:
            self._backend.destroy(info)
            logger.info(f"Destroyed sandbox {sandbox_id}")

    def shutdown(self) -> None:
        """关闭所有沙箱,线程安全且幂等。"""
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            sandbox_ids = list(self._sandboxes.keys())
            warm_items = list(self._warm_pool.items())
            self._warm_pool.clear()

        # Stop idle checker
        self._idle_checker_stop.set()
        if self._idle_checker_thread is not None and self._idle_checker_thread.is_alive():
            self._idle_checker_thread.join(timeout=5)
            logger.info("Stopped idle checker thread")

        logger.info(f"Shutting down {len(sandbox_ids)} active + {len(warm_items)} warm-pool sandbox(es)")

        for sandbox_id in sandbox_ids:
            try:
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy sandbox {sandbox_id} during shutdown: {e}")

        for sandbox_id, (info, _) in warm_items:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed warm-pool sandbox {sandbox_id} during shutdown")
            except Exception as e:
                logger.error(f"Failed to destroy warm-pool sandbox {sandbox_id} during shutdown: {e}")
