# 01 动态上下文注入机制

对应简历表述：

> 实现动态上下文注入机制，在模型调用前按规则注入长期记忆、当前日期、系统提醒等上下文信息，并通过隐藏消息机制降低对用户真实对话语义的干扰。

## 相关源码跳转

- [DynamicContextMiddleware：模型调用前注入隐藏 system-reminder](../../backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py#L61)
- [Lead Agent prompt：读取长期记忆上下文](../../backend/packages/harness/deerflow/agents/lead_agent/prompt.py#L1)
- [MemoryStorage：按 user/agent 维度隔离记忆文件](../../backend/packages/harness/deerflow/agents/memory/storage.py#L100)
- [SummarizationMiddleware：摘要时保护动态提醒](../../backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L194)

## 面试官想听什么

面试官不会只想听“我把记忆拼到 prompt 里”。他真正会考：

1. 为什么不能把所有上下文都塞进 system prompt？
2. 为什么要在模型调用前动态注入？
3. 为什么注入成 `HumanMessage`，而不是 `SystemMessage`？
4. 怎么避免重复注入？
5. 摘要压缩时怎么保护这些动态提醒？

## 设计目标

动态上下文注入解决三个问题：

1. **个性化**：模型需要知道用户长期偏好、当前任务背景、语言习惯。
2. **时效性**：模型需要知道当前日期、跨天变化、运行时状态。
3. **低污染**：这些上下文不是用户真实说过的话，不能混进正常对话语义里。

所以设计上采用：

```text
模型调用前
  -> 读取当前消息列表
  -> 判断是否需要注入长期记忆/日期/提醒
  -> 构造隐藏的 system-reminder 消息
  -> 追加到消息列表尾部
  -> 交给模型
```

## 核心流程

代码入口可以围绕 `DynamicContextMiddleware` 讲：

- `backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py`

典型流程：

```text
before_model(state, runtime)
  -> 读取 state["messages"]
  -> 找到真实用户消息
  -> 判断本轮是否已注入过动态上下文
  -> 构造 <system-reminder>...</system-reminder>
  -> 用 HumanMessage 注入到模型上下文
```

## 为什么是模型调用前注入

因为动态上下文依赖运行时状态：

- 当前日期可能变化。
- 长期记忆可能刚刚更新。
- 用户身份、线程、agent_name 可能不同。
- 上下文预算可能需要按当前消息长度做取舍。

如果写死在全局 system prompt 中，会有两个问题：

1. **不可动态变化**：无法按线程、用户、日期变化。
2. **污染基础指令**：长期记忆属于用户上下文，不应该和系统规则混在一起。

面试回答：

> 我把这类内容放在 before_model 阶段动态注入，而不是写死在 system prompt。原因是长期记忆、日期和运行时提醒都和当前线程有关，需要按调用实时生成；同时它们不是最高优先级的系统规则，不应该和系统安全指令混在一起。

## 为什么注入为 HumanMessage

这点容易被追问。

简洁回答：

> 这里的隐藏消息不是系统开发者规则，而是对当前用户上下文的补充。用 `HumanMessage` 可以让它出现在对话上下文里，被模型理解为“和用户相关的运行时提醒”，同时通过 `<system-reminder>` 标签和隐藏标记区分它不是真实用户输入。

关键点：

- 它不是用户真的说的话。
- 它也不是不可违背的系统规则。
- 它是“模型可见、UI 隐藏”的上下文补充。
- 使用 `<system-reminder>` 标签让模型知道这是提醒，不是普通自然语言请求。

## 隐藏消息是什么意思

“隐藏”不是模型看不到，而是用户界面不把它当作真实聊天内容展示。

```text
用户视角：看不到这条提醒
模型视角：能看到 <system-reminder>...</system-reminder>
系统视角：这条消息有特殊标记，摘要压缩时要保护位置
```

面试回答：

> 隐藏消息的意思是 UI 层不展示、用户历史里不当作真实输入，但模型调用时可见。这样既能给模型补充记忆和日期，又不会让用户对话记录里出现一堆系统提示。

## 注入规则

可以总结成四条：

1. **首轮注入完整上下文**：包括长期记忆、当前日期、关键提醒。
2. **同一天避免重复注入日期**：防止每轮都塞相同内容浪费 token。
3. **跨天更新日期提醒**：日期变化会影响“今天/昨天/明天”等相对时间。
4. **摘要消息不算真实用户消息**：摘要只是历史压缩结果，不代表用户新输入。

## 学习版：动态上下文到底是什么

可以把动态上下文理解成 Agent 的“运行时环境变量”。

传统后端函数执行时会读取：

```text
env / config / session / request headers / feature flags / current time
```

Agent 调模型时也一样，不能只看用户最新一句话，还要知道：

- 当前日期、时区。
- 当前用户、线程、run。
- 用户长期偏好和明确纠偏。
- 当前 workspace、sandbox、tool promoted 状态。
- 本轮系统提醒和安全边界。

所以动态上下文不是“多塞 prompt”，而是把当前运行时有效状态转成模型可见上下文。

## 成熟系统怎么分上下文

| 上下文类型 | 例子 | 放哪里 |
| --- | --- | --- |
| 静态身份 | Agent 名称、职责、基础规则 | System Prompt |
| 运行时状态 | 日期、thread_id、sandbox_id | Dynamic Context |
| 长期记忆 | 语言偏好、项目背景、纠偏事实 | 选择后注入 |
| 当前任务状态 | todo、当前文件、已提升工具 | Runtime State |
| 安全提醒 | 临时限制、系统提醒 | 靠近模型调用注入 |

面试回答：

> System Prompt 适合稳定规则，动态上下文适合每轮可能变化的状态。比如当前日期、长期记忆、系统提醒和工具状态，这些都应该在 before_model 阶段按规则注入。

## 当前项目落地模型

推荐把动态上下文拆成四步：

```text
collect -> select -> render -> inject
```

含义：

- `collect`：收集日期、长期记忆、thread_data、sandbox、promoted tools。
- `select`：只选择本轮相关、高置信、未重复的信息。
- `render`：渲染成 `<system-reminder>`。
- `inject`：以隐藏 HumanMessage 放到正确位置。

简化代码：

```python
def before_model(state, runtime):
    if already_injected_for_current_turn(state["messages"]):
        return None

    context = collect_dynamic_context(runtime)
    selected = select_relevant_context(context, state["messages"])
    if not selected:
        return None

    reminder = HumanMessage(
        content=render_system_reminder(selected),
        additional_kwargs={"hidden": True, "dynamic_context": True},
    )
    return {"messages": [reminder]}
```

这段代码面试重点：

- `before_model` 保证模型调用前看到最新上下文。
- `hidden=True` 降低对用户真实对话语义的干扰。
- `already_injected_for_current_turn` 防止重复注入。

## 注入内容优先级

| 优先级 | 内容 | 原因 |
| --- | --- | --- |
| P0 | 安全提醒、当前系统约束 | 错了可能越权 |
| P1 | 用户明确偏好和纠偏事实 | 影响正确性和个性化 |
| P1 | 当前日期、时区 | 影响相对时间 |
| P2 | 项目背景、技术栈 | 提升贴合度 |
| P3 | 弱推断兴趣 | 有帮助但不能强影响 |

## 常见错误

| 错误做法 | 问题 |
| --- | --- |
| 每轮注入完整长期记忆 | token 浪费，容易引入无关偏见 |
| 把动态提醒写进摘要 | 当前状态被误当成历史事实 |
| 用 summary 判断已注入 | summary 不是本轮真实用户消息 |
| 让用户可见隐藏提醒 | 干扰用户体验 |
| 不做去重 | 多条重复 reminder 让模型过度关注 |

## 评估和观测

指标：

| 指标 | 含义 |
| --- | --- |
| `context_injection_rate` | 应注入时实际注入比例 |
| `duplicate_injection_rate` | 重复注入比例 |
| `memory_relevance_score` | 注入记忆与当前任务相关性 |
| `preference_follow_rate` | 用户偏好遵守率 |
| `correction_repeat_rate` | 用户纠偏后重复犯错率 |
| `token_overhead` | 动态上下文 token 开销 |

事件：

```text
dynamic_context.injected
dynamic_context.skipped
dynamic_context.duplicate_prevented
dynamic_context.memory_selected
dynamic_context.reminder_preserved
```

排障口诀：

> 用户说“你怎么又忘了”，先看动态上下文是否注入，再看记忆是否被选中，最后看摘要是否把当前提醒压错位置。

## 为什么摘要消息不代表已经注入过

摘要消息说明历史被压缩过，不说明当前模型调用已经有动态提醒。

举例：

```text
summary: 上一段对话摘要
user: 帮我继续
```

这时仍然需要判断当前日期、当前记忆是否要注入。摘要只能代表旧内容压缩结果，不能替代运行时提醒。

## 难点：动态提醒位置保护

如果摘要压缩把 `<system-reminder>` 压到摘要里，会出现两个问题：

1. 模型看不出这是当前有效提醒，可能当成历史内容。
2. 新旧动态提醒混在一起，可能造成日期或记忆冲突。

所以摘要压缩时需要有 `_preserve_dynamic_context_reminders()` 之类的保护逻辑：

```text
messages_to_summarize
preserved_messages
  -> 如果发现 system-reminder 在错误分区
  -> 移到应该保留的位置
  -> 避免被摘要吞掉
```

面试回答：

> 动态提醒的位置很关键。它应该作为当前轮的运行时上下文出现，而不是被压缩进历史摘要。否则模型会把当前提醒理解成过去发生过的对话内容，尤其日期和记忆会产生语义错位。

## 可讲的 trade-off

方案一：全部写到 system prompt。

- 优点：简单。
- 缺点：不支持按用户/线程动态变化，容易污染系统指令。

方案二：每轮都注入普通用户消息。

- 优点：模型一定能看到。
- 缺点：污染对话历史，用户可见体验差。

方案三：模型可见、UI 隐藏的 reminder 消息。

- 优点：动态、可控、低污染。
- 缺点：需要处理重复注入和摘要保护。

推荐回答：

> 我选择第三种，因为 Agent 平台最重要的是长期可维护。动态上下文需要模型可见，但不应该成为真实用户消息；所以我用隐藏 reminder 消息承载，并额外做去重和摘要保护。

## 高频追问

### 1. 为什么不每轮都注入完整长期记忆？

因为 token 成本高，而且重复记忆会强化无关信息。更合理的是按线程、日期、预算和重要性控制注入。

### 2. 如果记忆错了怎么办？

长期记忆系统需要支持纠错事实，用户明确纠正时写入 `correction` 类事实，并删除冲突事实。动态注入只负责读取当前记忆，不负责判断事实真伪。

### 3. 动态提醒和 system prompt 优先级冲突怎么办？

system prompt 优先级更高。动态提醒只提供上下文，不改变系统安全规则。

### 4. 如何防止用户伪造 `<system-reminder>`？

可以通过消息来源标记和服务端构造区分真实动态提醒与用户输入。不要只靠字符串标签做安全边界。

## 深挖补充：一次动态上下文注入怎么落地

面试官如果追问实现细节，不要停留在“我拼了一段 prompt”。可以按下面这条链路讲：

```text
模型调用前
  -> 读取 run / thread / user / agent 标识
  -> 从 ThreadState 取当前消息
  -> 判断本轮是否已经注入过动态上下文
  -> 根据 user_id + agent_id 拉取长期记忆
  -> 根据系统时间生成当前日期提醒
  -> 合成隐藏 reminder message
  -> 插入到模型可见消息列表
  -> 记录 injected 标记和 token 估算
```

这条链路里有三个关键点：

1. **注入发生在模型调用前**，不是写入原始聊天历史。
2. **注入内容可追踪**，知道是哪类上下文、什么时候注入、消耗多少 token。
3. **注入要幂等**，同一轮不能因为重试、stream reconnect 或 middleware 重入重复插入。

面试讲法：

> 我把动态上下文当成运行时视图，而不是用户消息的一部分。它在模型调用前临时装配，服务端可控、可追踪、可去重，既能让模型看到必要背景，又不会污染真实对话历史。

## 深挖补充：为什么不能直接把所有东西都塞进 system prompt

system prompt 适合放稳定规则，比如角色、工具使用规范、安全要求。动态上下文适合放运行时信息，比如用户偏好、当前日期、线程摘要、临时环境提示。

| 问题 | 后果 |
| --- | --- |
| 来源混乱 | 不知道哪段是产品规则，哪段是用户记忆 |
| 更新困难 | 用户偏好变化后很难局部替换 |
| 优先级不清 | 临时信息可能覆盖长期安全规则 |
| 观测困难 | 难统计每类上下文 token 成本和命中率 |
| 摘要保护困难 | 摘要中间件很难判断哪些内容必须保留 |

推荐回答：

> system prompt 是稳定策略层，动态上下文是运行时数据层。两者拆开后，规则可以版本化，记忆可以按用户更新，摘要中间件也能识别哪些 reminder 必须保护。

## 深挖补充：边界条件

### 1. 用户没有长期记忆

不要生成一段空的 memory block。可以只注入日期和必要系统提醒，避免模型误以为“没有记忆”也是一条重要事实。

### 2. 记忆太长

记忆需要二次裁剪，优先保留和当前任务相关、用户明确表达、最近更新的事实。过长记忆不能直接注入，否则动态上下文会反过来挤掉真实任务信息。

### 3. 重试导致重复注入

同一轮 run 重试时，要检查是否已经存在服务端生成的 reminder。可以用 metadata 标记，而不是靠文本匹配。

### 4. 用户伪造系统标签

用户消息里的 `<system-reminder>` 只能作为普通文本，不能被 middleware 识别为可信上下文。可信上下文必须来自服务端，最好带不可见 metadata。

### 5. 摘要后再次注入

摘要消息不等于动态上下文已经注入。摘要只是旧历史的压缩表示，当前日期、最新记忆和运行时提醒仍然要在模型调用前重新判断。

## 深挖补充：观测指标怎么设计

动态上下文要能被评估，否则很难证明它有效。

| 指标 | 含义 |
| --- | --- |
| `context_injected` | 本轮是否注入 |
| `memory_facts_count` | 注入了多少条长期事实 |
| `dynamic_context_tokens` | 动态上下文消耗 token |
| `memory_hit_rate` | 回答是否使用了相关记忆 |
| `stale_memory_detected` | 是否命中过期或冲突记忆 |
| `injection_skipped_reason` | 未注入原因，如无记忆、超预算、已注入 |

排查问题时可以问：

```text
回答不个性化
  -> memory 是否存在
  -> dynamic context 是否注入
  -> 注入 token 是否被摘要/裁剪
  -> 模型是否实际使用了记忆
  -> 记忆是否过期或冲突
```

## 深挖补充：面试官可能追着问的实现题

### Q：如果动态上下文和用户最新输入冲突，听谁的？

优先听用户最新明确表达。长期记忆只是历史事实，用户当前输入可能是在纠偏。可以把冲突信息传给 Memory updater，让它异步更新或删除旧事实。

### Q：动态上下文会不会引入 prompt injection？

会，所以动态上下文本身也要分来源。服务端生成的提醒可信度高，用户上传内容或外部网页摘要可信度低。不同来源应该有不同标签和处理策略，不能把外部内容伪装成系统指令。

### Q：怎么控制动态上下文 token？

按优先级裁剪：安全提醒和当前日期优先，当前任务相关记忆其次，泛化偏好最后。超过预算时宁愿少注入，也不要挤掉最近用户目标和工具结果。

### Q：如果用户要求“忘掉之前所有偏好”怎么办？

当前轮要立即把这句话作为高优先级输入处理，同时异步触发 Memory correction 或 deletion。不能等后台记忆更新完成后才在当前回答里生效。
