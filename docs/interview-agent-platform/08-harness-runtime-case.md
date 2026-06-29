# 08 Agent Harness 运行时案例

这一篇参考同类案例文档的“组织方式”：先讲业务问题，再讲执行链路、模块分层、关键对象、面试讲法。内容全部回到当前项目：**智能 Agent Harness 平台**。

## 一句话项目介绍

> 我设计并实现了一套智能 Agent Harness 运行时平台，用统一的 Harness 层管理模型调用、线程状态、中间件链路、工具治理、长期记忆、子 Agent 调度、远程沙箱和 Guardrails 安全拦截，使复杂 Agent 能在长对话、多工具、多任务场景下稳定执行，并支持 Skill 自进化沉淀。

## 为什么要做 Harness

如果只是做一个简单 Agent，可以直接：

```text
用户输入 -> 模型 -> 工具调用 -> 模型回答
```

但复杂 Agent 平台会遇到这些问题：

| 问题 | 现象 | Harness 要解决什么 |
| --- | --- | --- |
| 长上下文膨胀 | 工具输出、历史消息、子任务结果越来越长 | 摘要压缩、动态上下文、token 预算控制 |
| 状态不一致 | 多个中间件/节点都要更新消息、产物、工具提升状态 | `ThreadState + reducer` 统一合并 |
| 工具太多 | MCP/内置/自定义工具 schema 全量绑定会撑爆上下文 | 工具分组、allowed-tools、deferred tools |
| 工具调用有副作用 | bash、写文件、外部 API 可能造成风险 | Guardrails + Sandbox |
| 子任务污染主线 | 搜索、代码探索、命令执行会产生大量中间消息 | `task` 工具 fork 子 Agent，隔离上下文 |
| 能力难沉淀 | 用户纠偏和复杂工作流只停留在本轮对话 | Memory + Skill 自进化 |

面试回答：

> Harness 的定位是 Agent 运行时底座。它不是单个功能，而是把模型、工具、状态、上下文、安全、记忆、子 Agent 这些能力统一接到一条可插拔执行链上。

## 学习版：Harness 可以类比什么

Harness 可以理解为 Agent 的“应用服务器”。

普通 Web 服务有：

```text
router
middleware
request context
session
auth
logging
background job
```

Agent Harness 对应：

```text
agent routing
middleware chain
runtime context
thread state
tool auth
trace / logging
state reducer
memory queue
```

所以 Harness 不是“套一层 Agent 封装”，而是把复杂 Agent 运行时需要的横切能力统一治理。

## 成熟 Agent 平台怎么分层

| 层 | 传统后端类比 | Agent Harness 职责 |
| --- | --- | --- |
| API Layer | Controller / Router | 接收用户请求，创建 run |
| Runtime | Request Context | thread_id、user_id、run_id、store |
| State | Session / DB transaction | messages、artifacts、sandbox、promoted |
| Middleware | Web Middleware | context、summary、安全、memory |
| Tool Runtime | Service Client | 工具调用、沙箱、外部 API |
| Orchestration | Job Scheduler | 子 Agent、任务拆分、并发 |
| Observability | APM | trace、metrics、logs、eval |

面试回答：

> Harness 的价值是把 Agent 从一个 prompt demo 变成可维护的应用运行时。模型只是其中一环，真正难的是状态、工具、安全、上下文、子任务和记忆怎么稳定协作。

## 当前项目的设计原则

### 1. 横切能力中间件化

```text
Dynamic Context
Summarization
Guardrails
Sandbox
Memory
Deferred Tool Filter
```

都不写进 Lead Agent 主逻辑，而是通过 middleware 接入。

### 2. 状态合并 reducer 化

```text
messages       -> append/remove/rebuild
artifacts      -> append + dedupe
promoted       -> hash-aware merge
viewed_images  -> dict merge or clear
```

### 3. 工具能力延迟暴露

```text
基础工具 + tool_search 先给模型
大规模工具进入 deferred catalog
召回后 promoted
调用时再校验
```

### 4. 执行能力隔离

```text
bash / file tools
  -> remote sandbox
  -> guardrails
  -> audit
```

### 5. 经验沉淀异步化

```text
用户响应先返回
Memory / Skill 后台更新
```

## 运行时对象关系

```text
Run
  ├─ Runtime.context
  │   ├─ thread_id
  │   ├─ user_id
  │   ├─ run_id
  │   └─ agent_name
  ├─ ThreadState
  │   ├─ messages
  │   ├─ artifacts
  │   ├─ promoted
  │   ├─ sandbox
  │   └─ thread_data
  ├─ Middleware chain
  ├─ Model
  ├─ Tools
  └─ Store / Memory
```

## 简化版代码

```python
def run_agent(user_input, thread_id, user_id):
    runtime = Runtime(
        context={
            "thread_id": thread_id,
            "user_id": user_id,
            "run_id": new_run_id(),
        },
        store=store,
    )

    state = load_thread_state(thread_id)
    state["messages"].append(HumanMessage(user_input))

    result = graph.invoke(
        state,
        runtime=runtime,
        middleware=[
            ThreadDataMiddleware(),
            SandboxMiddleware(lazy_init=True),
            DynamicContextMiddleware(),
            SummarizationMiddleware(),
            DeferredToolFilterMiddleware(),
            GuardrailMiddleware(),
            SandboxAuditMiddleware(),
            MemoryMiddleware(),
        ],
    )
    save_thread_state(thread_id, result)
    return result["messages"][-1]
```

## Harness 评估和观测

指标：

| 指标 | 含义 |
| --- | --- |
| `run_success_rate` | Agent run 成功率 |
| `middleware_error_rate` | 中间件错误率 |
| `state_merge_error_rate` | 状态合并异常 |
| `tool_call_success_rate` | 工具调用成功率 |
| `subagent_success_rate` | 子 Agent 成功率 |
| `memory_enqueue_latency` | 记忆入队延迟 |
| `sandbox_acquire_latency` | 沙箱获取耗时 |
| `token_per_success` | 成功任务成本 |

Trace：

```text
agent.run
  -> middleware.thread_data
  -> middleware.sandbox
  -> middleware.dynamic_context
  -> middleware.summarization
  -> model.call
  -> tool.call
  -> state.reducer
  -> memory.enqueue
```

排障：

```text
回答错了
  -> 看模型输入是否缺上下文
  -> 看摘要是否丢信息
  -> 看工具是否选错
  -> 看 Guardrails/Sandbox 是否拒绝
  -> 看 state reducer 是否丢状态
```

## 运行时整体链路

一次用户请求进入后，可以这样讲：

```text
用户消息进入
  -> ThreadDataMiddleware 准备线程工作目录
  -> SandboxMiddleware 懒加载远程 sandbox
  -> DynamicContextMiddleware 注入长期记忆 / 当前日期 / system-reminder
  -> SummarizationMiddleware 判断是否需要摘要压缩
  -> Lead Agent 调用模型
  -> 模型选择工具或 task 子 Agent
  -> DeferredToolFilter / Guardrail / SandboxAudit 等中间件包裹工具调用
  -> 工具结果写回 messages / artifacts / promoted 等 ThreadState 字段
  -> MemoryMiddleware 在响应后异步入队更新长期记忆
```

可以画成面试用流程：

```text
Request
  -> Runtime Context
  -> Middleware Chain
  -> Model Call
  -> Tool Call / Subagent Call
  -> State Reducer
  -> Memory Queue
  -> Response
```

## Harness 五层架构

| 层级 | 职责 | 当前项目对应点 |
| --- | --- | --- |
| Runtime | 管理线程、用户、上下文、任务生命周期 | `thread_id`、`runtime.context`、`ThreadDataMiddleware` |
| State | 统一维护消息、产物、图片、工具提升状态 | `ThreadState`、`merge_artifacts`、`merge_promoted` |
| Context | 模型调用前注入当前有效上下文 | `DynamicContextMiddleware`、`<system-reminder>` |
| Tooling | 工具加载、过滤、延迟提升、权限控制 | `get_available_tools`、`tool_search`、`allowed-tools` |
| Safety | 工具执行前授权和执行隔离 | `GuardrailMiddleware`、`RemoteSandboxBackend` |

一句话：

> Runtime 管生命周期，State 管合并，Context 管模型看到什么，Tooling 管能调用什么，Safety 管能不能执行以及在哪里执行。

## ThreadState 为什么重要

复杂 Agent 不是只有 `messages`。

当前线程状态还包括：

```text
messages       -> 对话消息
artifacts      -> 生成产物
viewed_images  -> 图片上下文
promoted       -> 已提升的延迟工具
todos          -> 任务状态
sandbox        -> 当前线程沙箱
thread_data    -> 工作目录、上传目录、输出目录
```

不同字段有不同合并策略：

| 字段 | 合并策略 | 为什么 |
| --- | --- | --- |
| `messages` | LangGraph 消息 reducer | 支持追加、删除、摘要重建 |
| `artifacts` | append + dedupe | 多工具生成产物时避免重复 |
| `viewed_images` | dict merge / 空 dict 清空 | 图片上下文需要按 key 更新 |
| `promoted` | `catalog_hash` 相同取并集，不同替换 | 防止工具目录变化后复用旧 schema |
| `todos` | last non-None wins | 任务状态以最新为准 |

面试回答：

> 我没有把线程状态做成一个普通 dict 简单覆盖，而是按字段定义 reducer。因为消息、产物、图片上下文、工具提升状态的合并语义完全不同，统一覆盖会丢状态，统一追加又会产生重复和脏数据。

## 中间件链路怎么讲

Harness 的核心扩展点是 Middleware。

按调用时机分：

```text
before_agent     -> 线程数据、沙箱初始化
before_model     -> 动态上下文、摘要压缩
wrap_model_call  -> 模型调用包裹
wrap_tool_call   -> 工具调用授权、审计、错误处理
after_agent      -> 长期记忆异步入队
```

用当前项目举例：

| 中间件 | 时机 | 解决什么 |
| --- | --- | --- |
| `ThreadDataMiddleware` | before_agent | 为线程准备 workspace/uploads/outputs |
| `SandboxMiddleware` | before_agent | 懒加载线程级远程沙箱 |
| `DynamicContextMiddleware` | before_model | 注入长期记忆、日期、系统提醒 |
| `DeerFlowSummarizationMiddleware` | before_model | token 超阈值时压缩历史 |
| `DeferredToolFilterMiddleware` | wrap_tool_call / model binding | 未提升工具不允许调用 |
| `GuardrailMiddleware` | wrap_tool_call | 工具调用前 allow/deny |
| `SandboxAuditMiddleware` | wrap_tool_call | bash 等高风险操作审计 |
| `MemoryMiddleware` | after_agent | 对话后异步更新长期记忆 |

面试回答：

> 我把 Agent 能力做成中间件链，而不是写在 Lead Agent 一个大函数里。这样上下文、摘要、工具过滤、安全拦截、记忆更新都可以独立演进，也能控制执行顺序。

## 子 Agent 调度怎么落在 Harness 里

当前项目通过 `task` 工具把复杂任务派给子 Agent。

典型场景：

```text
主 Agent：
  需要读大量代码 / 跑命令 / 做隔离探索
  -> 调 task(subagent_type="general-purpose" 或 "bash")

子 Agent：
  拿到独立上下文
  继承必要 runtime state
  使用同一个 sandbox / thread_data 引用
  完成任务后把结果返回主 Agent
```

为什么需要子 Agent：

| 条件 | 典型场景 |
| --- | --- |
| 能并行 | 多个互不依赖的代码探索或资料检索 |
| 上下文要隔离 | 子任务会产生大量工具输出，不该污染主对话 |
| 调用链较深 | 子任务自己需要多轮搜索、读文件、执行命令 |

当前项目的子 Agent 链路：

```text
Lead Agent
  -> task 工具
  -> subagent executor
  -> 独立上下文执行
  -> 结果回到主 Agent
```

面试回答：

> 子 Agent 的关键价值是上下文隔离。主 Agent 保留任务主线，子 Agent 负责探索型、命令型或长链路子任务，避免大量中间工具输出污染主上下文。

## 远程沙箱在 Harness 里的位置

沙箱不是工具本身，而是工具执行环境。

当前项目的简化方向：

```text
SandboxProvider
  -> AioSandboxProvider
  -> RemoteSandboxBackend
  -> HTTP provisioner
```

设计原则：

- Agent 进程不直接执行宿主机命令。
- 文件读写、bash、产物生成都进入远程 sandbox。
- `provisioner_url` 是必填配置。
- 本地 Docker/LocalSandbox 不再作为运行时 fallback。

面试回答：

> 我把沙箱收敛为远程 HTTP backend。Agent 侧只作为 client，通过 SandboxProvider 拿到 sandbox_id；真正的命令执行和文件系统隔离交给远程 provisioner。这样安全边界更清晰，也更接近生产部署。

## 工具治理在 Harness 里怎么工作

工具来源很多：

```text
内置工具
配置工具
MCP 工具
Skill 管理工具
Subagent task 工具
ACP 工具
```

Harness 的治理流程：

```text
get_available_tools()
  -> 按 group 过滤
  -> 加载内置工具
  -> 加载 MCP 工具
  -> 根据 skill allowed-tools 过滤
  -> 大规模 MCP 工具进入 deferred catalog
  -> 只把基础工具 + tool_search 绑定给模型
  -> tool_search 命中后写入 promoted
  -> DeferredToolFilterMiddleware 放行已提升工具
```

为什么这和 Harness 有关？

> 工具治理不是单个工具的逻辑，而是运行时决定“当前 Agent 能看到什么、能调用什么、什么时候能调用”的平台能力。

## Skill 自进化和 Harness 的关系

Skill 自进化发生在任务完成之后，但依赖 Harness 采集信号：

```text
任务执行过程
  -> 工具调用次数
  -> 遇到的错误和修复方式
  -> 用户纠偏
  -> 成功工作流
  -> Memory / Skill Evolution 判断
  -> 创建或更新 Skill
```

可以讲的触发条件：

- 任务需要 5 次以上工具调用。
- 解决了非显然错误。
- 用户纠正了方法且新方法有效。
- 出现可复用的复杂工作流。
- 使用某个 Skill 时发现缺口，需要修补。

面试回答：

> 自进化不是让模型随便改自己，而是 Harness 在任务结束后基于可观测信号判断是否值得沉淀。能沉淀的是用户纠偏、稳定流程和 Skill 修补，不是临时文件或未验证推断。

## 当前项目端到端案例

可以用“复杂代码任务”讲，不引入外部业务：

```text
用户：把沙箱系统简化成只保留远程 HTTP backend，并生成面试文档。
```

执行过程：

```text
1. Lead Agent 读取当前项目结构和沙箱相关代码
2. DynamicContextMiddleware 注入用户偏好、日期和当前记忆
3. Agent 判断这是多文件工程改造
4. 读取 SandboxProvider / AioSandboxProvider / config / scripts / tests
5. 修改运行路径：缺 provisioner_url 直接失败，不再 fallback local backend
6. 更新脚本、配置、测试、文档
7. Guardrails / SandboxAudit 保护高风险工具调用
8. Summarization 在长上下文时压缩历史，保护 system-reminder
9. MemoryMiddleware 在任务后记录用户偏好和纠偏
10. 若形成可复用流程，Skill 自进化可沉淀“沙箱系统改造检查清单”
```

这个例子能覆盖：

- Harness 运行时。
- 工具治理。
- 远程沙箱。
- 文档生成。
- 测试验证。
- Skill 自进化。

## 面试 2 分钟讲法

> 当前项目的核心是 Agent Harness，而不是单个聊天 Agent。我把一次 Agent 任务拆成 Runtime、State、Context、Tooling、Safety 五层：Runtime 管 thread_id、工作目录和任务生命周期；State 用 ThreadState reducer 合并消息、产物、图片和工具提升状态；Context 在模型调用前注入长期记忆、日期和系统提醒；Tooling 负责工具分组、allowed-tools、deferred tools 和 tool_search；Safety 在工具调用前做 Guardrails，并把文件和命令执行放到远程 sandbox。复杂任务通过 task 工具派给子 Agent 隔离上下文，任务结束后 Memory 和 Skill Evolution 再沉淀偏好、纠错和可复用流程。

## 面试官可能继续追问

### 1. Harness 和普通 Agent 封装有什么区别？

普通 Agent 封装通常只关心模型和工具；Harness 关心整个运行时，包括状态、上下文、工具权限、安全、沙箱、记忆、子 Agent 和可观测性。

### 2. 为什么中间件链比写死流程好？

因为不同能力的生命周期不同。摘要发生在模型调用前，Guardrails 发生在工具调用前，记忆发生在 Agent 响应后。中间件能把这些横切能力拆开。

### 3. 为什么 ThreadState 需要 reducer？

因为不同字段有不同合并语义。`promoted` 要按 catalog_hash 判断，`artifacts` 要去重，`messages` 要支持 RemoveMessage 重建，不能简单覆盖。

### 4. 为什么远程沙箱放在 Harness 层？

因为工具执行环境是平台能力。上层工具只应该依赖 Sandbox 抽象，不应该知道本地路径或容器细节。

## 深挖补充：Run 生命周期怎么讲

Harness 的核心不是“调用一次模型”，而是管理一次 run 的生命周期。

```text
created
  -> queued
  -> running
  -> streaming
  -> waiting_tool
  -> running_tool
  -> completed
```

异常状态包括：

```text
cancel_requested
  -> cancelled

running
  -> failed
  -> rollback / cleanup
```

每个状态要回答三个问题：

1. 当前 run 能不能被取消？
2. 当前 run 是否已经产生了可见输出？
3. 当前 run 失败后哪些资源要清理？

面试回答：

> Harness 要管理的是长任务，不是单次函数调用。所以 run 必须有状态机，能处理排队、执行、工具等待、取消、失败、清理和恢复。

## 深挖补充：ThreadState 的字段可以怎么拆

ThreadState 可以按职责拆成几类：

| 类别 | 字段示例 | 合并策略 |
| --- | --- | --- |
| 对话状态 | messages、summary | 追加、移除、替换 |
| 工具状态 | promoted_tools、tool_catalog_hash | 去重、过期失效 |
| 产物状态 | artifacts、images、files | 按 id 或路径 upsert |
| 运行状态 | run_id、status、errors | 状态机迁移 |
| 成本状态 | token_usage、tool_duration | 累加 |
| 安全状态 | guardrail_events、sandbox_id | 追加和绑定 |

高分表达：

> reducer 的意义是把每类字段的合并语义写清楚。消息是追加，工具是去重，成本是累加，状态是按状态机迁移，不能用一个浅 merge 解决。

## 深挖补充：中间件顺序为什么重要

中间件不是随便排列。顺序错了会产生实际 bug。

推荐顺序：

```text
user / thread context
  -> dynamic context
  -> summarization
  -> tool filtering / deferred setup
  -> model call
  -> tool call guardrails
  -> sandbox execution
  -> result normalization
  -> memory / skill / eval async hooks
```

典型错误：

| 错误顺序 | 后果 |
| --- | --- |
| 先摘要再保护 reminder | 当前日期和记忆被压进旧历史 |
| 先暴露工具再做权限过滤 | 模型可能看到不该看的 schema |
| 工具执行后才做 Guardrails | 安全拦截失去意义 |
| 主链路同步做 Skill 更新 | 用户响应被后台沉淀拖慢 |

面试回答：

> 中间件链路体现的是生命周期设计。不同能力必须挂在正确时机，否则不是代码风格问题，而是会破坏安全、上下文和延迟。

## 深挖补充：子 Agent 的边界

子 Agent 适合处理明确、可隔离的子任务：

- 检索资料。
- 检查代码。
- 生成某个文件。
- 分析某个模块。
- 运行一组测试并汇总。

不适合交给子 Agent 的任务：

- 需要主 Agent 持续和用户澄清的问题。
- 需要全局产品判断的问题。
- 高风险权限决策。
- 没有明确完成标准的开放任务。

子 Agent 返回结果应该结构化：

```text
status: success / failed / partial
summary: 做了什么
evidence: 文件、命令、输出、引用
risks: 未验证或失败点
next_steps: 主 Agent 应该怎么继续
```

## 深挖补充：Harness 故障排查

| 现象 | 优先排查 |
| --- | --- |
| 模型重复问同样问题 | summary 是否丢目标，memory 是否没注入 |
| 工具突然不可用 | allowed-tools、catalog_hash、promoted_tools |
| 子任务结果丢失 | ThreadState reducer 是否覆盖字段 |
| 流式输出中断 | run worker、SSE、取消状态 |
| 文件路径错乱 | sandbox workspace、thread_id、artifact mapping |
| 成本突然升高 | 工具 schema、动态上下文、摘要触发阈值 |

面试表达：

> 我会按 Harness 层次排查：先看 run 状态，再看 ThreadState，再看上下文和工具装配，再看安全与沙箱，最后看模型本身。

## 深挖补充：系统设计题怎么展开

如果面试官让你“现场设计一个 Agent Harness”，可以按下面答：

1. **入口**：thread/run API、鉴权、上传、流式响应。
2. **运行时**：run manager、worker、取消、超时、恢复。
3. **状态**：ThreadState、reducers、消息和产物持久化。
4. **上下文**：动态记忆、摘要、token budget。
5. **工具**：catalog、权限、deferred tools、结果标准化。
6. **安全**：Guardrails、Sandbox、审计。
7. **观测**：trace、metrics、eval case、dashboard。
8. **闭环**：Memory、Skill、数据飞轮。

收束句：

> 这个设计的关键不是堆功能，而是每层都有清晰输入输出和失败策略。
