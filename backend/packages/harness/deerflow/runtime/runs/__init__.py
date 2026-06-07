"""Run 生命周期管理——与 LangGraph Platform API 兼容。

提供 Run 创建、取消、查询、聚合、后台执行等核心能力，封装 ``RunManager``
（内存注册表 + 可选持久化）、``RunRecord``（运行期可变记录）、
``RunJournal`` 回调处理器（事件采集与 token 累计）以及 ``run_agent``
（后台协程入口）。
"""

from .manager import ConflictError, RunManager, RunRecord, UnsupportedStrategyError
from .schemas import DisconnectMode, RunStatus
from .worker import RunContext, run_agent

__all__ = [
    "ConflictError",
    "DisconnectMode",
    "RunContext",
    "RunManager",
    "RunRecord",
    "RunStatus",
    "UnsupportedStrategyError",
    "run_agent",
]
