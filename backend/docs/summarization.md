# 对话总结

DeerFlow 包括自动对话摘要，以处理接近模型令牌限制的长对话。启用后，系统会自动压缩较旧的消息，同时保留最近的上下文。

## 概述

摘要功能使用 LangChain 的 `SummarizationMiddleware` 来监控对话历史记录并根据可配置的阈值触发摘要。激活后，它：

1. 实时监控消息令牌计数
2. 满足阈值时触发摘要
3. 保持最近的消息完整，同时总结旧的交换
4. 一起维护 AI/Tool 消息对以实现上下文连续性
5. 将摘要注入对话中

## 配置

摘要在 `summarization`键下的`config.yaml` 中配置：

```yaml
summarization:
  enabled: true
  model_name: null  # Use default model or specify a lightweight model

  # Trigger conditions (OR logic - any condition triggers summarization)
  trigger:
    - type: tokens
      value: 4000
    # Additional triggers (optional)
    # - type: messages
    #   value: 50
    # - type: fraction
    #   value: 0.8  # 80% of model's max input tokens

  # Context retention policy
  keep:
    type: messages
    value: 20

  # Token trimming for summarization call
  trim_tokens_to_summarize: 4000

  # Custom summary prompt (optional)
  summary_prompt: null

  # Tool names treated as skill file reads for skill rescue
  skill_file_read_tool_names:
    - read_file
    - read
    - view
    - cat
```

### 配置选项

#### `enabled`
- **类型**：布尔值
- **默认**：`false`
- **说明**：启用或禁用自动摘要

#### `model_name`
- **类型**：字符串或 null
- **默认**：`null`（使用默认模型）
- **描述**：用于生成摘要的模型。建议使用轻量级、经济高效的模型，如 `gpt-4o-mini` 或同等模型。

#### `trigger`
- **类型**：单个 `ContextSize`或`ContextSize` 对象列表
- **必需**：启用时必须至少指定一个触发器
- **描述**：触发摘要的阈值。使用 OR 逻辑 - 当满足 ANY 阈值时运行摘要。

**ContextSize 类型：**

1. **基于令牌的触发器**：当令牌计数达到指定值时激活
   ```yaml
   trigger:
     type: tokens
     value: 4000
   ```

2. **基于消息的触发器**：当消息计数达到指定值时激活
   ```yaml
   trigger:
     type: messages
     value: 50
   ```

3. **基于分数的触发器**：当令牌使用量达到模型最大输入令牌的百分比时激活
   ```yaml
   trigger:
     type: fraction
     value: 0.8  # 80% of max input tokens
   ```

**多个触发器：**
```yaml
trigger:
  - type: tokens
    value: 4000
  - type: messages
    value: 50
```

#### `keep`
- **类型**：`ContextSize` 对象
- **默认**：`{type: messages, value: 20}`
- **描述**：指定摘要后要保留多少最近的对话历史记录。

**示例：**
```yaml
# Keep most recent 20 messages
keep:
  type: messages
  value: 20

# Keep most recent 3000 tokens
keep:
  type: tokens
  value: 3000

# Keep most recent 30% of model's max input tokens
keep:
  type: fraction
  value: 0.3
```

#### `trim_tokens_to_summarize`
- **类型**：整数或空
- **默认**：`4000`
- **描述**：为摘要调用本身准备消息时要包含的最大标记。设置为 `null` 以跳过修剪（不建议用于很长的对话）。

#### `summary_prompt`
- **类型**：字符串或 null
- **默认**：`null`（使用LangChain的默认提示）
- **描述**：用于生成摘要的自定义提示模板。提示应引导模型提取最重要的上下文。

#### `preserve_recent_skill_count`
- **类型**：整数（≥ 0）
- **默认**：`5`
- **描述**：从摘要中保留下来的最近加载技能文件数量（工具名称在 `skill_file_read_tool_names` 中且目标路径位于 `skills.container_path` 下的工具结果，例如 `/mnt/skills/...`）。这可以防止 agent 在压缩后丢失技能指令。设置为 `0` 可完全禁用技能保留。

#### `preserve_recent_skill_tokens`
- **类型**：整数（≥ 0）
- **默认**：`25000`
- **描述**：为救援技能读取保留的总token预算。一旦这个预算用完，旧的技能包就可以被总结。

#### `preserve_recent_skill_tokens_per_skill`
- **类型**：整数（≥ 0）
- **默认**：`5000`
- **描述**：每项技能token上限。工具结果超过此大小的任何个人技能读取都不会被保存（它会像普通内容一样落入摘要器）。

#### `skill_file_read_tool_names`
- **类型**：字符串列表
- **默认**：`["read_file", "read", "view", "cat"]`
- **描述**：在摘要救援期间被视为技能文件读取的工具名称。仅当工具调用的名称出现在此列表中并且其目标路径位于 `skills.container_path` 下时，工具调用才有资格进行技能救援。

**默认提示行为：**
默认的 LangChain 提示指示模型：
- 提取最高 quality/most 相关上下文
- 关注对总体目标至关重要的信息
- 避免重复已完成的操作
- 仅返回提取的上下文

## 它是如何工作的

### 总结流程

1. **监控**：在每次模型调用之前，中间件都会对消息历史记录中的令牌进行计数
2. **触发检查**：如果满足任何配置的阈值，则触发摘要
3. **消息分区**：消息分为：
   - 要摘要的消息（超出 `keep` 阈值的较旧消息）
   - 要保留的消息（`keep` 阈值内的最新消息）
4. **摘要生成**：模型生成旧消息的简明摘要
5. **上下文替换**：消息历史记录已更新：
   - 所有旧消息均已删除
   - 添加了一条摘要消息
   - 保留最近的消息
6. **AI/Tool 配对保护**：系统确保 AI 消息与其对应的工具消息保持在一起
7. **技能救援**：在生成摘要之前，最近加载的技能文件（工具名称在 `skill_file_read_tool_names`中且目标路径在`skills.container_path` 下的工具结果）将从摘要集中提取出来并添加到保留尾部。选择按照三个预算从最新到先进行：`preserve_recent_skill_count`、`preserve_recent_skill_tokens`和`preserve_recent_skill_tokens_per_skill`。触发的 AIMessage 及其所有配对的 ToolMessages 一起移动，因此 tool_call ↔ tool_result 配对保持完整。

### 令牌计数

- 使用基于字符计数的近似标记计数
- 对于人类模型：每个标记约 3.3 个字符
- 对于其他模型：使用 LangChain 的默认估计
- 可以使用自定义 `token_counter` 函数进行自定义

### 消息保存

中间件智能地保留消息上下文：

- **最近消息**：基于 `keep` 配置始终保持完整
- **AI/Tool 对**：永不拆分 - 如果截止点落在工具消息内，系统会进行调整以将整个 AI + 工具消息序列保持在一起
- **摘要格式**：摘要作为 HumanMessage 注入，格式如下：
  ```
  Here is a summary of the conversation to date:

  [Generated summary text]
  ```

## 最佳实践

### 选择触发阈值

1. **基于令牌的触发器**：推荐用于大多数用例
   - 设置为模型上下文窗口的 60-80%
   - 示例：对于 8K 上下文，使用 4000-6000 个令牌

2. **基于消息的触发器**：用于控制对话长度
   - 适合有很多短信的应用
   - 示例：50-100 条消息，具体取决于平均消息长度

3. **基于分数的触发器**：使用多个模型时的理想选择
   - 自动适应每个型号的容量
   - 示例：0.8（模型最大输入标记的 80%）

### 选择保留策略 (`keep`)

1. **基于消息的保留**：最适合大多数场景
   - 保留自然的对话流程
   - 推荐：15-25 条消息

2. **基于令牌的保留**：在需要精确控制时使用
   - 适合管理精确的token预算
   - 推荐：2000-4000 token

3. **基于分数的保留**：适用于多模型设置
   - 根据模型容量自动扩展
   - 建议：0.2-0.4（最大输入的 20-40%）

### 型号选择

- **推荐**：使用轻量级、经济高效的模型进行摘要
  - 示例：`gpt-4o-mini`、`claude-haiku` 或等效项
  - 摘要不需要最强大的模型
  - 大批量应用可显着节省成本

- **默认**：如果`model_name`是`null`，则使用默认模型
  - 可能更贵但保证一致性
  - 适合简单设置

### 优化技巧

1. **平衡触发器**：结合令牌和消息触发器以实现稳健的处理
   ```yaml
   trigger:
     - type: tokens
       value: 4000
     - type: messages
       value: 50
   ```

2. **保守保留**：最初保留更多消息，根据性能进行调整
   ```yaml
   keep:
     type: messages
     value: 25  # Start higher, reduce if needed
   ```

3. **策略性修剪**：限制发送到摘要模型的令牌
   ```yaml
   trim_tokens_to_summarize: 4000  # Prevents expensive summarization calls
   ```

4. **监控和迭代**：跟踪摘要质量并调整配置

## 故障排除

### 质量问题总结

**问题**：摘要丢失重要上下文

**解决方案**：
1. 增加 `keep` 值以保留更多消息
2. 降低触发阈值以更早进行总结
3. 自定义`summary_prompt`以强调关键信息
4. 使用更强大的模型进行总结

### 性能问题

**问题**：摘要调用花费的时间太长

**解决方案**：
1. 使用更快的模型进行摘要（例如，`gpt-4o-mini`）
2. 减少 `trim_tokens_to_summarize` 以发送更少的上下文
3. 增加触发阈值以降低摘要频率

### token限制错误

**问题**：尽管进行了摘要，但仍然达到了token限制

**解决方案**：
1. 降低触发阈值以便更早总结
2. 减少 `keep` 值以保留更少的消息
3. 检查单个消息是否很大
4. 考虑使用基于分数的触发器

## 实施细节

### 代码结构

- **配置**：`packages/harness/deerflow/config/summarization_config.py`
- **集成**：`packages/harness/deerflow/agents/lead_agent/agent.py`
- **中间件**：使用 `langchain.agents.middleware.SummarizationMiddleware`

### 中间件订单

摘要在 ThreadData 和沙箱初始化之后但在标题和说明之前运行：

1. ThreadDataMiddleware
2. SandboxMiddleware
3. **SummarizationMiddleware** ← 在此运行
4. TitleMiddleware
5. ClarificationMiddleware

### 状态管理

- 摘要是无状态的 - 配置在启动时加载一次
- 摘要作为常规消息添加到对话历史记录中
- 检查点自动保存摘要历史记录

## 配置示例

### 最低配置
```yaml
summarization:
  enabled: true
  trigger:
    type: tokens
    value: 4000
  keep:
    type: messages
    value: 20
```

### 生产配置
```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini  # Lightweight model for cost efficiency
  trigger:
    - type: tokens
      value: 6000
    - type: messages
      value: 75
  keep:
    type: messages
    value: 25
  trim_tokens_to_summarize: 5000
```

### 多型号配置
```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini
  trigger:
    type: fraction
    value: 0.7  # 70% of model's max input
  keep:
    type: fraction
    value: 0.3  # Keep 30% of max input
  trim_tokens_to_summarize: 4000
```

### 保守配置（高品质）
```yaml
summarization:
  enabled: true
  model_name: gpt-4  # Use full model for high-quality summaries
  trigger:
    type: tokens
    value: 8000
  keep:
    type: messages
    value: 40  # Keep more context
  trim_tokens_to_summarize: null  # No trimming
```

## 参考文献

- [LangChain Summarization Middleware Documentation](https://docs.langchain.com/oss/python/langchain/middleware/built-in#summarization)
- [LangChain Source Code](https://github.com/langchain-ai/langchain)
