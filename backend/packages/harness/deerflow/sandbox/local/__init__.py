"""本地沙箱子包:把本机文件系统视作沙箱,便于本地调试与单机部署。

导出 :class:`LocalSandboxProvider` 作为沙箱提供者的本地实现。
"""

from .local_sandbox_provider import LocalSandboxProvider

__all__ = ["LocalSandboxProvider"]
