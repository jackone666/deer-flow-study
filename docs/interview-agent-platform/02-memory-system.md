# 02 长期记忆管理系统

对应简历表述：

> 设计长期记忆管理系统，支持对话后异步入队、去抖合并、用户纠偏识别、强化信号检测、事实抽取、记忆摘要更新和冲突事实删除。

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
