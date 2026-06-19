# 19 Prompt 工程 — DeerFlow 实战 + CoT/ReAct/Reflexion 学术映射

> 面试口径：Prompt 工程在 DeerFlow 里**不是单文件，是分层注入**：① **静态主提示** (`SYSTEM_PROMPT_TEMPLATE`，1000+ 行) ② **动态注入** (`DynamicContextMiddleware` 把日期/记忆塞进 first HumanMessage) ③ **子模块提示**（subagent_section / skills_section / deferred_tools_section 按需拼接）④ **子 Agent 自己的 system_prompt** + **SKILL.md 注入**。**这不是"写个 prompt 就完了" —— 是工程化的 prompt 组装系统**。这一章拆这套体系，并讲清楚 CoT / ReAct / Reflexion 等学术思想在 DeerFlow 哪里体现。

**本章课程目标：**

- 理解 DeerFlow 的"静态 system prompt + 动态 first HumanMessage 注入"双轨设计（为 prompt cache 优化）
- 看懂 1000+ 行 SYSTEM_PROMPT_TEMPLATE 的 7 大模块构成
- 知道 ReAct / CoT / Reflexion / Constitutional AI 等学术 prompt 范式在 DeerFlow 哪里落地
- 掌握 Agent 项目 prompt 调优的 5 大经验法则
- 学会 Memory Update Prompt 的"对话提取事实"模式

**学习建议：** 这章建议**对照源码读** —— 打开 `agents/lead_agent/prompt.py` 1016 行，按本章 §3 的 7 大模块定位。看完后回答："为什么 DeerFlow 不把日期写进 system prompt？" 答得出来，prompt cache 这块就懂了。

---

## 1、本章导读

### 1.1 Prompt 工程的"两段定位"

```
学术 Prompt 工程               vs        工程级 Prompt 系统
───────────────────                       ─────────────────
单 prompt 模板                            静态主体 + 动态注入
Few-shot 例子                             示例分组件维护
"Let's think step by step"               系统化思考引导（thinking_style 节）
零样本 vs 少样本对比                       prompt cache 命中率优化
```

DeerFlow 的 prompt **不是"一段提示"** —— 是工程化的"提示组装系统"。

### 1.2 整章 6 节速查

```
§2 DeerFlow Prompt 全景         — 双轨设计 + 7 大模块
§3 静态主提示 7 大模块解析       — SYSTEM_PROMPT_TEMPLATE 拆解
§4 动态注入机制                  — DynamicContextMiddleware 怎么塞日期/记忆
§5 学术范式映射                  — CoT/ReAct/Reflexion/Constitutional AI 在哪
§6 子 Agent prompt 设计哲学      — system_prompt / SKILL.md / 工具描述三层
§7 prompt 调优 5 大经验法则      — 实战可用
```

---

## 2、DeerFlow Prompt 全景

### 2.1 双轨设计图

```
┌──────────────────────────────────────────────────────────────┐
│ 静态轨道（不变 → prompt cache 友好）                            │
│ ────────────────────────────────────                           │
│ SYSTEM_PROMPT_TEMPLATE 渲染                                   │
│   ├─ <role>                                                  │
│   ├─ <thinking_style>                                        │
│   ├─ <clarification_system>                                  │
│   ├─ <skills_section>           ← 启动时拼接                   │
│   ├─ <deferred_tools_section>   ← 启动时拼接                   │
│   ├─ <subagent_section>          ← 启动时拼接                   │
│   ├─ <working_directory>                                     │
│   ├─ <response_style>                                        │
│   ├─ <citations>                                             │
│   └─ <critical_reminders>                                    │
│                                                              │
│ ⚠️ 这部分一旦渲染完就不变 → Anthropic/OpenAI prompt cache 命中 │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ 动态轨道（每次变 → 不进 cache）                                  │
│ ────────────────────────────────────                           │
│ DynamicContextMiddleware.before_agent                         │
│   把以下内容拼到 first HumanMessage 内容前                     │
│   ├─ <system-reminder>                                       │
│   │   Today: 2026-06-18                                      │
│   │   User context (from memory):                            │
│   │   - 用户偏好简洁                                          │
│   │   - 用户在做 Agent 研究                                    │
│   │   </system-reminder>                                     │
│   └─ 用户原始消息                                             │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 为什么这么设计（核心创新）

**问题：** 早期 LangChain 项目把"今天日期" / "用户记忆"放在 system prompt 里：

```python
# ❌ 反模式
system_prompt = f"""You are an agent.
Today is {today}.
User memory: {user_memory}.
... (10k tokens of static content)
"""
```

**问题：** system_prompt 每次都不一样 → prompt cache **完全失效** → 每次 LLM 调用 input tokens 全价。

**DeerFlow 的解法：**

```python
# ✅ 正确做法
system_prompt = SYSTEM_PROMPT_TEMPLATE  # 完全静态

# 动态内容塞进第一条 HumanMessage（反正都要传）
first_human = HumanMessage(content=f"""<system-reminder>
Today: {today}
User memory: {user_memory}
</system-reminder>

{user_original_question}""")
```

**效果：** system_prompt 永不变 → prompt cache 命中 → input tokens 节约 80-90%（对长 prompt 的 Agent 项目）。

源码注释：

```python
# agents/lead_agent/prompt.py:1
# 注意：最终系统提示**不含**记忆和当前日期 —— 这些由 ``DynamicContextMiddleware`` 在每条
# HumanMessage 前以 ``<system-reminder>`` 注入，使系统提示保持完全静态以最大化前缀缓存复用。
```

---

## 3、静态主提示 7 大模块解析

`SYSTEM_PROMPT_TEMPLATE` 在 `prompt.py:432-619`，约 200 行。

### 3.1 模块 1：`<role>` 角色定位

```
<role>
你是 {agent_name}，一个开源超级 Agent。
</role>
```

**设计哲学：**
- 极简（2 行）—— 角色定位过长会喧宾夺主
- 用 `{agent_name}` 占位符，支持自定义 Agent 命名

### 3.2 模块 2：`<thinking_style>` 思考引导

```
<thinking_style>
- 在执行操作前，对用户的请求进行简洁、策略性的思考
- 分解任务：哪些是明确的？哪些是模糊的？哪些信息缺失？
- **优先级检查：如果有任何不明确、缺失或存在多重解读的情况，
  你必须首先请求澄清——不要继续工作**
- 不要在思考过程中写下完整的最终答案或报告，只写提纲
- 关键：思考结束后，你必须向用户提供实际回复
</thinking_style>
```

**对应学术范式：Chain of Thought (CoT) + Reflexion**
- 不是简单的 "Let's think step by step"
- 主动要求**任务分解** + **明确性检查** —— 是 ReAct 思路
- 提醒**思考 != 回复**（很多 LLM 会把思考当回复）

### 3.3 模块 3：`<clarification_system>` 澄清优先

这是 DeerFlow 的**特色设计**（70 行），核心规则：

```
**工作流优先级：澄清 → 规划 → 行动**

5 类必须澄清场景：
1. 信息缺失（missing_info）
2. 需求模糊（ambiguous_requirement）
3. 方案选择（approach_choice）
4. 风险操作（risk_confirmation）
5. 建议征求（suggestion）

严格执行：
❌ 不要先开始工作再澄清
❌ 不要为了"效率"跳过澄清
❌ 不要在信息缺失时做出假设
✅ 在思考中分析请求 → 识别模糊点 → 在任何操作前询问
```

**设计要点：**
- 用具体场景类型（5 类）让 LLM 容易识别
- 用 ❌/✅ 对照 —— LLM 对二分对照的服从度最高
- 给具体使用方式 + 示例

**为什么这么严：** Agent 失控的最大原因是"基于错误假设疯狂执行"。强制澄清是**最有效的护栏**。

### 3.4 模块 4：`{skills_section}` 动态拼接

```python
# prompt.py:709-720
<skill_system>
你可以使用以下 SKILL.md 文档来获得领域专业知识：
<available_skills>
    <skill>
        <name>citation_format</name>
        <description>引用格式规范</description>
        <category>public</category>
    </skill>
    ...
</available_skills>
</skill_system>
```

**关键点：**
- 启动时**根据已启用的 skills** 动态拼接
- 不直接展开 SKILL.md 内容（太长），只列名字和描述
- LLM 看到后调用 `read_skill(name="...")` 读详情

**vs 直接展开所有 SKILL.md：**
- 展开 = 每次调用消耗 几十 k tokens（即使没用到）
- 列名 = 几百 token 索引 + 按需 read

### 3.5 模块 5：`{deferred_tools_section}` MCP 工具索引

```
<deferred_tools>
你可以使用 tool_search 工具发现以下 MCP 工具：
- github_create_issue
- github_list_repos
- filesystem_read
- filesystem_list_dir
... (50 个)
</deferred_tools>
```

**对应第 15 章 §5.4 的 DeferredToolFilter：**
- 50 个 MCP 工具的 schema 不展示给 LLM
- 只在这里列名字
- LLM 调用 `tool_search(query="github issue")` → 提升相关工具到可见集合

**节约：** 50 个工具 × 平均 500 token = 25k token，每次调用都省。

### 3.6 模块 6：`{subagent_section}` 子 Agent 编排

见第 6 章 §3.1，约 130 行。核心是：
- 拆解 → 委派 → 综合三步法
- 硬性并发限制（≤ 3 / 轮）
- 多批次执行模板（>3 子任务时）

**对应学术范式：Plan-Solve / Decomposition**
- 鼓励 LLM "先想清楚有几个子任务"（Plan）
- 然后并行启动（Solve）
- 最后综合（Aggregate）

### 3.7 模块 7：`<response_style>` + `<citations>` + `<critical_reminders>`

```
<response_style>
- 简洁清晰，避免冗余
- 中文回复用中文，英文回复用英文
- 代码块用 markdown
</response_style>

<citations>
- 外部信息引用：[citation:Title](URL)
- 多个引用合并展示
- 工具结果引用：[citation:tool_name](result_id)
</citations>

<critical_reminders>
- 技能优先：开始**复杂**任务前始终加载相关技能
- 禁止凭空捏造（hallucinate）
- 禁止假装已经做了实际未做的事
</critical_reminders>
```

**作用：**
- 输出格式规范（前端渲染依赖）
- 引用规范（防 hallucinate）
- 重要警告（最后一段，LLM 对结尾内容关注度最高 —— 利用 recency bias）

### 3.8 全模板组装

```python
# prompt.py:1006-1015
return SYSTEM_PROMPT_TEMPLATE.format(
    agent_name=agent_name,
    soul=soul_content,
    self_update_section=self_update_section,
    subagent_thinking=subagent_thinking,
    skills_section=skills_section,
    deferred_tools_section=deferred_tools_section,
    subagent_section=subagent_section,
    acp_section=acp_section,
    subagent_reminder=subagent_reminder,
)
```

**所有 `{xxx}` 占位符在启动时一次性渲染** —— 渲染完就不变。

---

## 4、动态注入机制（DynamicContextMiddleware）

### 4.1 注入位置：first HumanMessage

```python
# agents/middlewares/dynamic_context_middleware.py（核心逻辑）
class DynamicContextMiddleware(AgentMiddleware):
    async def abefore_agent(self, state, runtime):
        first_human = self._find_first_human_message(state["messages"])
        if first_human is None:
            return None
        
        # 构造 system-reminder
        reminder_parts = [f"Today: {today_iso()}"]
        
        # 加 memory（如果启用）
        if self.memory_enabled:
            user_id = runtime.context.get("user_id")
            memory = await get_memory_storage().aload(user_id)
            if memory["facts"]:
                reminder_parts.append("User context:")
                for fact in memory["facts"]:
                    reminder_parts.append(f"- {fact['content']}")
        
        reminder = "\n".join(reminder_parts)
        new_content = f"<system-reminder>\n{reminder}\n</system-reminder>\n\n{first_human.content}"
        
        return {"messages": [first_human.model_copy(update={"content": new_content})]}
```

### 4.2 为什么用 `<system-reminder>` 标签

```
<system-reminder>
Today: 2026-06-18
User context (from memory):
- 用户偏好简洁回答
</system-reminder>

帮我做个市场调研报告
```

**好处：**
- LLM 把 `<system-reminder>` 视为"系统级指令"（不是用户内容）
- 用户问题不被污染
- 多个动态内容（日期 / 记忆 / uploaded_files）都可以塞进同一个标签

**vs 直接拼接：**
```
今天是 2026-06-18。用户偏好简洁。帮我做个市场调研报告。
```

LLM 容易把"今天是"理解成用户输入的一部分，造成混淆。

### 4.3 上传文件 / 已查看图片也走动态注入

```python
# 类似机制
<system-reminder>
Today: 2026-06-18

<uploaded_files>
- /mnt/user-data/uploads/data.csv (5KB)
- /mnt/user-data/uploads/spec.pdf (200KB) → 已转换 spec.md
</uploaded_files>

<viewed_images>
img_001: /mnt/user-data/uploads/screenshot.png (1024x768)
</viewed_images>
</system-reminder>
```

**关键：所有"会变"的内容**全部塞进 first HumanMessage 的 `<system-reminder>`。

---

## 5、学术范式映射

DeerFlow 不是"重新发明轮子"，而是把学术 prompt 范式工程化。

### 5.1 ReAct (Yao et al. 2022)

> **ReAct = Reason + Act**：让 LLM 交替输出思考（Thought）和动作（Action），而不是直接给答案。

**DeerFlow 怎么落地：**

```
<thinking_style>
- 在执行操作前，对用户的请求进行简洁、策略性的思考
- 分解任务：哪些是明确的？哪些是模糊的？哪些信息缺失？
</thinking_style>
```

+ LangGraph 的 `create_agent` 内置的 model + tools 循环就是 ReAct 实现：
```
模型节点（Think）→ 工具节点（Act）→ 模型节点（Observe + Think）→ ...
```

**DeerFlow 的扩展：** 把"是否需要 fork 子 Agent"也加入 Think 阶段（subagent_thinking 节）：

```
- 数出子任务数量：如果 ≤ 3，本轮全部启动；如果 > 3，本轮选择最重要的 3 个
```

### 5.2 Chain of Thought (Wei et al. 2022)

> **CoT = "Let's think step by step"**：通过示例引导 LLM 输出推理过程，而不是直接答案。

**DeerFlow 怎么落地：**

```
<thinking_style>
- 不要在思考过程中写下完整的最终答案或报告，只写提纲
- 关键：思考结束后，你必须向用户提供实际回复
</thinking_style>
```

**关键创新：DeerFlow 不写"think step by step"，因为：**
- 现代模型（Claude / GPT-4）默认就有 CoT 能力
- 显式写反而显得冗余
- DeerFlow 关注**思考的"边界"**（不要写答案在思考里）而不是"是否思考"

### 5.3 Reflexion (Shinn et al. 2023)

> **Reflexion = 让 LLM 在每次失败后写"反思"**：把错误经验当作 feedback 注入下一轮。

**DeerFlow 怎么落地：**

部分实现：
- ✅ `LoopDetectionMiddleware`：检测重复 tool_call → 注入"你陷入循环了，请重新规划" 提示
- ✅ `ToolErrorHandlingMiddleware`：工具异常 → 转 ToolMessage 让 LLM 看到错误
- ❌ 没有"会话级 reflection"（如每 N 轮强制反思）

**未实现的扩展空间：** 加一个 `ReflexionMiddleware`，在长任务每 5 轮强制 LLM 回顾"目前进度 / 错误 / 下一步"。

### 5.4 Constitutional AI (Anthropic 2022)

> **Constitutional AI = 用一份"章程"约束 LLM 行为**，比 prompt 列规则更系统。

**DeerFlow 怎么落地：**

```
<critical_reminders>
- 禁止凭空捏造（hallucinate）
- 禁止假装已经做了实际未做的事
- 技能优先：开始复杂任务前始终加载相关技能
</critical_reminders>
```

这就是迷你版"章程"。但 DeerFlow 没做到 Anthropic 的程度（70+ 条原则 + critic 模型自我评估）。

**第 11 章数据飞轮里**提到：未来可以加 Rubric Evaluator（用 Constitutional AI 思路评测 trace）。

### 5.5 Plan-and-Solve (Wang et al. 2023)

> **Plan-and-Solve = 先生成计划，再按计划执行**。

**DeerFlow 怎么落地：**

完美对应：
- ✅ `Plan Mode + Todo` 系统（第 18 章 §3）
- ✅ `subagent_section` 的"拆解 + 委派 + 综合"三步法

```python
# Plan Mode 启用时，LLM 第一步就调用：
write_todos([
    {content: "搜索行业现状", status: "pending"},
    {content: "对比 5 家竞品", status: "pending"},
    {content: "整理输出报告", status: "pending"},
])
# 然后按 todo 顺序执行
```

### 5.6 ReWOO (Xu et al. 2023)

> **ReWOO = 把推理和执行分离**：先规划所有工具调用，再批量执行。

**DeerFlow 部分对应：**
- ⚠️ 子 Agent 模式：主 Agent 一次输出多个 task tool_call → 并行执行 → 综合结果
- ❌ 主 Agent 单次响应内的 tool_calls 还是顺序处理（除了 task 工具）

### 5.7 学术范式 vs DeerFlow 对照表

| 范式 | 是否落地 | DeerFlow 哪里 |
| --- | --- | --- |
| ReAct | ✅ 完整 | LangGraph create_agent + thinking_style |
| Chain of Thought | ✅ 隐式（依赖模型本能） | thinking_style 引导边界 |
| Reflexion | ⚠️ 部分 | LoopDetection / ToolError 转 ToolMessage |
| Constitutional AI | ⚠️ 简版 | critical_reminders 节 |
| Plan-and-Solve | ✅ 完整 | Plan Mode + Todo 系统 |
| ReWOO | ⚠️ 部分 | 子 Agent 并行 task tool_call |
| Tree of Thoughts | ❌ 未实现 | 无 |
| Graph of Thoughts | ❌ 未实现 | 无 |

**面试可以提：** "DeerFlow 在 prompt 范式上比较保守，落地了 ReAct + Plan-and-Solve + 部分 Reflexion。Tree of Thoughts / Graph of Thoughts 这种树/图搜索结构还没引入。"

---

## 6、子 Agent prompt 设计哲学

### 6.1 三层 prompt 注入

子 Agent 启动时拿到的 prompt 由三部分拼成：

```
┌─────────────────────────────────────────┐
│ Layer 1: SubagentConfig.system_prompt   │  ← 子 Agent 角色定义
│ "You are a general-purpose subagent..." │
└─────────────────────────────────────────┘
┌─────────────────────────────────────────┐
│ Layer 2: SKILL.md 内容                   │  ← 加载的技能文档
│ "<skill name='citation_format'>...      │
│ </skill>"                                │
└─────────────────────────────────────────┘
┌─────────────────────────────────────────┐
│ Layer 3: 任务描述（HumanMessage）         │  ← 主 Agent 传过来的 prompt 参数
│ "搜索腾讯 2026 Q1 财报，提取关键指标"    │
└─────────────────────────────────────────┘
```

源码（executor.py:431-472）：

```python
async def _build_initial_state(self, task: str):
    skills = await self._load_skills()
    skill_messages = await self._load_skill_messages(skills)
    
    # 把 system_prompt + skills 合成一条 SystemMessage
    system_parts = []
    if self.config.system_prompt:
        system_parts.append(self.config.system_prompt)
    for skill_msg in skill_messages:
        system_parts.append(skill_msg.content)
    
    messages = []
    if system_parts:
        messages.append(SystemMessage(content="\n\n".join(system_parts)))
    messages.append(HumanMessage(content=task))
    
    state = {"messages": messages, ...}
    return state
```

**为什么合成一条 SystemMessage：** 部分 LLM API（如 OpenAI 旧版）不支持多条 SystemMessage，会报"System message must be at the beginning"。

### 6.2 GENERAL_PURPOSE_CONFIG 的 system_prompt 设计

```python
# subagents/builtins/general_purpose.py:16-44
system_prompt="""You are a general-purpose subagent working on a delegated task.
Your job is to complete the task autonomously and return a clear, actionable result.

<guidelines>
- Focus on completing the delegated task efficiently
- Use available tools as needed to accomplish the goal
- Think step by step but act decisively
- If you encounter issues, explain them clearly in your response
- Return a concise summary of what you accomplished
- Do NOT ask for clarification - work with the information provided  ← 关键
</guidelines>

<output_format>
When you complete the task, provide:
1. A brief summary of what was accomplished
2. Key findings or results
3. Any relevant file paths, data, or artifacts created
4. Issues encountered (if any)
5. Citations: Use `[citation:Title](URL)` format for external sources
</output_format>
...
"""
```

**关键设计点：**

1. **"Do NOT ask for clarification"**：子 Agent 不能问用户（没用户在听），只能基于现有信息工作
2. **`<output_format>` 强结构**：前端能解析、主 Agent 能整合
3. **简洁，不超过 50 行**：vs 主 Agent 的 1000+ 行 —— 子 Agent 不需要那么多上下文，越简洁 LLM 越聚焦

### 6.3 BASH_AGENT_CONFIG 的差异

```python
# subagents/builtins/bash_agent.py
system_prompt="""You are a bash command execution specialist...

<guidelines>
- Execute commands one at a time when they depend on each other
- Use parallel execution when commands are independent  ← 鼓励并行
- Report both stdout and stderr when relevant
- Be cautious with destructive operations (rm, overwrite, etc.)  ← 安全提醒
</guidelines>
"""
```

**与 general-purpose 差异：**
- 强调 bash 特性（并行 / 错误处理 / 危险操作）
- 不强调 citation（bash 输出不需要）
- 工具范围已在 config.tools 限制（只 5 个）

**设计哲学：每个子 Agent 类型的 prompt 都聚焦其专长**，不写通用废话。

### 6.4 SKILL.md 注入示例

```python
# 假设加载了 citation_format skill
SystemMessage(content='''<skill name="citation_format">
---
name: citation_format
description: 多对象对比时使用统一的特性矩阵格式
---

# When to use
当用户要求对比 ≥ 3 个实体时

# How to do it
1. 列出对比维度（最多 5 个）
2. 用 Markdown 表格输出
...
</skill>''')
```

**包裹在 `<skill name="...">` 标签里：**
- LLM 知道这是技能文档（不是指令）
- 多个技能能正确分组
- 工具调用 `read_skill` 时能精确定位

---

## 7、Memory Update Prompt（一个完整范例）

`agents/memory/prompt.py` 的 `MEMORY_UPDATE_PROMPT` 是个**好范例** —— 用来教 LLM "如何从对话提取事实"。

### 7.1 完整 prompt 结构

```python
MEMORY_UPDATE_PROMPT = """你是一个记忆管理系统。你的任务是分析对话并更新用户的记忆档案。

当前记忆状态：
<current_memory>
{current_memory}
</current_memory>

待处理的新对话：
<conversation>
{conversation}
</conversation>

操作指引：
1. 分析对话中与用户相关的重要信息
2. 提取相关的事实、偏好和上下文
3. 按照下方的详细长度指南，按需更新记忆各节

在提取事实之前，先对对话进行结构化反思：
1. 错误/重试检测：Agent 是否遇到了错误、需要重试或产生了不正确的结果？
2. 用户纠偏检测：用户是否纠正了 Agent 的方向、理解或输出？
3. 项目约束发现：对话中是否发现了项目特定的约束条件？

记忆各节指南：

**用户上下文**（当前状态 — 简洁摘要）：
- workContext：职业角色、公司、关键项目（2-3 句话）
- personalContext：语言、沟通偏好（1-2 句话）
- topOfMind：进行中的关注领域（3-5 句话）

**事实档案**（增量积累）：
- preferences：用户偏好（"喜欢 Python over Go"）
- knowledge：用户已知信息（避免重复解释）
- corrections：错误纠偏（这次错了下次别错）

输出 JSON：
{{
  "updated_memory": {{
    "userContext": {{...}},
    "facts": [...],
  }},
  "delta": {{
    "added": [...],
    "removed": [...],
  }}
}}
"""
```

### 7.2 这个 prompt 设计为什么好

**5 个亮点：**

1. **角色明确**："你是一个记忆管理系统" → 限制 LLM 不当聊天助手
2. **输入结构化**：用 `<current_memory>` `<conversation>` 标签清晰分块
3. **三步反思**：先做结构化反思（错误检测 / 纠偏检测 / 约束发现），再提取事实 —— 这是 ReAct 思路
4. **分类指南**：把事实分 workContext / personalContext / topOfMind / preferences / corrections，引导 LLM 思考"这事实属于哪类"
5. **强结构输出**：JSON schema + delta（只列变化），方便程序处理

### 7.3 vs 朴素 prompt

```python
# ❌ 朴素版
prompt = f"分析这个对话：{conversation}\n更新这个记忆：{current_memory}"
```

**问题：**
- LLM 不知道什么是"事实"
- 输出格式不固定（有时 JSON 有时纯文本）
- 错误检测 / 纠偏检测能力没启发

**DeerFlow 版精细程度比朴素版高 5x，结果质量也是。**

---

## 8、Prompt 调优 5 大经验法则

### 8.1 法则 1：分模块拼接，不要一坨

```python
# ❌ 一坨
system_prompt = """你是 Agent。可以做这些事... 你的工具有... 注意这些... 输出格式..."""

# ✅ 分模块（DeerFlow 做法）
SYSTEM_PROMPT_TEMPLATE = """
{role}
{thinking_style}
{clarification_system}
{skills_section}
{subagent_section}
...
"""
```

**好处：**
- 每个模块可独立测试 / 替换
- 按条件启用（plan_mode / 视觉 / subagent_enabled）
- 阅读维护容易

### 8.2 法则 2：动态内容塞 first HumanMessage（prompt cache 优化）

见本章 §2.2。**对长 prompt 的 Agent 项目能省 80%+ input tokens**。

### 8.3 法则 3：用结构化标签（XML-like）

```
<role>...</role>
<thinking_style>...</thinking_style>
<critical_reminders>...</critical_reminders>
```

**vs Markdown 标题：**
```
## 角色
## 思考风格
## 重要提醒
```

**XML 标签更优：**
- LLM 训练数据中 XML 标签普遍（HTML / 论文标记）
- 嵌套清晰（标签明确开闭）
- Claude / GPT-4 对 XML 标签的服从度比 Markdown 标题更好

**注意：** 不要真用 XML 1.0 严格语法，能解析的 XML-like 即可。

### 8.4 法则 4：用 ❌/✅ 对照规则

```
❌ 不要先开始工作再澄清
❌ 不要为了"效率"跳过澄清
✅ 在思考中分析请求 → 识别模糊点 → 在任何操作前询问
✅ 如果在思考中发现需要澄清，必须立即调用该工具
```

**为什么：** LLM 对二分对照的服从度最高。"不要 X，要 Y" 比"应该做 Y" 更明确。

### 8.5 法则 5：把"重要提醒"放最后（recency bias）

```
<critical_reminders>
- 禁止凭空捏造
- 禁止假装做了未做的事
</critical_reminders>
```

**LLM 对 prompt 末尾内容关注度最高**（attention 机制 + recency bias）。所以：
- ✅ 放安全约束 / 关键规则在最后
- ❌ 不要在 prompt 中间藏关键规则

### 8.6 5 法则汇总

| 法则 | 作用 | DeerFlow 落地 |
| --- | --- | --- |
| 分模块拼接 | 维护性 + 条件启用 | SYSTEM_PROMPT_TEMPLATE 7 大 section |
| 动态注入 first HumanMessage | prompt cache 命中 | DynamicContextMiddleware |
| XML-like 标签 | LLM 服从度高 | `<role>` `<thinking_style>` 等 |
| ❌/✅ 对照 | 规则明确 | `<clarification_system>` |
| 重要在最后 | recency bias | `<critical_reminders>` 在末尾 |

---

## 9、本章 ❓→💡 问答

### Q1：为什么 DeerFlow 不写 "Let's think step by step" ？

**A：** 三个原因：

1. **现代模型自带**：Claude / GPT-4 默认有 CoT 能力，不需要显式触发
2. **更精细的引导**：DeerFlow 的 `<thinking_style>` 关注**思考的边界**（"不要写答案在思考里"）而不只是"是否思考"
3. **工程化考虑**：现在主流是 "extended thinking" 模式（Anthropic Claude / OpenAI o1），由 API 参数控制，不靠 prompt 触发

### Q2：prompt cache 命中率怎么测？

**A：** Anthropic / OpenAI API 返回的 usage_metadata 里有 cache 字段：

```json
{
  "input_tokens": 1500,        // 总 input
  "input_tokens_details": {
    "cached_tokens": 12000,    // 命中 cache 的部分（折扣 90%）
    "input_tokens": 1500       // 实际计费的部分
  }
}
```

**实战指标：**
- 命中率 = `cached_tokens / (cached_tokens + input_tokens)`
- DeerFlow 预期命中率 **>80%**（system prompt 静态）
- 如果 <50% → 检查是不是动态内容混入了 system prompt

### Q3：SKILL.md 为什么不直接写进 system prompt？

**A：** 三个权衡：

1. **token 成本**：每个 SKILL.md 平均 1-3k token，加载 5 个就 10k+。直接写进 prompt 每次调用都消耗
2. **按需触发**：技能不一定每次都用到，列名 + 按需 read 更省
3. **演化能力**：SKILL.md 可以独立编辑（用户改 / Agent 自演化），不污染 system prompt 模板

**例外：** 子 Agent 的 SKILL.md **会**直接拼到 system_prompt（见 §6.4），因为子 Agent 任务短，全部加载也无妨。

### Q4：subagent_section 的并发限制为什么写在 prompt 里 + Middleware 双重防护？

**A：** 防御性设计（见第 9 章 三层并发防护）。

Prompt 是软约束（LLM 可能不听），Middleware 是硬截断。但**为什么还要写 prompt**？

- 让 LLM 知道为什么自己只能输出 N 个 task —— 不写的话 LLM 看到自己输出被截断会困惑
- 引导 LLM 主动"分批" —— 第 1 轮 3 个 → 等结果 → 第 2 轮 2 个，比"输出 5 个被砍 2 个"质量更好

### Q5：Memory Update Prompt 用什么模型跑？

**A：** **便宜模型**（gpt-4o-mini / claude-haiku）。原因：

- 任务相对简单（提取事实 + 分类）
- 频率高（每次对话都跑）
- 成本敏感（不能让 memory update 的 token 比主对话还多）

**估算：** 单次 update 约 2-5k tokens，用 mini 成本约 $0.0005，每天 1000 次更新 $0.5 → 可控。

如果用 GPT-4 / Claude Sonnet，单次 $0.05，每天 $50 → 不划算。

---

## 10、本章总结

**DeerFlow Prompt 工程的 4 大核心创新：**

| 创新 | 方法 | 收益 |
| --- | --- | --- |
| 双轨设计 | 静态 system prompt + 动态 first HumanMessage | prompt cache 命中率 >80% |
| 模块化 | 7 大 section 按条件拼接 | 维护性 + 灵活启用 |
| 澄清优先 | `<clarification_system>` 5 类强制场景 | 减少错误执行 |
| 工具索引化 | DeferredFilter + tool_search | 节约 25k token |

**学术范式落地映射：**

```
ReAct           ✅ 完整（thinking + tools 循环）
CoT             ✅ 隐式（不写 step by step，靠模型本能）
Plan-and-Solve  ✅ 完整（Plan Mode + Todo + subagent decomposition）
Reflexion       ⚠️ 部分（LoopDetection + ToolError）
Constitutional  ⚠️ 简版（critical_reminders）
Tree/Graph of Thoughts  ❌ 未实现
ReWOO           ⚠️ 部分（子 Agent 并行）
```

**5 大调优经验：**

```
1. 分模块拼接（不要一坨）
2. 动态内容塞 first HumanMessage（prompt cache）
3. 用 XML-like 标签（>Markdown 标题）
4. ❌/✅ 对照（>纯叙述）
5. 重要规则放最后（recency bias）
```

**面试金句：**

> "DeerFlow 的 Prompt 工程不是单文件，是**分层注入系统**：
> - **静态 SYSTEM_PROMPT_TEMPLATE**（7 大模块按条件拼接，1000+ 行）
> - **动态 first HumanMessage 注入**（DynamicContextMiddleware 塞日期/记忆 → prompt cache 命中率 >80%）
> - **子 Agent 三层 prompt**（SubagentConfig.system_prompt + SKILL.md + 任务描述）
>
> 学术范式上**完整落地了 ReAct 和 Plan-and-Solve**，**部分实现了 Reflexion 和 Constitutional AI**，没做 Tree of Thoughts。
>
> 调优上遵循 5 大原则：模块化、动态注入、XML 标签、❌/✅ 对照、重要规则放最后。这些**不是来自论文，是从生产 trial-and-error 总结的工程经验**。"

读完这章 + 前 18 章，DeerFlow 项目从工程到 prompt 全栈都覆盖了。**算法 / Prompt 工程这块的面试题不再是盲区**。
