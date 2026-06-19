"""Lead Agent 子包。

``make_lead_agent(config)`` 是 DeerFlow Agent 的唯一入口工厂，由 ``langgraph.json`` 注册：

```json
{"graphs": {"agent": "./packages/harness/deerflow/agents/lead_agent/agent.py:make_lead_agent"}}
```

调用链：
```
Gateway POST /api/threads/{id}/runs/stream
  → RunManager.create() → run_agent() (worker.py)
    → make_lead_agent(config)
      → _make_lead_agent(config, app_config=...)
        → _resolve_model_name()            ← 模型选择（含回退）
        → create_chat_model()              ← LLM 实例化（含 thinking/vision）
        → get_available_tools()            ← 工具集合（sandbox + MCP + community + subagent）
        → _build_middlewares()             ← 19 个中间件链
        → apply_prompt_template()          ← 系统提示渲染（含 skills/memory/subagent）
        → create_agent(model, tools, middleware, system_prompt, state_schema)
```

子模块：
- ``agent.py`` — ``make_lead_agent`` / ``_make_lead_agent`` 工厂 + 中间件链组装
- ``prompt.py`` — 系统提示模板 + 技能缓存管理 + 子代理提示片段生成
"""

from .agent import make_lead_agent

__all__ = ["make_lead_agent"]
