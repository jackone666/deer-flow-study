"""Assistant 兼容端点。

    提供与 LangGraph Platform 兼容的 assistants API，基于 ``langgraph.json``/``langgraph_api``
    模式实现。供仍依赖原始 OpenAI Assistants / LangGraph Platform 形态的客户端
    （例如旧版 LangGraph Studio）使用。
"""


from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistants", tags=["assistants-compat"])


class AssistantResponse(BaseModel):
    """LangGraph 兼容的 Assistant 响应模型。"""

    assistant_id: str
    graph_id: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    created_at: str = ""
    updated_at: str = ""
    version: int = 1


class AssistantSearchRequest(BaseModel):
    """Assistant 搜索请求体。"""

    graph_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] | None = None
    limit: int = 10
    offset: int = 0


def _get_default_assistant() -> AssistantResponse:
    """返回默认的 lead_agent assistant。"""

    now = datetime.now(UTC).isoformat()
    return AssistantResponse(
        assistant_id="lead_agent",
        graph_id="lead_agent",
        name="lead_agent",
        config={},
        metadata={"created_by": "system"},
        description="DeerFlow lead agent",
        created_at=now,
        updated_at=now,
        version=1,
    )


def _list_assistants() -> list[AssistantResponse]:
    """从配置中列出所有可用的 assistants。"""

    assistants = [_get_default_assistant()]

    # Also include custom agents from config.yaml agents directory
    try:
        from deerflow.config.agents_config import list_custom_agents

        for agent_cfg in list_custom_agents():
            now = datetime.now(UTC).isoformat()
            assistants.append(
                AssistantResponse(
                    assistant_id=agent_cfg.name,
                    graph_id="lead_agent",  # All agents use the same graph
                    name=agent_cfg.name,
                    config={},
                    metadata={"created_by": "user"},
                    description=agent_cfg.description or "",
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
            )
    except Exception:
        logger.debug("Could not load custom agents for assistants list")

    return assistants


@router.post("/search", response_model=list[AssistantResponse])
async def search_assistants(body: AssistantSearchRequest | None = None) -> list[AssistantResponse]:
    """搜索 assistants。
    
            返回所有已注册的 assistants（lead_agent + 配置中的自定义 agent）。
    """

    assistants = _list_assistants()

    if body and body.graph_id:
        assistants = [a for a in assistants if a.graph_id == body.graph_id]
    if body and body.name:
        assistants = [a for a in assistants if body.name.lower() in a.name.lower()]

    offset = body.offset if body else 0
    limit = body.limit if body else 10
    return assistants[offset : offset + limit]


@router.get("/{assistant_id}", response_model=AssistantResponse)
async def get_assistant_compat(assistant_id: str) -> AssistantResponse:
    """通过 ID 获取 assistant。"""

    for a in _list_assistants():
        if a.assistant_id == assistant_id:
            return a
    raise HTTPException(status_code=404, detail=f"Assistant {assistant_id} not found")


@router.get("/{assistant_id}/graph")
async def get_assistant_graph(assistant_id: str) -> dict:
    """获取 assistant 的图结构。
    
            返回最小的图描述。Gateway 不支持完整的图自省——
            这是一个为兼容性保留的桩。
    """

    found = any(a.assistant_id == assistant_id for a in _list_assistants())
    if not found:
        raise HTTPException(status_code=404, detail=f"Assistant {assistant_id} not found")

    return {
        "graph_id": "lead_agent",
        "nodes": [],
        "edges": [],
    }


@router.get("/{assistant_id}/schemas")
async def get_assistant_schemas(assistant_id: str) -> dict:
    """获取 assistant 的输入/输出/状态 JSON schema。
    
            返回空 schema——Gateway 不支持完整自省。
    """

    found = any(a.assistant_id == assistant_id for a in _list_assistants())
    if not found:
        raise HTTPException(status_code=404, detail=f"Assistant {assistant_id} not found")

    return {
        "graph_id": "lead_agent",
        "input_schema": {},
        "output_schema": {},
        "state_schema": {},
        "config_schema": {},
    }
