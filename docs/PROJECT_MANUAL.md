# 🦌 DeerFlow 2.0 — 项目说明书

> **版本**: 2.0 | **许可证**: MIT | **仓库**: [bytedance/deer-flow](https://github.com/bytedance/deer-flow)

---

## 一、项目概述

### 1.1 什么是 DeerFlow？

DeerFlow（**D**eep **E**xploration and **E**fficient **R**esearch **Flow）是字节跳动开源的一个**超级智能体框架（Super Agent Harness）**。它通过编排**子智能体（Sub-Agents）**、**持久化记忆（Memory）**和**沙箱执行环境（Sandbox）**，实现对复杂任务的自主完成——从深度研究、数据分析到代码生成、PPT 制作，几乎无所不能。

DeerFlow 2.0 是从零重写的全新版本，与 v1 完全独立。v1 作为"深度研究框架"维护在 [`1.x` 分支](https://github.com/bytedance/deer-flow/tree/main-1.x)上。

### 1.2 核心定位

| 维度 | 描述 |
|------|------|
| **类型** | AI 超级智能体框架 / 智能体编排平台 |
| **语言** | Python 3.12+ (后端) + TypeScript (前端) |
| **框架** | LangGraph + LangChain + FastAPI + Next.js 16 |
| **部署** | Docker / 本地开发 / 生产环境 |
| **特色** | 子智能体并行编排、沙箱隔离执行、持久化记忆、可扩展技能系统 |

### 1.3 与同类项目对比

| 特性 | DeerFlow | LangGraph 裸框架 | AutoGPT | CrewAI |
|------|----------|-----------------|---------|--------|
| 开箱即用的 Web UI | ✅ | ❌ | ❌ | ❌ |
| 子智能体并行编排 | ✅ | 需手动实现 | 有限 | ✅ |
| 沙箱隔离执行 | ✅ (本地+Docker) | ❌ | Docker | ❌ |
| 持久化记忆系统 | ✅ (自动提取) | ❌ | 有限 | 有限 |
| MCP 工具集成 | ✅ | 需手动集成 | ❌ | ❌ |
| IM 渠道接入 | ✅ (飞书/Slack/Telegram/钉钉) | ❌ | ❌ | ❌ |
| 技能市场 | ✅ (21+ 内置技能) | ❌ | ❌ | ❌ |
| 多语言 README | ✅ (中/英/日/法/俄) | N/A | N/A | N/A |

---

## 二、架构设计

### 2.1 整体架构

```
                         ┌──────────────────────────────────────┐
                         │          Nginx (端口 2026)            │
                         │         统一反向代理入口               │
                         └───────┬──────────────────┬───────────┘
                                 │                  │
              /api/langgraph/*   │     /api/*       │      /*
              (重写为 /api/*)    │  (其他 API)      │  (非 API)
                                 ▼                  ▼           ▼
                ┌────────────────────────────────────────┐  ┌──────────┐
                │        Gateway API (8001)              │  │ Frontend │
                │        FastAPI + Agent 运行时          │  │ Next.js  │
                │                                        │  │ (3000)   │
                │ 模型 · MCP · 技能 · 记忆 · 上传 ·      │  └──────────┘
                │ 制品 · 线程 · 运行 · 流式传输          │
                │                                        │
                │ ┌────────────────────────────────────┐ │
                │ │ Lead Agent (主智能体)               │ │
                │ │ 中间件链 · 工具 · 子智能体          │ │
                │ └────────────────────────────────────┘ │
                └────────────────────────────────────────┘
```

### 2.2 分层架构（Harness / App 分离）

```
backend/
├── packages/harness/deerflow/   ← Harness 层（可发布的智能体框架包）
│   ├── agents/                  ← 智能体系统
│   ├── sandbox/                 ← 沙箱执行
│   ├── subagents/               ← 子智能体编排
│   ├── tools/                   ← 工具系统
│   ├── mcp/                     ← MCP 集成
│   ├── models/                  ← 模型工厂
│   ├── skills/                  ← 技能系统
│   ├── memory/                  ← 记忆系统
│   ├── tracing/                 ← 追踪系统
│   ├── config/                  ← 配置系统
│   ├── runtime/                 ← 运行时
│   ├── community/               ← 社区工具
│   ├── guardrails/              ← 护栏系统
│   ├── reflection/              ← 动态模块加载
│   └── client.py                ← 嵌入式 Python 客户端
│
└── app/                         ← App 层（应用代码）
    ├── gateway/                 ← FastAPI REST API
    │   ├── app.py              ← 应用入口
    │   └── routers/            ← 9 个路由模块
    └── channels/                ← IM 渠道集成
        ├── feishu.py           ← 飞书
        ├── slack.py            ← Slack
        ├── telegram.py         ← Telegram
        └── dingtalk.py         ← 钉钉
```

**依赖规则**: `app → deerflow`（单向），`deerflow` 绝不引用 `app`。由 CI 测试 `test_harness_boundary.py` 强制执行。

---

## 三、核心功能详解

### 3.1 子智能体系统（Sub-Agent System）

DeerFlow 的核心能力之一。主智能体（Lead Agent）可以将复杂任务拆解后委派给多个子智能体**并行执行**。

| 特性 | 说明 |
|------|------|
| 内置类型 | `general-purpose`（全工具集）、`bash`（命令专家） |
| 并发上限 | 每轮最多 3 个子智能体 |
| 超时 | 15 分钟 |
| 执行方式 | 后台线程池，支持 SSE 事件流 |
| 触发方式 | 主智能体调用 `task()` 工具 |

### 3.2 沙箱执行系统（Sandbox System）

每个会话（Thread）拥有独立的沙箱执行环境，确保代码执行安全隔离。

| 提供者 | 说明 |
|--------|------|
| `LocalSandboxProvider` | 本地文件系统隔离，虚拟路径映射 |
| `AioSandboxProvider` | Docker 容器隔离（社区版） |

**虚拟路径映射**:
- `/mnt/user-data/workspace` → 线程专属工作目录
- `/mnt/user-data/uploads` → 上传文件目录
- `/mnt/user-data/outputs` → 输出制品目录
- `/mnt/skills` → 技能目录

**沙箱工具**: `bash`、`ls`、`read_file`、`write_file`、`str_replace`

### 3.3 持久化记忆系统（Memory System）

跨会话保留用户上下文和偏好，让 AI 越来越"懂你"。

```
MemoryMiddleware → 过滤消息 → 队列去重(30s) → LLM 提取 → 原子写入 → 下次注入
```

| 特性 | 说明 |
|------|------|
| 自动提取 | LLM 分析对话，提取用户上下文、偏好、知识 |
| 结构化存储 | `workContext`、`personalContext`、`topOfMind`、`facts` |
| 置信度评分 | 每个事实附带 0-1 置信度 |
| 注入策略 | 注入 top 15 事实 + 用户上下文到系统提示词 |
| 存储格式 | JSON 文件，按用户隔离 |
| 去重 | 空白标准化后比较 |

### 3.4 中间件链（Middleware Chain）

18 个中间件按严格顺序执行，构成完整的请求处理管线：

| 序号 | 中间件 | 职责 |
|------|--------|------|
| 1 | ThreadDataMiddleware | 创建线程专属隔离目录 |
| 2 | UploadsMiddleware | 注入新上传文件到对话上下文 |
| 3 | SandboxMiddleware | 获取沙箱环境 |
| 4 | DanglingToolCallMiddleware | 处理被中断的工具调用 |
| 5 | LLMErrorHandlingMiddleware | 规范化模型调用错误 |
| 6 | GuardrailMiddleware | 工具调用前授权检查 |
| 7 | SandboxAuditMiddleware | 沙箱安全审计 |
| 8 | ToolErrorHandlingMiddleware | 工具异常转错误消息 |
| 9 | SummarizationMiddleware | 上下文超限时自动摘要 |
| 10 | TodoListMiddleware | 计划模式任务追踪 |
| 11 | TokenUsageMiddleware | Token 使用量记录 |
| 12 | TitleMiddleware | 自动生成对话标题 |
| 13 | MemoryMiddleware | 队列化异步记忆更新 |
| 14 | ViewImageMiddleware | 视觉模型图像注入 |
| 15 | DeferredToolFilterMiddleware | MCP 工具延迟绑定 |
| 16 | SubagentLimitMiddleware | 子智能体并发限制 |
| 17 | LoopDetectionMiddleware | 工具调用死循环检测 |
| 18 | ClarificationMiddleware | 澄清请求拦截（必须最后） |

### 3.5 技能系统（Skills System）

类似"智能体插件市场"，可安装技能扩展 AI 能力。

**内置 21 个公共技能**:
`deep-research`、`data-analysis`、`code-documentation`、`frontend-design`、`ppt-generation`、`podcast-generation`、`video-generation`、`image-generation`、`chart-visualization`、`academic-paper-review`、`systematic-literature-review`、`consulting-analysis`、`newsletter-generation`、`github-deep-research`、`skill-creator`、`bootstrap`、`web-design-guidelines`、`vercel-deploy-claimable`、`surprise-me`、`claude-to-deerflow`、`find-skills`

**技能格式**: `SKILL.md`（YAML 头部: name, description, license, allowed-tools）

### 3.6 MCP 集成（Model Context Protocol）

支持接入任意 MCP 服务器，扩展工具生态：

| 传输方式 | 说明 |
|----------|------|
| stdio | 命令行进程通信 |
| SSE | 服务器发送事件 |
| HTTP | REST API 通信 |
| OAuth | 支持 token 端点认证（client_credentials、refresh_token） |

### 3.7 IM 渠道（IM Channels）

支持通过即时通讯平台使用 DeerFlow：

| 平台 | 流式方式 | 说明 |
|------|----------|------|
| 飞书 | SSE 实时卡片更新 | 流式传输，实时更新卡片 |
| Slack | runs.wait() | 等待完成后返回 |
| Telegram | runs.wait() | 等待完成后返回 |
| 钉钉 | AI Card 流式 | 可选卡片流式更新 |

---

## 四、技术栈

### 4.1 后端

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 编程语言 |
| LangGraph | 1.0.6+ | 智能体框架与多智能体编排 |
| LangChain | 1.2.3+ | LLM 抽象层 |
| FastAPI | 0.115.0+ | Gateway REST API |
| langchain-mcp-adapters | - | MCP 协议支持 |
| agent-sandbox | - | 沙箱代码执行 |
| markitdown | - | 多格式文档转换 |
| ruff | - | 代码检查与格式化 |

### 4.2 前端

| 组件 | 版本 | 用途 |
|------|------|------|
| Node.js | 22+ | 运行时 |
| pnpm | 10.26.2+ | 包管理器 |
| Next.js | 16.2+ | React 框架 |
| React | 19.0 | UI 库 |
| TypeScript | 5.8 | 类型系统 |
| Tailwind CSS | 4 | 样式框架 |
| Radix UI | - | UI 原语组件 |
| @xyflow/react | - | 流程图可视化 |
| CodeMirror | 6 | 代码编辑器 |
| TanStack Query | 5 | 服务端状态管理 |

### 4.3 基础设施

| 组件 | 用途 |
|------|------|
| Nginx | 统一反向代理（端口 2026） |
| Docker | 容器化部署 |
| LangSmith/Langfuse | 可观测性与追踪 |

---

## 五、快速开始

### 5.1 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器
- Node.js 22+
- pnpm 10.26.2+
- Docker（可选，用于沙箱隔离）

### 5.2 本地开发（推荐 Docker）

```bash
# 1. 克隆项目
git clone https://github.com/bytedance/deer-flow.git
cd deer-flow

# 2. 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，配置 LLM 模型和 API Key

# 3. 安装依赖
make install

# 4. 启动开发服务
make dev
# 访问 http://localhost:2026
```

### 5.3 Docker 部署

```bash
# 开发模式
make docker-start

# 生产模式
make up
```

### 5.4 推荐模型

- **豆包 Doubao-Seed-2.0-Code**（火山引擎）
- **DeepSeek v3.2**
- **Kimi 2.5**
- 支持任何兼容 OpenAI API 的模型

---

## 六、项目目录结构

```
deer-flow/
├── backend/                        ← 后端应用
│   ├── packages/harness/deerflow/  ← 核心框架（harness 层）
│   │   ├── agents/                 ← 智能体系统
│   │   │   ├── lead_agent/        ← 主智能体
│   │   │   ├── middlewares/       ← 18 个中间件
│   │   │   ├── memory/            ← 记忆系统
│   │   │   └── thread_state.py   ← 线程状态 Schema
│   │   ├── sandbox/               ← 沙箱系统
│   │   │   ├── local/            ← 本地沙箱
│   │   │   ├── sandbox.py        ← 抽象接口
│   │   │   ├── tools.py          ← 沙箱工具
│   │   │   └── middleware.py     ← 沙箱生命周期
│   │   ├── subagents/             ← 子智能体
│   │   │   ├── builtins/         ← 内置子智能体
│   │   │   ├── executor.py       ← 执行引擎
│   │   │   └── registry.py       ← 注册表
│   │   ├── tools/builtins/        ← 内置工具
│   │   ├── mcp/                   ← MCP 集成
│   │   ├── models/                ← 模型工厂
│   │   ├── skills/                ← 技能系统
│   │   ├── config/                ← 配置系统
│   │   ├── community/             ← 社区工具
│   │   ├── guardrails/            ← 护栏系统
│   │   ├── reflection/            ← 动态模块加载
│   │   ├── runtime/               ← 运行时
│   │   ├── tracing/               ← 追踪系统
│   │   ├── persistence/           ← 持久化层
│   │   ├── uploads/               ← 文件上传
│   │   ├── utils/                 ← 工具函数
│   │   └── client.py              ← 嵌入式客户端
│   ├── app/                       ← 应用层
│   │   ├── gateway/               ← FastAPI Gateway
│   │   │   ├── app.py            ← 应用入口
│   │   │   └── routers/          ← 路由模块
│   │   └── channels/              ← IM 渠道
│   ├── tests/                     ← 测试套件
│   └── docs/                      ← 后端文档
├── frontend/                      ← 前端应用
│   └── src/
│       ├── app/                   ← Next.js App Router
│       ├── components/            ← React 组件
│       │   ├── ui/               ← Shadcn UI 原语
│       │   ├── ai-elements/      ← Vercel AI SDK 元素
│       │   ├── workspace/        ← 工作区组件
│       │   └── landing/          ← 落地页组件
│       ├── core/                  ← 核心业务逻辑
│       │   ├── threads/          ← 线程管理
│       │   ├── api/              ← API 客户端
│       │   ├── artifacts/        ← 制品管理
│       │   ├── i18n/             ← 国际化
│       │   ├── settings/         ← 用户设置
│       │   ├── memory/           ← 记忆系统
│       │   ├── skills/           ← 技能管理
│       │   ├── messages/         ← 消息处理
│       │   ├── mcp/              ← MCP 集成
│       │   └── models/           ← 数据模型
│       ├── hooks/                 ← 共享 Hooks
│       ├── lib/                   ← 工具函数
│       └── styles/                ← 全局样式
├── skills/                        ← 技能市场
│   ├── public/                   ← 公共技能（21 个）
│   └── custom/                   ← 自定义技能
├── scripts/                       ← 运维脚本
├── docker/                        ← Docker 配置
├── docs/                          ← 项目文档
├── config.example.yaml            ← 配置模板
├── extensions_config.example.json ← 扩展配置模板
├── Makefile                       ← 统一命令入口
└── README.md                      ← 项目说明（英文）
```

---

## 七、配置系统

### 7.1 主配置（config.yaml）

关键配置段：

| 配置段 | 说明 |
|--------|------|
| `models[]` | LLM 模型配置（类路径、API Key、thinking/vision 标志） |
| `tools[]` | 工具定义（模块路径、分组） |
| `tool_groups[]` | 工具逻辑分组 |
| `sandbox.use` | 沙箱提供者类路径 |
| `skills` | 技能目录路径 |
| `title` | 自动标题生成设置 |
| `summarization` | 上下文摘要设置 |
| `subagents.enabled` | 子智能体总开关 |
| `memory` | 记忆系统设置 |
| `token_usage` | Token 用量追踪 |
| `guardrails` | 护栏策略设置 |
| `channels` | IM 渠道配置 |

### 7.2 扩展配置（extensions_config.json）

```json
{
  "mcpServers": {
    "github": { "enabled": true, "type": "stdio", "command": "npx", ... }
  },
  "skills": {
    "deep-research": { "enabled": true }
  }
}
```

### 7.3 关键环境变量

| 变量 | 说明 |
|------|------|
| `DEER_FLOW_CONFIG_PATH` | 覆盖 config.yaml 位置 |
| `DEER_FLOW_EXTENSIONS_CONFIG_PATH` | 覆盖 extensions_config.json 位置 |
| `DEER_FLOW_HOME` | 运行时数据目录 |
| `DEER_FLOW_ENV` | 部署环境标签 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` | 模型 API Key |
| `TAVILY_API_KEY` / `GITHUB_TOKEN` | 工具 API Key |

---

## 八、部署方案

### 8.1 部署模式对比

| 模式 | 命令 | 热重载 | 适用场景 |
|------|------|--------|----------|
| 本地开发 | `make dev` | ✅ | 日常开发 |
| 本地开发(后台) | `make dev-daemon` | ✅ | 开发后台运行 |
| 本地生产 | `make start` | ❌ | 本地生产验证 |
| Docker 开发 | `make docker-start` | ✅ | Docker 开发 |
| Docker 生产 | `make up` | ❌ | 生产部署 |

### 8.2 Nginx 路由规则

| 路径 | 转发目标 |
|------|----------|
| `/api/langgraph/*` | Gateway LangGraph 兼容 API（重写为 `/api/*`） |
| `/api/*`（其他） | Gateway REST API |
| `/`（非 API） | Frontend Next.js |

---

## 九、嵌入式客户端

DeerFlow 提供 `DeerFlowClient` 类，可在不启动 HTTP 服务的情况下直接使用所有功能：

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()
# 对话
response = client.chat("帮我做一个数据分析", thread_id="my-thread")
# 流式
for event in client.stream("分析这份数据", thread_id="my-thread"):
    print(event)
# 管理
client.list_models()
client.list_skills()
client.get_memory()
```

---

## 十、安全说明

### 10.1 重要提醒

⚠️ **DeerFlow 的沙箱功能可能存在被绕过的安全风险。** 在未采取额外安全措施的情况下，不应将 DeerFlow 暴露给不受信任的网络或客户端。

### 10.2 安全建议

1. **网络隔离**: 使用防火墙限制对 DeerFlow 服务的未授权网络访问
2. **容器化部署**: 推荐使用 Docker 沙箱提供者（`AioSandboxProvider`）而非本地沙箱
3. **禁用 Bash**: 使用 `LocalSandboxProvider` 时默认禁用 `bash` 工具
4. **生产环境**: 关闭 Gateway API 文档（`GATEWAY_ENABLE_DOCS=false`）
5. **认证**: 配置 CORS 同源策略和 CSRF 保护
6. **护栏**: 启用 Guardrail 中间件进行工具调用前授权检查

---

## 十一、社区与贡献

### 11.1 贡献指南

详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

### 11.2 行为准则

详见 [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。

### 11.3 Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=bytedance/deer-flow&type=Date)](https://star-history.com/#bytedance/deer-flow&Date)

---

## 十二、致谢

DeerFlow 基于以下开源项目构建:

- [LangGraph](https://github.com/langchain-ai/langgraph) — 智能体编排框架
- [LangChain](https://github.com/langchain-ai/langchain) — LLM 应用框架
- [FastAPI](https://github.com/tiangolo/fastapi) — Web API 框架
- [Next.js](https://github.com/vercel/next.js) — React 框架
- [Model Context Protocol](https://modelcontextprotocol.io/) — MCP 标准

---

> 📘 **下一步**: 阅读 [阅读说明 (READING_GUIDE.md)](./READING_GUIDE.md) 了解如何阅读和理解 DeerFlow 源代码。

---

## 十三、模块清单

本节按子包粒度罗列 `backend/packages/harness/deerflow/` 和 `backend/app/` 下的所有子包，每个子包给出包路径、主要职责、关键类 / 函数入口。供按包定位代码使用。

### 13.1 Harness 层（`backend/packages/harness/deerflow/`）

#### 13.1.1 `agents/` — 智能体系统

负责主智能体的工厂函数、中间件链、线程状态 Schema、智能体特征组装、记忆子模块的导出。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `agents/` 顶层 | `deerflow.agents` | 智能体系统的入口聚合层 | `create_deerflow_agent()`、`RuntimeFeatures`、`Next` / `Prev` 锚点装饰器 |
| `agents/thread_state.py` | `deerflow.agents.thread_state` | 线程级状态 Schema | `ThreadState`（继承 `AgentState`）、`SandboxState`、`ThreadDataState`、`ViewedImageData`、`PromotedTools`、`merge_artifacts`、`merge_viewed_images`、`merge_todos`、`merge_promoted` |
| `agents/lead_agent/agent.py` | `deerflow.agents.lead_agent` | 主智能体工厂 | `make_lead_agent(config)`、`_make_lead_agent`、`_build_middlewares`、`_resolve_model_name`、`_create_summarization_middleware`、`_create_todo_list_middleware`、`_assemble_deferred`、`_available_skill_names`、`_load_enabled_skills_for_tool_policy` |
| `agents/lead_agent/prompt.py` | `deerflow.agents.lead_agent.prompt` | 系统提示词模板与缓存 | `apply_prompt_template()`、`get_skills_prompt_section()`、`get_cached_enabled_skills()`、`get_enabled_skills_for_config()`、`get_agent_soul()`、`get_deferred_tools_prompt_section()`、`clear_skills_system_prompt_cache()`、`refresh_skills_system_prompt_cache_async()`、`prime_enabled_skills_cache()` |
| `agents/middlewares/` | `deerflow.agents.middlewares` | 18 个 LangGraph 中间件 | `ThreadDataMiddleware`、`UploadsMiddleware`、`SandboxMiddleware`、`DanglingToolCallMiddleware`、`LLMErrorHandlingMiddleware`、`GuardrailMiddleware`、`SandboxAuditMiddleware`、`ToolErrorHandlingMiddleware`、`SummarizationMiddleware`（`DeerFlowSummarizationMiddleware`）、`TodoMiddleware`（`TodoListMiddleware`）、`TokenUsageMiddleware`、`TitleMiddleware`、`MemoryMiddleware`、`ViewImageMiddleware`、`DeferredToolFilterMiddleware`、`SubagentLimitMiddleware`、`LoopDetectionMiddleware`、`ClarificationMiddleware`、`DynamicContextMiddleware`、`SafetyFinishReasonMiddleware`、`ToolOutputBudgetMiddleware` |
| `agents/memory/` | `deerflow.agents.memory` | 记忆系统（提取、队列、存储、提示词、消息处理） | `MemoryUpdater`、`update_memory_from_conversation()`、`get_memory_data()`、`reload_memory_data()`、`create_memory_fact()`、`update_memory_fact()`、`delete_memory_fact()`、`import_memory_data()`、`clear_memory_data()`、`MemoryUpdateQueue`、`get_memory_queue()`、`MemoryStorage`、`FileMemoryStorage`、`get_memory_storage()`、`format_memory_for_injection()`、`format_conversation_for_update()`、`filter_messages_for_memory()`、`detect_correction()`、`detect_reinforcement()`、`memory_flush_hook()` |
| `agents/factory.py` | `deerflow.agents.factory` | 智能体工厂的高级组装入口 | `create_deerflow_agent()`、`_assemble_from_features`、`_insert_extra` |
| `agents/features.py` | `deerflow.agents.features` | 中间件相对顺序的声明式锚点 | `RuntimeFeatures`、`Next`、`Prev` |

#### 13.1.2 `sandbox/` — 沙箱执行系统

抽象沙箱接口、虚拟路径翻译、沙箱工具、生命周期中间件、安全 / 异常、文件锁。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `sandbox/sandbox.py` | `deerflow.sandbox` | 抽象 `Sandbox` 接口 | `Sandbox`（ABC） |
| `sandbox/sandbox_provider.py` | `deerflow.sandbox.sandbox_provider` | 沙箱提供者协议与单例管理 | `SandboxProvider`（ABC）、`get_sandbox_provider()`、`reset_sandbox_provider()`、`shutdown_sandbox_provider()`、`set_sandbox_provider()` |
| `sandbox/local/` | `deerflow.sandbox.local` | 本地文件系统沙箱实现 | `LocalSandbox`（`execute_command` / `read_file` / `write_file` / `list_dir`）、`LocalSandboxProvider`、`PathMapping`、`ResolvedPath`、`list_dir()` |
| `sandbox/tools.py` | `deerflow.sandbox.tools` | 沙箱暴露给 LLM 的工具函数 | `bash_tool`、`ls_tool`、`glob_tool`、`grep_tool`、`read_file_tool`、`write_file_tool`、`str_replace_tool`、`replace_virtual_path()`、`replace_virtual_paths_in_command()`、`resolve_and_validate_user_data_path()`、`validate_local_bash_command_paths()`、`ensure_sandbox_initialized()`、`ensure_sandbox_initialized_async()`、`is_local_sandbox()`、`sandbox_from_runtime()` |
| `sandbox/middleware.py` | `deerflow.sandbox.middleware` | 沙箱生命周期中间件 | `SandboxMiddleware`、`SandboxMiddlewareState` |
| `sandbox/search.py` | `deerflow.sandbox.search` | 沙箱内 glob / grep 工具实现 | `find_glob_matches()`、`find_grep_matches()`、`is_binary_file()`、`should_ignore_name()`、`should_ignore_path()`、`path_matches()`、`truncate_line()`、`GrepMatch` |
| `sandbox/file_operation_lock.py` | `deerflow.sandbox.file_operation_lock` | 同沙箱同路径的串行化锁 | `get_file_operation_lock_key()`、`get_file_operation_lock()` |
| `sandbox/security.py` | `deerflow.sandbox.security` | 沙箱安全策略查询 | `uses_local_sandbox_provider()`、`is_host_bash_allowed()` |
| `sandbox/exceptions.py` | `deerflow.sandbox.exceptions` | 沙箱异常类型 | `SandboxError`、`SandboxNotFoundError`、`SandboxRuntimeError`、`SandboxCommandError`、`SandboxFileError`、`SandboxPermissionError`、`SandboxFileNotFoundError` |

#### 13.1.3 `subagents/` — 子智能体编排

子智能体定义、注册表、后台执行引擎、token 收集。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `subagents/config.py` | `deerflow.subagents` | 子智能体配置 | `SubagentConfig`、`resolve_subagent_model_name()`、`_default_model_name()` |
| `subagents/registry.py` | `deerflow.subagents.registry` | 子智能体注册表 | `get_subagent_config()`、`list_subagents()`、`get_subagent_names()`、`get_available_subagent_names()`、`_build_custom_subagent_config()` |
| `subagents/executor.py` | `deerflow.subagents.executor` | 后台双线程池执行引擎 | `SubagentExecutor`、`SubagentStatus`（枚举）、`SubagentResult`、`request_cancel_background_task()`、`get_background_task_result()`、`list_background_tasks()`、`cleanup_background_task()`、`_run_isolated_subagent_loop()`、`_get_isolated_subagent_loop()` |
| `subagents/token_collector.py` | `deerflow.subagents.token_collector` | 子智能体 token 用量回传 | `SubagentTokenCollector` |
| `subagents/builtins/bash_agent.py` | `deerflow.subagents.builtins` | `bash` 子智能体实现 | （模块导出 `bash` 子智能体） |
| `subagents/builtins/general_purpose.py` | `deerflow.subagents.builtins` | `general-purpose` 子智能体实现 | （模块导出通用子智能体） |

#### 13.1.4 `tools/` — 工具系统

工具组装、内置工具、ACP 子代理调用、工具搜索、MCP 元数据、技能管理工具。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `tools/tools.py` | `deerflow.tools` | 工具组装入口 | `get_available_tools()`、`_is_host_bash_tool`、`_ensure_sync_invocable_tool` |
| `tools/types.py` | `deerflow.tools.types` | 工具类型定义 | （共享类型 / TypedDict） |
| `tools/sync.py` | `deerflow.tools.sync` | 同步包装异步工具 | `make_sync_tool_wrapper()`、`_get_runnable_config_param()` |
| `tools/mcp_metadata.py` | `deerflow.tools.mcp_metadata` | MCP 工具标记 | `tag_mcp_tool()`、`is_mcp_tool()` |
| `tools/skill_manage_tool.py` | `deerflow.tools.skill_manage_tool` | 技能内容编辑工具（带历史） | `skill_manage_tool()`、`_skill_manage_impl()`、`_history_record()`、`_scan_or_raise()` |
| `tools/builtins/clarification_tool.py` | `deerflow.tools.builtins.clarification_tool` | 澄清请求工具 | `ask_clarification_tool()` |
| `tools/builtins/present_file_tool.py` | `deerflow.tools.builtins.present_file_tool` | 提交产出文件给用户 | `present_file_tool()` |
| `tools/builtins/view_image_tool.py` | `deerflow.tools.builtins.view_image_tool` | 把图片注入为 base64 | `view_image_tool()`、`_is_allowed_image_virtual_path()`、`_detect_image_mime()` |
| `tools/builtins/task_tool.py` | `deerflow.tools.builtins.task_tool` | 委派任务给子智能体 | `task_tool()`、`_schedule_deferred_subagent_cleanup()`、`_report_subagent_usage()`、`_await_subagent_terminal()` |
| `tools/builtins/setup_agent_tool.py` | `deerflow.tools.builtins.setup_agent_tool` | bootstrap 阶段创建自定义 agent | `setup_agent()` |
| `tools/builtins/update_agent_tool.py` | `deerflow.tools.builtins.update_agent_tool` | 自定义 agent 自更新 SOUL.md | `update_agent()` |
| `tools/builtins/invoke_acp_agent_tool.py` | `deerflow.tools.builtins.invoke_acp_agent_tool` | 调用 ACP 协议外部 agent | `build_invoke_acp_agent_tool()` |
| `tools/builtins/tool_search.py` | `deerflow.tools.builtins.tool_search` | 延迟绑定工具的搜索与目录 | `DeferredToolCatalog`、`DeferredToolSetup`、`build_tool_search_tool()`、`build_deferred_tool_setup()`、`_catalog_regex_score()` |

#### 13.1.5 `mcp/` — MCP 集成

MCP 客户端构建、缓存与失效、OAuth 工具拦截、会话池。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `mcp/client.py` | `deerflow.mcp.client` | 服务端参数构建 | `build_server_params()`、`build_servers_config()` |
| `mcp/cache.py` | `deerflow.mcp.cache` | 懒加载 + mtime 缓存失效 | `initialize_mcp_tools()`、`get_cached_mcp_tools()`、`reset_mcp_tools_cache()`、`_is_cache_stale()` |
| `mcp/tools.py` | `deerflow.mcp.tools` | 异步拉取并包装 MCP 工具 | `get_mcp_tools()`、`_make_session_pool_tool()`、`_convert_call_tool_result()` |
| `mcp/oauth.py` | `deerflow.mcp.oauth` | OAuth token 管理与拦截 | `OAuthTokenManager`、`_OAuthToken`、`build_oauth_tool_interceptor()`、`get_initial_oauth_headers()` |
| `mcp/session_pool.py` | `deerflow.mcp.session_pool` | MCP 会话池（避免重复握手） | （会话池模块） |

#### 13.1.6 `models/` — 模型工厂

LLM 客户端工厂、各家 Provider 适配、thinking / vision 标志处理。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `models/factory.py` | `deerflow.models` | 统一入口 | `create_chat_model(name, thinking_enabled, ...)`、`_deep_merge_dicts()`、`_vllm_disable_chat_template_kwargs()`、`_enable_stream_usage_by_default()` |
| `models/claude_provider.py` | `deerflow.models.claude_provider` | Anthropic Claude 适配 | （Claude provider 模块） |
| `models/openai_codex_provider.py` | `deerflow.models.openai_codex_provider` | OpenAI Codex 适配 | （Codex provider 模块） |
| `models/mindie_provider.py` | `deerflow.models.mindie_provider` | MindIE 推理服务适配 | （MindIE provider 模块） |
| `models/vllm_provider.py` | `deerflow.models.vllm_provider` | vLLM 0.19 适配 | `VllmChatModel`（继承 `ChatOpenAI`） |
| `models/credential_loader.py` | `deerflow.models.credential_loader` | 多源凭证加载 | （凭证加载模块） |
| `models/assistant_payload_replay.py` | `deerflow.models.assistant_payload_replay` | 助手 payload 回放 | （payload 处理模块） |
| `models/patched_*.py` | `deerflow.models.patched_*` | DeepSeek / mimo / MiniMax / OpenAI 的 patched 客户端 | （4 个 patch 模块） |

#### 13.1.7 `skills/` — 技能系统

技能解析、加载、安装、安全扫描、权限、存储。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `skills/types.py` | `deerflow.skills.types` | 技能类型定义 | `SkillCategory`（枚举）、`Skill` |
| `skills/parser.py` | `deerflow.skills.parser` | SKILL.md YAML 解析 | `parse_skill_file()`、`parse_allowed_tools()`、`_format_yaml_error()` |
| `skills/validation.py` | `deerflow.skills.validation` | frontmatter 校验 | `_validate_skill_frontmatter()` |
| `skills/installer.py` | `deerflow.skills.installer` | `.skill` 归档安装 | `safe_extract_skill_archive()`、`resolve_skill_dir_from_archive()`、`is_unsafe_zip_member()`、`is_symlink_member()`、`SkillAlreadyExistsError`、`SkillSecurityScanError` |
| `skills/security_scanner.py` | `deerflow.skills.security_scanner` | 技能内容安全扫描 | `ScanResult`、`scan_skill_content()`、`_extract_json_object()` |
| `skills/permissions.py` | `deerflow.skills.permissions` | 技能目录沙箱可读性 | `make_skill_path_sandbox_readable()`、`make_skill_tree_sandbox_readable()`、`make_skill_written_path_sandbox_readable()` |
| `skills/tool_policy.py` | `deerflow.skills.tool_policy` | 技能 allowed-tools 工具过滤 | `NamedTool`、`allowed_tool_names_for_skills()`、`filter_tools_by_skill_allowed_tools()` |
| `skills/storage/skill_storage.py` | `deerflow.skills.storage.skill_storage` | 技能存储抽象 | `SkillStorage`（ABC） |
| `skills/storage/local_skill_storage.py` | `deerflow.skills.storage.local_skill_storage` | 本地文件系统存储 | `LocalSkillStorage` |

#### 13.1.8 `memory/` —（与 agents/memory 并列的记忆系统导出）

无独立子包，仅作为 `deerflow.agents.memory` 的同义导出。详见 13.1.1 中 `agents/memory/`。

#### 13.1.9 `config/` — 配置系统

YAML / JSON 配置加载、缓存与 mtime 重载、各段配置数据类。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `config/app_config.py` | `deerflow.config` | 根配置聚合 | `AppConfig`（BaseModel）、`CircuitBreakerConfig`、`get_app_config()`、`reload_app_config()`、`reset_app_config()`、`set_app_config()`、`peek_current_app_config()`、`push_current_app_config()` / `pop_current_app_config()`、`apply_logging_level()` |
| `config/paths.py` | `deerflow.config.paths` | 路径工具 | （路径解析模块） |
| `config/runtime_paths.py` | `deerflow.config.runtime_paths` | 运行时数据目录解析 | （运行时路径模块） |
| `config/model_config.py` | `deerflow.config.model_config` | 模型配置 | `ModelConfig` |
| `config/tool_config.py` | `deerflow.config.tool_config` | 工具配置 | `ToolConfig` |
| `config/sandbox_config.py` | `deerflow.config.sandbox_config` | 沙箱配置 | `SandboxConfig` |
| `config/memory_config.py` | `deerflow.config.memory_config` | 记忆配置 | `MemoryConfig` |
| `config/subagents_config.py` | `deerflow.config.subagents_config` | 子智能体配置 | `SubagentsConfig` |
| `config/skills_config.py` | `deerflow.config.skills_config` | 技能配置 | `SkillsConfig` |
| `config/skill_evolution_config.py` | `deerflow.config.skill_evolution_config` | 技能自进化配置 | （技能进化配置） |
| `config/summarization_config.py` | `deerflow.config.summarization_config` | 上下文摘要配置 | `SummarizationConfig` |
| `config/title_config.py` | `deerflow.config.title_config` | 自动标题配置 | `TitleConfig` |
| `config/token_usage_config.py` | `deerflow.config.token_usage_config` | token 用量追踪配置 | （token 用量配置） |
| `config/guardrails_config.py` | `deerflow.config.guardrails_config` | 护栏配置 | （护栏配置） |
| `config/loop_detection_config.py` | `deerflow.config.loop_detection_config` | 循环检测配置 | （循环检测配置） |
| `config/safety_finish_reason_config.py` | `deerflow.config.safety_finish_reason_config` | 终止原因安全检测配置 | （safety 配置） |
| `config/tool_output_config.py` | `deerflow.config.tool_output_config` | 工具输出预算配置 | `ToolOutputConfig` |
| `config/tool_search_config.py` | `deerflow.config.tool_search_config` | 工具搜索配置 | （tool search 配置） |
| `config/run_events_config.py` | `deerflow.config.run_events_config` | 运行事件存储配置 | （run events 配置） |
| `config/stream_bridge_config.py` | `deerflow.config.stream_bridge_config` | 流桥接配置 | （stream bridge 配置） |
| `config/extensions_config.py` | `deerflow.config.extensions_config` | 扩展配置（MCP / 技能启用） | `ExtensionsConfig`、`McpServerConfig` |
| `config/agents_config.py` | `deerflow.config.agents_config` | 自定义 agent 配置 | `AgentConfig` |
| `config/agents_api_config.py` | `deerflow.config.agents_api_config` | agents API 启用开关 | （agents API 配置） |
| `config/acp_config.py` | `deerflow.config.acp_config` | ACP 协议配置 | （ACP 配置） |
| `config/checkpointer_config.py` | `deerflow.config.checkpointer_config` | checkpointer 配置 | `CheckpointerConfig` |
| `config/database_config.py` | `deerflow.config.database_config` | 数据库配置（SQLite / Postgres） | `DatabaseConfig` |
| `config/tracing_config.py` | `deerflow.config.tracing_config` | 追踪系统配置 | （tracing 配置） |

#### 13.1.10 `runtime/` — 运行时

LangGraph 运行时、运行记录管理、checkpointer / store / 流桥接、用户上下文、事件流、序列化和 checkpoint 滚动。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `runtime/runs/manager.py` | `deerflow.runtime.runs.manager` | 运行记录管理（RunManager） | `RunManager`、`RunRecord`、`PersistenceRetryPolicy`、`ConflictError`、`UnsupportedStrategyError` |
| `runtime/runs/worker.py` | `deerflow.runtime.runs.worker` | 智能体执行入口 | `run_agent()`、`RunContext`、`_build_runtime_context()`、`_install_runtime_context()`、`_extract_human_message()`、`_unpack_stream_item()` |
| `runtime/runs/schemas.py` | `deerflow.runtime.runs.schemas` | 运行状态枚举 | `RunStatus`、`DisconnectMode` |
| `runtime/runs/naming.py` | `deerflow.runtime.runs.naming` | run 名称解析 | `resolve_root_run_name()` |
| `runtime/runs/store/` | `deerflow.runtime.runs.store` | 运行持久化（SQL / 内存） | （run store 实现） |
| `runtime/checkpointer/provider.py` | `deerflow.runtime.checkpointer` | 同步 checkpointer 提供 | `get_checkpointer()`、`reset_checkpointer()`、`checkpointer_context()` |
| `runtime/checkpointer/async_provider.py` | `deerflow.runtime.checkpointer` | 异步 checkpointer 提供 | `make_checkpointer()`、`_build_postgres_pool()`、`_prepare_sqlite_checkpointer_path()` |
| `runtime/store/` | `deerflow.runtime.store` | LangGraph Store 后端 | （store provider） |
| `runtime/stream_bridge/base.py` | `deerflow.runtime.stream_bridge` | 流桥接抽象 | `StreamEvent`、`StreamBridge`（ABC） |
| `runtime/stream_bridge/memory.py` | `deerflow.runtime.stream_bridge.memory` | 内存版流桥接 | `MemoryStreamBridge`、`_RunStream` |
| `runtime/stream_bridge/async_provider.py` | `deerflow.runtime.stream_bridge` | 异步流桥接工厂 | `make_stream_bridge()` |
| `runtime/events/store/` | `deerflow.runtime.events.store` | 运行事件 store | （事件 store 实现） |
| `runtime/journal.py` | `deerflow.runtime.journal` | LangChain 回调 + 运行日志 | `RunJournal` |
| `runtime/user_context.py` | `deerflow.runtime.user_context` | 用户上下文 contextvar | `CurrentUser`（Protocol）、`set_current_user()`、`reset_current_user()`、`get_current_user()`、`require_current_user()`、`get_effective_user_id()`、`resolve_runtime_user_id()`、`resolve_user_id()` |
| `runtime/converters.py` | `deerflow.runtime.converters` | LangGraph 事件 / 消息转换 | （converter 模块） |
| `runtime/serialization.py` | `deerflow.runtime.serialization` | 状态序列化 | （序列化模块） |

#### 13.1.11 `persistence/` — 持久化层

SQLAlchemy 引擎、Alembic 迁移、用户 / 线程 / 运行 / 反馈 / 事件的数据模型。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `persistence/engine.py` | `deerflow.persistence` | 数据库引擎管理 | `init_engine()`、`init_engine_from_config()`、`get_session_factory()`、`get_engine()`、`close_engine()` |
| `persistence/base.py` | `deerflow.persistence.base` | 共享 declarative base | （SQLAlchemy Base） |
| `persistence/json_compat.py` | `deerflow.persistence.json_compat` | JSON 字段类型兼容 | （JSON 类型） |
| `persistence/models/run_event.py` | `deerflow.persistence.models` | 运行事件模型 | （`RunEvent`） |
| `persistence/feedback/model.py` | `deerflow.persistence.feedback` | 反馈数据模型 | （`Feedback`） |
| `persistence/feedback/sql.py` | `deerflow.persistence.feedback` | 反馈 SQL 操作 | （feedback CRUD） |
| `persistence/run/model.py` | `deerflow.persistence.run` | 运行元数据模型 | （`Run`） |
| `persistence/run/sql.py` | `deerflow.persistence.run` | 运行 SQL 操作 | （run CRUD） |
| `persistence/thread_meta/base.py` | `deerflow.persistence.thread_meta` | 线程元数据 base | （`ThreadMetaBase`） |
| `persistence/thread_meta/model.py` | `deerflow.persistence.thread_meta` | 线程元数据模型 | （`ThreadMeta`） |
| `persistence/thread_meta/memory.py` | `deerflow.persistence.thread_meta` | 线程元数据内存后端 | （内存版实现） |
| `persistence/thread_meta/sql.py` | `deerflow.persistence.thread_meta` | 线程元数据 SQL 操作 | （CRUD） |
| `persistence/user/model.py` | `deerflow.persistence.user` | 用户模型 | （`User`） |
| `persistence/migrations/` | `deerflow.persistence.migrations` | Alembic 迁移 | `alembic.ini`、`env.py`、`versions/` |

#### 13.1.12 `guardrails/` — 护栏系统

工具调用前的授权检查中间件、可插拔 Provider 协议、内置 allowlist。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `guardrails/middleware.py` | `deerflow.guardrails` | 护栏中间件 | `GuardrailMiddleware` |
| `guardrails/provider.py` | `deerflow.guardrails.provider` | Provider 协议与数据结构 | `GuardrailProvider`（Protocol）、`GuardrailRequest`、`GuardrailReason`、`GuardrailDecision` |
| `guardrails/builtin.py` | `deerflow.guardrails.builtin` | 内置 allowlist 实现 | `AllowlistProvider` |

#### 13.1.13 `reflection/` — 反射系统

`package.module:ClassName` 形式的动态导入与类校验。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `reflection/resolvers.py` | `deerflow.reflection` | 字符串路径动态解析 | `resolve_variable()`、`resolve_class()`、`_build_missing_dependency_hint()` |

#### 13.1.14 `tracing/` — 追踪系统

LangSmith / Langfuse 回调构造与 trace 元数据构建。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `tracing/factory.py` | `deerflow.tracing` | 回调工厂 | `build_tracing_callbacks()`、`_create_langsmith_tracer()`、`_create_langfuse_handler()` |
| `tracing/metadata.py` | `deerflow.tracing` | trace 元数据 | `build_langfuse_trace_metadata()` |

#### 13.1.15 `uploads/` — 文件上传

线程隔离的上传目录、文件名安全、符号链接防护、文件落盘原语。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `uploads/manager.py` | `deerflow.uploads` | 上传文件底层管理 | `validate_thread_id()`、`get_uploads_dir()`、`ensure_uploads_dir()`、`normalize_filename()`、`claim_unique_filename()`、`validate_path_traversal()`、`open_upload_file_no_symlink()`、`write_upload_file_no_symlink()`、`list_files_in_dir()`、`delete_file_safe()`、`upload_artifact_url()`、`upload_virtual_path()`、`enrich_file_listing()`、`PathTraversalError`、`UnsafeUploadPathError` |

#### 13.1.16 `community/` — 社区工具与第三方沙箱

可选的外部服务集成与容器化沙箱后端。每个子包都是独立的 `ConfigurableTool` 或 `SandboxProvider`。

| 子包 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `community/tavily/` | `deerflow.community.tavily` | Tavily 网页搜索 / 抓取 | `web_search_tool()`、`web_fetch_tool()` |
| `community/jina_ai/` | `deerflow.community.jina_ai` | Jina AI 网页抓取 | `web_fetch_tool()` |
| `community/firecrawl/` | `deerflow.community.firecrawl` | Firecrawl 网页抓取 | `web_search_tool()`、`web_fetch_tool()` |
| `community/infoquest/` | `deerflow.community.infoquest` | InfoQuest 搜索 / 抓取 / 图片 | `web_search_tool()`、`web_fetch_tool()`、`image_search_tool()` |
| `community/image_search/` | `deerflow.community.image_search` | DuckDuckGo 图片搜索 | `image_search_tool()`、`_search_images()` |
| `community/ddg_search/` | `deerflow.community.ddg_search` | DuckDuckGo 文本搜索 | `web_search_tool()`、`_search_text()` |
| `community/serper/` | `deerflow.community.serper` | Serper.dev Google 搜索 | `web_search_tool()` |
| `community/exa/` | `deerflow.community.exa` | Exa 神经搜索 | `web_search_tool()`、`web_fetch_tool()` |
| `community/aio_sandbox/` | `deerflow.community.aio_sandbox` | Docker 容器化沙箱 provider | `AioSandboxProvider`、`AioSandbox`、`local_backend` / `remote_backend`、`sandbox_info` |

#### 13.1.17 `utils/` — 工具函数

无状态的工具函数。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `utils/network.py` | `deerflow.utils.network` | 网络相关工具 | （网络工具） |
| `utils/readability.py` | `deerflow.utils.readability` | 内容可读性提取 | （readability 工具） |
| `utils/file_conversion.py` | `deerflow.utils.file_conversion` | 文档格式转换（PDF / Office → Markdown） | （`markitdown` 集成） |
| `utils/time.py` | `deerflow.utils.time` | 时间工具 | （时间工具） |

#### 13.1.18 `client.py` — 嵌入式客户端

不依赖 HTTP 服务、直接调用 harness 各能力的 Python 客户端。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `client.py` | `deerflow.client` | 嵌入式入口 | `DeerFlowClient`（含 `chat` / `stream` / `list_models` / `get_model` / `list_skills` / `get_skill` / `update_skill` / `install_skill` / `get_mcp_config` / `update_mcp_config` / `get_memory` / `reload_memory` / `get_memory_config` / `get_memory_status` / `upload_files` / `list_uploads` / `delete_upload` / `get_artifact` / `reset_agent`）、`StreamEvent` |

### 13.2 App 层（`backend/app/`）

#### 13.2.1 `gateway/` — FastAPI Gateway

REST API 应用、路由模块、鉴权 / CSRF / CORS 中间件、依赖注入、配置读取、工具函数。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `gateway/app.py` | `app.gateway` | FastAPI 应用创建、生命周期、嵌入式 LangGraph 运行时装配 | `create_app()`、`lifespan()`、`_ensure_admin_user()`、`_migrate_orphaned_threads()`、`app` |
| `gateway/config.py` | `app.gateway.config` | 启动期配置装载 | （启动配置模块） |
| `gateway/deps.py` | `app.gateway.deps` | FastAPI 依赖注入 | （共享 Depends） |
| `gateway/services.py` | `app.gateway.services` | 业务服务层 | （服务层模块） |
| `gateway/auth_middleware.py` | `app.gateway.auth_middleware` | 鉴权中间件 | （auth middleware） |
| `gateway/csrf_middleware.py` | `app.gateway.csrf_middleware` | CSRF 校验中间件 | `CSRFMiddleware` |
| `gateway/internal_auth.py` | `app.gateway.internal_auth` | 内部 channel 鉴权 | （内部鉴权） |
| `gateway/langgraph_auth.py` | `app.gateway.langgraph_auth` | LangGraph 兼容层鉴权 | （LG auth） |
| `gateway/authz.py` | `app.gateway.authz` | 授权检查 | （authz 模块） |
| `gateway/pagination.py` | `app.gateway.pagination` | 分页参数 | （分页工具） |
| `gateway/path_utils.py` | `app.gateway.path_utils` | 路径工具 | （路径工具） |
| `gateway/utils.py` | `app.gateway.utils` | 通用工具 | （util 模块） |
| `gateway/auth/` | `app.gateway.auth` | 鉴权子系统 | `config.py` / `credential_file.py` / `errors.py` / `jwt.py` / `local_provider.py` / `models.py` / `password.py` / `providers.py` / `reset_admin.py` / `repositories/` |
| `gateway/routers/models.py` | `app.gateway.routers.models` | `/api/models` 模型列表 / 详情 | `list_models()`、`get_model()`、`ModelResponse` / `ModelsListResponse` / `TokenUsageResponse` |
| `gateway/routers/mcp.py` | `app.gateway.routers.mcp` | `/api/mcp` 配置读写 | `get_mcp_configuration()`、`update_mcp_configuration()`、`McpConfigResponse` / `McpConfigUpdateRequest` / `McpServerConfigResponse` / `McpOAuthConfigResponse` |
| `gateway/routers/skills.py` | `app.gateway.routers.skills` | `/api/skills` 列表 / 安装 / 启停 / 自定义 / 历史 / 回滚 | `list_skills()`、`install_skill()`、`list_custom_skills()`、`get_custom_skill()`、`update_custom_skill()`、`delete_custom_skill()`、`get_custom_skill_history()`、`rollback_custom_skill()`、`get_skill()`、`update_skill()` |
| `gateway/routers/memory.py` | `app.gateway.routers.memory` | `/api/memory` 记忆 CRUD | `get_memory()`、`reload_memory()`、`clear_memory()`、`create_memory_fact_endpoint()`、`delete_memory_fact_endpoint()`、`update_memory_fact_endpoint()`、`export_memory()`、`import_memory()`、`get_memory_config_endpoint()`、`get_memory_status()` |
| `gateway/routers/uploads.py` | `app.gateway.routers.uploads` | `/api/threads/{id}/uploads` 上传 / 列表 / 删除 | `upload_files()`、`get_upload_limits()`、`list_uploaded_files()`、`delete_uploaded_file()` |
| `gateway/routers/threads.py` | `app.gateway.routers.threads` | `/api/threads` CRUD + state + history | `create_thread()`、`get_thread()`、`patch_thread()`、`delete_thread_data()`、`get_thread_state()`、`update_thread_state()`、`get_thread_history()`、`search_threads()` |
| `gateway/routers/thread_runs.py` | `app.gateway.routers.thread_runs` | `/api/threads/{id}/runs` 运行生命周期 | `create_run()`、`stream_run()`、`wait_run()`、`list_runs()`、`get_run()`、`cancel_run()`、`join_run()`、`stream_existing_run()`、`list_thread_messages()`、`list_run_messages()`、`list_run_events()`、`thread_token_usage()` |
| `gateway/routers/runs.py` | `app.gateway.routers.runs` | `/api/runs` 无状态运行 | `stateless_stream()`、`stateless_wait()`、`run_messages()`、`run_feedback()` |
| `gateway/routers/feedback.py` | `app.gateway.routers.feedback` | `/api/threads/{id}/runs/{rid}/feedback` | `upsert_feedback()`、`create_feedback()`、`list_feedback()`、`feedback_stats()`、`delete_feedback()`、`delete_run_feedback()` |
| `gateway/routers/agents.py` | `app.gateway.routers.agents` | `/api/agents` 自定义 agent 管理 + 用户 profile | `list_agents()`、`check_agent_name()`、`get_agent()`、`create_agent_endpoint()`、`update_agent()`、`delete_agent()`、`get_user_profile()`、`update_user_profile()` |
| `gateway/routers/suggestions.py` | `app.gateway.routers.suggestions` | `/api/threads/{id}/suggestions` 追问建议 | `generate_suggestions()` |
| `gateway/routers/channels.py` | `app.gateway.routers.channels` | `/api/channels` 渠道状态 / 重启 | `get_channels_status()`、`restart_channel()` |
| `gateway/routers/assistants_compat.py` | `app.gateway.routers.assistants_compat` | `/api/assistants` LangGraph 兼容 | `search_assistants()`、`get_assistant_compat()`、`get_assistant_graph()`、`get_assistant_schemas()` |
| `gateway/routers/artifacts.py` | `app.gateway.routers.artifacts` | `/api/threads/{id}/artifacts` 制品下发 | `get_artifact()` |

#### 13.2.2 `channels/` — IM 渠道

通过 `langgraph-sdk` 客户端把外部 IM 平台（飞书 / Slack / Telegram / 钉钉 / Discord / 微信 / 企业微信）接入 DeerFlow。

| 子包 / 文件 | 包路径 | 主要职责 | 关键类 / 函数 |
|------|--------|----------|---------------|
| `channels/base.py` | `app.channels.base` | 渠道抽象基类 | `Channel`（ABC，含 `start` / `stop` / `send` 生命周期） |
| `channels/message_bus.py` | `app.channels.message_bus` | 异步发布 / 订阅总线 | `MessageBus`、`InboundMessageType`、`InboundMessage`、`OutboundMessage`、`ResolvedAttachment` |
| `channels/store.py` | `app.channels.store` | 渠道 ↔ 线程映射持久化 | `ChannelStore` |
| `channels/manager.py` | `app.channels.manager` | 核心调度器 | `ChannelManager`、`_prepare_artifact_delivery()`、`_format_artifact_text()`、`_resolve_attachments()`、`_ingest_inbound_files()` |
| `channels/service.py` | `app.channels.service` | 渠道生命周期管理 | `ChannelService`、`start_channel_service()`、`stop_channel_service()`、`get_channel_service()` |
| `channels/commands.py` | `app.channels.commands` | IM 命令处理（`/new` / `/status` 等） | （命令处理模块） |
| `channels/feishu.py` | `app.channels.feishu` | 飞书实现（SSE 流式卡片） | `FeishuChannel` |
| `channels/slack.py` | `app.channels.slack` | Slack 实现 | `SlackChannel` |
| `channels/telegram.py` | `app.channels.telegram` | Telegram 实现 | `TelegramChannel` |
| `channels/dingtalk.py` | `app.channels.dingtalk` | 钉钉实现（AI Card 可选） | `DingTalkChannel`、`_DingTalkMessageHandler`、`_adapt_markdown_for_dingtalk()` |
| `channels/discord.py` | `app.channels.discord` | Discord 实现 | `DiscordChannel` |
| `channels/wechat.py` | `app.channels.wechat` | 微信实现（含 AES 加解密） | `WechatChannel`、`MessageItemType`、`UploadMediaType` |
| `channels/wecom.py` | `app.channels.wecom` | 企业微信实现 | `WeComChannel` |

---

> 📘 **继续阅读**: 阅读 [关键 API 索引 (READING_GUIDE.md#关键-api-索引)](./READING_GUIDE.md#关键-api-索引) 按"方法维度"查找每个核心模块的关键入口函数。
