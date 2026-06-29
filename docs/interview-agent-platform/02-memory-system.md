# 02 长期记忆管理系统

对应简历表述：

> 设计长期记忆管理系统，支持对话后异步入队、去抖合并、用户纠偏识别、强化信号检测、事实抽取、记忆摘要更新和冲突事实删除。

## 相关源码跳转

- [MemoryUpdater：抽取、纠偏、强化信号与持久化更新](../../backend/packages/harness/deerflow/agents/memory/updater.py#L457)
- [MemoryStorage：per-user / per-agent 文件存储与原子写入](../../backend/packages/harness/deerflow/agents/memory/storage.py#L1)
- [Memory summarization hook：摘要前触发记忆相关处理](../../backend/packages/harness/deerflow/agents/memory/summarization_hook.py#L1)
- [DynamicContextMiddleware：运行时读取并注入长期记忆](../../backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py#L89)

## 面试官想听什么

长期记忆不是“把聊天记录存起来”。面试官会关心：

1. 哪些信息值得记？
2. 什么时候更新记忆？
3. 怎么避免每轮对话都同步阻塞？
4. 怎么处理用户纠错？
5. 怎么删除过期或冲突事实？
6. 怎么避免把临时文件、一次性上下文记成长期事实？

## 设计目标

长期记忆系统解决的是“跨会话个性化”和“用户偏好延续”。

它不应该记录所有内容，只记录对未来交互有价值的信息：

- 用户长期偏好：语言、沟通风格、技术偏好。
- 工作上下文：当前项目、技术栈、正在排查的问题。
- 稳定事实：用户角色、长期目标、专业领域。
- 明确纠错：用户指出过的错误理解、正确做法。
- 强化信号：用户反复确认喜欢的回答方式或工具方式。

## 整体流程

```text
Agent 完成一轮响应后
  -> MemoryMiddleware.after_agent()
  -> 过滤可用于记忆的消息
  -> 判断是否有人类消息 + AI 消息
  -> 检测 correction / reinforcement 信号
  -> 写入 MemoryQueue
  -> 同一 thread/user/agent 在去抖窗口内合并
  -> 后台 worker 调用 MemoryUpdater
  -> LLM 根据 MEMORY_UPDATE_PROMPT 生成 JSON 更新
  -> 更新 user/history/newFacts/factsToRemove
```

关键代码：

- `backend/packages/harness/deerflow/agents/middlewares/memory_middleware.py`
- `backend/packages/harness/deerflow/agents/memory/queue.py`
- `backend/packages/harness/deerflow/agents/memory/updater.py`
- `backend/packages/harness/deerflow/agents/memory/prompt.py`

## 学习版：长期记忆不是聊天记录

长期记忆不是把所有对话存起来。

```text
聊天记录：发生过什么
长期记忆：未来交互仍然有用的事实、偏好、约束和纠偏
```

例子：

| 对话内容 | 是否该记 | 原因 |
| --- | --- | --- |
| “以后都用中文回复我” | 是 | 强偏好，长期有效 |
| “这个项目不是基于开源项目，是我自己做的” | 是 | 明确纠偏 |
| “帮我看这个临时上传文件” | 否 | 文件是会话临时资源 |
| “刚才命令失败了” | 视情况 | 只有形成稳定修复经验才记 |
| “今天下午有点忙” | 通常否 | 短期状态，对未来价值低 |

一句话：

> 记忆系统要解决的是“什么信息值得跨会话保留”，不是“怎么保存所有历史消息”。

## 成熟系统怎么分记忆

| 记忆类型 | 内容 | 当前项目对应 |
| --- | --- | --- |
| Profile Memory | 语言、长期偏好、用户身份 | `personalContext` |
| Project Memory | 当前项目、技术栈、约束 | `workContext` |
| Episodic Memory | 近期任务和决策 | `recentMonths` / `topOfMind` |
| Correction Memory | 用户纠偏、Agent 错误修正 | `facts.category=correction` |
| Skill Memory | 可复用工作流 | Skill 自进化 |

面试回答：

> 我把记忆拆成摘要字段和原子事实。摘要字段适合描述用户状态和历史脉络，原子事实适合做置信度、冲突删除和动态上下文注入。

## 当前项目落地流程

完整链路可以拆成八步：

```text
1. after_agent 捕获完整对话
2. 检测 correction / reinforcement 信号
3. 按 thread_id/user_id/agent_name 入队
4. 去抖合并同一目标
5. 后台 worker 拉取当前 memory
6. LLM 输出结构化 memory patch
7. schema 校验、冲突事实删除
8. 写回 memory store
```

简化代码：

```python
def after_agent(state, runtime):
    messages = state["messages"]
    memory_queue.enqueue(
        thread_id=runtime.context["thread_id"],
        user_id=runtime.context.get("user_id"),
        agent_name=runtime.context.get("agent_name"),
        messages=messages,
        correction_detected=detect_user_correction(messages),
        reinforcement_detected=detect_reinforcement(messages),
    )
```

后台 worker：

```python
def process_memory_job(job):
    current = memory_store.load(job.user_id, job.agent_name)
    patch = memory_llm.invoke({
        "current_memory": current,
        "conversation": render_conversation(job.messages),
        "correction_hint": job.correction_detected,
    })
    memory_store.apply(validate_memory_patch(patch))
```

## 纠错字段怎么更新

用户纠错时一般更新两类：

| 更新位置 | 作用 |
| --- | --- |
| `newFacts[].category = "correction"` | 记录明确纠偏事实 |
| `factsToRemove` | 删除或替换冲突旧事实 |

如果纠错影响当前项目背景，也可能更新：

```text
user.workContext.summary
user.topOfMind.summary
history.recentMonths.summary
```

例子：

```json
{
  "newFacts": [
    {
      "content": "User's DeerFlow Agent Harness project should be described as self-built, not as based on another open-source project.",
      "category": "correction",
      "confidence": 0.98
    }
  ],
  "factsToRemove": ["fact_claiming_based_on_open_source"]
}
```

面试回答：

> 纠错不能只追加新事实，还要删除冲突旧事实。否则长期记忆里新旧说法并存，后续动态上下文仍可能把旧错误带回来。

## 冲突处理优先级

```text
用户明确纠偏
  > 用户明确陈述
  > 多轮稳定行为
  > 模型推断
```

常见冲突：

| 冲突 | 处理 |
| --- | --- |
| 显式反转 | 删除旧事实，新增当前事实 |
| 用户纠错 | correction 高优先级 |
| 时间过期 | 从 topOfMind 移除 |
| 低置信推断冲突 | 保留用户明确说法 |

## 评估和观测

指标：

| 指标 | 含义 |
| --- | --- |
| `memory_precision` | 写入记忆有多少真的有用 |
| `memory_recall` | 该记的信息有多少被记住 |
| `correction_capture_rate` | 用户纠偏捕获率 |
| `conflict_removal_rate` | 冲突旧事实删除率 |
| `false_memory_rate` | 错误/臆测记忆比例 |
| `memory_update_latency` | 从对话到可用的延迟 |

事件：

```text
memory.enqueue.created
memory.enqueue.merged
memory.patch.generated
memory.patch.validated
memory.update.completed
memory.conflict.removed
memory.update.failed
```

排障：

```text
用户说“你没记住”
  -> 看 after_agent 是否入队
  -> 看去抖合并是否覆盖
  -> 看 worker 是否成功
  -> 看 patch 是否 shouldUpdate=false
  -> 看 dynamic context 是否选中了这条记忆
```

## 为什么要异步入队

记忆更新通常需要额外 LLM 调用，如果同步执行，会拉长用户请求耗时。

异步入队的好处：

1. 不阻塞主对话响应。
2. 多轮快速对话可以合并，减少重复更新。
3. 记忆更新失败不会直接导致主任务失败。
4. 后台 worker 可以做重试和错误隔离。

面试回答：

> 我把记忆更新放在 after_agent 后异步入队，而不是同步写。因为记忆更新需要额外模型调用，直接同步会影响主链路延迟。异步队列可以把同一用户、线程和 Agent 的短时间重复更新合并掉，同时保留纠偏和强化信号。

## 去抖合并设计

合并键：

```text
(thread_id, user_id, agent_name)
```

合并策略：

```text
如果同一 key 已存在待处理任务：
  -> 保留最新 messages
  -> correction_detected 做 OR
  -> reinforcement_detected 做 OR
  -> 移除旧 context
  -> append 新 context
```

为什么 correction/reinforcement 要 OR？

因为这些是稀有但重要的信号。如果某一轮出现用户纠偏，后面又很快追加新消息，不能因为覆盖 messages 就丢掉纠偏标记。

面试回答：

> 去抖合并不是简单覆盖。消息列表保留最新版本，但纠偏和强化信号要做逻辑或，因为这类信号一旦出现就应该影响后续记忆 prompt，否则用户刚纠正过的内容可能被漏记。

## 用户纠偏识别

纠偏信号通常包括：

- “不是这个意思”
- “你理解错了”
- “以后都用中文”
- “不要这样写，应该……”
- “刚才那个方案不对”

识别到纠偏后，记忆更新 prompt 会更强调：

```text
把正确做法记录为 correction 类事实
如果错误明确出现，记录 sourceError
删除冲突事实
```

## 强化信号检测

强化信号是用户确认某种方式有效：

- “对，以后就这样”
- “这个格式很好”
- “你这样解释我能懂”
- “以后都按这个方式”

它通常更新 `preference` 或 `behavior` 类事实。

注意：

> 如果同一轮已经检测到 correction，就不要再把后续确认误判成 reinforcement。

## 记忆结构

可以按三层讲：

### 1. 当前用户上下文

```text
user.workContext
user.personalContext
user.topOfMind
```

用途：

- `workContext`：当前工作、项目、技术栈。
- `personalContext`：语言、沟通偏好、兴趣。
- `topOfMind`：近期正在关注的多个主题。

### 2. 历史时间上下文

```text
history.recentMonths
history.earlierContext
history.longTermBackground
```

用途：

- `recentMonths`：最近 1-3 个月活动。
- `earlierContext`：3-12 个月仍相关的模式。
- `longTermBackground`：长期稳定背景。

### 3. 事实列表

```text
facts: [
  {
    id,
    content,
    category,
    confidence,
    sourceError?
  }
]
```

类别：

- `preference`：偏好。
- `knowledge`：能力或知识。
- `context`：背景事实。
- `behavior`：工作模式。
- `goal`：目标。
- `correction`：纠错。

## 事实抽取原则

只记录未来有用的信息：

应该记录：

- “用户明确要求全程中文回复。”
- “用户正在做智能 Agent 平台项目。”
- “用户偏好面试回答要能口语化表达。”

不应该记录：

- “用户上传了一个文件。”
- “用户今天问了某个一次性问题。”
- “用户打开过某个临时路径。”

面试回答：

> 长期记忆的关键是过滤。不是所有对话都值得记，只有对未来个性化、纠错、任务连续性有价值的信息才应该进入长期记忆。尤其上传文件这类会话临时事件不能记录，否则后续会话无法访问文件，反而造成幻觉。

## 冲突事实删除

记忆更新输出里有：

```json
{
  "newFacts": [],
  "factsToRemove": []
}
```

如果用户说“我不是前端，我主要做后端 Agent 平台”，系统应该：

1. 新增“用户主要做后端 Agent 平台”的事实。
2. 删除或替换“用户是前端”的旧事实。

## 失败策略

记忆更新最怕“半更新”。

例如模型输出：

```json
{
  "newFacts": "bad format",
  "factsToRemove": ["fact_1"]
}
```

如果直接执行删除，就可能丢失旧事实，但新事实没写进去。

所以安全策略应该是：

> 当 `factsToRemove` 非空且 `newFacts` 格式错误时，拒绝这次部分更新。

## 可讲的 trade-off

### 同步更新 vs 异步更新

同步：

- 优点：强一致。
- 缺点：慢，影响主对话。

异步：

- 优点：低延迟，可去抖，可重试。
- 缺点：下一轮极短时间内可能读不到刚更新的记忆。

我的选择：

> 对 Agent 平台来说，主交互延迟比记忆强一致更重要，所以记忆更新异步化；通过去抖合并和纠偏信号 OR 保证关键记忆不丢。

### 摘要字段 vs 原子事实

摘要字段：

- 优点：容易给模型读。
- 缺点：不便去重和冲突删除。

原子事实：

- 优点：可检索、可删除、可排序。
- 缺点：需要分类和置信度管理。

我的选择：

> 两者结合。摘要负责给模型快速理解用户背景，事实列表负责精确检索、纠错和冲突删除。

## 高频追问

### 1. 怎么判断一个信息要不要进长期记忆？

看它是否满足至少一个条件：跨会话仍有用、表达稳定偏好、影响未来回答方式、是明确纠错、与当前长期项目相关。

### 2. 用户说错了怎么办？

不把模型推断当成高置信事实。只有用户明确陈述才给高置信度；推断事实控制在较低置信度，并允许后续纠错删除。

### 3. 为什么 correction 要单独分类？

因为纠错事实优先级更高。它告诉系统“以前错过，以后不要再错”，比普通 preference 更需要在注入时优先考虑。

### 4. 如果后台记忆更新失败，会影响主任务吗？

不影响。记忆是增强能力，不是主链路硬依赖。失败应该记录日志或重试，但不能让用户请求失败。

## 深挖补充：长期记忆的写入链路

面试时可以把长期记忆讲成一条后台数据管线，而不是“对话后总结一下”。

```text
run finished
  -> 收集本轮用户消息、模型回答、工具结果摘要
  -> 判断是否存在可记忆信号
  -> 按 user_id + agent_id + thread_id 入队
  -> 去抖合并短时间内的多次更新
  -> 抽取偏好、事实、纠偏、强化信号
  -> 和旧记忆做冲突检测
  -> 更新 summary / facts / corrections
  -> 写入 memory store
  -> 记录 memory_update trace
```

这条链路有两个设计重点：

1. **记忆更新不阻塞主回答**，因为抽取、冲突判断和存储都可能慢。
2. **记忆写入要有门禁**，不是所有对话内容都应该进入长期记忆。

面试表达：

> 长期记忆本质上是用户画像和偏好的受控写入系统，不是聊天日志归档。它需要抽取、过滤、纠偏、冲突删除和审计。

## 深挖补充：什么应该记，什么不应该记

| 类型 | 是否进入长期记忆 | 示例 |
| --- | --- | --- |
| 稳定偏好 | 可以 | “默认用中文回复”“我喜欢先给结论” |
| 明确纠偏 | 必须处理 | “不是 Java，是 Go” |
| 长期身份事实 | 谨慎 | “我是后端工程师，主要做 Agent 平台” |
| 临时任务状态 | 不进长期记忆 | “这个 PR 还没 review” |
| 一次性文件路径 | 不进长期记忆 | `/tmp/report-1.pdf` |
| 敏感信息 | 默认不进 | token、密码、身份证、隐私数据 |
| 模糊推断 | 不进或低置信 | “用户可能喜欢 X” |

高分回答：

> 我只记对未来交互稳定有用的信息，尤其是用户明确表达的偏好、身份事实和纠偏。临时任务状态、一次性路径、敏感信息和模型猜测不应该进入长期记忆。

## 深挖补充：冲突处理要具体

冲突不是简单覆盖。可以按四个维度判断：

| 维度 | 规则 |
| --- | --- |
| 来源 | 用户明确纠偏 > 用户普通表达 > 模型推断 |
| 时间 | 新事实通常优先于旧事实 |
| 置信度 | 明确表达优先于模糊表达 |
| 作用域 | 当前线程事实不能随便覆盖全局偏好 |

例子：

```text
旧记忆：用户使用 Java。
新消息：不是 Java，我现在主要写 Go。
处理：删除或降权旧事实，新增 Go 事实，并记录 correction。
```

面试官如果追问“为什么不是追加一条新事实”，可以答：

> 追加会让未来检索同时命中 Java 和 Go，模型不知道听谁的。纠偏场景要显式处理冲突，否则长期记忆会越积越乱。

## 深挖补充：一致性取舍

异步记忆会带来一个问题：用户刚说完偏好，下一轮马上问，后台可能还没写完。

解决思路：

1. **短期一致性靠 ThreadState**：当前线程里刚出现的事实仍在最近消息中，模型可以看到。
2. **长期一致性靠异步 Memory**：跨线程复用等后台更新完成。
3. **关键纠偏可快路径处理**：如果检测到强 correction，可以优先写入或标记 pending memory。

推荐讲法：

> 我接受长期记忆的最终一致性，因为它不应该拖慢主链路。但当前对话的一致性由最近消息保证；如果是强纠偏，可以走更高优先级的更新队列。

## 深挖补充：失败排查

当用户说“你怎么又忘了”时，不要直接说模型问题。按下面排查：

```text
用户偏好没有生效
  -> 当时有没有被抽取成 memory candidate
  -> 是否被隐私/临时信息过滤掉
  -> 是否进入队列但还没消费
  -> 是否写入了错误 user_id / agent_id scope
  -> 动态上下文注入时是否取到了这条 memory
  -> 模型是否被当前输入或 system prompt 覆盖
```

需要观测的字段：

| 字段 | 用途 |
| --- | --- |
| `memory_candidate_count` | 本轮抽取了多少候选 |
| `memory_write_status` | 写入成功、跳过、失败 |
| `memory_skip_reason` | 为什么没有写 |
| `memory_scope` | user / agent / thread 作用域 |
| `memory_conflict_count` | 检测到多少冲突 |
| `memory_update_latency_ms` | 后台更新时间 |

## 深挖补充：面试攻防

### Q：为什么不用向量库保存所有聊天记录？

向量库适合检索资料，不等于长期记忆。聊天记录里有大量临时、重复、敏感、过期内容，直接向量化会污染未来上下文。长期记忆要抽取成稳定事实和偏好。

### Q：长期记忆怎么删除？

要支持按 fact id、用户、agent、来源和时间删除。用户明确要求忘记时，要删除或 tombstone 相关事实，并确保后续检索不会再召回。

### Q：记忆会不会让模型更偏见？

会，所以要限制写入类型，避免把模型推断、单次情绪和敏感属性写成事实。记忆系统应该偏保守。

### Q：多 Agent 共享记忆吗？

基础偏好可以共享，任务型经验最好按 agent scope 隔离。比如“默认中文回复”可以全局共享，但“这个代码助手常用某个仓库路径”不应污染其他 Agent。
