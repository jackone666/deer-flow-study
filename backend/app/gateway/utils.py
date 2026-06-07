"""Gateway 层的共享工具函数。"""


def sanitize_log_param(value: str) -> str:
    """去除控制字符以防止日志注入。

    Args:
        value: 原始字符串。

    Returns:
        去除换行、回车与 NUL 后的安全字符串。
    """
    return value.replace("\n", "").replace("\r", "").replace("\x00", "")
