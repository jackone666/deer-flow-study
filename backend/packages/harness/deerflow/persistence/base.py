"""SQLAlchemy 声明式基类，附带自动 ``to_dict`` 支持。

所有 DeerFlow ORM 模型都继承自该 :class:`Base`。它通过 SQLAlchemy
的 ``inspect()`` 提供通用的 ``to_dict()`` 方法，使各模型无需自行编写
序列化逻辑。

LangGraph 的 checkpointer 表 **不** 由该 Base 管理。
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 DeerFlow ORM 模型的基类。

    提供：
    - 通过 SQLAlchemy 列检查自动生成 ``to_dict()``。
    - 标准 ``__repr__()``，展示所有列值。
    """

    def to_dict(self, *, exclude: set[str] | None = None) -> dict:
        """将 ORM 实例转换为普通 dict。

        使用 SQLAlchemy 的 ``inspect()`` 遍历已映射的列属性。

        Args:
            exclude: 可选，要从结果中排除的列键集合。

        Returns:
            dict: 形如 ``{column_key: value}`` 的列键值对。
        """
        exclude = exclude or set()
        return {c.key: getattr(self, c.key) for c in sa_inspect(type(self)).mapper.column_attrs if c.key not in exclude}

    def __repr__(self) -> str:
        """展示所有映射列的 ``key=value`` 形式。"""
        cols = ", ".join(f"{c.key}={getattr(self, c.key)!r}" for c in sa_inspect(type(self)).mapper.column_attrs)
        return f"{type(self).__name__}({cols})"
