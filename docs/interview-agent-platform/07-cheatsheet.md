# 07 面试前速记卡

## 一句话总述

我做的是一套智能 Agent 平台，核心解决长上下文、多工具、多 Agent 和安全执行问题：模型调用前做动态上下文注入，对话后异步更新长期记忆，历史超阈值后摘要压缩，大规模工具通过分组、权限和延迟加载治理，工具执行前由 Guardrails 做确定性安全拦截。

## 核心源码速查

- [ThreadState reducer](../../backend/packages/harness/deerflow/agents/thread_state.py#L100)
- [DynamicContextMiddleware](../../backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py#L61)
- [DeerFlowSummarizationMiddleware](../../backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L100)
- [tool_search / deferred tools](../../backend/packages/harness/deerflow/tools/builtins/tool_search.py#L139)
- [GuardrailMiddleware](../../backend/packages/harness/deerflow/guardrails/middleware.py#L20)
- [Sandbox lazy init](../../backend/packages/harness/deerflow/sandbox/tools.py#L1313)
- [Run worker](../../backend/packages/harness/deerflow/runtime/runs/worker.py#L143)

## 端到端主线

```text
用户请求
  -> Gateway 创建 / 加载 thread
  -> Runtime 创建 run
  -> ThreadState 读取历史、附件、产物
  -> Context 注入记忆、日期、系统提醒
  -> Summarization 判断是否压缩
  -> Tooling 装配 allowed / deferred tools
  -> Model 决策回答、调工具或派子任务
  -> Guardrails 判断 allow / deny
  -> Sandbox 执行文件、命令、代码
  -> Tool result 写回 ThreadState
  -> Memory / Skill / Eval 异步沉淀
  -> Trace / Metrics 支撑观测和数据飞轮
```

30 秒收束：

> 模型负责推理，Harness 负责运行时、上下文、工具、安全、状态和评估闭环。

## 五个模块一句话

动态上下文：

> 在模型调用前按用户、线程和日期实时注入长期记忆、当前日期和系统提醒，并用隐藏 reminder 消息避免污染真实用户对话。

长期记忆：

> 对话结束后异步入队，按 thread/user/agent 去抖合并，识别纠偏和强化信号，再更新摘要、事实和冲突删除。

摘要压缩：

> token 超阈值时压缩旧消息，保留近期消息和动态提醒，通过 RemoveMessage 重建短上下文。

工具治理：

> 工具先按 group 和 allowed-tools 做权限过滤，大规模工具延迟加载，通过 tool_search 检索后 promoted，并用 catalog_hash 防止工具目录漂移。

Guardrails：

> 工具执行前构造 GuardrailRequest，由 provider 返回 allow/deny，拒绝或 provider 异常时返回标准化 error ToolMessage，默认 fail-closed。

## 最容易被追问的点

### 为什么隐藏消息不是普通用户消息？

因为它是模型可见、UI 隐藏的运行时上下文，不是用户真实输入。用 `<system-reminder>` 标签让模型知道这是提醒，同时不污染用户对话历史。

### 为什么摘要时要保护动态提醒？

动态提醒代表当前运行时上下文。如果被压进摘要，模型会把它当成历史内容，日期和记忆可能失去当前性。

### 为什么记忆异步？

记忆更新需要额外模型调用，同步会拖慢主链路。异步队列可以去抖合并，失败也不影响当前对话。

### 为什么 correction 信号要 OR 合并？

纠偏信号稀有但重要。一旦某轮出现纠偏，后续短时间消息覆盖时不能把这个信号丢掉。

### 为什么 tool hash 必须有？

promoted 工具会留在图状态里。工具目录变化后旧 promoted 可能引用不存在或 schema 已变化的工具，所以 catalog_hash 不同就丢弃旧 promoted。

### 为什么 Guardrails 要 fail-closed？

安全系统不可用时不能默认放行。工具可能有副作用，provider 出错时应该拒绝并返回 error ToolMessage。

## STAR 项目讲法

Situation：

> Agent 在长对话、多工具场景下容易出现上下文膨胀、记忆丢失、工具选择混乱和危险调用问题。

Task：

> 我要设计一套平台机制，让 Agent 能在长任务中保持上下文连续，同时控制工具暴露和执行安全。

Action：

> 我实现了动态上下文注入、长期记忆异步更新、摘要压缩、工具延迟加载和 Guardrails 安全拦截，并把这些能力放到中间件和状态管理层，保证各模块职责清晰。

Result：

> 系统可以在上下文超阈值时自动压缩历史，在对话后更新用户长期记忆，在工具很多时只提升相关工具，并在工具执行前做确定性安全拦截。

## 面试回答模板

当面试官问“你怎么实现 X”：

```text
我先说问题背景：
  X 解决的是...

然后说核心流程：
  输入是...
  中间经过...
  输出是...

再说关键难点：
  这里最容易出问题的是...

最后说权衡：
  我没有选择...，因为...
  我选择...，代价是...
```

## 不能这么说

不要说：

- “就是把记忆拼到 prompt 里。”
- “超过 token 就总结一下。”
- “工具多了就搜索一下。”
- “Guardrails 就是安全判断。”

要说：

- “动态上下文是模型可见但 UI 隐藏的运行时提醒，不等同于真实用户消息。”
- “摘要压缩要处理 cutoff、tool call 对齐、动态提醒保护和消息重建。”
- “工具治理包括静态权限、延迟 schema、检索提升和 catalog_hash 防漂移。”
- “Guardrails 是工具执行前的确定性拦截，拒绝后仍返回标准化 ToolMessage 保持协议完整。”

## 最后 60 秒复习

```text
动态上下文：before_model 注入，隐藏 HumanMessage，保护 system-reminder。
长期记忆：after_agent 入队，去抖合并，纠偏/强化，factsToRemove。
摘要压缩：token 阈值，cutoff，旧消息摘要，近期消息保留，RemoveMessage 重建。
工具治理：group、allowed-tools、deferred tools、tool_search、promoted、catalog_hash。
Guardrails：wrap_tool_call，GuardrailRequest，allow/deny，fail-closed，ToolMessage error。
```

## 当前项目 Harness 案例速记

一句话：

> 当前项目是智能 Agent Harness 平台，用统一运行时管理 ThreadState、中间件链、动态上下文、长期记忆、摘要压缩、工具治理、子 Agent、远程沙箱和 Guardrails。

核心链路：

```text
User Message
  -> ThreadData
  -> Remote Sandbox
  -> Dynamic Context
  -> Summarization
  -> Lead Agent
  -> Tool / Task Subagent
  -> Guardrails / Audit
  -> ThreadState Reducer
  -> Memory / Skill Evolution
```

Harness 讲法：

```text
Runtime：thread_id / session_dir / active_tasks
Context：长期偏好 + 当前日期 + system-reminder
Tooling：group + allowed-tools + deferred tools + tool_search
Orchestration：Lead Agent + task 子 Agent
Safety：Guardrails + Sandbox + 文件安全
Evolution：Memory + Skill + Rubric + SFT/RL
```

fork 三条件：

```text
能并行
上下文要隔离
调用链深度 >= 3
```

自进化三层：

```text
Memory：用户偏好和纠偏
Skill：复用成功工作流
Training：Rubric 高分轨迹 -> SFT / Agentic RL
```

## 评估、观测、数据飞轮速记

一句话：

> 评估回答“好不好”，观测回答“坏在哪”，数据飞轮回答“怎么越用越好”。

P0/P1/P2：

```text
P0：安全和正确性底线，触发即失败
P1：关键过程项，缺失严重扣分
P2：体验、结构、成本和解释质量
```

观测三件套：

```text
Logs：发生了什么
Metrics：趋势和比例如何
Traces：一次 run 每一步怎么走
```

关键指标：

```text
token、latency、tool_error_rate、guardrail_deny_rate
summary_compression_ratio、memory_update_fail_count
tool_search_precision@5、sandbox_acquire_latency
```

数据飞轮：

```text
任务轨迹
  -> 评测/纠偏/错误
  -> 数据清洗和门禁
  -> Memory / Skill / Tool / Guardrails
  -> 回归评测
  -> 新版本
```

## 沙箱系统速记

一句话：

> 沙箱管“工具在哪里执行”，不是管“模型能不能调用工具”。

链路：

```text
SandboxMiddleware
  -> ensure_sandbox_initialized()
  -> AioSandboxProvider
  -> RemoteSandboxBackend
  -> HTTP provisioner
  -> remote sandbox
```

四层分工：

```text
工具权限：有没有入口
Guardrails：这次允不允许
Sandbox：在哪里执行
SandboxAudit：bash 内容是否危险
```

关键设计：

```text
provisioner_url 必填，缺失 fail-fast
默认懒加载，首次工具调用才创建
thread_id 派生 deterministic sandbox_id
同线程复用，空闲回收，启动接管遗留 sandbox
```

## 从零学习速记

先抓总图：

```text
Harness = Agent 应用服务器
Context = 模型调用前看到什么
Memory = 哪些信息跨会话保留
Summary = 长上下文怎么压缩
Tools = 模型能调用什么
Guardrails = 这次调用允不允许
Sandbox = 允许后在哪里执行
Evolution = 经验怎么沉淀
Eval/Obs = 怎么证明有效、怎么定位问题
```

面试展开顺序：

```text
问题背景
  -> 为什么简单 Agent 不够
  -> 当前项目怎么分层
  -> 核心链路
  -> 关键 trade-off
  -> 怎么评估和观测
```

万能回答骨架：

```text
我不是只做了一个 prompt，而是做了运行时机制。
这个机制解决的是 [问题]。
链路是 [before/after/wrap/state/tool]。
为了安全/稳定，我加了 [门禁/去重/回滚/评测]。
最后用 [指标/trace/eval] 证明有效。
```
