---
name: consulting-analysis
description: Use this skill when the user requests to generate, create, or write professional research reports including but not limited to market analysis, consumer insights, brand analysis, financial analysis, industry research, competitive intelligence, investment due diligence, or any consulting-grade analytical report. This skill operates in two phases — (1) generating a structured analysis framework with chapter skeleton, data query requirements, and analysis logic, and (2) after data collection by other skills, producing the final consulting-grade report with structured narratives, embedded charts, and strategic insights.
---

# 专业研究报告技能（Professional Research Report Skill）

## 概述

本技能以 Markdown 格式产出专业的、可达到咨询水准的研究报告，覆盖 **市场分析、消费者洞察、品牌战略、财务分析、行业研究、竞争情报、投资研究、宏观经济分析** 等领域。它跨两个独立阶段工作：

1. **阶段 1 —— 分析框架生成**：给定研究主题，产出一套严谨的分析框架，包含章节骨架、各章的数据需求、分析逻辑与可视化方案。
2. **阶段 2 —— 报告生成**：在其他技能完成数据采集后，将所有输入综合为最终的润色报告。

输出遵循麦肯锡 / BCG（McKinsey / BCG）咨询话语规范。报告语言遵循 `output_locale` 设置（默认 `zh_CN`，即简体中文）。

## 数据真实性协议

**严格遵循原则**：报告中展示的所有数据以及图表中可视化的所有数据，**必须**直接来源于所提供的 **Data Summary（数据摘要）** 或 **External Search Findings（外部搜索发现）**。
- **严禁幻觉（NO Hallucinations）**：不得杜撰、估算或模拟数据。若数据缺失，请注明"数据不可得"，而非编造数字。
- **可追溯来源**：每一条主要主张与每一张图表都必须可回溯到输入的数据包。

## 核心能力

- 仅凭研究主题与范围便能从零**设计分析框架**
- 将原始数据转化为结构化、有深度的研究报告
- 遵循每个子章节 **"视觉锚点 → 数据对比 → 综合分析"** 的流程
- 沿 **"数据 → 用户心理 → 战略含义"** 的链条产出洞察
- 嵌入预生成图表并构造对比表
- 按 **GB/T 7714-2015** 标准生成行内引用
- 以 `output_locale` 指定的语言输出报告，采用专业咨询语气
- 适配不同领域（营销、金融、行业等）的分析深度与结构

## 适用场景

**在以下情况始终应加载本技能：**

- 用户请求市场分析、消费者洞察报告、财务分析、行业研究或任何咨询级分析报告
- 用户给出研究主题，需要在数据采集前先得到结构化的分析框架
- 用户提供数据摘要、分析框架或图表文件，需要合成为报告
- 用户需要一份专业咨询风格的研究报告
- 任务涉及将研究发现转化为结构化的战略叙事

---

# 阶段 1：分析框架生成

## 目标

给定一个**研究主题**（例如"Z 世代护肤市场分析"、"新能源汽车行业竞争格局"、"X 品牌消费者画像"），产出一份完整的**分析框架**，作为下游数据采集与最终报告生成的蓝图。

## 阶段 1 输入

| 输入 | 描述 | 是否必填 |
|------|------|----------|
| **研究主题（Research Subject）** | 待分析的话题或问题 | 是 |
| **范围 / 约束（Scope / Constraints）** | 地理范围、时间区间、行业细分、目标受众等 | 否 |
| **特定角度（Specific Angles）** | 用户希望探索的特定角度或假设 | 否 |
| **领域（Domain）** | 分析领域：市场、金融、行业、品牌、消费者、投资等 | 推断得出 |

## 阶段 1 工作流

### 步骤 1.1：理解研究主题

- 解析研究主题，识别**核心实体**（市场、品牌、产品、行业、消费者细分、金融工具等）
- 识别**分析领域**（营销、金融、行业、竞争、消费者、投资、宏观等）
- 根据领域确定**自然的分析维度**：

| 领域 | 典型维度 |
|------|----------|
| 市场分析 | 市场规模、增长趋势、市场细分、增长驱动、竞争格局、消费者画像 |
| 品牌分析 | 品牌定位、市场份额、消费者认知、营销策略、竞品对比 |
| 消费者洞察 | 人口画像、购买行为、决策旅程、痛点、场景分析 |
| 财务分析 | 宏观环境、行业趋势、公司基本面、财务指标、估值、风险评估 |
| 行业研究 | 价值链分析、市场规模、竞争格局、政策环境、技术趋势、进入壁垒 |
| 投资尽职调查 | 商业模式、财务健康、管理层评估、市场机会、风险因素、退出路径 |
| 竞争情报 | 竞争对手识别、战略对比、SWOT 分析、差异化定位、市场动态 |

### 步骤 1.2：选择分析框架与模型

基于已识别的领域与研究主题，选择**一个或多个**专业分析框架，用以组织各章的推理。所选框架将指导章节骨架（步骤 1.3）中的**分析逻辑**。

#### 战略与环境分析

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **SWOT 分析** | 优势、劣势、机会、威胁 | 品牌评估、竞争定位、战略规划 |
| **PEST / PESTEL 分析** | 政治、经济、社会、技术（+ 环境、法律） | 宏观环境扫描、市场进入评估、政策影响分析 |
| **波特五力** | 供应商议价能力、买方议价能力、新进入者威胁、替代品威胁、行业内竞争 | 行业竞争格局、进入壁垒评估、利润率分析 |
| **波特钻石模型** | 要素条件、需求条件、相关产业、企业战略与结构 | 国家 / 区域竞争优势分析 |
| **VRIO 分析** | 价值、稀缺性、难以模仿、组织 | 核心竞争力评估、资源优势分析 |

#### 市场与增长分析

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **STP 分析** | 市场细分、目标市场选择、市场定位 | 市场细分、目标市场选择、品牌定位 |
| **BCG 矩阵（增长 - 份额矩阵）** | 明星、现金牛、问号、瘦狗 | 产品组合管理、资源分配决策 |
| **安索夫矩阵** | 市场渗透、市场开发、产品开发、多元化 | 增长战略选择 |
| **产品生命周期（PLC）** | 导入、成长、成熟、衰退 | 产品战略制定、市场时机决策 |
| **TAM-SAM-SOM** | 总潜在市场 / 可服务市场 / 可获取市场 | 市场规模测算、机会量化 |
| **技术采用生命周期** | 创新者 → 早期采用者 → 早期大众 → 晚期大众 → 落后者 | 新兴技术 / 品类渗透分析 |

#### 消费者与行为分析

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **消费者决策旅程** | 认知 → 考虑 → 评估 → 购买 → 忠诚 | 消费者行为路径映射、触点优化 |
| **AARRR 漏斗（海盗指标）** | 获取、激活、留存、收入、推荐 | 用户增长分析、转化率优化 |
| **RFM 模型** | 最近一次消费（Recency）、消费频率（Frequency）、消费金额（Monetary） | 客户价值分层、精准营销 |
| **马斯洛需求层次理论** | 生理 → 安全 → 社交 → 尊重 → 自我实现 | 消费者心理分析、产品价值主张 |
| **Jobs-to-be-Done（JTBD，待完成的任务）** | 用户在特定情境下需要完成的"任务" | 需求洞察、产品创新方向 |

#### 财务与估值分析

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **杜邦分析** | ROE = 销售净利率 × 总资产周转率 × 权益乘数 | 盈利能力分解、财务健康诊断 |
| **DCF（现金流折现）** | 自由现金流折现 | 企业 / 项目估值 |
| **可比公司分析** | PE、PB、PS、EV/EBITDA 等倍数对比 | 相对估值、同业对标 |
| **EVA（经济增加值）** | 税后经营利润 − 资本成本 | 价值创造能力评估 |

#### 竞争与战略定位

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **对标分析（Benchmarking）** | 关键绩效指标逐项对比 | 竞争对手差距分析、最佳实践识别 |
| **战略群组映射** | 沿两个关键维度对竞争对手聚类 | 竞争格局可视化、蓝海识别 |
| **价值链分析** | 主要活动 + 支持活动的价值分解 | 成本优势来源、差异化机会识别 |
| **蓝海战略** | 价值曲线、四步动作框架（剔除 - 减少 - 增加 - 创造） | 差异化创新、新市场空间创造 |
| **知觉图（Perceptual Map）** | 沿两个消费者感知维度绘制品牌定位 | 品牌定位分析、市场空白发现 |

#### 行业与供应链分析

| 框架 | 描述 | 最适合场景 |
|------|------|-----------|
| **行业价值链** | 上游 → 中游 → 下游分解 | 行业结构理解、利润分布分析 |
| **Gartner 技术成熟度曲线** | 技术萌芽期 → 期望膨胀期 → 幻灭低谷期 → 复苏爬升期 → 生产力成熟期 | 新兴技术成熟度评估 |
| **GE - 麦肯锡矩阵** | 行业吸引力 × 竞争实力 | 业务组合优先级、投资决策 |

#### 选择原则

1. **领域优先**：根据步骤 1.1 识别的领域，从上述工具箱中选择 **2-4 个**最相关的框架
2. **互补性**：选择互补而非重叠的框架（例如：宏观层面用 PESTEL，微观层面用波特五力）
3. **深度优先于广度**：与其浅表地堆砌 6 个框架，不如深入地应用 2 个
4. **数据可行**：所选框架必须可由下游数据采集技能支撑 —— 若某个框架所需数据难以合理获取，则降级或替换
5. **明确映射**：在章节骨架中，明确标注每章使用哪个框架、如何应用

#### 框架选择输出格式

```markdown
## Framework Selection

| Chapter | Selected Framework(s) | Application |
|---------|----------------------|-------------|
| Market Size & Growth Trends | TAM-SAM-SOM + Product Life Cycle | TAM-SAM-SOM to quantify market space, PLC to determine market stage |
| Competitive Landscape Assessment | Porter's Five Forces + Strategic Group Mapping | Five Forces to assess industry competition intensity, Group Mapping to visualize competitive positioning |
| Consumer Profiling | RFM + Consumer Decision Journey | RFM to segment customer value, Decision Journey to identify key conversion nodes |
| Brand Strategy Recommendations | SWOT + Blue Ocean Strategy | SWOT to summarize overall landscape, Blue Ocean to guide differentiation direction |
```

### 步骤 1.3：设计章节骨架

产出一个分层的章节结构。每章必须包含：

1. **章节标题（Chapter Title）** —— 专业、简洁、基于主题（遵循"格式化"小节的命名规范）
2. **分析目标（Analysis Objective）** —— 本章旨在揭示什么
3. **分析逻辑（Analysis Logic）** —— 推理链或所用框架（必须引用步骤 1.2 中所选框架）
4. **核心假设（Core Hypothesis）** —— 拟由数据验证或证伪的初步假设

#### 章节骨架输出格式

```markdown
## Analysis Framework

### Chapter 1: [Title]
- **Analysis Objective**: [This chapter aims to...]
- **Analysis Logic**: [Framework or reasoning chain used]
- **Core Hypothesis**: [Hypotheses to validate]
- **Data Requirements**: (see Step 1.4)
- **Visualization Plan**: (see Step 1.5)

### Chapter 2: [Title]
...
```

### 步骤 1.4：定义各章的数据查询需求

针对每章，明确**需要采集哪些具体数据**。这是与下游数据采集技能之间的桥梁。

每条数据需求条目必须包含：

| 字段 | 描述 |
|------|------|
| **数据指标（Data Metric）** | 所需的具体指标或数据点（例如"中国护肤市场规模 2020-2025（亿元）"） |
| **数据类型（Data Type）** | 定量、定性或混合 |
| **建议来源（Suggested Sources）** | 建议的来源类别：行业报告、财报、政府统计、社交媒体、电商平台、调研数据、新闻 |
| **搜索关键词（Search Keywords）** | 建议的搜索查询，供数据采集代理使用 |
| **优先级（Priority）** | P0（必需）/ P1（重要）/ P2（补充） |
| **时间范围（Time Range）** | 数据应覆盖的时间区间 |

#### 数据需求输出格式（按章）

```markdown
#### Data Requirements

| # | Data Metric | Data Type | Suggested Sources | Search Keywords | Priority | Time Range |
|---|-------------|-----------|-------------------|-----------------|----------|------------|
| 1 | Market size (billion CNY) | Quantitative | Industry reports, government statistics | "China skincare market size 2024" | P0 | 2020-2025 |
| 2 | CAGR | Quantitative | Industry reports | "skincare CAGR growth rate" | P0 | 2020-2025 |
| 3 | Sub-category share | Quantitative | E-commerce platforms, industry reports | "skincare category share cream serum sunscreen" | P1 | Latest |
| 4 | Policy & regulatory updates | Qualitative | Government announcements, news | "cosmetics regulation 2024" | P2 | Past 1 year |
```

### 步骤 1.5：定义各章的可视化与内容结构

针对每章，明确最终报告中的**计划可视化**与**内容结构**：

| 字段 | 描述 |
|------|------|
| **可视化类型（Visualization Type）** | 图表类型：折线图、柱状图、饼图、散点图、雷达图、热力图、桑基图、对比表等 |
| **可视化标题（Visualization Title）** | 图表的描述性标题 |
| **数据映射（Visualization Data Mapping）** | 哪些数据指标映射到 X/Y 轴或分面 |
| **对比表设计（Comparison Table Design）** | 数据对比表的列头与对比维度 |
| **论证结构（Argument Structure）** | 计划的"What → Why → So What"叙事大纲 |

#### 可视化方案输出格式（按章）

```markdown
#### Visualization & Content Plan

**Chart 1**: [Type] — [Title]
- X-axis: [Dimension], Y-axis: [Metric]
- Data source: Corresponds to Data Requirement #1, #2

**Comparison Table**:
| Dimension | Item A | Item B | Item C |
|-----------|--------|--------|--------|

**Argument Structure**:
1. **Observation (What)**: [Surface phenomenon revealed by data]
2. **Attribution (Why)**: [Driving factors or underlying causes]
3. **Implication (So What)**: [Strategic implications or recommended actions]
```

### 步骤 1.6：输出完整的分析框架

将所有输出组装成一份结构化的**分析框架文档**：

```markdown
# [Research Subject] Analysis Framework

## Research Overview
- **Research Subject**: [...]
- **Scope**: [Geography, time range, industry segment]
- **Analysis Domain**: [Market / Finance / Industry / Brand / Consumer / ...]
- **Core Research Questions**: [1-3 key questions]

## Framework Selection

| Chapter | Selected Framework(s) | Application |
|---------|----------------------|-------------|
| ... | ... | ... |

## Chapter Skeleton

### 1. [Chapter Title]
- **Analysis Objective**: [...]
- **Analysis Logic**: [...]
- **Core Hypothesis**: [...]

#### Data Requirements
| # | Data Metric | Data Type | Suggested Sources | Search Keywords | Priority | Time Range |
|---|-------------|-----------|-------------------|-----------------|----------|------------|
| ... | ... | ... | ... | ... | ... | ... |

#### Visualization & Content Plan
[Chart plan + Comparison table design + Argument structure]

### 2. [Chapter Title]
...

### N. [Chapter Title]
...

## Data Collection Task List
[Consolidate all P0/P1 data requirements across chapters into a structured task list for downstream data collection skills to execute]
```

## 阶段 1 质量检查清单

- [ ] 分析框架覆盖了已识别领域的所有自然分析维度
- [ ] 选择 2-4 个专业分析框架，并明确映射到各章
- [ ] 所选框架具有互补性（不重叠）且数据可行
- [ ] 每章具有清晰的分析目标、分析逻辑（引用所选框架）以及核心假设
- [ ] 数据需求具体、可衡量，并附带搜索关键词
- [ ] 每章至少有一个可视化方案
- [ ] 数据优先级（P0/P1/P2）分配合理
- [ ] 框架可执行 —— 数据采集代理可直接根据搜索关键词执行
- [ ] 数据采集任务清单完整且去重

---

# 阶段 1→2 衔接：数据采集与图表生成

分析框架生成完成后，将交由**其他数据采集技能**（例如 deep-research、data-analysis、Web 搜索代理）执行：

1. 执行各章数据需求中的**搜索关键词**
2. 采集定量数据、定性洞察与源 URL
3. 基于**可视化与内容方案**生成图表
4. 返回一个**数据包（Data Package）**，包含：
   - **Data Summary**：按章组织的原始数字、指标与定性发现
   - **Chart Files**：生成的图表图片，附本地文件路径
   - **External Search Findings**：源 URL 与摘要，用于引用

> **本技能不执行数据采集。** 它仅产出框架（阶段 1）与最终报告（阶段 2）。
>
> **图表生成**：若可视化 / 图表技能可用（例如 data-analysis、image-generation），图表生成可推迟到阶段 2 之初 —— 参见步骤 2.3。

---

# 阶段 2：报告生成

## 目标

接收来自上游的**分析框架**与**数据包**，并将其综合为最终咨询级报告。

## 阶段 2 输入

| 输入 | 描述 | 是否必填 |
|------|------|----------|
| **分析框架（Analysis Framework）** | 阶段 1 产出的框架文档 | 是 |
| **Data Summary** | 数据采集阶段按章组织的采集数据 | 是 |
| **Chart Files** | 已生成图表的本地文件路径。若未提供，将在步骤 2.3 中使用可用的可视化技能生成 | 否 |
| **External Search Findings** | 用于行内引用的 URL 与摘要 | 否 |

## 阶段 2 工作流

### 步骤 2.1：接收并校验输入

确认所有必需输入是否齐备：

1. **分析框架** —— 确认包含章节骨架、数据需求与可视化方案
2. **Data Summary** —— 确认包含按章组织的数据，并与 P0 需求交叉对照
3. **Chart Files** —— 确认文件路径为有效本地路径

如有任何 P0 数据缺失，请在报告中注明并向用户标记。

### 步骤 2.2：映射报告结构

从分析框架映射出最终报告结构：

1. **摘要（Abstract）** —— 含关键要点的执行摘要
2. **引言（Introduction）** —— 背景、目标、方法论
3. **正文章节（2...N）** —— 由框架的章节骨架映射得到
4. **结论（Conclusion）** —— 纯粹的、客观的综合
5. **参考文献（References）** —— 符合 GB/T 7714-2015 格式

### 步骤 2.3：生成章节图表（报告前可视化）

在开始撰写报告之前，先基于分析框架的**可视化与内容方案**生成所有计划图表。这一步确保每个子章节的"视觉锚点"在叙事写作开始前就位。

#### 何时执行本步

- **Chart Files 已提供**：跳过本步，直接进入步骤 2.4。
- **未提供 Chart Files 但有可视化技能可用**：执行本步，先一次性生成所有图表。
- **既无 Chart Files 也无可视化技能**：跳过本步 —— 在步骤 2.4 中以对比表作为主要视觉锚点，并注明图表缺失。

#### 图表生成流程

1. **抽取图表任务**：解析分析框架中所有的"Visualization & Content Plan"条目，构建图表生成任务列表：

| # | 章节 | 图表类型 | 图表标题 | 数据映射 | 数据来源 |
|---|------|----------|----------|----------|----------|
| 1 | 2.1 | 折线图 | Market Size Trend 2020-2025 | X: Year, Y: Market Size (billion CNY) | Data Requirement #1, #2 |
| 2 | 3.1 | 饼图 | Consumer Age Distribution | Segments: Age groups, Values: Share % | Data Requirement #5 |
| ... | ... | ... | ... | ... | ... |

2. **准备图表数据**：针对每个图表任务，从 **Data Summary** 中抽取对应的数据点。
   > **关键原则**：仅使用 Data Summary 中提供的数字。**不得**为让图表"更好看"而捏造或"平滑"数据。若数据点缺失，图表必须如实反映现实（例如断线或缺失柱形），或调整图表类型。

3. **委派给可视化技能**：为每个图表任务调用可用的可视化 / 图表技能（例如 `data-analysis`），并提供：
   - 图表类型与标题
   - 结构化数据
   - 坐标轴标签与格式偏好
   - 输出文件路径约定：`charts/chapter_{N}_{chart_index}.png`

4. **收集图表文件路径**：记录所有生成的图表文件路径，以便在步骤 2.4 中嵌入：

```markdown
## Generated Charts
| # | Chapter | Chart Title | File Path |
|---|---------|-------------|-----------|
| 1 | 2.1 | Market Size Trend 2020-2025 | charts/chapter_2_1.png |
| 2 | 3.1 | Consumer Age Distribution | charts/chapter_3_1.png |
```

5. **校验**：确认所有 P0 优先级的图表均已生成。如有图表生成失败，请记录并对相应子章节降级为使用对比表。

> **原则**：在开始撰写报告前完成**所有**图表的生成。这保证了视觉叙事的连贯性，避免图表生成与写作交错进行。

### 步骤 2.4：撰写报告

对每个子章节，遵循 **"视觉锚点 → 数据对比 → 综合分析"** 的流程：

1. **视觉证据区块**：使用 `![Image Description](Actual_File_Path)` 嵌入图表 —— 路径取自步骤 2.3
2. **数据对比表**：用 Markdown 对比表呈现关键指标
   > **来源规则**：表中的每一个数字都必须来自 Data Summary。严禁幻觉。
3. **综合叙事分析**：按"What → Why → So What"撰写分析性文字
   > **叙事规则**：叙事必须解释**已提供的**数据，不得提出输入未支撑的主张。

每个子章节都必须以一段扎实的分析段落（不少于 200 字）收束：
- 综合相互冲突或相互印证的数据点
- 揭示其背后潜藏的用户张力或机会
- 可选地以一条简练的"One-Liner Truth"（一句话真相）收尾（采用引用块 `>`）

### 步骤 2.5：最终结构自检

输出前，确认报告按顺序**包含所有章节**：

```
Abstract → 1. Introduction → 2...N. Body Chapters → N+1. Conclusion → N+2. References
```

同时校验：
- 步骤 2.3 生成的所有图表都已嵌入对应的子章节
- `![](path)` 引用中的图表文件路径有效
- 无图表的子章节以对比表作为视觉锚点

报告**不得**在结论后戛然而止 —— **必须**以 References 作为最后一部分。

## 格式与语气规范

### 咨询话语

- **语气**：麦肯锡 / BCG 风格 —— 权威、客观、专业
- **语言**：所有标题与内容使用 `output_locale` 指定的语言
- **数字格式**：使用英文逗号作为千分位分隔符（`1,000` 而非 `1，000`）
- **数据强调**：对重要观点和关键数字使用**加粗**

### 标题规范

- **编号**：使用标准编号（`1.`、`1.1`）并紧跟标题
- **禁用前缀**：不要使用 "Chapter"、"Part"、"Section" 作为前缀
- **允许的语气词**：Analysis（分析）、Profiling（画像）、Overview（概览）、Insights（洞察）、Assessment（评估）
- **禁用词汇**："Decoding"（解码）、"DNA"、"Secrets"（秘密）、"Mindscape"（心智图景）、"Solar System"（太阳系）、"Unlocking"（解锁）

### 子章节结论

- **要求**：每章子节以一段扎实的分析段落收束（不少于 200 字）
- **叙事流**：该段必须是正文的自然延续，需将本节发现综合为战略判断
- **内容逻辑**：
    1.  综合相互冲突或相互印证的数据点
    2.  揭示其背后潜藏的用户张力或机会
    3.  关键洞察：**可选** —— 若你有一句简练有力的"One-Liner Truth"，可使用**引用块**（`>`）置于段末以锚定本节

### 洞察深度（"So What"链）

每条洞察都必须贯穿 **数据 → 用户心理 → 战略含义**：

```
❌ Bad: "Females are 60%. Strategy: Target females."

✅ Good: "Females constitute 60% with a high TGI of 180. **This suggests**
   the purchase decision is driven by aesthetic and social validation
   rather than pure utility. **Consequently**, media spend should pivot
   towards visual-heavy platforms (e.g., RED/Instagram) to maximize CTR,
   treating male audiences only as a secondary gift-giving segment."
```

### 参考文献

- **行内引用**：在使用 External Search Findings 时，使用 Markdown 链接标注来源（例如 `[Source Title](URL)`）
- **参考文献章节**：严格遵循 **GB/T 7714-2015** 格式

### Markdown 规范

- **直接开始**：以 `# Report Title` 直接开头 —— 不写引言性文字
- **禁止分隔线**：不要使用水平线（`---`）

## 报告结构模板

```markdown
# [Report Title]

## Abstract
[Executive summary with key takeaways]

## 1. Introduction
[Background, objectives, methodology]

## 2. [Body Chapter Title]
### 2.1 [Sub-chapter Title]
![Chart Description](chart_file_path)

| Metric | Brand A | Brand B |
|--------|---------|--------|
| ... | ... | ... |

[Integrated narrative analysis: What → Why → So What, min. 200 words]

> [Optional: One-liner strategic truth]

### 2.2 [Sub-chapter Title]
...

## N+1. Conclusion
[Pure objective synthesis, NO bullet points, neutral tone]
[Para 1: The fundamental nature of the group/market]
[Para 2: Core tension or behavior pattern]
[Final: One or two sentences stating the objective truth]

## N+2. References
[1] Author. Title[EB/OL]. URL, Date.
[2] ...
```

## 完整示例

### 阶段 1 示例：框架生成

用户提供：研究主题 "Gen-Z Skincare Market Analysis"

**阶段 1 输出（分析框架）：**

```markdown
# Gen-Z Skincare Market Analysis Framework

## Research Overview
- **Research Subject**: Gen-Z Skincare Market Deep Analysis
- **Scope**: China market, 2020-2025, consumers aged 18-27
- **Analysis Domain**: Market Analysis + Consumer Insights
- **Core Research Questions**:
  1. What is the size and growth momentum of the Gen-Z skincare market?
  2. What is unique about Gen-Z consumer skincare behavior patterns?
  3. How can brands effectively reach and convert Gen-Z consumers?

## Chapter Skeleton

### 1. Market Size & Growth Trends
- **Analysis Objective**: Quantify Gen-Z skincare market size and identify growth drivers
- **Analysis Logic**: Total market → Segmentation → Growth rate → Driver decomposition
- **Core Hypothesis**: Gen-Z is becoming the core engine of skincare consumption growth

#### Data Requirements
| # | Data Metric | Data Type | Suggested Sources | Search Keywords | Priority | Time Range |
|---|-------------|-----------|-------------------|-----------------|----------|------------|
| 1 | China skincare market total size | Quantitative | Industry reports | "China skincare market size 2024 2025" | P0 | 2020-2025 |
| 2 | Gen-Z skincare spending share | Quantitative | Industry reports, e-commerce platforms | "Gen-Z skincare spending share youth" | P0 | Latest |

#### Visualization & Content Plan
**Chart 1**: Line chart — China Skincare Market Size Trend 2020-2025
**Argument Structure**:
1. What: Quantified status of market size and Gen-Z share
2. Why: Consumption upgrade, ingredient-conscious consumers, social media driven
3. So What: Brands should prioritize building youth-oriented product lines

### 2. Consumer Profiling & Behavioral Insights
...

## Data Collection Task List
[Consolidated P0/P1 tasks]
```

### 阶段 2 示例：报告生成

数据采集完成后，用户提供：分析框架 + Data Summary（含品牌指标） + 图表文件路径。

**阶段 2 输出（最终报告）遵循如下流程：**

1. 以 `# Gen-Z Skincare Market Deep Analysis Report` 开头
2. Abstract（摘要） —— 以执行摘要形式呈现 3-5 条关键要点
3. 1. Introduction（引言） —— 市场背景、研究范围、数据来源
4. 2. Market Size & Growth Trend Analysis（市场规模与增长趋势分析） —— 嵌入趋势图、对比表、战略叙事
5. 3. Consumer Profiling & Behavioral Insights（消费者画像与行为洞察） —— 人口统计、购买驱动因素、So What 分析
6. 4. Brand Competitive Landscape Assessment（品牌竞争格局评估） —— 品牌定位、份额分析、竞争动态
7. 5. Marketing Strategy & Channel Insights（营销策略与渠道洞察） —— 渠道有效性、内容策略启示
8. 6. Conclusion（结论） —— 以流畅的散文形式进行客观综合（无要点）
9. 7. References（参考文献） —— GB/T 7714-2015 格式列表

---

## 质量检查清单

### 阶段 1 质量检查清单（分析框架）

- [ ] 框架覆盖了已识别领域的所有自然分析维度
- [ ] 每章具有清晰的分析目标、分析逻辑与核心假设
- [ ] 数据需求具体、可衡量，并附带可操作的搜索关键词
- [ ] 每章至少有一个可视化方案，包含图表类型与数据映射
- [ ] 已分配数据优先级（P0/P1/P2） —— P0 项是核心论证所必需
- [ ] 数据采集任务清单完整、去重，可由下游直接执行
- [ ] 框架适配正确的领域（市场 / 金融 / 行业 / 消费者等）

### 阶段 2 质量检查清单（最终报告）

- [ ] **严禁幻觉**：所有数字与图表均已对照输入的 Data Summary 核验
- [ ] 所有计划图表已在撰写报告前生成（步骤 2.3 已先完成）
- [ ] 所有章节按正确顺序齐全（Abstract → Introduction → Body → Conclusion → References）
- [ ] 每个子章节遵循"视觉锚点 → 数据对比 → 综合分析"
- [ ] 每个子章节以不少于 200 字的段落收束
- [ ] 所有洞察遵循"数据 → 用户心理 → 战略含义"链
- [ ] 所有标题使用正确编号（无 "Chapter/Part/Section" 前缀）
- [ ] 图表使用 `![Description](path)` 语法嵌入
- [ ] 数字使用英文逗号作为千分位分隔符
- [ ] 行内引用在合适处使用 Markdown 链接
- [ ] 参考文献章节遵循 GB/T 7714-2015
- [ ] 文档中无水平线（`---`）
- [ ] 结论采用流畅的散文 —— 无项目符号
- [ ] 报告以 `#` 标题直接开始 —— 无前置铺垫
- [ ] 缺失的 P0 数据已在报告中明确标记

## 输出格式

- **阶段 1**：以 **Markdown** 格式输出完整分析框架
- **阶段 2**：以 **Markdown** 格式输出完整报告

## 设置

```
output_locale = zh_CN  # 可根据用户请求配置
reasoning_locale = en
```

## 备注

- 本技能在多步 agent 工作流的**两个阶段**中运作：
  - **阶段 1** 产出分析框架与数据采集需求
  - **数据采集** 由其他技能（deep-research、data-analysis 等）执行
  - **阶段 2** 接收已采集数据并产出最终报告
- 动态标题：将框架中的主题**改写**为专业、简洁、基于主题的标题
- 结论部分**不得**包含详细建议 —— 详细建议应放在正文章节中
- **零幻觉原则（ZERO HALLUCINATION POLICY）**：报告中的每一条陈述、每一张图表、每一个数字都必须由输入 Data Summary 中的数据点支撑。数据缺失时，请坦承。
- **可追溯性**：如被要求，你必须能够指出 Data Summary 或 External Search Findings 中支撑某条主张的具体行
- 框架应适配特定领域并相应调整分析维度与深度（财务分析所用的框架与消费者洞察不同）
- 当研究主题含混不清时，默认采用最广的合理范围并注明假设
