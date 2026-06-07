"""Run 命名辅助函数（用于 LangChain / LangSmith 追踪）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def resolve_root_run_name(config: Mapping[str, Any], assistant_id: str | None) -> str:
    """解析根 Run 的追踪名称。

    依次检查 ``config["context"]`` 与 ``config["configurable"]`` 中的
    ``agent_name`` 字段；只要存在非空字符串就将其作为 Run 名称返回。
    若两者都没有提供，则回退到 ``assistant_id``，最后默认返回
    ``"lead_agent"``。

    Args:
        config: LangGraph ``RunnableConfig`` 映射，包含 ``context``
            和/或 ``configurable`` 容器。
        assistant_id: 助手 ID，作为 agent_name 缺失时的次选回退。

    Returns:
        解析得到的 Run 名称（用于 LangSmith / LangChain 追踪展示）。
    """
    for container_name in ("context", "configurable"):
        container = config.get(container_name)
        if isinstance(container, Mapping):
            agent_name = container.get("agent_name")
            if isinstance(agent_name, str) and agent_name.strip():
                return agent_name
    return assistant_id or "lead_agent"
