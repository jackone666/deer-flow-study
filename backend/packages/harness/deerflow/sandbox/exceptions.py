"""Sandbox 相关的结构化异常类型。

所有异常都继承自 :class:`SandboxError`,并通过 ``details`` 字段携带上下文信息,
便于上层在日志与 API 响应中做差异化处理。
"""


class SandboxError(Exception):
    """所有 Sandbox 相关异常的基类。

    Attributes:
        message: 人类可读的错误描述。
        details: 附带结构化上下文字典,默认空字典。
    """

    def __init__(self, message: str, details: dict | None = None):
        """初始化异常。

        Args:
            message: 错误描述信息。
            details: 附加上下文字典,可选;为 None 时取空字典。
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """返回带细节信息的字符串表示。

        Returns:
            当 ``details`` 非空时,返回 ``"message (k=v, ...)"`` 形式;否则仅返回 message。
        """
        if self.details:
            detail_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({detail_str})"
        return self.message


class SandboxNotFoundError(SandboxError):
    """无法找到或不可用指定的 sandbox 时抛出。"""

    def __init__(self, message: str = "Sandbox not found", sandbox_id: str | None = None):
        """初始化异常。

        Args:
            message: 错误描述,默认为 ``"Sandbox not found"``。
            sandbox_id: 出问题的 sandbox 标识,可为 None。
        """
        details = {"sandbox_id": sandbox_id} if sandbox_id else None
        super().__init__(message, details)
        self.sandbox_id = sandbox_id


class SandboxRuntimeError(SandboxError):
    """Sandbox 运行时不可用或配置错误时抛出。"""

    pass


class SandboxCommandError(SandboxError):
    """Sandbox 内执行命令失败时抛出。"""

    def __init__(self, message: str, command: str | None = None, exit_code: int | None = None):
        """初始化异常。

        Args:
            message: 错误描述。
            command: 实际执行的命令字符串,过长时会被截断到 100 字符。
            exit_code: 命令退出码,可为 None。
        """
        details = {}
        if command:
            details["command"] = command[:100] + "..." if len(command) > 100 else command
        if exit_code is not None:
            details["exit_code"] = exit_code
        super().__init__(message, details)
        self.command = command
        self.exit_code = exit_code


class SandboxFileError(SandboxError):
    """Sandbox 中文件操作失败时抛出。"""

    def __init__(self, message: str, path: str | None = None, operation: str | None = None):
        """初始化异常。

        Args:
            message: 错误描述。
            path: 出问题的文件路径,可为 None。
            operation: 文件操作类型(读/写/删 等),可为 None。
        """
        details = {}
        if path:
            details["path"] = path
        if operation:
            details["operation"] = operation
        super().__init__(message, details)
        self.path = path
        self.operation = operation


class SandboxPermissionError(SandboxFileError):
    """文件操作过程中出现权限错误时抛出。"""

    pass


class SandboxFileNotFoundError(SandboxFileError):
    """指定的文件或目录不存在时抛出。"""

    pass
