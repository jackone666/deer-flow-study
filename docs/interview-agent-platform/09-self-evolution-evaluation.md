# 09 自进化与评测闭环

这一篇补充当前项目里的“自进化”讲法。这里的自进化不是让 Agent 无约束修改自己，而是让 Harness 在任务结束后，把**用户纠偏、工具调用经验、失败修复路径、可复用工作流**沉淀到 Memory 和 Skill 中，并通过评测闭环验证是否真的变好。

## 自进化要解决什么

没有自进化时，Agent 平台会遇到这些问题：

```text
用户纠正一次 -> 下一次又犯同样错误
复杂任务跑通一次 -> 经验没有沉淀
某个 Skill 有缺口 -> 只能靠人工记住下次修
Prompt 改了一点 -> 不知道整体效果变好还是变差
```

自进化的目标：

```text
任务执行
  -> 采集信号
  -> 判断是否值得沉淀
  -> 更新 Memory / Skill
  -> 通过评测或回归验证
  -> 后续任务复用
```

## 学习版：自进化不是模型自己改自己

自进化可以理解成 Agent 的“经验沉淀机制”。

人类工程师完成任务后会做：

```text
复盘问题
记录踩坑
更新文档
抽象脚本
补测试
加入 checklist
```

Agent 自进化对应：

```text
用户纠偏 -> Memory correction
重复流程 -> Skill
失败案例 -> Eval case
高分轨迹 -> Training data
安全问题 -> Guardrail rule
工具失败 -> Tool schema / description
```

一句话：

> 自进化不是让模型无约束修改自己，而是 Harness 把可复用经验从一次任务里提炼出来，经过门禁后进入长期系统。

## 成熟系统怎么做

成熟 Agent 平台不会让线上数据直接改线上能力，而是走受控闭环：

```text
capture
  -> triage
  -> label
  -> propose change
  -> review / validate
  -> eval regression
  -> rollout
  -> monitor
```

对应当前项目：

| 阶段 | 当前项目对象 |
| --- | --- |
| capture | trace、messages、tool errors、user corrections |
| triage | MemoryMiddleware、Skill evolution 判断 |
| label | correction、reinforcement、failure type |
| propose | memory patch、skill patch、tool doc patch |
| validate | JSON schema、rubric、eval suite |
| rollout | 写入 memory/skill/tool config |
| monitor | 用户纠偏率、case pass rate |

面试回答：

> 我会把自进化做成受控闭环，而不是让模型直接改线上规则。所有更新都要有来源、门禁、评测和回滚。

## 自进化对象

| 对象 | 适合沉淀什么 | 不适合沉淀什么 |
| --- | --- | --- |
| Memory | 用户偏好、项目背景、明确纠偏 | 临时文件、低置信猜测 |
| Skill | 可复用流程、稳定步骤 | 一次性任务、未验证做法 |
| Eval Case | 失败样本、边界条件、安全规则 | 没有期望行为的模糊样本 |
| Tool Docs | 工具别名、参数说明、错误提示 | 与工具无关的用户事实 |
| Guardrails | 高风险规则、拒绝原因 | 主观风格偏好 |
| Training Data | 高质量轨迹、标注结果 | P0 失败、脏数据 |

## 简化版代码

```python
def after_agent_evolution(state, runtime):
    trace = runtime.context["trace"]
    signals = collect_signals(
        messages=state["messages"],
        tool_calls=trace.tool_calls,
        errors=trace.errors,
    )

    candidates = []
    if signals.user_correction:
        candidates.append(propose_memory_correction(signals))
    if signals.reusable_workflow:
        candidates.append(propose_skill_patch(signals))
    if signals.failure_reproducible:
        candidates.append(propose_eval_case(signals))

    for candidate in candidates:
        if passes_gate(candidate):
            enqueue_review_or_apply(candidate)
```

门禁：

```python
def passes_gate(candidate):
    return (
        candidate.source_is_trusted
        and candidate.schema_valid
        and candidate.has_expected_behavior
        and not candidate.contains_sensitive_data
        and not candidate.from_prompt_injection
    )
```

## Skill 自进化怎么设计

一个 Skill 至少包含：

```text
name
description
when_to_use
inputs
steps
tools_needed
failure_modes
verification
examples
version
```

候选 Skill 判断：

- 是否重复出现。
- 是否需要多步工具调用。
- 是否有稳定成功路径。
- 是否能抽象成通用流程。
- 是否有验证方法。

不应该沉淀为 Skill：

- 一次性命令。
- 用户临时偏好。
- 未验证 workaround。
- 包含隐私或临时文件路径的流程。

## 评估和观测

指标：

| 指标 | 含义 |
| --- | --- |
| `correction_repeat_rate` | 同类纠错是否减少 |
| `skill_reuse_success_rate` | Skill 复用成功率 |
| `time_to_complete_delta` | 使用 Skill 后耗时是否下降 |
| `tool_call_count_delta` | 使用 Skill 后工具调用是否减少 |
| `eval_case_fix_rate` | 新增失败 case 是否被修复 |
| `regression_rate_after_evolution` | 自进化后是否引入回归 |

事件：

```text
evolution.signal.detected
evolution.memory.proposed
evolution.skill.proposed
evolution.eval_case.created
evolution.gate.rejected
evolution.change.applied
evolution.rollback
```

风险：

| 风险 | 表现 | 解决 |
| --- | --- | --- |
| 以错训错 | 错误轨迹被沉淀 | P0 fail 不进训练/Skill |
| 记忆污染 | 低置信推断写入 | 置信度和用户明确性门禁 |
| Skill 膨胀 | 太多低质量 Skill | 复用率和成功率淘汰 |
| 过拟合用户 | 单个用户偏好影响全局 | 用户级/全局级隔离 |
| 隐私泄漏 | 临时文件或敏感信息固化 | 脱敏和禁止入库规则 |

## 回滚机制

任何自进化对象都要可回滚：

```text
memory fact -> fact_id + created_at + source_run_id
skill -> versioned file + changelog
tool doc -> catalog hash + version
guardrail rule -> policy version
eval case -> active flag
```

面试回答：

> 自进化必须可追踪和可回滚。每条记忆、每个 Skill patch、每条规则都要知道来源 run，出了问题能撤掉。

## 自进化分三层

| 层级 | 目标 | 当前项目例子 |
| --- | --- | --- |
| Memory 自进化 | 记住用户偏好、纠偏和长期上下文 | 用户要求“以后全程中文”，写入长期偏好 |
| Skill 自进化 | 沉淀可复用工作流和踩坑经验 | 复杂文档生成流程、沙箱改造检查清单 |
| Harness 自进化 | 优化平台策略和评测标准 | 工具召回指标、Guardrails 拒绝率、摘要成功率 |

面试回答：

> 我把自进化拆成 Memory、Skill 和 Harness 策略三层。Memory 解决用户个性化，Skill 解决工作流复用，Harness 评测解决平台能力是否真的变好。

## 触发条件

不是每次对话都应该自进化。

适合触发：

1. 任务用了很多工具调用，说明流程复杂。
2. 用户明确纠正了方向、格式、语言或实现方式。
3. Agent 遇到非显然错误并成功修复。
4. 某个流程反复出现，可以抽象成 Skill。
5. 使用已有 Skill 时发现它没有覆盖当前坑点。

不适合触发：

- 简单问答。
- 一次性闲聊。
- 临时上传文件事件。
- 未经验证的模型推断。
- 失败且没有明确修复方案的轨迹。

## Memory 自进化

Memory 自进化关注“用户是谁、偏好什么、纠正过什么”。

例子：

```text
用户：以后全程中文回答。
```

应该沉淀为：

```json
{
  "content": "用户明确要求全程使用中文回复。",
  "category": "preference",
  "confidence": 0.98
}
```

再比如：

```text
用户：不是基于什么，这是我自己做的。
```

应该沉淀为：

```json
{
  "content": "描述用户项目时应避免使用“基于某开源项目”等表述，需强调这是用户自己设计实现的项目。",
  "category": "correction",
  "confidence": 0.98,
  "sourceError": "之前将用户项目描述成基于某项目改造。"
}
```

关键点：

- correction 类事实优先级高。
- 明确纠错才写 `sourceError`。
- 与新事实冲突的旧事实进入 `factsToRemove`。
- 上传文件、临时路径不能进长期记忆。

## Skill 自进化

Skill 自进化关注“任务应该怎么做”。

当前项目里可以沉淀的 Skill 例子：

### 1. 沙箱系统改造 Skill

适用场景：

```text
用户要求改造 sandbox/provider/backend/配置/脚本/测试。
```

可沉淀流程：

```text
1. 先读 SandboxProvider 抽象和当前 provider 实现
2. 找运行路径中的 fallback 分支
3. 改配置 schema 和默认 config
4. 改启动脚本和 doctor 检查
5. 更新提示词里对沙箱能力的描述
6. 补 provider 行为测试和脚本模式探测测试
7. 跑 diff --check、相关 pytest、py_compile
```

### 2. 面试文档生成 Skill

适用场景：

```text
用户要求围绕简历项目生成面试准备文档。
```

可沉淀流程：

```text
1. 先抽简历 bullet 对应的能力点
2. 每个能力点拆成：设计目标 / 核心流程 / 关键类方法 / trade-off / 高频追问
3. 新增总览、专题、题库、速记卡
4. 禁止引入不属于当前项目的业务内容
5. 只参考外部材料的组织格式，不照搬内容
```

### 3. Guardrails 深挖 Skill

适用场景：

```text
用户问安全拦截中间件怎么实现。
```

可沉淀流程：

```text
1. 先讲为什么 prompt 不是安全边界
2. 定义 GuardrailRequest / Decision / Reason
3. 定义 Provider 接口
4. 展示 AllowlistProvider
5. 展示 wrap_tool_call / awrap_tool_call
6. 解释 fail-closed 和 ToolMessage(status="error")
7. 对比 Guardrails、工具权限、Sandbox
```

面试回答：

> Skill 自进化沉淀的是“解决问题的方法”，不是用户偏好。比如“以后中文回答”是 Memory；“生成面试文档时按总览、专题、题库、速记卡组织，并且外部材料只参考格式不搬内容”就是 Skill。

## Harness 如何采集自进化信号

自进化依赖运行时可观测性。

Harness 能采集：

| 信号 | 来源 | 用途 |
| --- | --- | --- |
| 工具调用次数 | Tool middleware | 判断任务复杂度 |
| 工具错误 | ToolErrorHandlingMiddleware | 记录踩坑和修复 |
| 用户纠偏 | MemoryMiddleware 检测 | 写 correction fact |
| 摘要触发 | SummarizationMiddleware | 评估长上下文压力 |
| Guardrail 拒绝 | GuardrailMiddleware | 评估安全策略 |
| deferred tool 命中 | tool_search / promoted | 评估工具检索效果 |
| 子 Agent 使用 | task 工具 | 判断任务拆分策略 |

面试回答：

> 自进化必须基于运行时信号，而不是只靠模型主观判断。Harness 正好处在模型、工具、状态和安全策略之间，所以它能采集工具次数、错误、用户纠偏、摘要触发、Guardrail 拒绝等信号。

## 自进化门禁

自进化最怕把错误经验固化，所以需要门禁。

### 1. 来源门禁

只允许这些来源触发：

- 用户明确纠偏。
- 成功完成的复杂任务。
- 已验证修复的错误。
- 高质量人工确认的流程。

不允许：

- 模型随手推断。
- 失败任务的未验证方案。
- 临时文件路径。
- 外部资料中不属于当前项目的业务内容。

### 2. 格式门禁

Memory 必须符合结构：

```json
{
  "content": "...",
  "category": "preference|knowledge|context|behavior|goal|correction",
  "confidence": 0.0
}
```

Skill 必须符合：

```text
name
description
when to use
step-by-step workflow
pitfalls
verification
```

### 3. 效果门禁

更新后要验证：

- 文档是否还基于当前项目。
- 测试是否通过。
- 相关工具是否还能正常被发现。
- Guardrails 是否没有被绕过。
- 面试问答是否能自洽。

## 评测闭环

当前项目的评测可以围绕 Agent Harness 能力设计，不需要引入外部业务。

### P0 一票否决

- 将用户项目错误描述成“基于某开源项目”。
- 把临时上传文件写入长期记忆。
- Guardrails provider 异常时 fail-open 放行高风险工具。
- 摘要压缩后丢失当前动态提醒。
- deferred tool 未提升却允许调用。
- 沙箱缺 `provisioner_url` 时静默 fallback 到本地执行。

### P1 重要流程项

- 是否解释清楚 Harness 分层。
- 是否说明 ThreadState reducer。
- 是否说明 Memory / Skill 区别。
- 是否说明 ToolMessage error 的原因。
- 是否说明远程 sandbox 的安全边界。
- 是否给出验证命令。

### P2 质量评分项

- 讲法是否适合面试。
- 是否有 trade-off。
- 是否能落到具体类/方法。
- 是否能回答追问。
- 是否避免空泛形容词。

## 评测样例

```json
{
  "question": "Guardrails 安全拦截中间件怎么实现？",
  "must_have": [
    "GuardrailRequest",
    "GuardrailDecision",
    "Provider evaluate",
    "wrap_tool_call",
    "fail-closed",
    "ToolMessage(status=\"error\")"
  ],
  "p0_fail": [
    "只说 prompt 安全提示",
    "provider 异常时默认放行高风险工具"
  ],
  "score_dimensions": {
    "architecture": 5,
    "code_simplification": 5,
    "tradeoff": 5,
    "project_specificity": 5
  }
}
```

## SFT / RL 如何讲但不夸大

如果面试官追问训练闭环，可以谨慎讲成“可扩展方向”：

> 当前系统先以 Memory 和 Skill 自进化为主，训练闭环可以作为下一阶段扩展。Harness 已经能记录任务轨迹、工具调用、错误、Guardrail 决策和最终回答，这些数据可以被 Rubric judge 评分。高分轨迹可进入 SFT 数据池，低分轨迹进入错误分析；更进一步可以把工具调用顺序、约束满足率、安全违规率作为 reward 做 Agentic RL。

不要说：

> 我已经训练出了一个更强模型。

除非你确实做了训练实验。

要说：

> 我设计了评测和训练数据闭环，当前主要用于回归评测和 Skill/Memory 更新，后续可扩展到 SFT / Agentic RL。

## 自进化和 Guardrails 的关系

自进化不能绕过安全系统。

原则：

```text
Agent 可以沉淀经验
Agent 可以修补 Skill
Agent 可以记录偏好
但不能提升自己的系统权限
不能绕过 Guardrails
不能绕过 Sandbox
不能把不安全命令写成推荐流程
```

例子：

- 可以沉淀：“沙箱改造后要跑 provider 测试和脚本探测测试。”
- 不可以沉淀：“提交失败就直接跳过所有检查。”
- 可以记录：“用户希望简历突出 Harness 和自进化。”
- 不可以记录：“用户上传过某个临时文件，后续继续访问。”

## 面试 2 分钟讲法

> 当前项目里的自进化分三层：Memory、Skill 和 Harness 评测。Memory 记录用户长期偏好和纠偏，比如用户明确说项目不能写成“基于某开源项目”，这会成为 correction fact。Skill 记录可复用工作流，比如沙箱系统改造的检查清单、Guardrails 深挖讲法、面试文档生成结构。Harness 负责采集自进化信号，包括工具次数、错误、用户纠偏、Guardrail 拒绝、摘要触发和子 Agent 使用。为了避免错误固化，我加了来源门禁、格式门禁和效果门禁；训练闭环则作为扩展方向，通过 Rubric judge 评估任务轨迹，高分样本可用于 SFT，低分样本进入错误分析。

## 高频追问

### 1. 自进化会不会把错误记住？

会，所以必须有门禁。用户明确纠偏优先级最高；模型推断不能直接高置信写入；失败任务如果没有验证修复方案，不能沉淀为 Skill。

### 2. Skill 自进化和长期记忆有什么区别？

长期记忆记录“用户偏好和事实”；Skill 记录“任务该怎么做”。比如“用户要求中文回答”是 Memory，“生成面试文档时外部资料只参考格式，内容必须回到当前项目”是 Skill。

### 3. 自进化什么时候触发？

复杂任务、用户纠偏、非显然错误修复、重复工作流、已有 Skill 暴露缺口时触发。简单问答和一次性任务不触发。

### 4. 如何证明自进化有效？

看回归评测：同类任务是否少犯错，工具调用是否更少，用户纠偏是否减少，Guardrail 拒绝是否合理，面试回答是否更贴合项目。

### 5. 如果 Skill 更新后变差怎么办？

Skill 更新要保留历史版本，并跑回归问题集。效果变差就回滚；用户明确纠偏可以作为高优先级修补依据。
