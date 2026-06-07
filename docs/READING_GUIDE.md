# 📖 DeerFlow 2.0 — 阅读说明

> 本文档面向**新开发者**和**贡献者**，提供按依赖关系组织的代码阅读路径。建议按顺序阅读，从底层基础设施逐步到上层业务逻辑。

---

## 阅读路线图

```
第 1 阶段: 基础设施  →  第 2 阶段: 核心能力  →  第 3 阶段: 智能体系统
    (配置/反射/模型)      (沙箱/MCP/工具/技能)      (中间件/记忆/子智能体)

   第 4 阶段: 应用层   →   第 5 阶段: 前端      →   第 6 阶段: 进阶
    (Gateway/IM渠道)       (组件/核心逻辑)           (测试/部署/贡献)
```

---

## 第 1 阶段：基础设施层（阅读时间: ~2 小时）

从最底层、无依赖的模块开始，理解配置与类型系统。

### 1.1 配置系统 `packages/harness/deerflow/config/`

**优先级**: ⭐⭐⭐⭐⭐（必读，所有模块都依赖它）

| 文件 | 说明 | 建议 |
|------|------|------|
| `config.py` | `AppConfig` 数据类，含所有配置段的定义和 YAML 解析 | **必须精读** |
| `models.py` | `ModelConfig` 模型配置，含 thinking/vision 支持标志 | 重点阅读 |
| `tool_group.py` | 工具组配置 | 浏览即可 |
| `skill.py` | 技能配置 | 浏览即可 |

**关键概念**:
- `AppConfig.from_file()` 加载 `config.yaml`，支持环境变量 `$VAR` 引用
- 配置缓存 + mtime 自动重载机制
- 配置版本管理（`config_version` 字段）

### 1.2 反射系统 `packages/harness/deerflow/reflection/`

**优先级**: ⭐⭐⭐（理解动态加载机制）

| 文件 | 说明 |
|------|------|
| `resolve_variable(path)` | 从字符串路径动态导入模块并返回变量 |
| `resolve_class(path, base_class)` | 动态导入并验证类继承关系 |

**用处**: 配置文件中 `use: package.module:ClassName` 格式的解析，实现了工具和模型的插件化加载。

### 1.3 模型工厂 `packages/harness/deerflow/models/`

**优先级**: ⭐⭐⭐⭐（理解模型加载机制）

| 文件 | 说明 | 建议 |
|------|------|------|
| `factory.py` | `create_chat_model()` 工厂函数 | **必读** |
| `vllm_provider.py` | vLLM 推理服务适配器 | 按需阅读 |

**关键函数**:
```python
create_chat_model(name: str, thinking_enabled: bool) -> BaseChatModel
```

### 1.4 线程状态 `packages/harness/deerflow/agents/thread_state.py`

**优先级**: ⭐⭐⭐⭐⭐（必读，所有中间件都操作它）

定义了 `ThreadState`（继承 `AgentState`），是整个系统的状态中心：

```python
class ThreadState(AgentState):
    sandbox: Optional[Sandbox]        # 沙箱实例
    thread_data: ThreadData           # 线程元数据
    title: Optional[str]              # 对话标题
    artifacts: list[Artifact]         # 生成的文件/代码
    todos: list[TodoItem]            # 任务列表
    uploaded_files: list[UploadedFile] # 上传文件
    viewed_images: list[str]          # 已查看图像
```

---

## 第 2 阶段：核心能力层（阅读时间: ~3 小时）

### 2.1 沙箱系统 `packages/harness/deerflow/sandbox/`

**优先级**: ⭐⭐⭐⭐⭐（必读，核心隔离执行）

| 文件 | 说明 | 建议 |
|------|------|------|
| `sandbox.py` | 抽象 `Sandbox` 接口和 `SandboxProvider` 协议 | **必读** |
| `local/` | `LocalSandboxProvider` 本地文件系统实现 | **必读** |
| `tools.py` | `bash`、`ls`、`read_file`、`write_file`、`str_replace` | **必读** |
| `middleware.py` | 沙箱生命周期管理（获取/释放） | 重点阅读 |

**设计要点**:
- 虚拟路径 → 物理路径翻译
- 线程隔离：每个线程拥有独立的沙箱实例
- `str_replace` 工具使用 `(sandbox.id, path)` 锁保证并发安全

### 2.2 MCP 集成 `packages/harness/deerflow/mcp/`

**优先级**: ⭐⭐⭐（理解工具扩展机制）

| 文件 | 说明 |
|------|------|
| 主模块 | `get_cached_mcp_tools()` 懒加载 + mtime 缓存失效 |
| 支持传输 | stdio、SSE、HTTP、OAuth 认证 |

### 2.3 工具系统 `packages/harness/deerflow/tools/`

**优先级**: ⭐⭐⭐⭐（理解工具的组装逻辑）

| 文件 | 说明 | 建议 |
|------|------|------|
| `get_available_tools()` | 组装所有可用工具的入口函数 | **必读** |
| `builtins/` | 内置工具实现 | 浏览 |

**工具组装流程**:
```
配置定义 → 反射解析 → MCP 加载 → 内置工具 → 社区工具 → 子智能体工具 → 最终工具列表
```

### 2.4 技能系统 `packages/harness/deerflow/skills/`

**优先级**: ⭐⭐⭐

| 功能 | 说明 |
|------|------|
| 技能发现 | 递归扫描 `skills/{public,custom}/` 下的 `SKILL.md` |
| 技能格式 | YAML 头部 + Markdown 正文 |
| 注入方式 | 通过系统提示词注入给智能体 |

### 2.5 社区工具 `packages/harness/deerflow/community/`

**优先级**: ⭐⭐（按需）

| 工具 | 用途 |
|------|------|
| `tavily/` | Tavily 网页搜索 |
| `jina_ai/` | Jina AI 网页抓取 |
| `firecrawl/` | Firecrawl 网页抓取 |
| `aio_sandbox/` | Docker 沙箱提供者 |
| `image_search/` | DuckDuckGo 图片搜索 |

---

## 第 3 阶段：智能体系统（阅读时间: ~4 小时）

这是 DeerFlow 最核心的部分，建议投入最多时间理解。

### 3.1 中间件链（按执行顺序）

**优先级**: ⭐⭐⭐⭐⭐（必读，理解请求处理全流程）

中间件按 `append` 顺序组装，执行也是这个顺序。建议按以下顺序阅读：

```
build_lead_runtime_middlewares()  →  _build_middlewares()
```

| 阅读顺序 | 中间件 | 文件位置 | 关注点 |
|----------|--------|----------|--------|
| 1 | ThreadDataMiddleware | `agents/middlewares/` | 目录隔离如何实现 |
| 2 | UploadsMiddleware | `agents/middlewares/` | 文件注入机制 |
| 3 | SandboxMiddleware | `sandbox/middleware.py` | 沙箱获取生命周期 |
| 4 | DanglingToolCallMiddleware | `agents/middlewares/` | 中断处理 |
| 5 | LLMErrorHandlingMiddleware | `agents/middlewares/` | 错误规范化 |
| 6 | GuardrailMiddleware | `agents/middlewares/` | 工具授权 |
| 7 | SandboxAuditMiddleware | `agents/middlewares/` | 安全审计 |
| 8 | ToolErrorHandlingMiddleware | `agents/middlewares/` | 异常恢复 |
| 9 | SummarizationMiddleware | `agents/middlewares/` | 上下文管理 |
| 10 | TodoListMiddleware | `agents/middlewares/` | 任务追踪 |
| 11 | TokenUsageMiddleware | `agents/middlewares/` | 用量记录 |
| 12 | TitleMiddleware | `agents/middlewares/` | 标题生成 |
| 13 | MemoryMiddleware | `agents/middlewares/` | 记忆队列 |
| 14 | ViewImageMiddleware | `agents/middlewares/` | 视觉注入 |
| 15 | DeferredToolFilterMiddleware | `agents/middlewares/` | MCP 延迟绑定 |
| 16 | SubagentLimitMiddleware | `agents/middlewares/` | 并发限制 |
| 17 | LoopDetectionMiddleware | `agents/middlewares/` | 循环检测 |
| 18 | ClarificationMiddleware | `agents/middlewares/` | 澄清拦截 |

**中间件基类**: `AgentMiddleware` 提供 `abefore_model` / `aafter_model` / `awrap_tool_call` / `abefore_agent` / `aafter_agent` 五个钩子。

### 3.2 主智能体 `packages/harness/deerflow/agents/lead_agent/`

**优先级**: ⭐⭐⭐⭐⭐（必读，理解整个智能体的创建）

| 文件 | 说明 | 建议 |
|------|------|------|
| `agent.py` | `make_lead_agent()` 工厂函数 | **必读** |
| 系统提示词 | 提示词模板（含技能、记忆、子智能体指令注入） | **必读** |

**关键函数**:
```python
def make_lead_agent(config: RunnableConfig) -> CompiledStateGraph:
    # 1. 选择模型 (create_chat_model)
    # 2. 加载工具 (get_available_tools)
    # 3. 组装中间件 (_build_middlewares)
    # 4. 构建系统提示词 (apply_prompt_template)
    # 5. 创建并编译 LangGraph 图
```

### 3.3 记忆系统 `packages/harness/deerflow/agents/memory/`

**优先级**: ⭐⭐⭐⭐

| 文件 | 说明 | 建议 |
|------|------|------|
| `updater.py` | LLM 驱动的记忆提取和更新 | **必读** |
| `queue.py` | 防抖更新队列（30s 默认） | 重点阅读 |
| `prompt.py` | 记忆更新提示词模板 | 浏览 |
| `storage.py` | JSON 文件存储（按用户隔离） | 重点阅读 |

**数据流**:
```
MemoryMiddleware.aafter_agent()
  → 过滤消息（用户输入 + 最终 AI 回复）
  → 加入去重队列
  → (30s 防抖后) 后台线程调用 LLM
  → 提取用户上下文 + 事实
  → 原子写入 memory.json
  → 下次对话注入系统提示词
```

### 3.4 子智能体系统 `packages/harness/deerflow/subagents/`

**优先级**: ⭐⭐⭐⭐

| 文件 | 说明 | 建议 |
|------|------|------|
| `executor.py` | 后台执行引擎（双线程池） | **必读** |
| `registry.py` | 子智能体注册表 | 重点阅读 |
| `builtins/` | `general-purpose` 和 `bash` 实现 | 浏览 |

**执行流程**:
```
Lead Agent 调用 task() 工具
  → SubagentLimitMiddleware 截断超限调用
  → SubagentExecutor 在后台线程执行子智能体
  → 每 5 秒轮询状态
  → SSE 事件: task_started → task_running → task_completed/failed/timed_out
```

### 3.5 运行时 `packages/harness/deerflow/runtime/`

**优先级**: ⭐⭐⭐

| 文件 | 说明 |
|------|------|
| `RunManager` | 运行记录管理 |
| `StreamBridge` | SSE 流桥接 |
| `run_agent()` | 智能体执行入口 |

---

## 第 4 阶段：应用层（阅读时间: ~2 小时）

### 4.1 Gateway API `app/gateway/`

**优先级**: ⭐⭐⭐⭐

| 文件 | 说明 | 建议 |
|------|------|------|
| `app.py` | FastAPI 应用创建、生命周期管理 | **必读** |
| `routers/models.py` | 模型列表/详情 API | 浏览 |
| `routers/mcp.py` | MCP 配置管理 API | 浏览 |
| `routers/skills.py` | 技能管理/安装 API | 浏览 |
| `routers/memory.py` | 记忆管理 API | 浏览 |
| `routers/uploads.py` | 文件上传 API | 重点阅读 |
| `routers/threads.py` | 线程管理 API | 重点阅读 |
| `routers/artifacts.py` | 制品服务 API | 浏览 |
| `routers/agents.py` | 自定义智能体 API | 浏览 |
| `routers/suggestions.py` | 建议生成 API | 浏览 |
| `routers/channels.py` | 渠道路由 | 浏览 |

### 4.2 IM 渠道 `app/channels/`

**优先级**: ⭐⭐（按需，仅当需要理解 IM 接入时）

| 文件 | 说明 |
|------|------|
| `manager.py` | 核心调度器（消息队列消费） |
| `message_bus.py` | 异步发布/订阅总线 |
| `store.py` | JSON 文件持久化（渠道↔线程映射） |
| `service.py` | 渠道生命周期管理 |
| `base.py` | 抽象渠道基类 |
| `feishu.py` | 飞书实现（SSE 流式卡片） |
| `slack.py` | Slack 实现 |
| `telegram.py` | Telegram 实现 |
| `dingtalk.py` | 钉钉实现（AI Card） |

### 4.3 嵌入式客户端 `packages/harness/deerflow/client.py`

**优先级**: ⭐⭐⭐

`DeerFlowClient` 提供不依赖 HTTP 服务的直接调用方式。与 Gateway API 返回格式完全一致。

---

## 第 5 阶段：前端（阅读时间: ~3 小时）

### 5.1 入口与路由 `frontend/src/app/`

**优先级**: ⭐⭐⭐⭐

| 路径 | 说明 |
|------|------|
| `[lang]/` | 国际化路由（en/zh） |
| `workspace/chats/[thread_id]/` | 对话页面（核心页面） |
| `(auth)/` | 认证相关 |

### 5.2 核心业务逻辑 `frontend/src/core/`

**优先级**: ⭐⭐⭐⭐⭐（必读，前端的数据层）

| 目录 | 说明 | 建议 |
|------|------|------|
| `api/` | LangGraph SDK 客户端单例 | **必读** |
| `threads/` | 线程 Hook（`useThreadStream`、`useSubmitThread`） | **必读** |
| `messages/` | 消息处理和转换 | 重点阅读 |
| `artifacts/` | 制品加载和缓存 | 浏览 |
| `i18n/` | 国际化（en-US、zh-CN） | 浏览 |
| `settings/` | 用户偏好（localStorage） | 浏览 |
| `memory/` | 持久化记忆系统 | 浏览 |
| `skills/` | 技能安装管理 | 浏览 |
| `mcp/` | MCP 集成 | 浏览 |
| `models/` | TypeScript 类型和数据模型 | 浏览 |
| `tools/` | 工具管理 | 浏览 |
| `todos/` | 任务追踪 | 浏览 |
| `uploads/` | 文件上传 | 浏览 |
| `agents/` | 智能体管理 | 浏览 |
| `auth/` | 认证 | 浏览 |
| `notification/` | 通知系统 | 浏览 |
| `config/` | 前端配置 | 浏览 |
| `blog/` | 博客功能 | 浏览 |
| `rehype/` | Markdown 渲染 | 浏览 |
| `streamdown/` | 流式 Markdown 渲染 | 浏览 |

### 5.3 组件结构 `frontend/src/components/`

**优先级**: ⭐⭐⭐

| 目录 | 说明 | 建议 |
|------|------|------|
| `workspace/` | 对话页面组件（消息、制品、设置） | **重点阅读** |
| `ui/` | Shadcn UI 原语（自动生成） | 浏览 |
| `ai-elements/` | Vercel AI SDK 元素（自动生成） | 浏览 |
| `landing/` | 落地页组件 | 浏览 |

### 5.4 数据流

```
用户输入
  → thread hooks (core/threads/hooks.ts)
  → LangGraph SDK 流式传输
  → 流事件更新线程状态 (messages, artifacts, todos)
  → TanStack Query 管理服务端状态
  → localStorage 存储用户设置
  → 组件订阅状态并渲染更新
```

---

## 第 6 阶段：进阶主题（按需阅读）

### 6.1 测试 `backend/tests/`

| 测试 | 说明 |
|------|------|
| `test_harness_boundary.py` | App → Harness 导入防火墙 |
| `test_memory_updater.py` | 记忆更新器回归测试 |
| `test_client.py` | 嵌入式客户端单元测试（77 个） |
| `test_client_live.py` | 客户端实时集成测试 |
| `test_tracing_*.py` | 追踪系统测试 |
| `blocking_io/` | 阻塞 IO 运行时门禁测试 |

### 6.2 关键脚本 `scripts/`

| 脚本 | 说明 |
|------|------|
| `serve.sh` | 本地服务启动/停止管理 |
| `deploy.sh` | Docker 生产部署 |
| `docker.sh` | Docker 开发环境管理 |
| `doctor.py` | 系统诊断工具 |
| `setup_wizard.py` | 交互式配置向导 |
| `configure.py` | 配置文件生成 |
| `config-upgrade.sh` | 配置升级（合并新字段） |
| `check.py` | 依赖检查 |

### 6.3 CI/CD `.github/`

| 工作流 | 说明 |
|--------|------|
| `backend-unit-tests.yml` | 后端单元测试 |
| `backend-blocking-io-tests.yml` | 阻塞 IO 测试 |

---

## 阅读建议

### 按角色推荐路径

| 角色 | 推荐路径 |
|------|----------|
| **后端开发者** | 第 1 阶段 → 第 2 阶段 → 第 3 阶段 → 第 4 阶段 → 第 6 阶段 |
| **前端开发者** | 第 1 阶段（略读线程状态）→ 第 5 阶段 |
| **工具/插件开发者** | 第 1 阶段 → 第 2 阶段（重点 MCP/工具/技能）→ 第 3 阶段（中间件） |
| **运维/部署** | 第 1 阶段（配置）→ 第 4 阶段（Gateway）→ 第 6 阶段（脚本） |

### 最小必读路径（最快上手）

如果时间有限，至少阅读以下文件：

```
1. config.example.yaml                           ← 理解配置结构
2. packages/harness/deerflow/agents/thread_state.py  ← 理解状态模型
3. packages/harness/deerflow/agents/lead_agent/agent.py ← 理解智能体创建
4. packages/harness/deerflow/sandbox/sandbox.py   ← 理解沙箱接口
5. packages/harness/deerflow/sandbox/tools.py     ← 理解沙箱工具
6. app/gateway/app.py                             ← 理解 API 入口
7. frontend/src/core/threads/hooks.ts             ← 理解前端数据流
8. frontend/src/app/workspace/                    ← 理解前端页面结构
```

### 核心概念速查

| 概念 | 一句话解释 |
|------|-----------|
| **Lead Agent** | 通过 LangGraph 图编排的主智能体，调用工具 + 委派子智能体 |
| **Middleware** | 围绕模型调用的 18 个钩子，处理横切关注点 |
| **Sandbox** | 线程隔离的执行环境，虚拟路径映射到物理目录 |
| **Sub-Agent** | 被 Lead Agent 委派的独立智能体，并行执行子任务 |
| **Skill** | 可安装的领域专用工作流，通过系统提示词注入 |
| **MCP** | 外部工具服务器协议，支持 stdio/SSE/HTTP 传输 |
| **ThreadState** | 线程级状态对象，贯穿整个请求生命周期 |
| **Harness/App 分离** | 框架代码（deerflow）不依赖应用代码（app），单向依赖 |

---

> 📘 **相关文档**:
> - [项目说明书 (PROJECT_MANUAL.md)](./PROJECT_MANUAL.md) — 项目概述、架构、功能全览
> - [安装指南 (Install.md)](../Install.md) — 详细安装步骤
> - [后端 README](../backend/README.md) — 后端架构细节
> - [配置说明 (CONFIGURATION.md)](./CONFIGURATION.md) — 完整配置参考

---

## 关键 API 索引

本节按"方法维度"列出 DeerFlow 后端每个核心模块的关键入口函数 / 类，给出**文件相对路径:行号**、**函数签名**、**一句话作用**。行号取自 `backend/` 目录当前代码状态，路径相对于 `backend/`。

> **行号校准说明**：本节行号采集自 2026-06-07 15:00 (Asia/Shanghai) 时刻 `backend/` 实际代码状态。期间 `coder` 团队为多个 .py 文件补充了方法级中文 docstring，导致部分文件行号相对早期版本偏移 1-30 行；本节已用 `grep -n '^class \|^def \|^async def '` 重新核对每个文件，确保 `file:line` 指向当前代码中定义或装饰器的真实行号。

格式说明：每行 `file:line | name | 一句话作用`。多个同名函数用括号标注重载语义。

### 1. 基础设施层

#### 1.1 配置系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/config/app_config.py:92` | `class AppConfig` | Pydantic 根配置聚合，YAML 整体反序列化的目标类型 |
| `packages/harness/deerflow/config/app_config.py:379` | `get_app_config()` | 读取并缓存 `AppConfig`（按 mtime 自动重载） |
| `packages/harness/deerflow/config/app_config.py:413` | `reload_app_config()` | 强制重载 `AppConfig` |
| `packages/harness/deerflow/config/app_config.py:427` | `reset_app_config()` | 清除配置缓存 |
| `packages/harness/deerflow/config/app_config.py:440` | `set_app_config(config)` | 注入一个 `AppConfig` 实例（测试 / 启动期） |
| `packages/harness/deerflow/config/app_config.py:455` | `peek_current_app_config()` | 取当前 contextvar 顶部的配置（不触发加载） |
| `packages/harness/deerflow/config/app_config.py:464` / `:471` | `push_current_app_config()` / `pop_current_app_config()` | contextvar 压栈 / 弹栈（嵌套调用） |
| `packages/harness/deerflow/config/app_config.py:76` | `apply_logging_level(name)` | 把配置里的 `log_level` 反映到 root logger |
| `packages/harness/deerflow/config/app_config.py:49` | `class CircuitBreakerConfig` | 熔断器配置数据类 |
| `packages/harness/deerflow/config/extensions_config.py` | `ExtensionsConfig`、`McpServerConfig` | `extensions_config.json` 的 Pydantic 模型 |

#### 1.2 反射系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/reflection/resolvers.py:46` | `resolve_variable[T](module_path)` | 解析 `package.module:VAR` 形式的字符串到变量 |
| `packages/harness/deerflow/reflection/resolvers.py:94` | `resolve_class[T](class_path, base_class)` | 解析并校验类继承关系 |
| `packages/harness/deerflow/reflection/resolvers.py:21` | `_build_missing_dependency_hint(module_path, err)` | 缺包时返回 `uv add ...` 安装提示 |

#### 1.3 模型工厂

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/models/factory.py:70` | `create_chat_model(name, thinking_enabled, *, app_config, attach_tracing, **kwargs)` | 通用 LLM 工厂：通过 `app_config.models[].use` 反射加载 provider |
| `packages/harness/deerflow/models/factory.py:15` | `_deep_merge_dicts(base, override)` | 深度合并字典（model 字段覆盖） |
| `packages/harness/deerflow/models/factory.py:34` | `_vllm_disable_chat_template_kwargs(...)` | vLLM 关闭 chat_template_kwargs |
| `packages/harness/deerflow/models/factory.py:51` | `_enable_stream_usage_by_default(...)` | 默认开启流式 usage 回调 |
| `packages/harness/deerflow/models/vllm_provider.py` | `class VllmChatModel(ChatOpenAI)` | vLLM 0.19 兼容端点的 `ChatOpenAI` 子类 |
| `packages/harness/deerflow/models/credential_loader.py` | （凭证加载模块） | 多源凭证（env / file / keyring）加载 |

#### 1.4 线程状态

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/agents/thread_state.py:100` | `class ThreadState(AgentState)` | 线程级状态 Schema（`sandbox` / `thread_data` / `title` / `artifacts` / `todos` / `uploaded_files` / `viewed_images`） |
| `packages/harness/deerflow/agents/thread_state.py:12` | `class SandboxState(TypedDict)` | 沙箱子状态 |
| `packages/harness/deerflow/agents/thread_state.py:18` | `class ThreadDataState(TypedDict)` | 线程元数据（user_id / sandbox 路径等） |
| `packages/harness/deerflow/agents/thread_state.py:26` | `class ViewedImageData(TypedDict)` | 已查看图像的 TypedDict |
| `packages/harness/deerflow/agents/thread_state.py:33` | `merge_artifacts(existing, new)` | 制品列表去重合并 reducer |
| `packages/harness/deerflow/agents/thread_state.py:43` | `merge_viewed_images(existing, new)` | 已查看图像 merge / clear reducer |
| `packages/harness/deerflow/agents/thread_state.py:60` | `merge_todos(existing, new)` | todo 列表合并 reducer |
| `packages/harness/deerflow/agents/thread_state.py:79` | `merge_promoted(existing, new)` | 已提升工具集合并 reducer |
| `packages/harness/deerflow/agents/thread_state.py:72` | `class PromotedTools(TypedDict)` | 已提升工具集结构 |

#### 1.5 运行时基础设施

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/runtime/runs/worker.py:143` | `async run_agent(...)` | 智能体执行入口：装配 context、调用 LangGraph、产出 SSE 事件 |
| `packages/harness/deerflow/runtime/runs/worker.py:83` | `class RunContext` | 单次 run 的运行时上下文 |
| `packages/harness/deerflow/runtime/runs/worker.py:47` | `_build_runtime_context(...)` | 构造 LangGraph 调用所需的 `config["configurable"]` 上下文 |
| `packages/harness/deerflow/runtime/runs/worker.py:103` | `_install_runtime_context(config, runtime_context)` | 把 runtime_context 装入 RunnableConfig |
| `packages/harness/deerflow/runtime/runs/worker.py:104` / `:112` / `:116` | `_compute_agent_factory_supports_app_config` / `_cached_agent_factory_supports_app_config` / `_agent_factory_supports_app_config` | 探测 agent 工厂是否接受 app_config |
| `packages/harness/deerflow/runtime/runs/worker.py:478` | `_call_checkpointer_method(...)` | 异步调 checkpointer 同步方法（带分流） |
| `packages/harness/deerflow/runtime/runs/worker.py:489` | `_rollback_to_pre_run_checkpoint(...)` | 回滚到 run 启动前的 checkpoint |
| `packages/harness/deerflow/runtime/runs/worker.py:582` | `_new_checkpoint_marker()` | 构造新 checkpoint 标记 |
| `packages/harness/deerflow/runtime/runs/worker.py:588` | `_lg_mode_to_sse_event(mode)` | LangGraph stream mode → SSE event 映射 |
| `packages/harness/deerflow/runtime/runs/worker.py:605` | `_error_fallback_message_from_metadata(...)` | 从 metadata 构造错误回退消息 |
| `packages/harness/deerflow/runtime/runs/worker.py:618` | `_try_extract_from_message(obj)` | 尝试从对象中抽取消息文本 |
| `packages/harness/deerflow/runtime/runs/worker.py:631` | `_extract_llm_error_fallback_message(value)` | 抽取 LLM 错误回退消息 |
| `packages/harness/deerflow/runtime/runs/worker.py:691` | `_extract_human_message(graph_input)` | 从 graph_input 取 HumanMessage |
| `packages/harness/deerflow/runtime/runs/worker.py:722` | `_unpack_stream_item(...)` | 拆解 stream 事件 |
| `packages/harness/deerflow/runtime/runs/manager.py:147` | `class RunManager` | 运行记录管理器（in-memory + RunStore 双层） |
| `packages/harness/deerflow/runtime/runs/manager.py:87` | `class RunRecord` | 单次 run 的内存记录 |
| `packages/harness/deerflow/runtime/runs/manager.py:70` | `class PersistenceRetryPolicy` | 持久化失败重试策略 |
| `packages/harness/deerflow/runtime/runs/manager.py:34` | `_is_retryable_persistence_error(exc)` | 判断持久化错误是否可重试 |
| `packages/harness/deerflow/runtime/runs/manager.py:649` / `:653` | `ConflictError` / `UnsupportedStrategyError` | run 冲突 / 策略不支持异常 |
| `packages/harness/deerflow/runtime/runs/schemas.py:6` | `class RunStatus(StrEnum)` | run 状态枚举（pending / running / done / error / interrupted） |
| `packages/harness/deerflow/runtime/runs/schemas.py:29` | `class DisconnectMode(StrEnum)` | 客户端断连模式（cancel / continue） |
| `packages/harness/deerflow/runtime/runs/naming.py:9` | `resolve_root_run_name(config, assistant_id)` | 解析 run 的根名 |
| `packages/harness/deerflow/runtime/checkpointer/provider.py:49` | `_sync_checkpointer_cm(config)` | 同步 checkpointer 上下文管理器 |
| `packages/harness/deerflow/runtime/checkpointer/provider.py:103` | `get_checkpointer()` | 同步获取 checkpointer 单例 |
| `packages/harness/deerflow/runtime/checkpointer/provider.py:149` | `reset_checkpointer()` | 重置 checkpointer |
| `packages/harness/deerflow/runtime/checkpointer/provider.py:170` | `checkpointer_context()` | 上下文管理器版本 |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:37` | `_prepare_sqlite_checkpointer_path(raw)` | 准备 SQLite checkpointer 路径 |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:43` | `_prepare_database_sqlite_checkpointer_path(db_config)` | 准备 db 子配置的 SQLite checkpointer 路径 |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:49` | `_build_postgres_pool(conn_string)` | 构造 Postgres 连接池 |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:69` | `_ensure_postgres_imports()` | 校验 Postgres 依赖 |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:90` | `async _async_checkpointer(config)` | 异步 checkpointer 迭代器（config 形式） |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:131` | `async _async_checkpointer_from_database(db_config)` | 异步 checkpointer 迭代器（database 形式） |
| `packages/harness/deerflow/runtime/checkpointer/async_provider.py:167` | `async make_checkpointer(app_config)` | 异步构造 checkpointer（SQLite / Postgres） |
| `packages/harness/deerflow/runtime/stream_bridge/base.py:17` | `class StreamEvent` | 流事件数据类 |
| `packages/harness/deerflow/runtime/stream_bridge/base.py:37` | `class StreamBridge(abc.ABC)` | 流桥接抽象（`subscribe` / `publish`） |
| `packages/harness/deerflow/runtime/stream_bridge/memory.py:18` | `class _RunStream` | 单次 run 的流对象（内部） |
| `packages/harness/deerflow/runtime/stream_bridge/memory.py:25` | `class MemoryStreamBridge` | 内存版流桥接（in-process） |
| `packages/harness/deerflow/runtime/stream_bridge/async_provider.py:29` | `async make_stream_bridge(app_config)` | 根据 config 选择并构造流桥接 |
| `packages/harness/deerflow/runtime/journal.py:39` | `class RunJournal(BaseCallbackHandler)` | LangChain 回调，把模型 / 工具事件记录到 journal |
| `packages/harness/deerflow/runtime/user_context.py:38` | `class CurrentUser(Protocol)` | 当前用户协议 |
| `packages/harness/deerflow/runtime/user_context.py:51` | `set_current_user(user)` | 设置当前用户 contextvar |
| `packages/harness/deerflow/runtime/user_context.py:60` | `reset_current_user(token)` | 重置当前用户 contextvar |
| `packages/harness/deerflow/runtime/user_context.py:65` | `get_current_user()` | 取当前用户 contextvar |
| `packages/harness/deerflow/runtime/user_context.py:74` | `require_current_user()` | 取当前用户（无则抛错） |
| `packages/harness/deerflow/runtime/user_context.py:93` | `get_effective_user_id()` | 解析有效 user_id（无 auth 模式下为 `"default"`） |
| `packages/harness/deerflow/runtime/user_context.py:105` | `resolve_runtime_user_id(runtime)` | 从 LangGraph runtime 解析 user_id |
| `packages/harness/deerflow/runtime/user_context.py:141` | `class _AutoSentinel` | 自动哨兵值（让 contextvar 区分"未设"和"设了 None"） |
| `packages/harness/deerflow/runtime/user_context.py:158` | `resolve_user_id(...)` | 多源 user_id 解析（state / config / auth） |
| `packages/harness/deerflow/runtime/converters.py` | （converter 模块） | LangGraph 事件 / 消息转换 |
| `packages/harness/deerflow/runtime/serialization.py` | （序列化模块） | 状态 JSON 序列化 |

#### 1.6 持久化层

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/persistence/engine.py:19` | `_json_serializer(obj)` | JSON 序列化兜底（处理 datetime / UUID） |
| `packages/harness/deerflow/persistence/engine.py:30` | `async _auto_create_postgres_db(url)` | Postgres 模式下自动建库 |
| `packages/harness/deerflow/persistence/engine.py:61` | `async init_engine(config)` | 初始化 SQLAlchemy 异步引擎 |
| `packages/harness/deerflow/persistence/engine.py:174` | `async init_engine_from_config(config)` | 从 `AppConfig.database.*` 启动引擎（启动期调用一次） |
| `packages/harness/deerflow/persistence/engine.py:192` | `get_session_factory()` | 取 `async_sessionmaker` |
| `packages/harness/deerflow/persistence/engine.py:197` | `get_engine()` | 取 `AsyncEngine` 单例 |
| `packages/harness/deerflow/persistence/engine.py:202` | `async close_engine()` | 关闭引擎连接池 |
| `packages/harness/deerflow/persistence/feedback/model.py` | `class Feedback` | 反馈 ORM 模型 |
| `packages/harness/deerflow/persistence/run/model.py` | `class Run` | 运行 ORM 模型 |
| `packages/harness/deerflow/persistence/thread_meta/model.py` | `class ThreadMeta` | 线程元数据 ORM 模型 |
| `packages/harness/deerflow/persistence/user/model.py` | `class User` | 用户 ORM 模型 |
| `packages/harness/deerflow/persistence/models/run_event.py` | （`RunEvent`） | 运行事件 ORM 模型 |
| `packages/harness/deerflow/persistence/migrations/` | （Alembic 迁移） | `alembic.ini` / `env.py` / `versions/` |

#### 1.7 上传底层原语

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/uploads/manager.py:18` | `class PathTraversalError` | path traversal 异常 |
| `packages/harness/deerflow/uploads/manager.py:22` | `class UnsafeUploadPathError` | 不安全的上传路径异常 |
| `packages/harness/deerflow/uploads/manager.py:30` | `validate_thread_id(thread_id)` | 校验 thread_id 格式（防路径穿越） |
| `packages/harness/deerflow/uploads/manager.py:40` | `get_uploads_dir(thread_id)` | 取线程上传目录（不创建） |
| `packages/harness/deerflow/uploads/manager.py:56` | `ensure_uploads_dir(thread_id)` | 取并创建线程上传目录 |
| `packages/harness/deerflow/uploads/manager.py:74` | `normalize_filename(filename)` | 文件名清理（去危险字符） |
| `packages/harness/deerflow/uploads/manager.py:102` | `claim_unique_filename(name, seen)` | 在已占用集合里挑一个不冲突的文件名（加 `_N` 后缀） |
| `packages/harness/deerflow/uploads/manager.py:127` | `validate_path_traversal(path, base)` | 拒绝 path traversal |
| `packages/harness/deerflow/uploads/manager.py:143` | `open_upload_file_no_symlink(base, filename)` | 用 `O_NOFOLLOW` 安全打开（拒绝 symlink） |
| `packages/harness/deerflow/uploads/manager.py:250` | `write_upload_file_no_symlink(base, filename, data)` | 安全写入（原子替换） |
| `packages/harness/deerflow/uploads/manager.py:274` | `list_files_in_dir(directory)` | 列目录并补齐元信息 |
| `packages/harness/deerflow/uploads/manager.py:306` | `delete_file_safe(base, filename, *, convertible_extensions)` | 安全删除（含可转换扩展名支持） |
| `packages/harness/deerflow/uploads/manager.py:340` | `upload_artifact_url(thread_id, filename)` | 构造可下载 URL |
| `packages/harness/deerflow/uploads/manager.py:356` | `upload_virtual_path(filename)` | 映射到 `/mnt/user-data/uploads/...` 虚拟路径 |
| `packages/harness/deerflow/uploads/manager.py:368` | `enrich_file_listing(result, thread_id)` | 补齐列表响应中的 URL / 虚拟路径 |

#### 1.8 追踪系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/tracing/factory.py:18` | `_create_langsmith_tracer(config)` | 构造 LangSmith handler |
| `packages/harness/deerflow/tracing/factory.py:32` | `_create_langfuse_handler(config)` | 构造 Langfuse v4 handler |
| `packages/harness/deerflow/tracing/factory.py:57` | `build_tracing_callbacks()` | 根据 env 构造 LangSmith / Langfuse 回调列表 |
| `packages/harness/deerflow/tracing/metadata.py:28` | `build_langfuse_trace_metadata(...)` | 构造 Langfuse trace 元数据（session_id / user_id / tags） |
| `packages/harness/deerflow/tracing/metadata.py:75` | `inject_langfuse_metadata(...)` | 注入到 `RunnableConfig.metadata` |

#### 1.9 工具函数

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/utils/network.py` | （网络工具） | HTTP 探测 / 重试 |
| `packages/harness/deerflow/utils/readability.py` | （readability 工具） | HTML → 纯文本提取 |
| `packages/harness/deerflow/utils/file_conversion.py` | （文档转换） | `markitdown` 包装，PDF / Office → Markdown |
| `packages/harness/deerflow/utils/time.py` | （时间工具） | UTC ISO 8601、相对时间 |

### 2. 核心能力层

#### 2.1 沙箱系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/sandbox/sandbox.py:12` | `class Sandbox(ABC)` | 抽象沙箱（`execute_command` / `read_file` / `write_file` / `list_dir`） |
| `packages/harness/deerflow/sandbox/sandbox_provider.py:16` | `class SandboxProvider(ABC)` | 沙箱提供者协议（`acquire` / `get` / `release` 等） |
| `packages/harness/deerflow/sandbox/sandbox_provider.py:71` | `get_sandbox_provider(**kwargs)` | 反射加载并返回 provider 单例 |
| `packages/harness/deerflow/sandbox/sandbox_provider.py:88` | `reset_sandbox_provider()` | 清除 provider 缓存 |
| `packages/harness/deerflow/sandbox/sandbox_provider.py:107` | `shutdown_sandbox_provider()` | 关闭 provider，释放资源 |
| `packages/harness/deerflow/sandbox/sandbox_provider.py:119` | `set_sandbox_provider(provider)` | 注入 provider 实例 |
| `packages/harness/deerflow/sandbox/local/local_sandbox.py:54` | `class LocalSandbox(Sandbox)` | 本地沙箱（虚拟路径 ↔ 物理路径） |
| `packages/harness/deerflow/sandbox/local/local_sandbox.py:28` | `class PathMapping` | 虚拟路径 → 物理目录的映射条目 |
| `packages/harness/deerflow/sandbox/local/local_sandbox.py:42` | `class ResolvedPath(NamedTuple)` | 已解析路径 (虚拟, 物理) |
| `packages/harness/deerflow/sandbox/local/local_sandbox_provider.py:40` | `class LocalSandboxProvider(SandboxProvider)` | 本地 provider：每线程一个 `LocalSandbox`，LRU 缓存 |
| `packages/harness/deerflow/sandbox/local/list_dir.py:12` | `list_dir(path, max_depth=2)` | 树形目录列表（最多 2 层） |
| `packages/harness/deerflow/sandbox/middleware.py:33` | `class SandboxMiddleware` | 沙箱生命周期中间件（在请求期内 acquire / release） |
| `packages/harness/deerflow/sandbox/middleware.py:21` | `class SandboxMiddlewareState(AgentState)` | 沙箱中间件状态扩展 |
| `packages/harness/deerflow/sandbox/tools.py:1516` | `bash_tool(runtime, description, command)` | 同步版 bash 工具 |
| `packages/harness/deerflow/sandbox/tools.py:1564` | `async _bash_tool_async(...)` | 异步版 bash 工具 |
| `packages/harness/deerflow/sandbox/tools.py:1572` | `ls_tool(runtime, description, path)` | 同步版 ls 工具 |
| `packages/harness/deerflow/sandbox/tools.py:1618` | `async _ls_tool_async(...)` | 异步版 ls |
| `packages/harness/deerflow/sandbox/tools.py:1626` | `glob_tool(...)` | 同步版 glob 工具 |
| `packages/harness/deerflow/sandbox/tools.py:1675` | `async _glob_tool_async(...)` | 异步版 glob |
| `packages/harness/deerflow/sandbox/tools.py:1698` | `grep_tool(...)` | 同步版 grep 工具 |
| `packages/harness/deerflow/sandbox/tools.py:1767` | `async _grep_tool_async(...)` | 异步版 grep |
| `packages/harness/deerflow/sandbox/tools.py:1794` | `read_file_tool(...)` | 同步版 read_file |
| `packages/harness/deerflow/sandbox/tools.py:1848` | `async _read_file_tool_async(...)` | 异步版 read_file |
| `packages/harness/deerflow/sandbox/tools.py:1862` | `write_file_tool(...)` | 同步版 write_file（含 `append` 参数） |
| `packages/harness/deerflow/sandbox/tools.py:1908` | `async _write_file_tool_async(...)` | 异步版 write_file |
| `packages/harness/deerflow/sandbox/tools.py:1922` | `str_replace_tool(...)` | 同步版 str_replace（substring 替换） |
| `packages/harness/deerflow/sandbox/tools.py:1972` | `async _str_replace_tool_async(...)` | 异步版 str_replace |
| `packages/harness/deerflow/sandbox/tools.py:597` | `replace_virtual_path(path, thread_data)` | 虚拟路径 → 物理路径 |
| `packages/harness/deerflow/sandbox/tools.py:1127` | `replace_virtual_paths_in_command(command, thread_data)` | 替换 shell 命令里的虚拟路径 |
| `packages/harness/deerflow/sandbox/tools.py:1076` | `resolve_and_validate_user_data_path(path, thread_data)` | 解析 + 校验 user-data 路径 |
| `packages/harness/deerflow/sandbox/tools.py:1081` | `validate_local_bash_command_paths(command, thread_data)` | 校验 bash 命令路径白名单 |
| `packages/harness/deerflow/sandbox/tools.py:1225` | `sandbox_from_runtime(runtime)` | 从 LangGraph runtime 取沙箱实例 |
| `packages/harness/deerflow/sandbox/tools.py:1260` | `ensure_sandbox_initialized(runtime)` | 同步版：保证沙箱已初始化 |
| `packages/harness/deerflow/sandbox/tools.py:1317` | `async ensure_sandbox_initialized_async(runtime)` | 异步版：保证沙箱已初始化 |
| `packages/harness/deerflow/sandbox/tools.py:1199` | `is_local_sandbox(runtime)` | 判断当前是否在本地沙箱内 |
| `packages/harness/deerflow/sandbox/tools.py:1388` | `ensure_thread_directories_exist(runtime)` | 一次性创建线程 user-data 子目录 |
| `packages/harness/deerflow/sandbox/search.py:168` | `find_glob_matches(...)` | glob 匹配实现 |
| `packages/harness/deerflow/sandbox/search.py:223` | `find_grep_matches(...)` | grep 匹配实现 |
| `packages/harness/deerflow/sandbox/search.py:149` | `is_binary_file(path)` | 二进制文件检测 |
| `packages/harness/deerflow/sandbox/search.py:71` | `class GrepMatch` | grep 匹配结果数据类 |
| `packages/harness/deerflow/sandbox/search.py:85` / `:100` / `:112` | `should_ignore_name` / `should_ignore_path` / `path_matches` | glob/grep 路径过滤辅助 |
| `packages/harness/deerflow/sandbox/search.py:133` | `truncate_line(line, max_chars)` | 行截断（避免超长行爆输出） |
| `packages/harness/deerflow/sandbox/file_operation_lock.py:19` | `get_file_operation_lock_key(sandbox, path)` | 计算 `(sandbox.id, path)` 锁键 |
| `packages/harness/deerflow/sandbox/file_operation_lock.py:38` | `get_file_operation_lock(sandbox, path)` | `(sandbox.id, path)` 粒度的串行化锁 |
| `packages/harness/deerflow/sandbox/security.py:23` | `uses_local_sandbox_provider(config)` | 配置里是否使用本地 provider |
| `packages/harness/deerflow/sandbox/security.py:42` | `is_host_bash_allowed(config)` | host bash 是否被允许 |
| `packages/harness/deerflow/sandbox/exceptions.py:8` / `:39` / `:54` / `:60` / `:81` / `:102` / `:108` | `SandboxError` / `SandboxNotFoundError` / `SandboxRuntimeError` / `SandboxCommandError` / `SandboxFileError` / `SandboxPermissionError` / `SandboxFileNotFoundError` | 沙箱异常类型层次 |

#### 2.2 MCP 集成

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/mcp/client.py:11` | `build_server_params(server_name, config)` | 把单条 MCP server 配置转成 `MultiServerMCPClient` 需要的参数 |
| `packages/harness/deerflow/mcp/client.py:48` | `build_servers_config(extensions_config)` | 把 `ExtensionsConfig` 整本展开成多 server 配置 |
| `packages/harness/deerflow/mcp/cache.py:17` | `_get_config_mtime()` | 取 `extensions_config.json` 的 mtime |
| `packages/harness/deerflow/mcp/cache.py:31` | `_is_cache_stale()` | 内部：判断缓存是否过期 |
| `packages/harness/deerflow/mcp/cache.py:56` | `async initialize_mcp_tools()` | 首次初始化 MCP 工具（懒加载入口） |
| `packages/harness/deerflow/mcp/cache.py:82` | `get_cached_mcp_tools()` | 取缓存的 MCP 工具列表（mtime 失效） |
| `packages/harness/deerflow/mcp/cache.py:128` | `reset_mcp_tools_cache()` | 清空 MCP 工具缓存 |
| `packages/harness/deerflow/mcp/tools.py:23` | `_extract_thread_id(runtime)` | 从 runtime 抽 thread_id |
| `packages/harness/deerflow/mcp/tools.py:41` | `_convert_call_tool_result(call_tool_result)` | 把 MCP CallToolResult 转成 LangChain 兼容结果 |
| `packages/harness/deerflow/mcp/tools.py:107` | `_make_session_pool_tool(...)` | 把 MCP 工具包成带 session pool 的 langchain 工具 |
| `packages/harness/deerflow/mcp/tools.py:195` | `async get_mcp_tools()` | 异步获取并包装 MCP 工具 |
| `packages/harness/deerflow/mcp/oauth.py:17` | `class _OAuthToken` | 单条 token 状态 |
| `packages/harness/deerflow/mcp/oauth.py:31` | `class OAuthTokenManager` | OAuth token 生命周期管理（`client_credentials` / `refresh_token`） |
| `packages/harness/deerflow/mcp/oauth.py:146` | `build_oauth_tool_interceptor(extensions_config)` | 构造可注入 `Authorization` 头的 MCP 工具拦截器 |
| `packages/harness/deerflow/mcp/oauth.py:172` | `async get_initial_oauth_headers(extensions_config)` | 取首调用前的初始 OAuth header |
| `packages/harness/deerflow/mcp/session_pool.py:25` | `class MCPSessionPool` | MCP session 复用池 |
| `packages/harness/deerflow/mcp/session_pool.py:183` | `get_session_pool()` | 取 session pool 单例 |
| `packages/harness/deerflow/mcp/session_pool.py:193` | `reset_session_pool()` | 重置 session pool |

#### 2.3 工具系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/tools/tools.py:28` | `_is_host_bash_tool(tool)` | 判断工具是否为 host bash 工具 |
| `packages/harness/deerflow/tools/tools.py:39` | `_ensure_sync_invocable_tool(tool)` | 把 async 工具包成同步可调用 |
| `packages/harness/deerflow/tools/tools.py:46` | `get_available_tools(groups, include_mcp, model_name, subagent_enabled, ...)` | 工具组装总入口：合并 config / MCP / 内置 / 社区 / 子智能体工具 |
| `packages/harness/deerflow/tools/sync.py:22` | `_get_runnable_config_param(func)` | 抽取 sync wrapper 需要注入的 RunnableConfig 参数名 |
| `packages/harness/deerflow/tools/sync.py:45` | `make_sync_tool_wrapper(coro, tool_name)` | 把 async 工具包装成同步版 |
| `packages/harness/deerflow/tools/mcp_metadata.py:19` | `tag_mcp_tool(tool)` | 标记工具为 MCP 来源 |
| `packages/harness/deerflow/tools/mcp_metadata.py:32` | `is_mcp_tool(tool)` | 判断工具是否 MCP 来源 |
| `packages/harness/deerflow/tools/skill_manage_tool.py:25` | `_get_lock(name)` | 取技能名级 asyncio 锁 |
| `packages/harness/deerflow/tools/skill_manage_tool.py:34` | `_get_thread_id(runtime)` | 从 runtime 取 thread_id |
| `packages/harness/deerflow/tools/skill_manage_tool.py:43` | `_history_record(...)` | 构造技能历史记录条目 |
| `packages/harness/deerflow/tools/skill_manage_tool.py:56` | `async _scan_or_raise(content, *, executable, location)` | 扫描内容（不通过则抛） |
| `packages/harness/deerflow/tools/skill_manage_tool.py:66` | `async _to_thread(func, /, *args, **kwargs)` | 把 sync IO 卸到线程 |
| `packages/harness/deerflow/tools/skill_manage_tool.py:71` | `async _skill_manage_impl(...)` | 技能管理内部实现 |
| `packages/harness/deerflow/tools/skill_manage_tool.py:218` | `async skill_manage_tool(...)` | 技能内容编辑 / 历史查看（带安全扫描） |
| `packages/harness/deerflow/tools/builtins/clarification_tool.py:9` | `ask_clarification_tool(...)` | 澄清请求工具（被 `ClarificationMiddleware` 拦截） |
| `packages/harness/deerflow/tools/builtins/present_file_tool.py:18` | `_get_thread_id(runtime)` | 从 runtime 取 thread_id（present_file 用） |
| `packages/harness/deerflow/tools/builtins/present_file_tool.py:35` | `_normalize_presented_filepath(...)` | 规范化 / 校验用户提交的文件路径 |
| `packages/harness/deerflow/tools/builtins/present_file_tool.py:89` | `present_file_tool(...)` | 把输出文件声明为可下载制品 |
| `packages/harness/deerflow/tools/builtins/view_image_tool.py:31` | `_is_allowed_image_virtual_path(image_path)` | 校验图像虚拟路径白名单 |
| `packages/harness/deerflow/tools/builtins/view_image_tool.py:36` | `_detect_image_mime(image_data)` | 探测图片 MIME |
| `packages/harness/deerflow/tools/builtins/view_image_tool.py:47` | `_sanitize_image_error(error, thread_data)` | 图像读取错误脱敏 |
| `packages/harness/deerflow/tools/builtins/view_image_tool.py:55` | `view_image_tool(...)` | 把图片读取为 base64（仅 vision 模型） |
| `packages/harness/deerflow/tools/builtins/task_tool.py:35` | `_token_usage_cache_enabled(app_config)` | 是否缓存子智能体 token 用量 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:45` | `_cache_subagent_usage(...)` | 缓存单次子智能体调用的 usage |
| `packages/harness/deerflow/tools/builtins/task_tool.py:51` | `pop_cached_subagent_usage(tool_call_id)` | 取出并清掉缓存的子智能体 usage |
| `packages/harness/deerflow/tools/builtins/task_tool.py:56` | `_is_subagent_terminal(result)` | 判断子智能体结果是否终态 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:61` | `async _await_subagent_terminal(...)` | 轮询等待子智能体终态 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:73` | `async _deferred_cleanup_subagent_task(...)` | 延迟清理子智能体后台任务 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:90` | `_log_cleanup_failure(cleanup_task, *, trace_id, task_id)` | 记录清理失败日志 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:100` | `_schedule_deferred_subagent_cleanup(...)` | 调度延迟清理 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:107` | `_find_usage_recorder(runtime)` | 从 runtime 找 token 用量记录器 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:138` | `_summarize_usage(records)` | 汇总子智能体 usage |
| `packages/harness/deerflow/tools/builtins/task_tool.py:149` | `_report_subagent_usage(runtime, result)` | 上报子智能体 usage 到 lead agent |
| `packages/harness/deerflow/tools/builtins/task_tool.py:170` | `_get_runtime_app_config(runtime)` | 从 runtime 取 app_config |
| `packages/harness/deerflow/tools/builtins/task_tool.py:180` | `_merge_skill_allowlists(parent, child)` | 合并父子 skill allowed-tools 列表 |
| `packages/harness/deerflow/tools/builtins/task_tool.py:192` | `async task_tool(...)` | 委派任务给子智能体（描述 + 提示 + 类型） |
| `packages/harness/deerflow/tools/builtins/setup_agent_tool.py:19` | `setup_agent(...)` | bootstrap 阶段：创建新自定义 agent |
| `packages/harness/deerflow/tools/builtins/update_agent_tool.py:37` | `_stage_temp(path, text)` | 写到临时文件，原子替换准备 |
| `packages/harness/deerflow/tools/builtins/update_agent_tool.py:69` | `_cleanup_temps(temps)` | 清理临时文件 |
| `packages/harness/deerflow/tools/builtins/update_agent_tool.py:78` | `_is_nullish_string(value)` | 判断字符串是否 nullish |
| `packages/harness/deerflow/tools/builtins/update_agent_tool.py:83` | `_normalize_nullish_string(value)` | 规范化 nullish 字符串 |
| `packages/harness/deerflow/tools/builtins/update_agent_tool.py:93` | `update_agent(...)` | 自定义 agent 自更新 `SOUL.md` / `config.yaml` |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:15` | `class _InvokeACPAgentInput(BaseModel)` | ACP 工具入参模型 |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:22` | `_get_work_dir(thread_id)` | 按 thread 取 ACP 工作目录 |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:55` | `_build_mcp_servers()` | 构造 ACP 工具的 MCP servers 段 |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:63` | `_build_acp_mcp_servers()` | 构造 ACP 协议专用 MCP servers |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:105` | `_build_permission_response(options, *, auto_approve)` | 构造 ACP permission 响应 |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:135` | `_format_invocation_error(agent, cmd, exc)` | ACP 调用错误格式化 |
| `packages/harness/deerflow/tools/builtins/invoke_acp_agent_tool.py:156` | `build_invoke_acp_agent_tool(agents)` | 构造 ACP 协议外部 agent 调用工具 |
| `packages/harness/deerflow/tools/builtins/tool_search.py:37` | `_compile_catalog_regex(pattern)` | 编译工具目录的 regex |
| `packages/harness/deerflow/tools/builtins/tool_search.py:55` | `class DeferredToolCatalog` | 延迟绑定工具的目录 |
| `packages/harness/deerflow/tools/builtins/tool_search.py:109` | `_catalog_regex_score(pattern, t)` | regex 模式对工具的得分 |
| `packages/harness/deerflow/tools/builtins/tool_search.py:119` | `class DeferredToolSetup` | 延迟工具绑定上下文（catalog + regex） |
| `packages/harness/deerflow/tools/builtins/tool_search.py:138` | `build_tool_search_tool(catalog)` | 构造 `tool_search` 工具 |
| `packages/harness/deerflow/tools/builtins/tool_search.py:172` | `build_deferred_tool_setup(filtered_tools, *, enabled)` | 构造 `DeferredToolSetup` 实例 |

#### 2.4 技能系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/skills/types.py:10` | `class SkillCategory(StrEnum)` | 技能分类枚举（public / custom） |
| `packages/harness/deerflow/skills/types.py:22` | `class Skill` | 技能数据类 |
| `packages/harness/deerflow/skills/parser.py:14` | `_format_yaml_error(skill_file, exc, source)` | 格式化 YAML 解析错误 |
| `packages/harness/deerflow/skills/parser.py:45` | `parse_allowed_tools(raw, skill_file)` | 解析 frontmatter 里的 `allowed-tools` |
| `packages/harness/deerflow/skills/parser.py:75` | `parse_skill_file(skill_file, category, relative_path)` | 解析单个 `SKILL.md`（YAML + Markdown） |
| `packages/harness/deerflow/skills/validation.py:18` | `_validate_skill_frontmatter(skill_dir)` | frontmatter 必填字段校验 |
| `packages/harness/deerflow/skills/installer.py:24` | `class SkillAlreadyExistsError` | 技能已存在异常 |
| `packages/harness/deerflow/skills/installer.py:28` | `class SkillSecurityScanError` | 技能安全扫描失败异常 |
| `packages/harness/deerflow/skills/installer.py:32` | `is_unsafe_zip_member(info)` | zip 成员路径穿越检查 |
| `packages/harness/deerflow/skills/installer.py:50` | `is_symlink_member(info)` | 拒绝 symlink 成员 |
| `packages/harness/deerflow/skills/installer.py:56` | `should_ignore_archive_entry(path)` | 是否忽略该归档条目 |
| `packages/harness/deerflow/skills/installer.py:61` | `resolve_skill_dir_from_archive(temp_path)` | 决定归档解压到哪个 skill 名 |
| `packages/harness/deerflow/skills/installer.py:81` | `safe_extract_skill_archive(...)` | 安全解压 `.skill` 归档（拒绝 unsafe / symlink） |
| `packages/harness/deerflow/skills/installer.py:130` | `_is_script_support_file(rel_path)` | 是否为脚本类支持文件 |
| `packages/harness/deerflow/skills/installer.py:135` | `_should_scan_support_file(rel_path)` | 是否需要扫描该支持文件 |
| `packages/harness/deerflow/skills/installer.py:142` | `_move_staged_skill_into_reserved_target(staging_target, target)` | 把暂存目录搬到保留的目标位置 |
| `packages/harness/deerflow/skills/installer.py:160` | `async _scan_skill_file_or_raise(...)` | 扫描单个文件（失败则抛） |
| `packages/harness/deerflow/skills/installer.py:186` | `async _scan_skill_archive_contents_or_raise(...)` | 扫描归档全部内容 |
| `packages/harness/deerflow/skills/installer.py:206` | `_run_async_install(coro)` | 同步上下文跑异步安装流程 |
| `packages/harness/deerflow/skills/security_scanner.py:19` | `class ScanResult` | 扫描结果数据类 |
| `packages/harness/deerflow/skills/security_scanner.py:31` | `_extract_json_object(raw)` | 从 raw 文本中提取 JSON 对象 |
| `packages/harness/deerflow/skills/security_scanner.py:78` | `async scan_skill_content(content, *, executable, location, app_config)` | 技能内容安全扫描（异步 LLM 检查） |
| `packages/harness/deerflow/skills/permissions.py:7` | `make_skill_path_sandbox_readable(path)` | 把单文件权限设为沙箱可读 |
| `packages/harness/deerflow/skills/permissions.py:19` | `make_skill_tree_sandbox_readable(target)` | 递归把目录权限设为沙箱可读 |
| `packages/harness/deerflow/skills/permissions.py:26` | `make_skill_written_path_sandbox_readable(skill_root, target)` | 写入后恢复沙箱可读 |
| `packages/harness/deerflow/skills/tool_policy.py:11` | `class NamedTool(Protocol)` | 带 `name` 属性的工具协议 |
| `packages/harness/deerflow/skills/tool_policy.py:17` | `allowed_tool_names_for_skills(skills)` | 聚合所有 skill 的 `allowed-tools` |
| `packages/harness/deerflow/skills/tool_policy.py:43` | `filter_tools_by_skill_allowed_tools(tools, skills)` | 按 skill 策略过滤工具 |
| `packages/harness/deerflow/skills/storage/skill_storage.py:18` | `class SkillStorage(ABC)` | 技能存储抽象 |
| `packages/harness/deerflow/skills/storage/local_skill_storage.py:25` | `class LocalSkillStorage(SkillStorage)` | 本地文件实现（`load_skills` 等异步化） |

#### 2.5 护栏系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/guardrails/middleware.py:20` | `class GuardrailMiddleware(AgentMiddleware[AgentState])` | 工具调用前授权检查中间件 |
| `packages/harness/deerflow/guardrails/provider.py:10` | `class GuardrailRequest` | 单次工具调用的护栏请求 |
| `packages/harness/deerflow/guardrails/provider.py:31` | `class GuardrailReason` | 拒绝 / 通过的原因 |
| `packages/harness/deerflow/guardrails/provider.py:44` | `class GuardrailDecision` | 护栏决策 |
| `packages/harness/deerflow/guardrails/provider.py:61` | `class GuardrailProvider(Protocol)` | 护栏 provider 协议 |
| `packages/harness/deerflow/guardrails/builtin.py:10` | `class AllowlistProvider` | 内置 zero-deps allowlist 实现 |

#### 2.6 社区工具与 Docker 沙箱

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/community/tavily/tools.py:11` | `_get_tavily_client()` | 取 / 构造 Tavily 客户端 |
| `packages/harness/deerflow/community/tavily/tools.py:21` | `web_search_tool(query)` | Tavily 文本搜索 |
| `packages/harness/deerflow/community/tavily/tools.py:47` | `web_fetch_tool(url)` | Tavily 网页抓取 |
| `packages/harness/deerflow/community/jina_ai/tools.py:15` | `async web_fetch_tool(url)` | Jina reader 抓取 |
| `packages/harness/deerflow/community/firecrawl/tools.py:11` | `_get_firecrawl_client(tool_name)` | 取 / 构造 Firecrawl 客户端 |
| `packages/harness/deerflow/community/firecrawl/tools.py:18` / `:50` | `web_search_tool` / `web_fetch_tool` | Firecrawl 搜索 / 抓取 |
| `packages/harness/deerflow/community/infoquest/tools.py:13` | `_get_infoquest_client()` | 取 / 构造 InfoQuest 客户端 |
| `packages/harness/deerflow/community/infoquest/tools.py:47` / `:59` / `:78` | `web_search_tool` / `web_fetch_tool` / `image_search_tool` | InfoQuest 三件套 |
| `packages/harness/deerflow/community/image_search/tools.py:13` | `_search_images(...)` | DuckDuckGo 图片搜索内部实现 |
| `packages/harness/deerflow/community/image_search/tools.py:75` | `image_search_tool(query)` | DuckDuckGo 图片搜索 |
| `packages/harness/deerflow/community/ddg_search/tools.py:13` | `_search_text(...)` | DuckDuckGo 文本搜索内部实现 |
| `packages/harness/deerflow/community/ddg_search/tools.py:53` | `web_search_tool(query)` | DuckDuckGo 文本搜索 |
| `packages/harness/deerflow/community/serper/tools.py:22` | `_get_api_key()` | 取 Serper API key |
| `packages/harness/deerflow/community/serper/tools.py:33` | `web_search_tool(query, max_results=5)` | Serper.dev Google 搜索 |
| `packages/harness/deerflow/community/exa/tools.py:11` | `_get_exa_client(tool_name)` | 取 / 构造 Exa 客户端 |
| `packages/harness/deerflow/community/exa/tools.py:18` / `:57` | `web_search_tool` / `web_fetch_tool` | Exa 神经搜索 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py:35` | `class AioSandbox(Sandbox)` | Docker 容器化沙箱实现 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:53` | `_lock_file_exclusive(lock_file)` | flock 排他锁 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:62` | `_unlock_file(lock_file)` | flock 释放 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:71` | `_open_lock_file(lock_path)` | 打开 lock 文件 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:75` | `async _acquire_thread_lock_async(lock)` | 异步拿线程锁 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:90` | `_release_cancelled_lock_acquire(lock, task)` | 释放被取消的锁获取 |
| `packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py:105` | `class AioSandboxProvider(SandboxProvider)` | Docker provider（带 async 锁） |

### 3. 智能体系统

#### 3.1 智能体工厂

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/agents/factory.py:59` | `create_deerflow_agent(...)` | 高级智能体工厂：基于 `RuntimeFeatures` 组装中间件链 |
| `packages/harness/deerflow/agents/factory.py:142` | `_assemble_from_features(...)` | 内部：按 features 顺序拼接中间件 |
| `packages/harness/deerflow/agents/factory.py:293` | `_insert_extra(chain, extras)` | 在中间件链中插入额外的中间件 |
| `packages/harness/deerflow/agents/features.py:15` | `class RuntimeFeatures` | 声明式中间件链配置（anchor 模式） |
| `packages/harness/deerflow/agents/features.py:42` / `:54` | `Next(anchor)` / `Prev(anchor)` | 中间件相对位置装饰器 |
| `packages/harness/deerflow/agents/lead_agent/agent.py:57` | `_get_runtime_config(config)` | 从 RunnableConfig 抽 runtime config |
| `packages/harness/deerflow/agents/lead_agent/agent.py:66` | `_resolve_model_name(requested_model_name, *, app_config)` | 解析要用的模型名（按 config 兜底） |
| `packages/harness/deerflow/agents/lead_agent/agent.py:81` | `_create_summarization_middleware(*, app_config)` | 按 config 构造摘要中间件（可能为 None） |
| `packages/harness/deerflow/agents/lead_agent/agent.py:147` | `_create_todo_list_middleware(is_plan_mode)` | 按 plan mode 构造 todo 中间件（可能为 None） |
| `packages/harness/deerflow/agents/lead_agent/agent.py:272` | `_build_middlewares(...)` | 主智能体中间件链组装 |
| `packages/harness/deerflow/agents/lead_agent/agent.py:368` | `_assemble_deferred(filtered_tools, *, enabled)` | 构造延迟工具绑定集合 |
| `packages/harness/deerflow/agents/lead_agent/agent.py:387` | `_available_skill_names(agent_config, is_bootstrap)` | 决定当前 agent 可用的技能名集合 |
| `packages/harness/deerflow/agents/lead_agent/agent.py:395` | `_load_enabled_skills_for_tool_policy(available_skills, *, app_config)` | 加载已启用的技能以做工具策略过滤 |
| `packages/harness/deerflow/agents/lead_agent/agent.py:409` | `make_lead_agent(config: RunnableConfig)` | 主智能体工厂（`langgraph.json` 中注册的入口） |
| `packages/harness/deerflow/agents/lead_agent/agent.py:416` | `_make_lead_agent(config, *, app_config)` | 内部：实际构建 LangGraph 图 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:34` | `_load_enabled_skills_sync()` | 同步加载已启用技能（启动期） |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:39` | `_start_enabled_skills_refresh_thread()` | 启动后台技能刷新线程 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:48` | `_refresh_enabled_skills_cache_worker()` | 后台刷新技能缓存工作函数 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:74` / `:91` | `_ensure_enabled_skills_cache()` / `_invalidate_enabled_skills_cache()` | 缓存就绪 / 失效原语 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:109` | `prime_enabled_skills_cache()` | 启动期预热技能缓存 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:114` | `warm_enabled_skills_cache(timeout_seconds)` | 同步等待后台技能刷新结束 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:123` | `_get_enabled_skills()` | 内部：取已启用技能（无锁） |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:128` | `get_cached_enabled_skills()` | 取已启用技能（带 mtime 缓存） |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:144` | `get_enabled_skills_for_config(app_config)` | 按 app_config 取已启用技能 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:168` | `_skill_mutability_label(category)` | 技能可变性标签 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:173` | `clear_skills_system_prompt_cache()` | 清空技能 prompt 缓存 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:178` | `async refresh_skills_system_prompt_cache_async()` | 异步刷新技能 prompt 缓存 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:183` | `_build_skill_evolution_section(skill_evolution_enabled)` | 拼装技能自进化提示段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:200` | `_build_available_subagents_description(...)` | 拼装可用子智能体描述 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:230` | `_build_subagent_section(max_concurrent, *, app_config)` | 拼装子智能体使用说明段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:572` | `_get_memory_context(agent_name, *, app_config)` | 拼装 `<memory>` 段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:613` | `_get_cached_skills_prompt_section(...)` | 缓存版技能 prompt 段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:644` | `get_skills_prompt_section(available_skills, *, app_config)` | 拼装技能描述段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:677` | `get_agent_soul(agent_name)` | 读自定义 agent 的 `SOUL.md` |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:686` | `_build_self_update_section(agent_name)` | 拼装自定义 agent 自更新提示 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:707` | `get_deferred_tools_prompt_section(*, deferred_names)` | 拼装延迟工具段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:720` | `_build_acp_section(*, app_config)` | 拼装 ACP 段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:744` | `_build_custom_mounts_section(*, app_config)` | 拼装自定义挂载段 |
| `packages/harness/deerflow/agents/lead_agent/prompt.py:771` | `apply_prompt_template(...)` | 系统提示词模板：注入技能 / 记忆 / 子智能体指令 |

#### 3.2 中间件（按执行顺序）

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/agents/middlewares/thread_data_middleware.py:20` | `class ThreadDataMiddlewareState(AgentState)` | thread_data 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/thread_data_middleware.py:26` | `class ThreadDataMiddleware` | 创建线程隔离目录 |
| `packages/harness/deerflow/agents/middlewares/uploads_middleware.py:59` | `class UploadsMiddlewareState(AgentState)` | uploads 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/uploads_middleware.py:65` | `class UploadsMiddleware` | 把新上传文件注入对话上下文 |
| `packages/harness/deerflow/sandbox/middleware.py:33` | `class SandboxMiddleware` | 获取 / 释放沙箱（沙箱生命周期） |
| `packages/harness/deerflow/agents/middlewares/dangling_tool_call_middleware.py:35` | `class DanglingToolCallMiddleware` | 为中断的工具调用补 `ToolMessage` |
| `packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py:66` | `class LLMErrorHandlingMiddleware` | 规范化 LLM 调用错误 |
| `packages/harness/deerflow/guardrails/middleware.py:20` | `class GuardrailMiddleware` | 工具调用前授权检查 |
| `packages/harness/deerflow/agents/middlewares/sandbox_audit_middleware.py:194` | `class SandboxAuditMiddleware` | 沙箱安全审计日志 |
| `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py:21` | `class ToolErrorHandlingMiddleware` | 工具异常转 `ToolMessage` |
| `packages/harness/deerflow/agents/middlewares/summarization_middleware.py:24` | `class SummarizationEvent` | 摘要事件数据类 |
| `packages/harness/deerflow/agents/middlewares/summarization_middleware.py:35` | `class BeforeSummarizationHook(Protocol)` | 摘要前钩子协议 |
| `packages/harness/deerflow/agents/middlewares/summarization_middleware.py:88` | `class _SkillBundle` | 摘要中的 skill bundle（内部） |
| `packages/harness/deerflow/agents/middlewares/summarization_middleware.py:98` | `class DeerFlowSummarizationMiddleware` | 上下文超限自动摘要 |
| `packages/harness/deerflow/agents/middlewares/todo_middleware.py:108` | `class TodoMiddleware(TodoListMiddleware)` | 计划模式任务追踪 |
| `packages/harness/deerflow/agents/middlewares/token_usage_middleware.py:269` | `class TokenUsageMiddleware` | Token 用量记录（可与子智能体回写） |
| `packages/harness/deerflow/agents/middlewares/title_middleware.py:23` | `class TitleMiddlewareState(AgentState)` | title 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/title_middleware.py:29` | `class TitleMiddleware` | 自动生成对话标题 |
| `packages/harness/deerflow/agents/middlewares/memory_middleware.py:22` | `class MemoryMiddlewareState(AgentState)` | memory 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/memory_middleware.py:28` | `class MemoryMiddleware` | 队列化异步记忆更新 |
| `packages/harness/deerflow/agents/middlewares/view_image_middleware.py:15` | `class ViewImageMiddlewareState(ThreadState)` | view_image 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/view_image_middleware.py:19` | `class ViewImageMiddleware` | 把已查看图像注入 LLM 输入 |
| `packages/harness/deerflow/agents/middlewares/deferred_tool_filter_middleware.py:29` | `class DeferredToolFilterMiddleware` | MCP 工具延迟绑定 |
| `packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py:25` | `class SubagentLimitMiddleware` | 子智能体并发限制（截断超额调用） |
| `packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py:174` | `class LoopDetectionMiddleware` | 工具调用死循环检测 |
| `packages/harness/deerflow/agents/middlewares/clarification_middleware.py:19` | `class ClarificationMiddlewareState(AgentState)` | clarification 中间件状态 |
| `packages/harness/deerflow/agents/middlewares/clarification_middleware.py:25` | `class ClarificationMiddleware` | 澄清请求拦截（必须最后） |
| `packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py:80` | `class DynamicContextMiddleware` | 动态上下文注入 |
| `packages/harness/deerflow/agents/middlewares/safety_finish_reason_middleware.py:67` | `class SafetyFinishReasonMiddleware` | 终止原因安全检查 |
| `packages/harness/deerflow/agents/middlewares/safety_termination_detectors.py:24` | `class SafetyTermination` | 终止原因枚举 |
| `packages/harness/deerflow/agents/middlewares/safety_termination_detectors.py:46` | `class SafetyTerminationDetector(Protocol)` | 终止原因检测器协议 |
| `packages/harness/deerflow/agents/middlewares/safety_termination_detectors.py:78` | `class OpenAICompatibleContentFilterDetector` | OpenAI 兼容 content filter 检测器 |
| `packages/harness/deerflow/agents/middlewares/safety_termination_detectors.py:116` | `class AnthropicRefusalDetector` | Anthropic refusal 检测器 |
| `packages/harness/deerflow/agents/middlewares/safety_termination_detectors.py:141` | `class GeminiSafetyDetector` | Gemini safety 检测器 |
| `packages/harness/deerflow/agents/middlewares/tool_output_budget_middleware.py:415` | `class ToolOutputBudgetMiddleware` | 工具输出按预算截断 |
| `packages/harness/deerflow/agents/middlewares/tool_call_metadata.py` | （metadata 工具） | 工具调用 metadata 注入 / 清理 |

#### 3.3 记忆系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/agents/memory/updater.py:41` | `_create_empty_memory()` | 构造空 memory 字典（顶层函数） |
| `packages/harness/deerflow/agents/memory/updater.py:46` | `_save_memory_to_file(...)` | 原子写 memory.json |
| `packages/harness/deerflow/agents/memory/updater.py:51` | `get_memory_data(agent_name, *, user_id)` | 读记忆 JSON |
| `packages/harness/deerflow/agents/memory/updater.py:56` | `reload_memory_data(...)` | 重新加载记忆（清缓存） |
| `packages/harness/deerflow/agents/memory/updater.py:61` | `import_memory_data(memory_data, ...)` | 导入记忆 |
| `packages/harness/deerflow/agents/memory/updater.py:81` | `clear_memory_data(...)` | 清空记忆 |
| `packages/harness/deerflow/agents/memory/updater.py:89` | `_validate_confidence(confidence)` | 校验置信度（0-1） |
| `packages/harness/deerflow/agents/memory/updater.py:96` | `create_memory_fact(...)` | 创建事实 |
| `packages/harness/deerflow/agents/memory/updater.py:133` | `delete_memory_fact(fact_id, ...)` | 删除事实 |
| `packages/harness/deerflow/agents/memory/updater.py:150` | `update_memory_fact(...)` | 更新事实 |
| `packages/harness/deerflow/agents/memory/updater.py:193` | `_extract_text(content)` | 从 LLM 响应内容中抽纯文本 |
| `packages/harness/deerflow/agents/memory/updater.py:231` | `_normalize_memory_update_fact(fact)` | 规范化 LLM 给出的事实 |
| `packages/harness/deerflow/agents/memory/updater.py:279` | `_normalize_memory_update_data(update_data)` | 规范化整个 update 结构 |
| `packages/harness/deerflow/agents/memory/updater.py:311` | `_parse_memory_update_response(response_content)` | 解析 LLM 响应为 memory 更新 |
| `packages/harness/deerflow/agents/memory/updater.py:346` | `_strip_upload_mentions_from_memory(memory_data)` | 从 memory 中剥离 upload 提及 |
| `packages/harness/deerflow/agents/memory/updater.py:369` | `_fact_content_key(content)` | 事实内容归一化 key（去重） |
| `packages/harness/deerflow/agents/memory/updater.py:378` | `class MemoryUpdater` | LLM 驱动的记忆更新器（含事实去重 / 原子写） |
| `packages/harness/deerflow/agents/memory/updater.py:680` | `update_memory_from_conversation(...)` | 从对话历史更新记忆（顶层入口） |
| `packages/harness/deerflow/agents/memory/storage.py:19` | `utc_now_iso_z()` | UTC ISO8601（带 Z） |
| `packages/harness/deerflow/agents/memory/storage.py:24` | `create_empty_memory()` | 构造空记忆字典 |
| `packages/harness/deerflow/agents/memory/storage.py:43` | `class MemoryStorage(ABC)` | 记忆存储抽象 |
| `packages/harness/deerflow/agents/memory/storage.py:62` | `class FileMemoryStorage(MemoryStorage)` | 文件存储实现（按 user_id / agent_name 隔离） |
| `packages/harness/deerflow/agents/memory/storage.py:196` | `get_memory_storage()` | 取 memory storage 单例（按 config 反射） |
| `packages/harness/deerflow/agents/memory/queue.py:16` | `class ConversationContext` | 一段对话的元数据（user_id / thread 等） |
| `packages/harness/deerflow/agents/memory/queue.py:28` | `class MemoryUpdateQueue` | 防抖去重更新队列 |
| `packages/harness/deerflow/agents/memory/queue.py:263` | `get_memory_queue()` | 取队列单例 |
| `packages/harness/deerflow/agents/memory/queue.py:276` | `reset_memory_queue()` | 重置队列 |
| `packages/harness/deerflow/agents/memory/prompt.py:163` | `_count_tokens(text, encoding_name)` | 简单 token 计数 |
| `packages/harness/deerflow/agents/memory/prompt.py:185` | `_coerce_confidence(value, default)` | 强制 LLM 给出的 confidence 到 [0,1] |
| `packages/harness/deerflow/agents/memory/prompt.py:200` | `format_memory_for_injection(memory_data, max_tokens=2000)` | 拼装注入到 prompt 的 `<memory>` 段 |
| `packages/harness/deerflow/agents/memory/prompt.py:319` | `format_conversation_for_update(messages)` | 把对话转成 LLM 可读的更新提示 |
| `packages/harness/deerflow/agents/memory/message_processing.py:40` | `extract_message_text(message)` | 从 LangChain 消息抽纯文本 |
| `packages/harness/deerflow/agents/memory/message_processing.py:56` | `filter_messages_for_memory(messages)` | 过滤要进记忆更新的消息 |
| `packages/harness/deerflow/agents/memory/message_processing.py:88` / `:100` | `detect_correction(messages)` / `detect_reinforcement(messages)` | 检测"纠正" / "强化"信号 |
| `packages/harness/deerflow/agents/memory/summarization_hook.py:12` | `memory_flush_hook(event: SummarizationEvent)` | 摘要事件触发的记忆 flush 钩子 |

#### 3.4 子智能体系统

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/subagents/config.py:11` | `class SubagentConfig` | 子智能体配置（name / description / system_prompt / tools） |
| `packages/harness/deerflow/subagents/config.py:38` | `_default_model_name(app_config)` | 取子智能体默认模型名 |
| `packages/harness/deerflow/subagents/config.py:45` | `resolve_subagent_model_name(config, parent_model, *, app_config)` | 解析子智能体实际用哪个模型 |
| `packages/harness/deerflow/subagents/registry.py:14` | `_resolve_subagents_app_config(app_config)` | 解析子智能体用的 app_config |
| `packages/harness/deerflow/subagents/registry.py:23` | `_build_custom_subagent_config(name, *, app_config)` | 按名构建自定义子智能体配置 |
| `packages/harness/deerflow/subagents/registry.py:51` | `get_subagent_config(name, *, app_config)` | 按名取子智能体配置 |
| `packages/harness/deerflow/subagents/registry.py:120` | `list_subagents(*, app_config)` | 列出所有子智能体（含自定义） |
| `packages/harness/deerflow/subagents/registry.py:137` | `get_subagent_names(*, app_config)` | 取所有子智能体名 |
| `packages/harness/deerflow/subagents/registry.py:157` | `get_available_subagent_names(*, app_config)` | 取当前可用的子智能体名 |
| `packages/harness/deerflow/subagents/executor.py:40` | `class SubagentStatus(Enum)` | 子智能体状态枚举 |
| `packages/harness/deerflow/subagents/executor.py:62` | `class SubagentResult` | 子智能体结果 |
| `packages/harness/deerflow/subagents/executor.py:162` | `_run_isolated_subagent_loop()` | 启动隔离的事件循环 |
| `packages/harness/deerflow/subagents/executor.py:175` | `_shutdown_isolated_subagent_loop()` | 关闭隔离的事件循环 |
| `packages/harness/deerflow/subagents/executor.py:212` | `_get_isolated_subagent_loop()` | 取隔离的事件循环 |
| `packages/harness/deerflow/subagents/executor.py:243` | `_submit_to_isolated_loop_in_context(...)` | 在隔离 loop 上提交任务 |
| `packages/harness/deerflow/subagents/executor.py:264` | `_filter_tools(...)` | 按子智能体配置过滤可用工具 |
| `packages/harness/deerflow/subagents/executor.py:294` | `class SubagentExecutor` | 后台双线程池执行引擎 |
| `packages/harness/deerflow/subagents/executor.py:819` | `request_cancel_background_task(task_id)` | 取消后台任务 |
| `packages/harness/deerflow/subagents/executor.py:836` | `get_background_task_result(task_id)` | 取后台任务结果 |
| `packages/harness/deerflow/subagents/executor.py:849` | `list_background_tasks()` | 列所有后台任务 |
| `packages/harness/deerflow/subagents/executor.py:859` | `cleanup_background_task(task_id)` | 清理已完成任务 |
| `packages/harness/deerflow/subagents/token_collector.py:14` | `class SubagentTokenCollector` | 子智能体 token 用量回传 |
| `packages/harness/deerflow/subagents/builtins/general_purpose.py` | `general-purpose` 子智能体 | 全工具集（除 `task` 自身） |
| `packages/harness/deerflow/subagents/builtins/bash_agent.py` | `bash` 子智能体 | 命令专家 |

#### 3.5 嵌入式客户端

| 位置 | 名称 | 作用 |
|------|------|------|
| `packages/harness/deerflow/client.py:84` | `class DeerFlowClient` | 嵌入式 Python 客户端（不依赖 HTTP） |
| `packages/harness/deerflow/client.py:66` | `class StreamEvent` | 流事件 |
| `packages/harness/deerflow/client.py` | `client.chat(message, thread_id)` | 同步对话 |
| `packages/harness/deerflow/client.py` | `client.stream(message, thread_id)` | 流式对话 |
| `packages/harness/deerflow/client.py` | `client.list_models()` / `get_model(name)` | 模型列表 / 详情 |
| `packages/harness/deerflow/client.py` | `client.list_skills()` / `get_skill(name)` / `update_skill(name, enabled)` / `install_skill(path)` | 技能 CRUD |
| `packages/harness/deerflow/client.py` | `client.get_mcp_config()` / `update_mcp_config(servers)` | MCP 配置读写 |
| `packages/harness/deerflow/client.py` | `client.get_memory()` / `reload_memory()` / `get_memory_config()` / `get_memory_status()` | 记忆 |
| `packages/harness/deerflow/client.py` | `client.upload_files(thread_id, files)` / `list_uploads(thread_id)` / `delete_upload(thread_id, filename)` | 上传 |
| `packages/harness/deerflow/client.py` | `client.get_artifact(thread_id, path) -> (bytes, mime_type)` | 取制品 |
| `packages/harness/deerflow/client.py` | `client.reset_agent()` | 强制重建 agent（配置变更后） |

### 4. 应用层

#### 4.1 Gateway 应用

| 位置 | 名称 | 作用 |
|------|------|------|
| `app/gateway/app.py:226` | `create_app() -> FastAPI` | 构造 FastAPI 应用（含 routers / 中间件 / lifespan） |
| `app/gateway/app.py:167` | `async lifespan(app)` | 启动期：装载配置、初始化引擎 / checkpointer / 流桥接 / 渠道服务 |
| `app/gateway/app.py:57` | `async _ensure_admin_user(app)` | 首次启动建管理员 |
| `app/gateway/app.py:129` | `async _iter_store_items(store, namespace, *, page_size)` | 异步迭代 store 全部条目 |
| `app/gateway/app.py:149` | `async _migrate_orphaned_threads(store, admin_user_id)` | 迁移孤儿线程到管理员名下 |
| `app/gateway/app.py:397` | `app = create_app()` | 模块级 ASGI 入口 |

#### 4.2 路由模块（FastAPI）

| 位置 | 名称 | 作用 |
|------|------|------|
| `app/gateway/routers/models.py:39` | `@router.get(...)` 装饰器 | `GET /api/models` 列表装饰器 |
| `app/gateway/routers/models.py:45` | `list_models(config)` | `GET /api/models` 列表 |
| `app/gateway/routers/models.py:39` | `@router.get(...)` 装饰器 | `GET /api/models/{name}` 装饰器 |
| `app/gateway/routers/models.py:104` | `get_model(model_name, config)` | `GET /api/models/{name}` 详情 |
| `app/gateway/routers/mcp.py:164` | `@router.get(...)` 装饰器 | `GET /api/mcp/config` 装饰器 |
| `app/gateway/routers/mcp.py:170` | `get_mcp_configuration()` | `GET /api/mcp/config` 读 MCP 配置 |
| `app/gateway/routers/mcp.py:197` | `@router.put(...)` 装饰器 | `PUT /api/mcp/config` 装饰器 |
| `app/gateway/routers/mcp.py:203` | `update_mcp_configuration(request)` | `PUT /api/mcp/config` 写 MCP 配置 |
| `app/gateway/routers/skills.py:102` | `@router.get(...)` 装饰器 | `GET /api/skills/` 装饰器 |
| `app/gateway/routers/skills.py:108` | `list_skills(config)` | `GET /api/skills/` 列表 |
| `app/gateway/routers/skills.py:118` | `@router.post(...)` 装饰器 | `POST /api/skills/install` 装饰器 |
| `app/gateway/routers/skills.py:124` | `install_skill(request, config)` | `POST /api/skills/install` 安装 `.skill` 归档 |
| `app/gateway/routers/skills.py:102` | `@router.get(...)` 装饰器 | `GET /api/skills/custom` 装饰器 |
| `app/gateway/routers/skills.py:145` | `list_custom_skills(config)` | `GET /api/skills/custom` 自定义技能 |
| `app/gateway/routers/skills.py:102` | `@router.get(...)` 装饰器 | `GET /api/skills/custom/{name}` 装饰器 |
| `app/gateway/routers/skills.py:156` | `get_custom_skill(skill_name, config)` | `GET /api/skills/custom/{name}` 读内容 |
| `app/gateway/routers/skills.py:172` | `@router.put(...)` 装饰器 | `PUT /api/skills/custom/{name}` 装饰器 |
| `app/gateway/routers/skills.py:173` | `update_custom_skill(skill_name, request, config)` | `PUT /api/skills/custom/{name}` 编辑 |
| `app/gateway/routers/skills.py:210` | `@router.delete(...)` 装饰器 | `DELETE /api/skills/custom/{name}` 装饰器 |
| `app/gateway/routers/skills.py:211` | `delete_custom_skill(skill_name, config)` | `DELETE /api/skills/custom/{name}` 删除 |
| `app/gateway/routers/skills.py:102` | `@router.get(...)` 装饰器 | `GET /api/skills/custom/{name}/history` 装饰器 |
| `app/gateway/routers/skills.py:240` | `get_custom_skill_history(skill_name, config)` | `GET /api/skills/custom/{name}/history` |
| `app/gateway/routers/skills.py:118` | `@router.post(...)` 装饰器 | `POST /api/skills/custom/{name}/rollback` 装饰器 |
| `app/gateway/routers/skills.py:256` | `rollback_custom_skill(skill_name, request, config)` | `POST /api/skills/custom/{name}/rollback` |
| `app/gateway/routers/skills.py:102` | `@router.get(...)` 装饰器 | `GET /api/skills/{name}` 装饰器 |
| `app/gateway/routers/skills.py:309` | `get_skill(skill_name, config)` | `GET /api/skills/{name}` |
| `app/gateway/routers/skills.py:172` | `@router.put(...)` 装饰器 | `PUT /api/skills/{name}` 装饰器 |
| `app/gateway/routers/skills.py:333` | `update_skill(skill_name, request, config)` | `PUT /api/skills/{name}` 启停 |
| `app/gateway/routers/memory.py:110` | `@router.get(...)` 装饰器 | `GET /api/memory/` 装饰器 |
| `app/gateway/routers/memory.py:117` | `get_memory()` | `GET /api/memory/` |
| `app/gateway/routers/memory.py:155` | `@router.post(...)` 装饰器 | `POST /api/memory/reload` 装饰器 |
| `app/gateway/routers/memory.py:162` | `reload_memory()` | `POST /api/memory/reload` |
| `app/gateway/routers/memory.py:174` | `@router.delete(...)` 装饰器 | `DELETE /api/memory/` 装饰器 |
| `app/gateway/routers/memory.py:181` | `clear_memory()` | `DELETE /api/memory/` |
| `app/gateway/routers/memory.py:155` | `@router.post(...)` 装饰器 | `POST /api/memory/facts` 装饰器 |
| `app/gateway/routers/memory.py:198` | `create_memory_fact_endpoint(request)` | `POST /api/memory/facts` |
| `app/gateway/routers/memory.py:174` | `@router.delete(...)` 装饰器 | `DELETE /api/memory/facts/{id}` 装饰器 |
| `app/gateway/routers/memory.py:222` | `delete_memory_fact_endpoint(fact_id)` | `DELETE /api/memory/facts/{id}` |
| `app/gateway/routers/memory.py:234` | `@router.patch(...)` 装饰器 | `PATCH /api/memory/facts/{id}` 装饰器 |
| `app/gateway/routers/memory.py:241` | `update_memory_fact_endpoint(fact_id, request)` | `PATCH /api/memory/facts/{id}` |
| `app/gateway/routers/memory.py:110` | `@router.get(...)` 装饰器 | `GET /api/memory/export` 装饰器 |
| `app/gateway/routers/memory.py:268` | `export_memory()` | `GET /api/memory/export` |
| `app/gateway/routers/memory.py:155` | `@router.post(...)` 装饰器 | `POST /api/memory/import` 装饰器 |
| `app/gateway/routers/memory.py:281` | `import_memory(request)` | `POST /api/memory/import` |
| `app/gateway/routers/memory.py:110` | `@router.get(...)` 装饰器 | `GET /api/memory/config` 装饰器 |
| `app/gateway/routers/memory.py:297` | `get_memory_config_endpoint()` | `GET /api/memory/config` |
| `app/gateway/routers/memory.py:110` | `@router.get(...)` 装饰器 | `GET /api/memory/status` 装饰器 |
| `app/gateway/routers/memory.py:335` | `get_memory_status()` | `GET /api/memory/status` |
| `app/gateway/routers/uploads.py:218` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/uploads` 装饰器 |
| `app/gateway/routers/uploads.py:220` | `async upload_files(...)` | `POST /api/threads/{id}/uploads` |
| `app/gateway/routers/uploads.py:354` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/uploads/limits` 装饰器 |
| `app/gateway/routers/uploads.py:356` | `get_upload_limits(...)` | `GET /api/threads/{id}/uploads/limits` |
| `app/gateway/routers/uploads.py:354` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/uploads/list` 装饰器 |
| `app/gateway/routers/uploads.py:367` | `list_uploaded_files(thread_id, request)` | `GET /api/threads/{id}/uploads/list` |
| `app/gateway/routers/uploads.py:384` | `@router.delete(...)` 装饰器 | `DELETE /api/threads/{id}/uploads/{filename}` 装饰器 |
| `app/gateway/routers/uploads.py:386` | `delete_uploaded_file(thread_id, filename, request)` | `DELETE /api/threads/{id}/uploads/{filename}` |
| `app/gateway/routers/threads.py:211` | `@router.delete(...)` 装饰器 | `DELETE /api/threads/{id}` 装饰器 |
| `app/gateway/routers/threads.py:213` | `delete_thread_data(thread_id, request)` | `DELETE /api/threads/{id}` 删线程 + 本地数据 |
| `app/gateway/routers/threads.py:244` | `@router.post(...)` 装饰器 | `POST /api/threads` 装饰器 |
| `app/gateway/routers/threads.py:245` | `create_thread(body, request)` | `POST /api/threads` |
| `app/gateway/routers/threads.py:244` | `@router.post(...)` 装饰器 | `POST /api/threads/search` 装饰器 |
| `app/gateway/routers/threads.py:310` | `search_threads(body, request)` | `POST /api/threads/search` |
| `app/gateway/routers/threads.py:346` | `@router.patch(...)` 装饰器 | `PATCH /api/threads/{id}` 装饰器 |
| `app/gateway/routers/threads.py:348` | `patch_thread(thread_id, body, request)` | `PATCH /api/threads/{id}` |
| `app/gateway/routers/threads.py:375` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}` 装饰器 |
| `app/gateway/routers/threads.py:377` | `get_thread(thread_id, request)` | `GET /api/threads/{id}` |
| `app/gateway/routers/threads.py:375` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/state` 装饰器 |
| `app/gateway/routers/threads.py:435` | `get_thread_state(thread_id, request)` | `GET /api/threads/{id}/state` |
| `app/gateway/routers/threads.py:244` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/state` 装饰器 |
| `app/gateway/routers/threads.py:486` | `update_thread_state(thread_id, body, request)` | `POST /api/threads/{id}/state` |
| `app/gateway/routers/threads.py:244` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/history` 装饰器 |
| `app/gateway/routers/threads.py:575` | `get_thread_history(thread_id, body, request)` | `POST /api/threads/{id}/history` |
| `app/gateway/routers/thread_runs.py:149` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/runs` 装饰器 |
| `app/gateway/routers/thread_runs.py:151` | `create_run(thread_id, body, request)` | `POST /api/threads/{id}/runs` |
| `app/gateway/routers/thread_runs.py:149` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/runs/stream` 装饰器 |
| `app/gateway/routers/thread_runs.py:159` | `stream_run(thread_id, body, request)` | `POST /api/threads/{id}/runs/stream` SSE |
| `app/gateway/routers/thread_runs.py:149` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/runs/wait` 装饰器 |
| `app/gateway/routers/thread_runs.py:186` | `wait_run(thread_id, body, request)` | `POST /api/threads/{id}/runs/wait` 阻塞 |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs` 装饰器 |
| `app/gateway/routers/thread_runs.py:213` | `list_runs(thread_id, request)` | `GET /api/threads/{id}/runs` |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}` 装饰器 |
| `app/gateway/routers/thread_runs.py:223` | `get_run(thread_id, run_id, request)` | `GET /api/threads/{id}/runs/{rid}` |
| `app/gateway/routers/thread_runs.py:149` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/runs/{rid}/cancel` 装饰器 |
| `app/gateway/routers/thread_runs.py:235` | `cancel_run(...)` | `POST /api/threads/{id}/runs/{rid}/cancel` |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}/join` 装饰器 |
| `app/gateway/routers/thread_runs.py:270` | `join_run(thread_id, run_id, request)` | `GET /api/threads/{id}/runs/{rid}/join` 续 SSE |
| `app/gateway/routers/thread_runs.py:286` / `:287` | `@router.get(...)` / `@router.post(...)` 装饰器 | `GET/POST /api/threads/{id}/runs/{rid}/stream` 装饰器 |
| `app/gateway/routers/thread_runs.py:298` | `stream_existing_run(...)` | `GET/POST /api/threads/{id}/runs/{rid}/stream` |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/messages` 装饰器 |
| `app/gateway/routers/thread_runs.py:350` | `list_thread_messages(...)` | `GET /api/threads/{id}/messages`（带反馈） |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}/messages` 装饰器 |
| `app/gateway/routers/thread_runs.py:395` | `list_run_messages(...)` | `GET /api/threads/{id}/runs/{rid}/messages` |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}/events` 装饰器 |
| `app/gateway/routers/thread_runs.py:421` | `list_run_events(...)` | `GET /api/threads/{id}/runs/{rid}/events` |
| `app/gateway/routers/thread_runs.py:211` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/token-usage` 装饰器 |
| `app/gateway/routers/thread_runs.py:436` | `thread_token_usage(thread_id, request)` | `GET /api/threads/{id}/token-usage` |
| `app/gateway/routers/runs.py:34` | `@router.post(...)` 装饰器 | `POST /api/runs/stream` 装饰器 |
| `app/gateway/routers/runs.py:35` | `stateless_stream(body, request)` | `POST /api/runs/stream` 无线程 SSE |
| `app/gateway/routers/runs.py:34` | `@router.post(...)` 装饰器 | `POST /api/runs/wait` 装饰器 |
| `app/gateway/routers/runs.py:59` | `stateless_wait(body, request)` | `POST /api/runs/wait` 无线程阻塞 |
| `app/gateway/routers/runs.py:103` | `@router.get(...)` 装饰器 | `GET /api/runs/{rid}/messages` 装饰器 |
| `app/gateway/routers/runs.py:105` | `run_messages(...)` | `GET /api/runs/{rid}/messages` |
| `app/gateway/routers/runs.py:103` | `@router.get(...)` 装饰器 | `GET /api/runs/{rid}/feedback` 装饰器 |
| `app/gateway/routers/runs.py:136` | `run_feedback(run_id, request)` | `GET /api/runs/{rid}/feedback` |
| `app/gateway/routers/feedback.py:68` | `@router.put(...)` 装饰器 | `PUT /api/threads/{id}/runs/{rid}/feedback` 装饰器 |
| `app/gateway/routers/feedback.py:70` | `upsert_feedback(...)` | `PUT /api/threads/{id}/runs/{rid}/feedback` |
| `app/gateway/routers/feedback.py:99` | `@router.delete(...)` 装饰器 | `DELETE /api/threads/{id}/runs/{rid}/feedback` 装饰器 |
| `app/gateway/routers/feedback.py:101` | `delete_run_feedback(...)` | `DELETE /api/threads/{id}/runs/{rid}/feedback` |
| `app/gateway/routers/feedback.py:119` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/runs/{rid}/feedback` 装饰器 |
| `app/gateway/routers/feedback.py:121` | `create_feedback(...)` | `POST /api/threads/{id}/runs/{rid}/feedback` |
| `app/gateway/routers/feedback.py:152` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}/feedback` 装饰器 |
| `app/gateway/routers/feedback.py:154` | `list_feedback(...)` | `GET /api/threads/{id}/runs/{rid}/feedback` |
| `app/gateway/routers/feedback.py:152` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/runs/{rid}/feedback/stats` 装饰器 |
| `app/gateway/routers/feedback.py:166` | `feedback_stats(...)` | `GET /api/threads/{id}/runs/{rid}/feedback/stats` |
| `app/gateway/routers/feedback.py:99` | `@router.delete(...)` 装饰器 | `DELETE /api/threads/{id}/runs/{rid}/feedback/{fid}` 装饰器 |
| `app/gateway/routers/feedback.py:178` | `delete_feedback(...)` | `DELETE /api/threads/{id}/runs/{rid}/feedback/{fid}` |
| `app/gateway/routers/agents.py:106` | `@router.get(...)` 装饰器 | `GET /api/agents` 装饰器 |
| `app/gateway/routers/agents.py:112` | `list_agents()` | `GET /api/agents` 自定义 agent 列表 |
| `app/gateway/routers/agents.py:106` | `@router.get(...)` 装饰器 | `GET /api/agents/check-name` 装饰器 |
| `app/gateway/routers/agents.py:134` | `check_agent_name(name)` | `GET /api/agents/check-name` |
| `app/gateway/routers/agents.py:106` | `@router.get(...)` 装饰器 | `GET /api/agents/{name}` 装饰器 |
| `app/gateway/routers/agents.py:164` | `get_agent(name)` | `GET /api/agents/{name}` |
| `app/gateway/routers/agents.py:191` | `@router.post(...)` 装饰器 | `POST /api/agents` 装饰器 |
| `app/gateway/routers/agents.py:198` | `create_agent_endpoint(request)` | `POST /api/agents` |
| `app/gateway/routers/agents.py:259` | `@router.put(...)` 装饰器 | `PUT /api/agents/{name}` 装饰器 |
| `app/gateway/routers/agents.py:265` | `update_agent(name, request)` | `PUT /api/agents/{name}` |
| `app/gateway/routers/agents.py:106` | `@router.get(...)` 装饰器 | `GET /api/agents/profile` 装饰器 |
| `app/gateway/routers/agents.py:363` | `get_user_profile()` | `GET /api/agents/profile` |
| `app/gateway/routers/agents.py:259` | `@router.put(...)` 装饰器 | `PUT /api/agents/profile` 装饰器 |
| `app/gateway/routers/agents.py:388` | `update_user_profile(request)` | `PUT /api/agents/profile` |
| `app/gateway/routers/agents.py:410` | `@router.delete(...)` 装饰器 | `DELETE /api/agents/{name}` 装饰器 |
| `app/gateway/routers/agents.py:416` | `delete_agent(name)` | `DELETE /api/agents/{name}` |
| `app/gateway/routers/suggestions.py:110` | `@router.post(...)` 装饰器 | `POST /api/threads/{id}/suggestions` 装饰器 |
| `app/gateway/routers/suggestions.py:117` | `generate_suggestions(...)` | `POST /api/threads/{id}/suggestions` |
| `app/gateway/routers/channels.py:29` | `@router.get(...)` 装饰器 | `GET /api/channels/` 装饰器 |
| `app/gateway/routers/channels.py:30` | `get_channels_status()` | `GET /api/channels/` 渠道状态 |
| `app/gateway/routers/channels.py:41` | `@router.post(...)` 装饰器 | `POST /api/channels/{name}/restart` 装饰器 |
| `app/gateway/routers/channels.py:42` | `restart_channel(name)` | `POST /api/channels/{name}/restart` |
| `app/gateway/routers/assistants_compat.py:92` | `@router.post(...)` 装饰器 | `POST /api/assistants/search` 装饰器 |
| `app/gateway/routers/assistants_compat.py:93` | `search_assistants(body)` | `POST /api/assistants/search` |
| `app/gateway/routers/assistants_compat.py:110` | `@router.get(...)` 装饰器 | `GET /api/assistants/{id}` 装饰器 |
| `app/gateway/routers/assistants_compat.py:111` | `get_assistant_compat(assistant_id)` | `GET /api/assistants/{id}` |
| `app/gateway/routers/assistants_compat.py:110` | `@router.get(...)` 装饰器 | `GET /api/assistants/{id}/graph` 装饰器 |
| `app/gateway/routers/assistants_compat.py:120` | `get_assistant_graph(assistant_id)` | `GET /api/assistants/{id}/graph` |
| `app/gateway/routers/assistants_compat.py:110` | `@router.get(...)` 装饰器 | `GET /api/assistants/{id}/schemas` 装饰器 |
| `app/gateway/routers/assistants_compat.py:138` | `get_assistant_schemas(assistant_id)` | `GET /api/assistants/{id}/schemas` |
| `app/gateway/routers/artifacts.py:106` | `@router.get(...)` 装饰器 | `GET /api/threads/{id}/artifacts/{path}` 装饰器 |
| `app/gateway/routers/artifacts.py:112` | `get_artifact(thread_id, path, request, download=False)` | `GET /api/threads/{id}/artifacts/{path}` |
| `app/gateway/routers/auth.py:269` | `@router.post(...)` 装饰器 | `POST /api/auth/login/local` 装饰器 |
| `app/gateway/routers/auth.py:270` | `login_local(request, ...)` | `POST /api/auth/login/local` |
| `app/gateway/routers/auth.py:269` | `@router.post(...)` 装饰器 | `POST /api/auth/register` 装饰器 |
| `app/gateway/routers/auth.py:299` | `register(request, response, body)` | `POST /api/auth/register` |
| `app/gateway/routers/auth.py:269` | `@router.post(...)` 装饰器 | `POST /api/auth/logout` 装饰器 |
| `app/gateway/routers/auth.py:320` | `logout(request, response)` | `POST /api/auth/logout` |
| `app/gateway/routers/auth.py:269` | `@router.post(...)` 装饰器 | `POST /api/auth/change-password` 装饰器 |
| `app/gateway/routers/auth.py:327` | `change_password(request, response, body)` | `POST /api/auth/change-password` |
| `app/gateway/routers/auth.py:372` | `@router.get(...)` 装饰器 | `GET /api/auth/me` 装饰器 |
| `app/gateway/routers/auth.py:373` | `get_me(request)` | `GET /api/auth/me` |
| `app/gateway/routers/auth.py:372` | `@router.get(...)` 装饰器 | `GET /api/auth/setup-status` 装饰器 |
| `app/gateway/routers/auth.py:391` | `setup_status(request)` | `GET /api/auth/setup-status` |
| `app/gateway/routers/auth.py:269` | `@router.post(...)` 装饰器 | `POST /api/auth/initialize` 装饰器 |
| `app/gateway/routers/auth.py:458` | `initialize_admin(request, response, body)` | `POST /api/auth/initialize` |
| `app/gateway/routers/auth.py:372` | `@router.get(...)` 装饰器 | `GET /api/auth/oauth/{provider}` 装饰器 |
| `app/gateway/routers/auth.py:490` | `oauth_login(provider)` | `GET /api/auth/oauth/{provider}` |
| `app/gateway/routers/auth.py:372` | `@router.get(...)` 装饰器 | `GET /api/auth/callback/{provider}` 装饰器 |
| `app/gateway/routers/auth.py:509` | `oauth_callback(provider, code, state)` | `GET /api/auth/callback/{provider}` |

#### 4.3 Gateway 内部组件

| 位置 | 名称 | 作用 |
|------|------|------|
| `app/gateway/csrf_middleware.py` | `class CSRFMiddleware` | 跨站请求伪造防护（与 CORS 同步读 `GATEWAY_CORS_ORIGINS`） |
| `app/gateway/auth_middleware.py` | （auth middleware） | 请求级 auth 解析 |
| `app/gateway/internal_auth.py` | （内部鉴权） | channel 进程内 SDK 调用专用 |
| `app/gateway/langgraph_auth.py` | （LG auth） | LangGraph 兼容层鉴权适配 |
| `app/gateway/authz.py` | （authz 模块） | 资源访问授权检查 |
| `app/gateway/pagination.py` | （分页工具） | 通用分页参数解析 |
| `app/gateway/path_utils.py` | （路径工具） | 路径安全 / 解析 |
| `app/gateway/utils.py` | （util 模块） | 通用工具 |
| `app/gateway/deps.py` | （共享 Depends） | FastAPI Depends 集合 |
| `app/gateway/services.py` | （服务层） | 跨路由服务 |
| `app/gateway/config.py` | （启动配置） | 启动期一次性读取的配置 |
| `app/gateway/auth/` | （auth 子系统） | `config.py` / `credential_file.py` / `errors.py` / `jwt.py` / `local_provider.py` / `models.py` / `password.py` / `providers.py` / `reset_admin.py` / `repositories/` |

#### 4.4 IM 渠道

| 位置 | 名称 | 作用 |
|------|------|------|
| `app/channels/base.py:14` | `class Channel(ABC)` | 渠道抽象基类（`start` / `stop` / `send` 生命周期） |
| `app/channels/message_bus.py:25` | `class InboundMessageType(StrEnum)` | 入站消息类型（text / file / command / ...） |
| `app/channels/message_bus.py:33` | `class InboundMessage` | 入站消息数据类 |
| `app/channels/message_bus.py:64` | `class ResolvedAttachment` | 附件解析结果 |
| `app/channels/message_bus.py:85` | `class OutboundMessage` | 出站消息数据类 |
| `app/channels/message_bus.py:119` | `class MessageBus` | 异步发布 / 订阅总线（inbound / outbound） |
| `app/channels/store.py:16` | `class ChannelStore` | 渠道 ↔ 线程映射持久化（JSON 文件） |
| `app/channels/manager.py:611` | `class ChannelManager` | 核心调度器（消费队列、创建线程、流式回复） |
| `app/channels/manager.py:71` | `register_inbound_file_reader(channel_name, reader)` | 注册渠道专用文件读取器 |
| `app/channels/manager.py:76` / `:87` / `:106` | `async _read_http_inbound_file` / `async _read_wecom_inbound_file` / `async _read_wechat_inbound_file` | 各渠道入站文件读取器实现 |
| `app/channels/manager.py:127` | `class InvalidChannelSessionConfigError` | 渠道 session 配置无效异常 |
| `app/channels/manager.py:131` | `_is_thread_busy_error(exc)` | 判断线程是否忙 |
| `app/channels/manager.py:140` / `:145` / `:154` | `_as_dict` / `_merge_dicts` / `_normalize_custom_agent_name` | 内部 dict 工具 |
| `app/channels/manager.py:164` | `_extract_response_text(result)` | 从 SDK 结果抽响应文本 |
| `app/channels/manager.py:220` | `_messages_from_result(result)` | 从 SDK 结果抽消息列表 |
| `app/channels/manager.py:231` | `_current_turn_messages(result)` | 抽当前轮次消息 |
| `app/channels/manager.py:245` | `_has_current_turn_clarification(result)` | 当前轮是否含澄清请求 |
| `app/channels/manager.py:263` | `_response_metadata(base_metadata, *, pending_clarification)` | 构造响应 metadata |
| `app/channels/manager.py:271` | `_extract_text_content(content)` | 从 LangChain content 抽纯文本 |
| `app/channels/manager.py:297` | `_merge_stream_text(existing, chunk)` | 合并流式文本片段 |
| `app/channels/manager.py:310` | `_extract_stream_message_id(payload, metadata)` | 从 stream payload 抽 message id |
| `app/channels/manager.py:326` | `_accumulate_stream_text(...)` | 累积流式文本 |
| `app/channels/manager.py:363` | `_extract_artifacts(result)` | 从结果抽制品列表 |
| `app/channels/manager.py:397` | `_is_hidden_human_control_message(msg)` | 判断是否隐藏的人工控制消息 |
| `app/channels/manager.py:409` | `_format_artifact_text(artifacts)` | 格式化制品为可投递文本 |
| `app/channels/manager.py:422` | `_resolve_attachments(thread_id, artifacts)` | 解析制品为可投递附件 |
| `app/channels/manager.py:470` | `_prepare_artifact_delivery(...)` | 准备附件投递 |
| `app/channels/manager.py:497` | `async _ingest_inbound_files(thread_id, msg)` | 入站文件下载并入会上传 |
| `app/channels/manager.py:583` | `_format_uploaded_files_block(files)` | 格式化上传文件块为消息段 |
| `app/channels/service.py:45` | `_resolve_service_url(config, config_key, env_key, default)` | 解析渠道服务的 URL（env 兜底） |
| `app/channels/service.py:56` | `class ChannelService` | 渠道生命周期管理（拉起 / 停止所有渠道） |
| `app/channels/service.py:241` | `get_channel_service()` | 取 service 单例 |
| `app/channels/service.py:246` | `async start_channel_service(app_config)` | 启动渠道服务（启动期调用） |
| `app/channels/service.py:256` | `async stop_channel_service()` | 停止所有渠道 |
| `app/channels/commands.py` | （IM 命令处理） | `/new` / `/status` / `/models` / `/memory` / `/help` 等 |
| `app/channels/feishu.py:32` | `_is_feishu_command(text)` | 判断文本是否为飞书命令 |
| `app/channels/feishu.py:39` | `class FeishuChannel(Channel)` | 飞书实现（SSE 流式卡片，在位 patch） |
| `app/channels/slack.py:19` | `_normalize_allowed_users(allowed_users)` | 规范化允许用户列表 |
| `app/channels/slack.py:36` | `class SlackChannel(Channel)` | Slack 实现（`runs.wait()`） |
| `app/channels/telegram.py:16` | `class TelegramChannel(Channel)` | Telegram 实现（`runs.wait()`） |
| `app/channels/dingtalk.py:32` | `_normalize_conversation_type(raw)` | 规范化会话类型 |
| `app/channels/dingtalk.py:45` | `_normalize_allowed_users(allowed_users)` | 规范化允许用户列表 |
| `app/channels/dingtalk.py:62` | `_is_dingtalk_command(text)` | 判断文本是否为钉钉命令 |
| `app/channels/dingtalk.py:69` | `_extract_text_from_rich_text(rich_text_list)` | 从富文本消息抽纯文本 |
| `app/channels/dingtalk.py:84` | `_convert_markdown_table(text)` | Markdown 表格转钉钉支持的形式 |
| `app/channels/dingtalk.py:108` | `_adapt_markdown_for_dingtalk(text)` | Markdown → 钉钉卡片格式 |
| `app/channels/dingtalk.py:126` | `class DingTalkChannel(Channel)` | 钉钉实现（AI Card 可选流式） |
| `app/channels/dingtalk.py:761` | `class _DingTalkMessageHandler` | 钉钉消息处理器 |
| `app/channels/discord.py:20` | `class DiscordChannel(Channel)` | Discord 实现 |
| `app/channels/wechat.py:30` | `class MessageItemType(IntEnum)` | 微信消息项类型枚举 |
| `app/channels/wechat.py:41` | `class UploadMediaType(IntEnum)` | 微信上传媒体类型枚举 |
| `app/channels/wechat.py:46` / `:64` / `:69` | `_build_ilink_client_version` / `_build_wechat_uin` / `_md5_hex` | 微信客户端版本 / uin 构造 / md5 |
| `app/channels/wechat.py:74` / `:81` / `:87` / `:97` | `_encrypted_size_for_aes_128_ecb` / `_validate_aes_128_key` / `_encrypt_aes_128_ecb` / `_decrypt_aes_128_ecb` | 微信 AES-128-ECB 加解密辅助 |
| `app/channels/wechat.py:112` | `_safe_media_filename(prefix, extension, ...)` | 构造安全媒体文件名 |
| `app/channels/wechat.py:120` | `_build_cdn_upload_url(cdn_base_url, upload_param, filekey)` | 构造 CDN 上传 URL |
| `app/channels/wechat.py:125` | `_encode_outbound_media_aes_key(aes_key)` | 编码出站媒体 AES key |
| `app/channels/wechat.py:130` | `_detect_image_extension_and_mime(content)` | 探测图片扩展名和 MIME |
| `app/channels/wechat.py:145` | `class WechatChannel(Channel)` | 微信实现（含 AES 加解密） |
| `app/channels/wecom.py:23` | `class WeComChannel(Channel)` | 企业微信实现 |
