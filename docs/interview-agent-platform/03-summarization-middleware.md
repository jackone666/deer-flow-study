# 03 对话摘要压缩中间件

对应简历表述：

> 实现对话摘要压缩中间件，基于 token 阈值触发历史消息压缩，支持摘要生成、消息裁剪、动态提醒保护、关键上下文保留和压缩后消息重建。

## 面试官想听什么

这个模块很适合考工程细节。面试官可能会追问：

1. 为什么需要摘要压缩？
2. 什么时候触发？
3. 压缩哪些消息，保留哪些消息？
4. 为什么 tool message 不一定保留？
5. 动态提醒为什么要保护？
6. 压缩后怎么重建 LangGraph 消息状态？

## 设计目标

长对话 Agent 会遇到三个问题：

1. **上下文窗口有限**：历史消息越来越多，模型无法完整接收。
2. **成本上升**：每轮都带完整历史，token 成本线性上涨。
3. **噪声变大**：旧工具输出和探索过程会干扰当前任务。

摘要压缩的目标：

```text
保留近期关键消息
压缩较旧历史
保护动态上下文提醒
避免破坏 tool call 对齐关系
重建一个更短但语义连续的消息列表
```

## 核心流程

关键代码：

- `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`

典型流程：

```text
before_model()
  -> _maybe_summarize(state, runtime)
     -> _ensure_message_ids(messages)
     -> token_counter(messages)
     -> _should_summarize(messages, total_tokens)
     -> _determine_cutoff_index(messages)
     -> _partition_with_skill_rescue(messages, cutoff_index)
     -> _preserve_dynamic_context_reminders(messages_to_summarize, preserved_messages)
     -> _fire_hooks(...)
     -> _create_summary(messages_to_summarize)
     -> _build_new_messages(summary)
     -> RemoveMessage(REMOVE_ALL_MESSAGES) + summary + preserved_messages
```

## 学习版：摘要压缩是什么

摘要压缩是 Agent 的“上下文垃圾回收”。

长对话里会累积：

```text
用户消息
AI 回答
工具调用
工具输出
子 Agent 结果
动态提醒
历史摘要
```

如果不压缩：

- 容易超出模型上下文窗口。
- token 成本越来越高。
- 旧工具输出干扰当前目标。
- 模型注意力被无关历史稀释。

但摘要不能随便压。它要保留：

- 用户目标和约束。
- 已做决策。
- 文件、路径、产物。
- 错误和修复方式。
- 当前待办。
- 不能被压错位置的动态提醒。

## 成熟系统怎么处理长上下文

常见三种策略：

| 策略 | 作用 | 风险 |
| --- | --- | --- |
| Sliding Window | 保留最近 N 条消息 | 早期关键决策丢失 |
| Summary Memory | 历史压成摘要 | 摘要可能失真 |
| Retrieval Memory | 外部召回相关片段 | 召回可能漏掉 |

当前项目更像：

```text
Sliding Window + Summary Memory + Protected Messages
```

也就是：

```text
早期历史 -> summary
近期消息 -> 原样保留
动态提醒 -> 特殊保护
工具调用关系 -> 避免拆坏
```

## 摘要中间件六个模块

| 模块 | 职责 |
| --- | --- |
| TokenCounter | 估算当前 messages token |
| ShouldSummarize | 判断是否超过阈值 |
| CutoffPolicy | 决定压缩到哪里 |
| PartitionPolicy | 拆成待摘要和保留消息 |
| SummaryGenerator | 调模型生成摘要 |
| MessageRebuilder | `RemoveMessage + summary + preserved` |

简化代码：

```python
def before_model(state, runtime):
    messages = state["messages"]
    total_tokens = token_counter(messages)
    if not should_summarize(messages, total_tokens):
        return None

    cutoff = determine_cutoff_index(messages)
    to_summarize, preserved = partition_messages(messages, cutoff)
    to_summarize, preserved = preserve_dynamic_reminders(to_summarize, preserved)

    summary = create_summary(to_summarize)
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *build_new_messages(summary),
            *preserved,
        ]
    }
```

面试重点：

> 它不是直接删除旧消息，而是先全量移除，再用 summary 和 preserved messages 重建线程消息，确保状态里只有压缩后的干净上下文。

## TokenCounter 怎么实现

常见三档：

| 实现 | 说明 |
| --- | --- |
| 模型 tokenizer | 最准确 |
| provider usage | 事后统计准确 |
| 字符估算 | 快，适合触发判断 |

推荐：

```text
优先模型 tokenizer
  -> 没有则用通用 tokenizer
  -> 再没有则字符估算
  -> 预留安全 buffer
```

面试回答：

> TokenCounter 的作用是触发压缩，不一定百分百精确。工程上可以优先用模型 tokenizer，没有就用字符估算，并保留 buffer，避免刚好卡在上下文窗口边缘。

## 摘要质量 rubric

| 维度 | 好摘要应该保留 |
| --- | --- |
| Goal | 用户目标 |
| Constraints | 限制条件和偏好 |
| Decisions | 已做决定 |
| Artifacts | 文件、路径、产物 |
| Errors | 错误和修复方式 |
| Todos | 未完成事项 |
| Runtime | 不把当前动态提醒当历史事实 |

## 评估和观测

指标：

| 指标 | 含义 |
| --- | --- |
| `summary_trigger_rate` | 摘要触发比例 |
| `compression_ratio` | 压缩比例 |
| `post_summary_success_rate` | 摘要后任务成功率 |
| `context_loss_rate` | 摘要后丢关键上下文比例 |
| `reminder_preserve_rate` | 动态提醒保护成功率 |
| `summary_hallucination_rate` | 摘要产生不存在事实比例 |

事件：

```text
summary.check
summary.skipped
summary.started
summary.created
summary.failed
summary.reminder_preserved
summary.rebuild_messages
```

排障：

```text
摘要后回答跑偏
  -> 看 summary 是否缺目标/约束
  -> 看 preserved_messages 是否太少
  -> 看 cutoff 是否切断 tool pair
  -> 看 dynamic reminder 是否被压错位置
```

## 触发条件

摘要不应该每轮都做，而是基于阈值触发。

常见条件：

- 总 token 超过配置阈值。
- 消息数量超过最小压缩条件。
- 能找到合理 cutoff。

面试回答：

> 我不是固定轮数压缩，而是先用 token_counter 估算当前消息体量，超过阈值才触发。这样可以避免短对话产生不必要摘要，也能在工具输出特别长时及时收缩上下文。

## cutoff 怎么理解

`cutoff_index` 是压缩边界：

```text
[旧消息 ... cutoff_index 前]      -> messages_to_summarize
[cutoff_index 后 ... 近期消息]   -> preserved_messages
```

原则：

1. 旧消息更适合摘要。
2. 近期消息更可能被下一轮直接引用。
3. 不要把一组 tool call / tool result 切坏。
4. 不要把当前动态提醒压进摘要。

## 为什么 tool message 不全部保留

工具消息通常包括：

- 大段日志。
- 文件内容。
- 搜索结果。
- 命令输出。
- 中间失败重试信息。

这些内容对当前任务未必都有长期价值。全部保留会导致 token 爆炸。

面试回答：

> tool message 不是不重要，而是不能无条件保留。工具输出往往很长，而且很多只是中间探索过程。摘要时我更关注“工具调用产生的结论”和“当前仍需要引用的结果”，旧 tool message 可以压缩进摘要；近期 tool call 链路或关键技能上下文才保留。

## tool call 对齐风险

在 LangGraph / LLM 消息协议里，AI 发出 tool call 后，通常需要对应 ToolMessage。

如果压缩时切成：

```text
AIMessage(tool_calls=[...])  保留了
ToolMessage(...)             被摘要了
```

可能导致消息序列非法或模型困惑。

所以切分时要注意边界，或者把旧 tool call 链路整体摘要掉。

## 动态提醒保护

动态提醒类似：

```xml
<system-reminder>
当前日期...
长期记忆...
</system-reminder>
```

它应该出现在当前上下文，而不是历史摘要里。

如果被摘要掉，会出现两个问题：

1. 日期、记忆被模型当成过去信息。
2. 新旧 reminder 混杂，当前有效性不清楚。

面试回答：

> 动态提醒保护的核心是避免“当前运行时上下文”被压缩成“历史内容”。尤其日期和长期记忆有当前性，如果进入摘要，模型会失去它的指令边界，甚至和新注入的 reminder 冲突。

## summary 消息怎么重建

压缩后不是简单 append summary，而是替换整个消息列表：

```text
RemoveMessage(id=REMOVE_ALL_MESSAGES)
summary message
preserved_messages
```

这样做的原因：

1. 清空旧消息，避免重复上下文。
2. summary 作为新的历史开头。
3. preserved_messages 保持近期对话连续。

面试回答：

> 我用 RemoveMessage 清空原消息，再放入摘要和保留消息。这样不会出现“旧消息还在 + 摘要又重复描述一遍”的上下文重复问题，也能让图状态保持一个干净的新历史。

## 摘要内容应该包含什么

好的摘要要包含：

- 用户目标。
- 已做过的关键决策。
- 当前任务进度。
- 重要文件、模块、函数名。
- 已知错误和解决方案。
- 用户明确偏好。
- 待办和下一步。

不应该包含：

- 大段工具原始输出。
- 无关寒暄。
- 已失败且不再相关的探索细节。
- 临时上传事件的不可用路径。

## 与长期记忆的区别

摘要压缩和长期记忆很容易混。

```text
摘要压缩：服务当前线程，让长对话继续。
长期记忆：服务未来会话，让个性化延续。
```

面试回答：

> 摘要不是长期记忆。摘要是线程内部的上下文压缩，目标是让当前对话继续；长期记忆是跨会话用户画像，目标是未来个性化。两者生命周期和写入标准不同。

## 可讲的 trade-off

### 保留完整历史 vs 摘要压缩

完整历史：

- 优点：信息不丢。
- 缺点：token 成本高、噪声大、可能超窗口。

摘要压缩：

- 优点：成本低、上下文更聚焦。
- 缺点：细节可能丢失，摘要质量依赖模型。

我的选择：

> 用 token 阈值触发摘要，同时保留近期消息和动态提醒，减少细节丢失风险。

### 摘要越短越好？

不是。太短会丢上下文，太长失去压缩意义。

更合理的是：

> 摘要长度和任务复杂度、剩余上下文预算相关。复杂代码任务摘要要保留文件名、函数名、错误信息；普通问答可以更短。

## 高频追问

### 1. 怎么评估摘要效果？

可以看：

- 压缩后模型是否还能正确继续任务。
- 是否遗漏关键决策。
- 是否出现重复提问。
- token 使用是否下降。
- 摘要前后任务成功率是否下降。

### 2. 摘要错了怎么办？

保留近期原始消息降低风险；对关键事实可以进入长期记忆或结构化状态；必要时允许用户纠错并写入 correction。

### 3. 为什么摘要消息不算真实用户消息？

摘要是系统生成的历史压缩结果，不是用户新输入。动态上下文注入判断真实用户消息时不能把 summary 当成用户消息，否则会误判注入时机。

### 4. 为什么不把所有历史都交给向量库？

向量库适合检索片段，不适合替代顺序对话上下文。摘要保留任务进程和因果链，向量检索保留局部事实，两者可以互补。

## 深挖补充：摘要压缩的完整执行链路

面试时可以按这条链路讲：

```text
模型调用前
  -> 统计 messages token
  -> 判断是否超过 threshold
  -> 选择 cutoff
  -> 标记必须保留的消息
  -> 对可压缩区间生成 summary
  -> 校验 summary 是否包含关键字段
  -> 用 summary message 替换旧消息
  -> 保留 recent messages 和动态 reminder
  -> 继续模型调用
```

这里最重要的是：摘要中间件不是为了“让文本变短”，而是为了在有限窗口里保留任务连续性。

高分表达：

> 我把摘要当成状态压缩，而不是聊天总结。它必须保留目标、约束、决策、失败尝试、工具结果、产物路径和未完成事项。

## 深挖补充：哪些消息必须保留

| 消息类型 | 处理 |
| --- | --- |
| 最近用户目标 | 必须保留原文 |
| 最近模型计划 | 尽量保留 |
| 动态上下文 reminder | 必须保留或重新注入 |
| 未闭合 tool call | 必须保留，避免 tool_call_id 对不上 |
| 关键工具结果 | 原文或结构化摘要 |
| 旧寒暄和重复确认 | 可以压缩 |
| 大段日志输出 | 截断后摘要 |
| 临时错误堆栈 | 保留错误类型和关键行 |

如果面试官追问“为什么 tool message 不能随便删”，可以答：

> 很多框架要求 assistant 的 tool call 和 tool response 成对出现。删错会破坏消息协议，不只是丢信息，甚至会导致模型调用报错。

## 深挖补充：摘要质量怎么评估

摘要质量不能只看压缩率。推荐按 P0/P1/P2 看。

| 等级 | 检查项 |
| --- | --- |
| P0 | 不得丢用户最终目标、硬约束、安全提醒、未完成任务 |
| P1 | 保留关键决策、工具结果、失败尝试、产物路径 |
| P2 | 表达清晰、短、结构化、无无关细节 |

可用回归任务验证：

```text
同一条长对话
  -> 不摘要跑一遍作为 baseline
  -> 触发摘要后再跑
  -> 比较任务成功率、工具选择、最终答案一致性、token 成本
```

## 深挖补充：摘要错误怎么恢复

摘要一旦错了，后续模型可能沿着错误状态继续推理。所以要有恢复策略：

1. **保留原始消息窗口**：至少最近 N 轮不压缩。
2. **摘要可重建**：旧消息最好仍存储在持久层，只是在模型窗口里替换。
3. **用户纠错优先**：用户说“不是这样”时，把纠错放入当前上下文并触发新摘要。
4. **摘要版本化**：记录 summary 来源区间和生成时间，方便定位哪次摘要引入错误。
5. **失败回退**：如果摘要模型失败，宁愿跳过摘要或裁剪低价值消息，不要生成半截摘要。

面试回答：

> 摘要不是不可逆删除，而是模型窗口里的压缩视图。原始消息仍然在存储层，摘要要记录覆盖区间，出错时能定位和重建。

## 深挖补充：常见事故

### 事故 1：摘要丢了用户硬约束

表现：用户明确说“不要改 API”，摘要后模型改了 API。

原因：摘要 rubric 没把 constraints 标成必须保留。

修复：摘要模板里单独列 `Hard constraints`，并在 eval 里加入约束遵守检查。

### 事故 2：摘要丢了文件路径

表现：后续模型找不到之前生成的报告或代码文件。

原因：产物路径被当成普通细节压缩掉。

修复：artifact path、commit hash、issue id、配置项这类精确引用必须结构化保留。

### 事故 3：摘要破坏 tool call 对齐

表现：模型 API 报消息格式错误。

原因：删除了 assistant tool call 或对应 tool response。

修复：压缩前做消息协议校验，未闭合 tool call 不进入压缩区间。

## 深挖补充：面试攻防

### Q：为什么不用更大上下文模型？

更大窗口能缓解问题，但不能消除问题。长上下文成本高、延迟高、注意力会稀释，而且工具日志和重复历史仍然会污染决策。摘要是成本、质量和稳定性的工程治理。

### Q：摘要和 Memory 会不会重复？

会有少量重叠，但作用不同。摘要服务当前线程连续性，Memory 服务跨线程长期个性化。摘要可以包含临时文件路径，Memory 不应该记这些。

### Q：摘要触发阈值怎么定？

不是固定越高越好。要给模型输出、工具 schema 和动态上下文预留预算。比如窗口 128k，不应该等到 127k 才压缩，因为下一步工具结果可能直接超限。

### Q：摘要模型本身会不会幻觉？

会，所以摘要 prompt 要结构化，要求只基于原文总结；关键字段尽量用规则提取；最终用 eval 检查摘要后任务是否还能完成。
