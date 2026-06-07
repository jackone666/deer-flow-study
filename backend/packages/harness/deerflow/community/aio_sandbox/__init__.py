"""AIO 沙箱子包:基于 AIO sandbox 的执行环境。

本子包提供与 :mod:`deerflow.sandbox` 一致的抽象,并额外支持通过本地 Docker
或远程 K8s 等后端创建有状态的 AIO 沙箱。
"""

from .aio_sandbox import AioSandbox
from .aio_sandbox_provider import AioSandboxProvider
from .backend import SandboxBackend
from .local_backend import LocalContainerBackend
from .remote_backend import RemoteSandboxBackend
from .sandbox_info import SandboxInfo

__all__ = [
    "AioSandbox",
    "AioSandboxProvider",
    "LocalContainerBackend",
    "RemoteSandboxBackend",
    "SandboxBackend",
    "SandboxInfo",
]
