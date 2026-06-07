"""沙箱供应后端的抽象基类与就绪轮询工具。

该模块定义了 :class:`SandboxBackend` 抽象基类,以及
:func:`wait_for_sandbox_ready` / :func:`wait_for_sandbox_ready_async`
两个用于在新沙箱创建后等待其 HTTP 健康端点就绪的工具函数。
具体实现见 :mod:`deerflow.community.aio_sandbox.local_backend` 与
:mod:`deerflow.community.aio_sandbox.remote_backend`。
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

import httpx
import requests

from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def wait_for_sandbox_ready(sandbox_url: str, timeout: int = 30) -> bool:
    """轮询沙箱健康端点,直到就绪或超时。

    Args:
        sandbox_url: 沙箱 URL,例如 ``http://k3s:30001``。
        timeout: 最长等待秒数,默认 30。

    Returns:
        沙箱在超时前就绪返回 True,否则 False。
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{sandbox_url}/v1/sandbox", timeout=5)
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


async def wait_for_sandbox_ready_async(sandbox_url: str, timeout: int = 30, poll_interval: float = 1.0) -> bool:
    """异步版本的沙箱就绪轮询。

    供异步运行时路径使用,使沙箱启动等待不会阻塞事件循环。同步的
    :func:`wait_for_sandbox_ready` 仍然保留,供既有同步后端/提供者调用点使用。

    Args:
        sandbox_url: 沙箱 URL。
        timeout: 最长等待秒数,默认 30。
        poll_interval: 两次轮询的最小间隔,默认 1.0。

    Returns:
        沙箱在超时前就绪返回 True,否则 False。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                response = await client.get(f"{sandbox_url}/v1/sandbox", timeout=min(5.0, remaining))
                if response.status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
    return False


class SandboxBackend(ABC):
    """沙箱供应后端抽象基类。

    现有两种实现:
    - :class:`LocalContainerBackend`:在本地启动 Docker/Apple Container 并管理端口。
    - :class:`RemoteSandboxBackend`:连接到已存在的 URL(K8s 服务、外部服务等)。
    """

    @abstractmethod
    def create(self, thread_id: str | None, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """创建/供应一个新沙箱。

        Args:
            thread_id: 正在创建沙箱的线程 ID;按线程组织沙箱的后端可使用。
            sandbox_id: 确定性沙箱标识。
            extra_mounts: 额外卷挂载,``(host_path, container_path, read_only)`` 三元组列表;
                不管理容器的后端(如远程)可以忽略。

        Returns:
            含连接详情的 :class:`SandboxInfo`。
        """
        ...

    @abstractmethod
    def destroy(self, info: SandboxInfo) -> None:
        """销毁/清理沙箱并释放其资源。

        Args:
            info: 待销毁的沙箱元数据。
        """
        ...

    @abstractmethod
    def is_alive(self, info: SandboxInfo) -> bool:
        """轻量级存活检查,不应做完整健康检查。

        Args:
            info: 待检查的沙箱元数据。

        Returns:
            沙箱看似存活时返回 True。
        """
        ...

    @abstractmethod
    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """按确定性 ID 查找已存在的沙箱,用于跨进程恢复。

        Args:
            sandbox_id: 确定性沙箱 ID。

        Returns:
            找到且健康时返回 :class:`SandboxInfo`,否则 None。
        """
        ...

    def list_running(self) -> list[SandboxInfo]:
        """枚举该后端管理的所有运行中沙箱。

        用于启动协调:进程重启时需要发现由旧进程启动的容器,以便被并入热池
        或在空闲过长时销毁。

        默认实现返回空列表,这对不管理本地容器的后端是正确的(例如
        :class:`RemoteSandboxBackend` 把生命周期交给 provisioner 处理)。

        Returns:
            所有运行中沙箱的 :class:`SandboxInfo` 列表。
        """
        return []
