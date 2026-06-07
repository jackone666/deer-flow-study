"""ISO 8601 时间戳辅助工具，供 Gateway 与嵌入式运行时使用。

DeerFlow 将 thread/run 时间戳以 ISO 8601 UTC 字符串的形式进行存储和序列化，
以匹配 LangGraph Platform 的 schema（参见
``langgraph_sdk.schema.Thread``，其中 ``created_at`` / ``updated_at``
均为 ``datetime``，在 JSON 编码时会以 ISO 8601 输出）。所有时间戳的生成
都应通过 :func:`now_iso` 走统一入口，从而保证各端点、嵌入式 ``RunManager``
以及 Gateway 写入的 checkpoint 元数据在传输格式上保持一致。

:func:`coerce_iso` 提供向前兼容的读路径，用于将旧版本中以
``str(time.time())`` 浮点字符串形式存储的历史记录转换为 ISO 格式，
无需一次性迁移脚本。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

__all__ = ["coerce_iso", "now_iso"]

_UNIX_TIMESTAMP_PATTERN = re.compile(r"^\d{10}(?:\.\d+)?$")
"""匹配 ``str(time.time())`` 历史输出形式的 Unix 时间戳字符串
（10 位秒级数字加可选的小数部分）。10 位锚定既可避免误把 ISO 年份
（如 ``"2026"``）当作 Unix 时间戳，又可在 2286 年之前保持有效。
"""


def now_iso() -> str:
    """以 ISO 8601 字符串形式返回当前 UTC 时间。

    返回:
        ISO 8601 格式的当前 UTC 时间字符串。

    示例:
        ``"2026-04-27T03:19:46.511479+00:00"``。
    """
    return datetime.now(UTC).isoformat()


def coerce_iso(value: object) -> str:
    """尽最大努力将已存储的时间戳强制转换为 ISO 8601 字符串。

    该函数用于把旧版 DeerFlow 写入的 Unix 时间戳浮点数/字符串转换为
    ISO 字符串，无需一次性迁移。规则如下：

    - ISO 字符串原样返回；
    - ``datetime`` 实例会归一化为 UTC（无时区信息时视为 UTC）并通过
      ``isoformat()`` 输出，保证传输格式始终使用 ``T`` 分隔符；
    - 空值返回 ``""``；
    - 未能识别的值最终退化为 ``str(value)``。

    Args:
        value: 待转换的时间戳值，可以是 ``datetime``、数字、字符串或任意对象。

    Returns:
        ISO 8601 格式的字符串；无法解析时回退为 ``str(value)``。
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — treat as garbage, not 0/1.
        return str(value)
    if isinstance(value, datetime):
        # ``datetime`` must be handled before the ``int``/``float`` check;
        # str(datetime) would produce ``"YYYY-MM-DD HH:MM:SS+00:00"``
        # (space separator), which breaks strict ISO 8601 consumers.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            return str(value)
    if isinstance(value, str):
        if _UNIX_TIMESTAMP_PATTERN.match(value):
            try:
                return datetime.fromtimestamp(float(value), UTC).isoformat()
            except (ValueError, OverflowError, OSError):
                return value
        return value
    return str(value)
