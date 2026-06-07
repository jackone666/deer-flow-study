"""``create_deerflow_agent`` 的声明式特性开关与中间件定位装饰器。

仅包含纯数据类与装饰器——不进行 I/O，不产生副作用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain.agents.middleware import AgentMiddleware


@dataclass
class RuntimeFeatures:
    """``create_deerflow_agent`` 的声明式特性开关集合。

    大部分特性接受以下取值：
    - ``True``：使用内建默认中间件。
    - ``False``：禁用该特性。
    - ``AgentMiddleware`` 实例：使用自定义实现替换内建默认。

    ``summarization`` 和 ``guardrail`` 没有内建默认实现——它们仅接受
    ``False``（禁用）或 ``AgentMiddleware`` 实例（自定义）。
    """

    sandbox: bool | AgentMiddleware = True
    memory: bool | AgentMiddleware = False
    summarization: Literal[False] | AgentMiddleware = False
    subagent: bool | AgentMiddleware = False
    vision: bool | AgentMiddleware = False
    auto_title: bool | AgentMiddleware = False
    guardrail: Literal[False] | AgentMiddleware = False
    loop_detection: bool | AgentMiddleware = True


# ---------------------------------------------------------------------------
# Middleware positioning decorators
# ---------------------------------------------------------------------------


def Next(anchor: type[AgentMiddleware]):
    """声明该中间件应放在链中 *anchor* 之后。"""
    if not (isinstance(anchor, type) and issubclass(anchor, AgentMiddleware)):
        raise TypeError(f"@Next expects an AgentMiddleware subclass, got {anchor!r}")

    def decorator(cls: type[AgentMiddleware]) -> type[AgentMiddleware]:
        """执行赋值。
        
                Args:
                    cls: type[AgentMiddleware]: 参数说明。
        
                Returns:
                    type[AgentMiddleware]。
        """
        cls._next_anchor = anchor  # type: ignore[attr-defined]
        return cls

    return decorator


def Prev(anchor: type[AgentMiddleware]):
    """声明该中间件应放在链中 *anchor* 之前。"""
    if not (isinstance(anchor, type) and issubclass(anchor, AgentMiddleware)):
        raise TypeError(f"@Prev expects an AgentMiddleware subclass, got {anchor!r}")

    def decorator(cls: type[AgentMiddleware]) -> type[AgentMiddleware]:
        """执行赋值。
        
                Args:
                    cls: type[AgentMiddleware]: 参数说明。
        
                Returns:
                    type[AgentMiddleware]。
        """
        cls._prev_anchor = anchor  # type: ignore[attr-defined]
        return cls

    return decorator
