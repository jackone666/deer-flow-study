---
name: deep-research
description: Use this skill instead of WebSearch for ANY question requiring web research. Trigger on queries like "what is X", "explain X", "compare X and Y", "research X", or before content generation tasks. Provides systematic multi-angle research methodology instead of single superficial searches. Use this proactively when the user's question needs online information.
---

# 深度研究技能（Deep Research Skill）

## 概述

本技能提供一套系统的网络研究方法论。**在进行任何内容生成任务之前，请先加载本技能**，确保从多个角度、深度和来源收集到充足信息。

## 何时使用本技能

**以下场景务必加载本技能：**

### 研究类问题
- 用户提问 "what is X"、"explain X"、"research X"、"investigate X"
- 用户希望深入理解某个概念、技术或主题
- 该问题需要来自多个来源的、当前且全面的信息
- 一次搜索不足以给出合理答案

### 内容生成（前置研究）
- 制作演示文稿（PPT / 幻灯片）
- 设计前端页面或 UI 原型
- 撰写文章、报告或文档
- 制作视频或多媒体内容
- 任何需要现实世界信息、案例或最新数据的内容

## 核心原则

**绝不能仅凭通识就生成内容。** 输出质量直接取决于前置研究的数量和质量。一次搜索远远不够。

## 研究方法论

### 阶段 1：广度探索

先做宽泛搜索，建立全局视野：

1. **初步调查（Initial Survey）**：搜索主话题，理解整体背景
2. **识别维度（Identify Dimensions）**：从初步结果中识别需要更深入的关键子主题、角度或方面
3. **勾勒全貌（Map the Territory）**：记录存在的不同视角、相关方或观点

示例：

```
Topic: "AI in healthcare"
Initial searches:
- "AI healthcare applications 2024"
- "artificial intelligence medical diagnosis"
- "healthcare AI market trends"

Identified dimensions:
- Diagnostic AI (radiology, pathology)
- Treatment recommendation systems
- Administrative automation
- Patient monitoring
- Regulatory landscape
- Ethical considerations
```

### 阶段 2：深入挖掘

针对识别出的每个关键维度，做有针对性的研究：

1. **精准查询（Specific Queries）**：用精确关键词检索每个子主题
2. **多种措辞（Multiple Phrasings）**：尝试不同的关键词组合和表达方式
3. **抓取全文（Fetch Full Content）**：用 `web_fetch` 完整阅读重要来源，而不仅看摘要
4. **顺藤摸瓜（Follow References）**：当来源提到其他重要资源时，也一并搜索

示例：

```
Dimension: "Diagnostic AI in radiology"
Targeted searches:
- "AI radiology FDA approved systems"
- "chest X-ray AI detection accuracy"
- "radiology AI clinical trials results"

Then fetch and read:
- Key research papers or summaries
- Industry reports
- Real-world case studies
```

### 阶段 3：多样性与验证

通过寻找不同类型的信息来保证覆盖度：

| 信息类型 | 用途 | 搜索示例 |
|-----------------|---------|------------------|
| **事实与数据（Facts & Data）** | 硬证据 | "statistics", "data", "numbers", "market size" |
| **案例与实例（Examples & Cases）** | 真实应用 | "case study", "example", "implementation" |
| **专家观点（Expert Opinions）** | 权威视角 | "expert analysis", "interview", "commentary" |
| **趋势与预测（Trends & Predictions）** | 未来方向 | "trends 2024", "forecast", "future of" |
| **对比（Comparisons）** | 上下文与替代方案 | "vs", "comparison", "alternatives" |
| **挑战与批评（Challenges & Criticisms）** | 平衡视角 | "challenges", "limitations", "criticism" |

### 阶段 4：综合校验

在进入内容生成之前，先自检：

- [ ] 我是否已从至少 3-5 个不同角度搜索过？
- [ ] 我是否抓取并完整阅读过最重要的来源？
- [ ] 我是否掌握了具体的数据、案例和专家观点？
- [ ] 我是否同时考察了正面与挑战 / 局限？
- [ ] 信息是否来自权威来源并且是较新的？

**如果任何一项答否，请先继续研究，再开始生成内容。**

## 搜索策略要点

### 高效查询模式

```
# Be specific with context
❌ "AI trends"
✅ "enterprise AI adoption trends 2024"

# Include authoritative source hints
"[topic] research paper"
"[topic] McKinsey report"
"[topic] industry analysis"

# Search for specific content types
"[topic] case study"
"[topic] statistics"
"[topic] expert interview"

# Use temporal qualifiers — always use the ACTUAL current year from <current_date>
"[topic] 2026"   # ← replace with real current year, never hardcode a past year
"[topic] latest"
"[topic] recent developments"
```

### 时间敏感度

**在构造任何搜索查询之前，务必先查看上下文中的 `<current_date>`。**

`<current_date>` 会给出完整的年、月、日、星期（例如 `2026-02-28, Saturday`）。按用户意图选取合适的时间精度：

| 用户意图 | 需要的时间精度 | 查询示例 |
|---|---|---|
| "今天 / 早上 / 刚发布" | **月 + 日** | `"tech news February 28 2026"` |
| "本周" | **周区间** | `"technology releases week of Feb 24 2026"` |
| "最近 / 最新 / 新的" | **月** | `"AI breakthroughs February 2026"` |
| "今年 / 趋势" | **年** | `"software trends 2026"` |

**规则：**

- 当用户问 "今天" 或 "刚发布" 时，搜索中必须使用 **月 + 日 + 年**，以拿到当日的结果
- 不要在该用日级精度时退化为仅年 —— `"tech news 2026"` 不会带出今天的新闻
- 多用不同写法：数字形式（`2026-02-28`）、文字形式（`February 28 2026`）、相对词（`today`、`this week`）分多条查询

❌ 用户问 "what's new in tech today" → 搜 `"new technology 2026"` → 错过今日新闻
✅ 用户问 "what's new in tech today" → 搜 `"new technology February 28 2026"` + `"tech news today Feb 28"` → 拿到当日结果

### 何时使用 web_fetch

以下场景使用 `web_fetch` 读取完整内容：

- 搜索结果看起来高度相关且权威
- 需要摘要之外的详细信息
- 来源包含数据、案例或专家分析
- 想要理解一个发现的完整背景

### 迭代式收敛

研究是迭代的过程。完成初步搜索后：

1. 复盘已学到的内容
2. 找出知识空白
3. 构造新的、更精准的查询
4. 重复直到覆盖足够全面

## 质量门槛

研究够不够，看能否自信回答下列问题：

- 关键事实和数据点是什么？
- 能列出 2-3 个具体的真实案例吗？
- 专家对这一话题有什么看法？
- 当前趋势和未来方向是什么？
- 有哪些挑战或局限？
- 这个话题为何当下值得关注？

## 常见误区

- ❌ 搜了 1-2 次就停
- ❌ 只看搜索摘要，不读完整来源
- ❌ 只搜了多面话题的某一个面
- ❌ 忽略相左的观点或挑战
- ❌ 明明有更新数据却用过时信息
- ❌ 研究未结束就开始生成内容

## 输出

完成研究后，你应该拥有：

1. 对该主题多角度的全面理解
2. 具体的事实、数据点和统计
3. 真实世界的案例和故事
4. 专家视角和权威来源
5. 当前趋势和相关的语境信息

**之后再进入内容生成阶段**，用收集到的信息做出高质量、视角充分的内容。
