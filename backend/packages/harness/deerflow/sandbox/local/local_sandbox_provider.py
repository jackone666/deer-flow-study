"""本地沙箱提供者:按 thread 维度管理 :class:`LocalSandbox` 缓存与路径映射。

该提供者对外既支持无 thread 上下文的旧式单例获取,也支持按 ``thread_id`` 创建
线程专属沙箱,使 ``/mnt/user-data/...`` 等虚拟路径能够正确解析到该线程的主机目录。
"""

import logging
import threading
from collections import OrderedDict
from pathlib import Path

from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider

logger = logging.getLogger(__name__)

# Module-level alias kept for backward compatibility with older callers/tests
# that reach into ``local_sandbox_provider._singleton`` directly. New code reads
# the provider instance attributes (``_generic_sandbox`` / ``_thread_sandboxes``)
# instead.
_singleton: LocalSandbox | None = None

# Virtual prefixes that must be reserved by the per-thread mappings created in
# ``acquire`` — custom mounts from ``config.yaml`` may not overlap with these.
_USER_DATA_VIRTUAL_PREFIX = "/mnt/user-data"
_ACP_WORKSPACE_VIRTUAL_PREFIX = "/mnt/acp-workspace"

# Default upper bound on per-thread LocalSandbox instances retained in memory.
# Each cached instance is cheap (a small Python object with a list of
# PathMapping and a set of agent-written paths used for reverse resolve), but
# in a long-running gateway the number of distinct thread_ids is unbounded.
# When the cap is exceeded the least-recently-used entry is dropped; the next
# ``acquire(thread_id)`` for that thread simply rebuilds the sandbox at the
# cost of losing its accumulated ``_agent_written_paths`` (read_file falls
# back to no reverse resolution, which is the same behaviour as a fresh run).
DEFAULT_MAX_CACHED_THREAD_SANDBOXES = 256


class LocalSandboxProvider(SandboxProvider):
    """基于本地文件系统的沙箱提供者,支持按线程的路径作用域。

    早期版本仅返回一个进程级 ``LocalSandbox``(ID 为字面量 ``"local"``),无法在
    公共 :class:`Sandbox` API 边界兑现 ``/mnt/user-data/...`` 约定,因为对应的主
    机目录按线程隔离(``{base_dir}/users/{user_id}/threads/{thread_id}/user-data/``)。

    现在该提供者会为每个 ``thread_id`` 创建一个新的 :class:`LocalSandbox`,其
    ``path_mappings`` 包含 ``/mnt/user-data/{workspace,uploads,outputs}`` 与
    ``/mnt/acp-workspace`` 的线程级条目,与 :class:`AioSandboxProvider` 在
    docker 容器中 bind-mount 这些路径的方式保持一致。无 ``thread_id`` 上下文的
    旧式调用 ``acquire()`` / ``acquire(None)`` 仍返回 ID 为 ``"local"`` 的泛
    用单例,以兼容旧测试与脚本。

    线程安全:``acquire``、``get``、``reset`` 可能被 Gateway 工具分发、子 Agent
    工作者池、后台 memory updater 等多线程调用,所有缓存状态变更都通过提供者级
    :class:`threading.Lock` 串行化,与 :class:`AioSandboxProvider` 保持一致。

    内存上限:``_thread_sandboxes`` 是 LRU 缓存,容量上限为 ``max_cached_threads``
    (默认 :data:`DEFAULT_MAX_CACHED_THREAD_SANDBOXES`)。超出时下次 ``acquire``
    会淘汰最久未使用条目;被淘汰线程下次 ``acquire`` 会重新构建一个新沙箱
    (仅丢失其 ``_agent_written_paths`` 反向解析提示,``read_file`` 行为会平滑降级)。
    """

    uses_thread_data_mounts = True
    needs_upload_permission_adjustment = False

    def __init__(self, max_cached_threads: int = DEFAULT_MAX_CACHED_THREAD_SANDBOXES):
        """初始化本地沙箱提供者,并预构建静态路径映射。

        Args:
            max_cached_threads: LRU 缓存中每个线程沙箱的上限。超出后下次
                ``acquire`` 会淘汰最久未使用的条目。
        """
        self._path_mappings = self._setup_path_mappings()
        self._generic_sandbox: LocalSandbox | None = None
        self._thread_sandboxes: OrderedDict[str, LocalSandbox] = OrderedDict()
        self._max_cached_threads = max_cached_threads
        self._lock = threading.Lock()

    def _setup_path_mappings(self) -> list[PathMapping]:
        """构建本提供者所有沙箱共享的静态路径映射。

        静态映射包括 skills 目录与 ``config.yaml`` 中声明的所有自定义挂载点,
        这些是进程级、对所有线程一致的。线程级 ``/mnt/user-data/...`` 与
        ``/mnt/acp-workspace`` 映射在 :meth:`acquire` 中按 ``thread_id`` 和
        有效 ``user_id`` 追加。

        Returns:
            静态 :class:`PathMapping` 列表;配置不可用时为空列表。
        """
        mappings: list[PathMapping] = []

        # Map skills container path to local skills directory
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            # Only add mapping if skills directory exists
            if skills_path.exists():
                mappings.append(
                    PathMapping(
                        container_path=container_path,
                        local_path=str(skills_path),
                        read_only=True,  # Skills directory is always read-only
                    )
                )

            # Map custom mounts from sandbox config
            _RESERVED_CONTAINER_PREFIXES = [
                container_path,
                _ACP_WORKSPACE_VIRTUAL_PREFIX,
                _USER_DATA_VIRTUAL_PREFIX,
            ]
            sandbox_config = config.sandbox
            if sandbox_config and sandbox_config.mounts:
                for mount in sandbox_config.mounts:
                    host_path = Path(mount.host_path)
                    container_path = mount.container_path.rstrip("/") or "/"

                    if not host_path.is_absolute():
                        logger.warning(
                            "Mount host_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue

                    if not container_path.startswith("/"):
                        logger.warning(
                            "Mount container_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue

                    # Reject mounts that conflict with reserved container paths
                    if any(container_path == p or container_path.startswith(p + "/") for p in _RESERVED_CONTAINER_PREFIXES):
                        logger.warning(
                            "Mount container_path conflicts with reserved prefix, skipping: %s",
                            mount.container_path,
                        )
                        continue
                    # Ensure the host path exists before adding mapping
                    if host_path.exists():
                        mappings.append(
                            PathMapping(
                                container_path=container_path,
                                local_path=str(host_path.resolve()),
                                read_only=mount.read_only,
                            )
                        )
                    else:
                        logger.warning(
                            "Mount host_path does not exist, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
        except Exception as e:
            # Log but don't fail if config loading fails
            logger.warning("Could not setup path mappings: %s", e, exc_info=True)

        return mappings

    @staticmethod
    def _build_thread_path_mappings(thread_id: str) -> list[PathMapping]:
        """为指定 ``thread_id`` 构建 ``/mnt/user-data`` 与 ``/mnt/acp-workspace`` 的线程级路径映射。

        通过 :func:`get_effective_user_id` 解析 ``user_id``(与
        :class:`AioSandboxProvider` 使用同一入口),并确保底层主机目录在挂载前
        已经创建。

        Args:
            thread_id: 当前线程 ID。

        Returns:
            按 (父聚合 → 三个子目录 → acp-workspace) 顺序排列的线程级
            :class:`PathMapping` 列表。
        """
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)

        return [
            # Aggregate parent mapping so ``ls /mnt/user-data`` and other
            # parent-level operations behave the same as inside AIO (where the
            # parent directory is real and contains the three subdirs). Longer
            # subpath mappings below still win for ``/mnt/user-data/workspace/...``
            # because ``_find_path_mapping`` sorts by container_path length.
            PathMapping(
                container_path=_USER_DATA_VIRTUAL_PREFIX,
                local_path=str(paths.sandbox_user_data_dir(thread_id, user_id=user_id)),
                read_only=False,
            ),
            PathMapping(
                container_path=f"{_USER_DATA_VIRTUAL_PREFIX}/workspace",
                local_path=str(paths.sandbox_work_dir(thread_id, user_id=user_id)),
                read_only=False,
            ),
            PathMapping(
                container_path=f"{_USER_DATA_VIRTUAL_PREFIX}/uploads",
                local_path=str(paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
                read_only=False,
            ),
            PathMapping(
                container_path=f"{_USER_DATA_VIRTUAL_PREFIX}/outputs",
                local_path=str(paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
                read_only=False,
            ),
            PathMapping(
                container_path=_ACP_WORKSPACE_VIRTUAL_PREFIX,
                local_path=str(paths.acp_workspace_dir(thread_id, user_id=user_id)),
                read_only=False,
            ),
        ]

    def acquire(self, thread_id: str | None = None) -> str:
        """返回按 ``thread_id`` 隔离的沙箱 ID(或通用单例)。

        - ``thread_id=None`` 保留旧式单例(ID 为 ``"local"``),用于无线程上下文的
          调用方(老测试、脚本等)。
        - ``thread_id="abc"`` 返回一个 ID 为 ``"local:abc"`` 的线程级
          :class:`LocalSandbox`,其 ``path_mappings`` 会把 ``/mnt/user-data/...``
          解析到该线程的主机目录。

        并发安全:缓存检查 + 插入由 ``self._lock`` 保护,两个调用方对同一
        ``thread_id`` 竞争时始终能观察到同一个 :class:`LocalSandbox` 实例。

        Args:
            thread_id: 可选的线程 ID。

        Returns:
            沙箱 ID(``"local"`` 或 ``"local:{thread_id}"``)。
        """
        global _singleton

        if thread_id is None:
            with self._lock:
                if self._generic_sandbox is None:
                    self._generic_sandbox = LocalSandbox("local", path_mappings=list(self._path_mappings))
                    _singleton = self._generic_sandbox
                return self._generic_sandbox.id

        # Fast path under lock.
        with self._lock:
            cached = self._thread_sandboxes.get(thread_id)
            if cached is not None:
                # Mark as most-recently used so frequently-touched threads
                # survive eviction.
                self._thread_sandboxes.move_to_end(thread_id)
                return cached.id

        # ``_build_thread_path_mappings`` touches the filesystem
        # (``ensure_thread_dirs``); release the lock during I/O.
        new_mappings = list(self._path_mappings) + self._build_thread_path_mappings(thread_id)

        with self._lock:
            # Re-check after the lock-free I/O: another caller may have
            # populated the cache while we were computing mappings.
            cached = self._thread_sandboxes.get(thread_id)
            if cached is None:
                cached = LocalSandbox(f"local:{thread_id}", path_mappings=new_mappings)
                self._thread_sandboxes[thread_id] = cached
                self._evict_until_within_cap_locked()
            else:
                self._thread_sandboxes.move_to_end(thread_id)
            return cached.id

    def _evict_until_within_cap_locked(self) -> None:
        """当缓存大小超过上限时进行 LRU 淘汰。

        调用方必须持有 ``self._lock``。
        """
        while len(self._thread_sandboxes) > self._max_cached_threads:
            evicted_thread_id, _ = self._thread_sandboxes.popitem(last=False)
            logger.info(
                "Evicting LocalSandbox cache entry for thread %s (cap=%d)",
                evicted_thread_id,
                self._max_cached_threads,
            )

    def get(self, sandbox_id: str) -> Sandbox | None:
        """根据沙箱 ID 查找对应 :class:`LocalSandbox`。

        Args:
            sandbox_id: 沙箱 ID;支持旧式 ``"local"`` 与按线程的 ``"local:{thread_id}"``。

        Returns:
            命中的沙箱实例,未命中或类型不符时返回 None。
        """
        if sandbox_id == "local":
            with self._lock:
                generic = self._generic_sandbox
            if generic is None:
                self.acquire()
                with self._lock:
                    return self._generic_sandbox
            return generic
        if isinstance(sandbox_id, str) and sandbox_id.startswith("local:"):
            thread_id = sandbox_id[len("local:") :]
            with self._lock:
                cached = self._thread_sandboxes.get(thread_id)
                if cached is not None:
                    # Touching a thread via ``get`` (used by tools.py to look
                    # up the sandbox once per tool call) promotes it in LRU
                    # order so an active thread isn't evicted under load.
                    self._thread_sandboxes.move_to_end(thread_id)
                return cached
        return None

    def release(self, sandbox_id: str) -> None:
        """释放沙箱(本地沙箱无外部资源,这里保留缓存条目)。"""
        # LocalSandbox has no resources to release; keep the cached instance so
        # that ``_agent_written_paths`` (used to reverse-resolve agent-authored
        # file contents on read) survives between turns. LRU eviction in
        # ``acquire`` and explicit ``reset()`` / ``shutdown()`` are the only
        # paths that drop cached entries.
        #
        # Note: This method is intentionally not called by SandboxMiddleware
        # to allow sandbox reuse across multiple turns in a thread.
        pass

    def reset(self) -> None:
        """清空所有缓存的 :class:`LocalSandbox` 实例。

        :func:`reset_sandbox_provider` 会调用本方法以确保配置/挂载变更在下次
        ``acquire()`` 时生效。同时会重置模块级 ``_singleton`` 别名,让访问它的
        旧调用方/测试看到全新状态。
        """
        global _singleton
        with self._lock:
            self._generic_sandbox = None
            self._thread_sandboxes.clear()
            _singleton = None

    def shutdown(self) -> None:
        """关闭提供者。本地沙箱无额外资源,行为同 :meth:`reset`。"""
        # LocalSandboxProvider has no extra resources beyond the cached
        # ``LocalSandbox`` instances, so shutdown uses the same cleanup path
        # as ``reset``.
        self.reset()
