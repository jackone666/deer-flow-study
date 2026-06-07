"""远程沙箱后端 — 把 Pod 生命周期委托给 provisioner 服务。

provisioner 在 k3s 中为每个沙箱 ID 动态创建 Pod + NodePort Service。
本后端通过 ``k3s:{NodePort}`` 直接访问沙箱 Pod。

架构::

    ┌────────────┐  HTTP   ┌─────────────┐  K8s API  ┌──────────┐
    │ this file  │ ──────▸ │ provisioner │ ────────▸ │   k3s    │
    │ (backend)  │         │ :8002       │           │ :6443    │
    └────────────┘         └─────────────┘           └─────┬────┘
                                                            │ creates
                            ┌─────────────┐           ┌─────▼──────┐
                            │   backend   │ ────────▸ │  sandbox   │
                            │             │  direct   │  Pod(s)    │
                            └─────────────┘ k3s:NPort └────────────┘
"""

from __future__ import annotations

import logging

import requests

from deerflow.runtime.user_context import get_effective_user_id

from .backend import SandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


class RemoteSandboxBackend(SandboxBackend):
    """把沙箱生命周期委托给 provisioner 服务的 :class:`SandboxBackend` 实现。

    所有 Pod 的创建、销毁、发现都交给 provisioner 处理,本后端是
    一个轻量的 HTTP 客户端。

    典型 ``config.yaml`` 配置::

        sandbox:
          use: deerflow.community.aio_sandbox:AioSandboxProvider
          provisioner_url: http://provisioner:8002
    """

    def __init__(self, provisioner_url: str):
        """使用 provisioner 服务 URL 初始化。

        Args:
            provisioner_url: provisioner 服务 URL,例如 ``http://provisioner:8002``。
        """
        self._provisioner_url = provisioner_url.rstrip("/")

    @property
    def provisioner_url(self) -> str:
        """provisioner 服务 URL(去除尾部 ``/``)。"""
        return self._provisioner_url

    # ── SandboxBackend interface ──────────────────────────────────────────

    def create(
        self,
        thread_id: str | None,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        """通过 provisioner 创建沙箱 Pod + Service。

        调用 ``POST /api/sandboxes``,由其在 k3s 中创建专用 Pod +
        NodePort Service。``extra_mounts`` 远程后端不使用,保留以满足
        抽象接口。

        Args:
            thread_id: 沙箱所属的线程 ID。
            sandbox_id: 确定性沙箱 ID。
            extra_mounts: 远程后端忽略该参数。

        Returns:
            SandboxInfo: 包含 ``sandbox_url`` 的元数据。
        """
        return self._provisioner_create(thread_id, sandbox_id, extra_mounts)

    def destroy(self, info: SandboxInfo) -> None:
        """通过 provisioner 销毁沙箱 Pod + Service。"""
        self._provisioner_destroy(info.sandbox_id)

    def is_alive(self, info: SandboxInfo) -> bool:
        """检查沙箱 Pod 是否在运行。"""
        return self._provisioner_is_alive(info.sandbox_id)

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """通过 provisioner 发现已存在的沙箱。

        调用 ``GET /api/sandboxes/{sandbox_id}``,Pod 存在时返回元数据。

        Args:
            sandbox_id: 确定性沙箱 ID。

        Returns:
            找到时返回 :class:`SandboxInfo`,否则 ``None``(包含 404 与网络错误)。
        """
        return self._provisioner_discover(sandbox_id)

    def list_running(self) -> list[SandboxInfo]:
        """返回 provisioner 当前管理的全部沙箱。

        调用 ``GET /api/sandboxes``,以便
        :meth:`AioSandboxProvider._reconcile_orphans` 能接管由旧进程
        创建但从未显式销毁的 Pod。

        如果没有该方法,进程重启后会静默地孤立所有现有 k8s Pod —
        它们会一直保持运行,这是因为空闲回收只跟踪进程内状态。

        Returns:
            当前 provisioner 已知的所有 :class:`SandboxInfo` 列表。
        """
        return self._provisioner_list()

    # ── Provisioner API calls ─────────────────────────────────────────────

    def _provisioner_list(self) -> list[SandboxInfo]:
        """``GET /api/sandboxes`` → 列出所有运行中沙箱。

        对响应体结构进行防御性校验:顶层必须是 dict,``sandboxes``
        字段必须是 list,每条记录必须是 dict,只有同时拥有合法
        ``sandbox_id`` 与 ``sandbox_url`` 字符串的条目才会被纳入。

        Returns:
            解析得到的 :class:`SandboxInfo` 列表;网络错误或响应异常
            时返回空列表,绝不抛错。
        """
        try:
            resp = requests.get(f"{self._provisioner_url}/api/sandboxes", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                logger.warning("Provisioner list_running returned non-dict payload: %r", type(data))
                return []

            sandboxes = data.get("sandboxes", [])
            if not isinstance(sandboxes, list):
                logger.warning("Provisioner list_running returned non-list sandboxes: %r", type(sandboxes))
                return []

            infos: list[SandboxInfo] = []
            for sandbox in sandboxes:
                if not isinstance(sandbox, dict):
                    logger.warning("Provisioner list_running entry is not a dict: %r", type(sandbox))
                    continue

                sandbox_id = sandbox.get("sandbox_id")
                sandbox_url = sandbox.get("sandbox_url")
                if isinstance(sandbox_id, str) and sandbox_id and isinstance(sandbox_url, str) and sandbox_url:
                    infos.append(SandboxInfo(sandbox_id=sandbox_id, sandbox_url=sandbox_url))

            logger.info("Provisioner list_running: %d sandbox(es) found", len(infos))
            return infos
        except requests.RequestException as exc:
            logger.warning("Provisioner list_running failed: %s", exc)
            return []

    def _provisioner_create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """``POST /api/sandboxes`` → 创建 Pod + Service。

        请求体会附带当前用户的 ``user_id``(来自
        :func:`deerflow.runtime.user_context.get_effective_user_id`),
        便于 provisioner 在多租户场景下做归属/审计。

        Args:
            thread_id: 沙箱所属的线程 ID(传递给 provisioner)。
            sandbox_id: 确定性沙箱 ID。
            extra_mounts: 远程后端不使用。

        Returns:
            SandboxInfo: 包含 provisioner 返回的 ``sandbox_url``。

        Raises:
            RuntimeError: provisioner 调用失败(包装自 ``RequestException``)。
        """
        try:
            resp = requests.post(
                f"{self._provisioner_url}/api/sandboxes",
                json={
                    "sandbox_id": sandbox_id,
                    "thread_id": thread_id,
                    "user_id": get_effective_user_id(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Provisioner created sandbox {sandbox_id}: sandbox_url={data['sandbox_url']}")
            return SandboxInfo(
                sandbox_id=sandbox_id,
                sandbox_url=data["sandbox_url"],
            )
        except requests.RequestException as exc:
            logger.error(f"Provisioner create failed for {sandbox_id}: {exc}")
            raise RuntimeError(f"Provisioner create failed: {exc}") from exc

    def _provisioner_destroy(self, sandbox_id: str) -> None:
        """``DELETE /api/sandboxes/{sandbox_id}`` → 销毁 Pod + Service。

        不会因为 HTTP 状态码或网络错误而抛错:把任何失败降级为
        ``warning`` 日志,以免阻塞 provider 的清理流程。
        """
        try:
            resp = requests.delete(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=15,
            )
            if resp.ok:
                logger.info(f"Provisioner destroyed sandbox {sandbox_id}")
            else:
                logger.warning(f"Provisioner destroy returned {resp.status_code}: {resp.text}")
        except requests.RequestException as exc:
            logger.warning(f"Provisioner destroy failed for {sandbox_id}: {exc}")

    def _provisioner_is_alive(self, sandbox_id: str) -> bool:
        """``GET /api/sandboxes/{sandbox_id}`` → 检查 Pod phase。

        通过响应体中的 ``status`` 字段判断,只有 ``"Running"`` 才视为存活。
        """
        try:
            resp = requests.get(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                return data.get("status") == "Running"
            return False
        except requests.RequestException:
            return False

    def _provisioner_discover(self, sandbox_id: str) -> SandboxInfo | None:
        """``GET /api/sandboxes/{sandbox_id}`` → 发现已存在的沙箱。

        返回 404 时返回 ``None``(不是错误);其他网络错误也降级为
        ``None`` 并打 ``debug`` 日志。
        """
        try:
            resp = requests.get(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=10,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return SandboxInfo(
                sandbox_id=sandbox_id,
                sandbox_url=data["sandbox_url"],
            )
        except requests.RequestException as exc:
            logger.debug(f"Provisioner discover failed for {sandbox_id}: {exc}")
            return None
            resp.raise_for_status()
            data = resp.json()
            return SandboxInfo(
                sandbox_id=sandbox_id,
                sandbox_url=data["sandbox_url"],
            )
        except requests.RequestException as exc:
            logger.debug(f"Provisioner discover failed for {sandbox_id}: {exc}")
            return None
