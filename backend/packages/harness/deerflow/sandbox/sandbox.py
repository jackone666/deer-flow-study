"""Sandbox 抽象基类定义。

通过 :class:`abc.ABC` 规定所有具体沙箱实现必须提供的统一接口,使得上层 Agent 在
本地/远程等不同执行环境中可以无差别地使用命令执行、文件读写、目录列举等能力。
"""

from abc import ABC, abstractmethod

from deerflow.sandbox.search import GrepMatch


class Sandbox(ABC):
    """沙箱环境的抽象基类。

    Attributes:
        _id: 沙箱唯一标识,在初始化时由具体实现传入。
    """

    _id: str

    def __init__(self, id: str):
        """初始化沙箱。

        Args:
            id: 沙箱唯一标识字符串。
        """
        self._id = id

    @property
    def id(self) -> str:
        """沙箱唯一标识。"""
        return self._id

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """在沙箱内执行 bash 命令。

        Args:
            command: 待执行的命令字符串。

        Returns:
            命令的标准输出或错误输出文本。
        """
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """读取沙箱内文件内容。

        Args:
            path: 待读取文件的绝对路径。

        Returns:
            文件文本内容。
        """
        pass

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """下载沙箱内文件的二进制内容。

        Args:
            path: 待下载文件的绝对路径。

        Returns:
            文件原始字节内容。

        Raises:
            PermissionError: 检测到路径穿越或路径不在允许的虚拟前缀内时抛出。
            OSError: 文件不可读或不存在时抛出,本地与远程实现需统一抛该异常以便调用方统一处理。
        """
        pass

    @abstractmethod
    def list_dir(self, path: str, max_depth=2) -> list[str]:
        """列出目录内容。

        Args:
            path: 待列举目录的绝对路径。
            max_depth: 最大递归深度,默认 2。

        Returns:
            目录内容条目列表。
        """
        pass

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """向沙箱内文件写入内容。

        Args:
            path: 目标文件的绝对路径。
            content: 待写入的文本内容。
            append: 是否以追加模式写入;为 False 时创建或覆盖文件。
        """
        pass

    @abstractmethod
    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """在指定根目录下按 glob 模式查找匹配路径。"""
        pass

    @abstractmethod
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
        """在目录下搜索文本文件内的匹配项。"""
        pass

    @abstractmethod
    def update_file(self, path: str, content: bytes) -> None:
        """以二进制内容更新文件。

        Args:
            path: 目标文件的绝对路径。
            content: 待写入的二进制内容。
        """
        pass
