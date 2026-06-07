"""为 SQLAlchemy（SQLite + PostgreSQL）提供方言感知的 JSON 值匹配。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import BigInteger, Float, String, bindparam
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import Boolean, TypeEngine

# key 会内插到编译出的 SQL 中；限制字符集以防注入。
_KEY_CHARSET_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# 元数据 filter value 允许的类型集合（与 JsonMatch 接受的范围一致）。
ALLOWED_FILTER_VALUE_TYPES: tuple[type, ...] = (type(None), bool, int, float, str)

# SQLite 在绑定超出 signed 64-bit 范围的值时会抛 overflow；
# PostgreSQL 在 BIGINT 强转时也会 overflow。统一在校验阶段拒绝。
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def validate_metadata_filter_key(key: object) -> bool:
    """判断 *key* 是否能安全用作 JSON 元数据 filter 的键。

    当 key 是匹配 ``[A-Za-z0-9_-]+`` 的字符串时为 ``True``。限定字符集
    是因为 key 会被内插到编译出的 SQL path 表达式（``$."<key>"`` /
    ``->`` 字面量），更宽松的模式会打开 SQL/JSONPath 注入面。

    Args:
        key: 任意候选键。

    Returns:
        bool: 安全时为 ``True``。
    """
    return isinstance(key, str) and bool(_KEY_CHARSET_RE.match(key))


def validate_metadata_filter_value(value: object) -> bool:
    """判断 *value* 是否是 JSON 元数据 filter 接受的类型。

    匹配 :func:`_build_clause` 能编译为方言无关谓词的集合。其他类型
    （list / dict / bytes / ...）刻意拒绝，而不是通过 ``str()`` 静默
    强转——静默强转会 (a) 产生错误匹配；(b) 在 ``value`` 不可哈希时
    破坏 SQLAlchemy 的 ``inherit_cache`` 不变式。

    整型值额外限制在 signed 64-bit 范围 ``[-2**63, 2**63 - 1]``：SQLite
    绑定更大值时会 overflow，PostgreSQL 在 ``BIGINT`` 强转时也会。

    Args:
        value: 任意候选值。

    Returns:
        bool: 类型与范围都合规时为 ``True``。
    """
    if not isinstance(value, ALLOWED_FILTER_VALUE_TYPES):
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        if not (_INT64_MIN <= value <= _INT64_MAX):
            return False
    return True


class JsonMatch(ColumnElement):
    """针对 JSON 列的方言无关 ``column[key] == value`` 表达式。

    在 SQLite 上编译为 ``json_type`` / ``json_extract``，在 PostgreSQL
    上编译为 ``json_typeof`` / ``->>``，比较时区分 bool vs int
    以及 NULL vs 缺失键。

    *key* 必须是匹配 ``[A-Za-z0-9_-]+`` 的字面键。
    *value* 必须是 ``None`` / ``bool`` / ``int``（signed 64-bit） / ``float`` / ``str`` 之一。
    """

    inherit_cache = True
    type = Boolean()
    _is_implicitly_boolean = True

    _traverse_internals = [
        ("column", InternalTraversal.dp_clauseelement),
        ("key", InternalTraversal.dp_string),
        ("value", InternalTraversal.dp_plain_obj),
    ]

    def __init__(self, column: ColumnElement, key: str, value: object) -> None:
        """构造 :class:`JsonMatch`，并校验 key/value 合法性。"""
        if not validate_metadata_filter_key(key):
            raise ValueError(f"JsonMatch key must match {_KEY_CHARSET_RE.pattern!r}; got: {key!r}")
        if not validate_metadata_filter_value(value):
            if isinstance(value, int) and not isinstance(value, bool):
                raise TypeError(f"JsonMatch int value out of signed 64-bit range [-2**63, 2**63-1]: {value!r}")
            raise TypeError(f"JsonMatch value must be None, bool, int, float, or str; got: {type(value).__name__!r}")
        self.column = column
        self.key = key
        self.value = value
        super().__init__()


@dataclass(frozen=True)
class _Dialect:
    """生成 JSON 类型/值比较时使用的各 dialect 名称集合。"""

    null_type: str
    num_types: tuple[str, ...]
    num_cast: str
    int_types: tuple[str, ...]
    int_cast: str
    # SQLite 上 ``json_type`` 已经返回 'integer' / 'real'，这里为 None；
    # PostgreSQL 上 ``json_typeof`` 对 int 和 float 都返回 'number'，
    # 因此需要额外 regex 守卫以避免对 float 做 CAST 时报错。
    int_guard: str | None
    string_type: str
    bool_type: str | None


_SQLITE = _Dialect(
    null_type="null",
    num_types=("integer", "real"),
    num_cast="REAL",
    int_types=("integer",),
    int_cast="INTEGER",
    int_guard=None,
    string_type="text",
    bool_type=None,
)

_PG = _Dialect(
    null_type="null",
    num_types=("number",),
    num_cast="DOUBLE PRECISION",
    int_types=("number",),
    int_cast="BIGINT",
    int_guard="'^-?[0-9]+$'",
    string_type="string",
    bool_type="boolean",
)


def _bind(compiler: SQLCompiler, value: object, sa_type: TypeEngine[Any], **kw: Any) -> str:
    """把 ``value`` 绑定为 SQLAlchemy 参数，并返回渲染后的 SQL 字面量。"""
    param = bindparam(None, value, type_=sa_type)
    return compiler.process(param, **kw)


def _type_check(typeof: str, types: tuple[str, ...]) -> str:
    """生成 ``typeof = 'X'`` 或 ``typeof IN ('X','Y')`` 形式的 SQL 片段。"""
    if len(types) == 1:
        return f"{typeof} = '{types[0]}'"
    quoted = ", ".join(f"'{t}'" for t in types)
    return f"{typeof} IN ({quoted})"


def _build_clause(compiler: SQLCompiler, typeof: str, extract: str, value: object, dialect: _Dialect, **kw: Any) -> str:
    """为给定的 ``value`` 构造方言无关的 JSON 匹配 SQL 片段。"""
    if value is None:
        return f"{typeof} = '{dialect.null_type}'"
    if isinstance(value, bool):
        # bool 判断必须先于 int——Python 中 bool 是 int 的子类
        bool_str = "true" if value else "false"
        if dialect.bool_type is None:
            return f"{typeof} = '{bool_str}'"
        return f"({typeof} = '{dialect.bool_type}' AND {extract} = '{bool_str}')"
    if isinstance(value, int):
        bp = _bind(compiler, value, BigInteger(), **kw)
        if dialect.int_guard:
            # CASE 防止当 json_typeof='number' 时对 float 强转报错
            return f"(CASE WHEN {_type_check(typeof, dialect.int_types)} AND {extract} ~ {dialect.int_guard} THEN CAST({extract} AS {dialect.int_cast}) END = {bp})"
        return f"({_type_check(typeof, dialect.int_types)} AND CAST({extract} AS {dialect.int_cast}) = {bp})"
    if isinstance(value, float):
        bp = _bind(compiler, value, Float(), **kw)
        return f"({_type_check(typeof, dialect.num_types)} AND CAST({extract} AS {dialect.num_cast}) = {bp})"
    bp = _bind(compiler, str(value), String(), **kw)
    return f"({typeof} = '{dialect.string_type}' AND {extract} = {bp})"


@compiles(JsonMatch, "sqlite")
def _compile_sqlite(element: JsonMatch, compiler: SQLCompiler, **kw: Any) -> str:
    """把 :class:`JsonMatch` 编译为 SQLite 方言的 SQL。"""
    if not validate_metadata_filter_key(element.key):
        raise ValueError(f"Key escaped validation: {element.key!r}")
    col = compiler.process(element.column, **kw)
    path = f'$."{element.key}"'
    typeof = f"json_type({col}, '{path}')"
    extract = f"json_extract({col}, '{path}')"
    return _build_clause(compiler, typeof, extract, element.value, _SQLITE, **kw)


@compiles(JsonMatch, "postgresql")
def _compile_pg(element: JsonMatch, compiler: SQLCompiler, **kw: Any) -> str:
    """把 :class:`JsonMatch` 编译为 PostgreSQL 方言的 SQL。"""
    if not validate_metadata_filter_key(element.key):
        raise ValueError(f"Key escaped validation: {element.key!r}")
    col = compiler.process(element.column, **kw)
    typeof = f"json_typeof({col} -> '{element.key}')"
    extract = f"({col} ->> '{element.key}')"
    return _build_clause(compiler, typeof, extract, element.value, _PG, **kw)


@compiles(JsonMatch)
def _compile_default(element: JsonMatch, compiler: SQLCompiler, **kw: Any) -> str:
    """未知 dialect 的兜底编译器：显式报错。"""
    raise NotImplementedError(f"JsonMatch supports only sqlite and postgresql; got dialect: {compiler.dialect.name}")


def json_match(column: ColumnElement, key: str, value: object) -> JsonMatch:
    """便捷构造 :class:`JsonMatch` 的工厂函数。"""
    return JsonMatch(column, key, value)
