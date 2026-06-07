---
name: newsletter-generation
description: Use this skill when the user requests to generate, create, write, or draft a newsletter, email digest, weekly roundup, industry briefing, or curated content summary. Supports topic-based research, content curation from multiple sources, and professional formatting for email or web distribution. Trigger on requests like "create a newsletter about X", "write a weekly digest", "generate a tech roundup", or "curate news about Y".
---

# 邮件简报生成技能（Newsletter Generation Skill）

## 概述（Overview）

本技能用于生成专业的、经过充分调研的邮件简报（newsletter），内容融合了从多个来源精选的素材以及原创的分析与点评。它遵循 Morning Brew、The Hustle、TLDR、Benedict Evans 等现代邮件简报的最佳实践，产出兼具信息量、可读性与可操作性（informative, engaging, actionable）的内容。

输出为一份完整、可直接发布的 Markdown 邮件简报，适用于邮件分发平台、Web 发布或转换为 HTML。

## 核心能力（Core Capabilities）

- 围绕指定主题从多个 Web 来源调研并精选内容
- 生成单一主题或多主题邮件简报，保持一致的语调
- 撰写吸引人的标题、摘要与原创点评
- 以最利于扫读的结构组织内容
- 支持多种邮件简报格式（每日摘要、每周回顾、深度专题、行业简报）
- 附带相关链接、来源与出处标注
- 适配不同目标受众的语调与风格（技术、高管、通用）
- 为定期邮件简报保持品牌与结构的一致性

## 何时使用本技能（When to Use This Skill）

**在以下场景中应始终加载本技能：**

- 用户请求生成邮件简报、邮件摘要或内容合集
- 用户请求对某个主题的新闻或动态进行精选汇总
- 用户希望创建一份定期发送的邮件简报
- 用户希望把某个领域的近期动态汇编成简报
- 用户需要一份格式化为可直接邮件发送、含多条精选内容的内容
- 用户请求"weekly roundup（每周回顾）""monthly digest（每月摘要）""morning briefing（早间简报）"

## 邮件简报工作流（Newsletter Workflow）

### 阶段 1：规划（Planning）

#### 步骤 1.1：理解邮件简报需求（Understand Newsletter Requirements）

明确关键参数：

| 参数 | 描述 | 默认值 |
|-----------|-------------|---------|
| **Topic(s)** | 要覆盖的主要主题领域 | 必填 |
| **Format** | 每日摘要、每周回顾、深度专题、行业简报 | 每周回顾 |
| **Target Audience** | 技术、高管、通用或细分社群 | 通用 |
| **Tone** | 专业、对话、诙谐、分析型 | 对话-专业混合 |
| **Length** | 短（5 分钟阅读）、中（10 分钟）、长（15 分钟以上） | 中 |
| **Sections** | 内容板块的数量与类型 | 4-6 个板块 |
| **Frequency Context** | 一次性或定期系列的一部分 | 一次性 |

#### 步骤 1.2：定义邮件简报结构（Define Newsletter Structure）

根据所选格式选定对应结构：

**每日摘要（Daily Digest）结构**：
```
1. Top Story（头条，1 条，详细）
2. Quick Hits（速览，3-5 条，简短）
3. One Stat / Quote of the Day（一项数据 / 今日金句）
4. What to Watch（值得关注）
```

**每周回顾（Weekly Roundup）结构**：
```
1. Editor's Note / Intro（编者按 / 引言）
2. Top Stories（头条，2-3 条，详细）
3. Trends & Analysis（趋势与分析，1-2 条，原创点评）
4. Quick Bites（速食消息，4-6 条，简短摘要）
5. Tools & Resources（工具与资源，2-3 条）
6. One More Thing / Closing（收尾短文 / 收场）
```

**深度专题（Deep-Dive）结构**：
```
1. Introduction & Context（引言与背景）
2. Background / Why It Matters（背景 / 为何重要）
3. Key Developments（关键进展，详尽分析）
4. Expert Perspectives（专家观点）
5. What's Next / Implications（展望 / 影响）
6. Further Reading（延伸阅读）
```

**行业简报（Industry Briefing）结构**：
```
1. Executive Summary（执行摘要）
2. Market Developments（市场动态）
3. Company News & Moves（公司新闻与动向）
4. Product & Technology Updates（产品与技术更新）
5. Regulatory & Policy Changes（法规与政策变化）
6. Data & Metrics（数据与指标）
7. Outlook（展望）
```

### 阶段 2：调研与精选（Research & Curation）

#### 步骤 2.1：多源调研（Multi-Source Research）

使用 Web 检索进行充分调研。**邮件简报的质量直接取决于调研的质量与时效。**

**检索策略**：

```
# 当前新闻与动态
"[topic] news [current month] [current year]"
"[topic] latest developments"
"[topic] announcement this week"

# 趋势与分析
"[topic] trends [current year]"
"[topic] analysis expert opinion"
"[topic] industry report"

# 数据与统计
"[topic] statistics [current year]"
"[topic] market data latest"
"[topic] growth metrics"

# 工具与资源
"[topic] new tools [current year]"
"[topic] open source release"
"best [topic] resources [current year]"
```

> **IMPORTANT**：始终通过 `<current_date>` 确认检索查询使用的是正确的时点上下文。切勿硬编码年份。

#### 步骤 2.2：来源评估与筛选（Source Evaluation and Selection）

评估每条来源并挑选最优质内容：

| 评估维度 | 优先级 |
|-----------|----------|
| **Recency（时效性）** | 优先选取过去 7-30 天的内容 |
| **Authority（权威性）** | 优先选取一手来源、官方公告、知名出版物 |
| **Uniqueness（独特性）** | 选择能提供新颖视角或被报道较少的题材 |
| **Relevance（相关性）** | 每一条都应与简报明确的主题挂钩 |
| **Actionability（可操作性）** | 优先选取读者可付诸行动的内容（工具、洞察、策略） |
| **Diversity（多样性）** | 新闻、分析、数据、实用资源等多种类型组合 |

#### 步骤 2.3：深度内容抽取（Deep Content Extraction）

对于重要新闻，使用 `web_fetch` 全文阅读并抽取：

1. **核心事实（Core facts）** —— 发生了什么、谁参与、什么时间
2. **背景信息（Context）** —— 为何重要，背景知识
3. **数据点（Data points）** —— 具体数字、指标或统计数据
4. **引述（Quotes）** —— 相关专家观点或官方声明
5. **影响（Implications）** —— 对读者意味着什么

### 阶段 3：写作（Writing）

#### 步骤 3.1：邮件简报头部（Newsletter Header）

每期邮件简报都以统一的页眉开始：

```markdown
# [Newsletter Name]

*[Tagline or description] — [Date]*

---

[Optional: One-sentence preview of what's inside]
```

#### 步骤 3.2：板块写作指南（Section Writing Guidelines）

**头条 / 重点条目（Top Stories / Featured Items）**：

- **标题（Headline）**：引人但不浮夸，清晰且对读者有益
- **引子（Hook）**：1-2 句开场白，让读者在意
- **主体（Body）**：关键事实与背景，2-4 段
- **意义解读（Why it matters）**：与读者世界相连接，1 段
- **来源链接（Source link）**：始终注明并附原文链接

**速食消息 / 简讯（Quick Bites / Brief Items）**：

- **格式**：加粗标题 + 2-3 句摘要 + 来源链接
- **聚焦**：每条只传达一个关键要点
- **高效性**：读者无需点开链接即可掌握核心信息

**分析 / 评论板块（Analysis / Commentary Sections）**：

- **语调（Voice）**：邮件简报对趋势或动态的独特视角
- **结构（Structure）**：观察 → 背景 → 影响 →（可选）可执行建议
- **证据（Evidence）**：每一条主张都有数据或来源支撑

#### 步骤 3.3：写作标准（Writing Standards）

| 原则 | 落实方式 |
|-----------|---------------|
| **Scannable（可扫读）** | 使用标题、加粗、项目符号与短段落 |
| **Engaging（引人入胜）** | 以最有趣的视角切入，而非按时间顺序 |
| **Concise（简洁）** | 每一句都要值得保留 —— 坚决砍掉冗词 |
| **Accurate（准确）** | 每条事实可追溯，每项数字已核实 |
| **Attributive（注明来源）** | 始终以行内链接致谢原始来源 |
| **Human（有温度）** | 写得像个懂行的朋友，而不是新闻通稿 |

**按受众调整语调（Tone Calibration by Audience）**：

| 受众 | 语调 | 示例 |
|----------|------|---------|
| **Technical（技术）** | 精确、不解释术语、假定专业背景 | "The new API supports gRPC streaming with backpressure handling via flow control windows." |
| **Executive（高管）** | 聚焦影响、关注结果、强调战略 | "This acquisition gives Company X a 40% market share in the enterprise segment, directly threatening Incumbent Y's pricing power." |
| **General（通用）** | 平易近人、善用类比、解释概念 | "Think of it like a universal translator for data — it lets any app talk to any database without learning a new language." |

### 阶段 4：组装与打磨（Assembly & Polish）

#### 步骤 4.1：组装邮件简报（Assemble the Newsletter）

将所有板块按所选结构模板合并为最终文档。

#### 步骤 4.2：页脚（Footer）

每期邮件简报都以以下页脚结束：

```markdown
---

*[Newsletter Name] is [description of what it is].*
*[How to subscribe/share/give feedback]*

*Sources: All links are provided inline. This newsletter curates and summarizes
publicly available information with original commentary.*
```

#### 步骤 4.3：质量清单（Quality Checklist）

在定稿之前，请逐项确认：

- [ ] **每条事实性主张都附有来源链接** —— 不出现无来源的断言
- [ ] **所有链接均可正常打开** —— 链接均经过搜索结果核验
- [ ] **日期使用真实当前日期** —— 无硬编码或假定日期
- [ ] **内容具有时效性** —— 重要条目均处于预期时间窗口内
- [ ] **无重复条目** —— 同一则消息只出现一次
- [ ] **格式一致** —— 标题、列表、链接风格贯穿全文一致
- [ ] **覆盖均衡** —— 不被单一来源或视角主导
- [ ] **长度合适** —— 与指定的长度目标匹配
- [ ] **开头引人** —— 前两句就能让读者继续读下去
- [ ] **收尾清晰** —— 以一句令人难忘或可执行的话结束
- [ ] **已校对** —— 无错字、格式错误或残句

## 邮件简报输出模板（Newsletter Output Template）

```markdown
# [Newsletter Name]

*[Tagline] — [Full date, e.g., April 4, 2026]*

---

[Preview sentence: "This week: [topic 1], [topic 2], and [topic 3]."]

## 🔥 Top Stories

### [Headline 1]

[Hook — why this matters in 1-2 sentences.]

[Body — 2-4 paragraphs covering key facts, context, and implications.]

**Why it matters:** [1 paragraph connecting to reader's interests or industry impact.]

📎 [Source: Publication Name](URL)

### [Headline 2]

[Same structure as above]

## 📊 Trends & Analysis

### [Trend Title]

[Original commentary on an emerging trend, backed by data from research.]

[Key data points presented clearly — consider inline stats or a brief comparison.]

**The bottom line:** [One-sentence takeaway.]

## ⚡ Quick Bites

- **[Headline]** — [2-3 sentence summary with key takeaway.] [Source](URL)
- **[Headline]** — [2-3 sentence summary.] [Source](URL)
- **[Headline]** — [2-3 sentence summary.] [Source](URL)
- **[Headline]** — [2-3 sentence summary.] [Source](URL)

## 🛠️ Tools & Resources

- **[Tool/Resource Name]** — [What it does and why it's useful.] [Link](URL)
- **[Tool/Resource Name]** — [Description.] [Link](URL)

## 💬 One More Thing

[Closing thought, insightful quote, or forward-looking statement.]

---

*[Newsletter Name] curates the most important [topic] news and analysis.*
*Found this useful? Share it with a colleague.*

*All sources are linked inline. Views and commentary are original.*
```

## 适配示例（Adaptation Examples）

### 科技类邮件简报
- Emoji 使用：✅ 中等（用于板块标题）
- 板块：头条、深度专题、速食消息、开源聚焦、开发工具
- 语调：技术-对话混合

### 商业/财经类邮件简报
- Emoji 使用：❌ 极少或不用
- 板块：市场概览、交易动态、公司新闻、数据角、展望
- 语调：专业-分析型

### 行业垂直邮件简报
- Emoji 使用：中等
- 板块：监管动态、市场数据、创新观察、人事变动、活动
- 语调：专家-权威型

### 创意/营销类邮件简报
- Emoji 使用：✅ 充分
- 板块：案例聚焦、趋势观察、本周爆款、爱用工具、灵感
- 语调：热情-专业型

## 输出处理（Output Handling）

生成完成后：

- 将邮件简报保存到 `/mnt/user-data/outputs/newsletter-{topic}-{date}.md`
- 使用 `present_files` 工具将邮件简报展示给用户
- 主动询问是否需要调整板块、语调、长度或焦点
- 若用户希望输出 HTML，请说明 Markdown 可用通用工具直接转换

## 备注（Notes）

- 本技能与 `deep-research` 技能组合效果最佳 —— 对于需要深度分析的邮件简报，建议同时加载两者
- 在检索和日期引用中始终使用 `<current_date>` 获取时点上下文
- 对于定期邮件简报，建议保持一致的结构以便读者形成预期
- 精选时"质大于量" —— 5 条优秀条目胜过 15 条平庸条目
- 妥善标注所有内容来源 —— 邮件简报的可信度建立在透明的来源之上
- 避免总结读者无法访问的付费墙内容
- 若用户提供了具体 URL 或文章，应在精选结果之外一并纳入
- 邮件简报应在摘要中提供足够价值，让读者即便不点开每条链接也能受益
