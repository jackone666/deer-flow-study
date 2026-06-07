"""Sandbox 子包:封装代码执行沙箱与沙箱提供者。

本子包对外暴露 :class:`Sandbox`、:class:`SandboxProvider` 抽象与
:func:`get_sandbox_provider` 全局访问入口,是 Agent 在受控环境中执行命令、读写文件
等操作的能力底座。
"""

from .sandbox import Sandbox
from .sandbox_provider import SandboxProvider, get_sandbox_provider

__all__ = [
    "Sandbox",
    "SandboxProvider",
    "get_sandbox_provider",
]
