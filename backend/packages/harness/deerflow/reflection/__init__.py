"""Reflection 子包：动态模块加载工具。

通过 ``module:attr`` 字符串解析出对应的变量或类，供 DeerFlow 的模型、
工具、sandbox、guardrails 等可插拔组件使用统一加载路径。
"""

from .resolvers import resolve_class, resolve_variable

__all__ = ["resolve_class", "resolve_variable"]
