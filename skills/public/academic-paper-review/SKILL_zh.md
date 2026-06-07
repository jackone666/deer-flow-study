---
name: academic-paper-review
description: Use this skill when the user requests to review, analyze, critique, or summarize academic papers, research articles, preprints, or scientific publications. Supports comprehensive structured reviews covering methodology assessment, contribution evaluation, literature positioning, and constructive feedback generation. Trigger on queries involving paper URLs, uploaded PDFs, arXiv links, or requests like "review this paper", "analyze this research", "summarize this study", or "write a peer review".
---

# 学术论文评审技能（Academic Paper Review Skill）

## 概述（Overview）

本技能用于产出结构化、达到同行评审质量的学术论文与研究出版物分析。它遵循顶级会议与期刊（NeurIPS、ICML、ACL、Nature、IEEE）通行的学术评审标准，提供严谨、建设性、平衡的评估。

评审内容覆盖 **摘要、优点、缺点、方法论评估、贡献评价、文献定位、可执行的改进建议** —— 全部基于论文自身提供的证据。

## 核心能力（Core Capabilities）

- 解析并理解来自上传 PDF 或抓取 URL 的学术论文
- 依照顶级会议/期刊的评审模板生成结构化评审意见
- 评估方法论的严谨性（实验设计、统计有效性、可复现性）
- 评价贡献的新颖性与重要性
- 通过有针对性的文献检索将研究工作置于更宏观的学术图景中
- 识别研究中的局限、空白与潜在改进点
- 同时产出详细评审与简明执行摘要两种格式
- 支持任意学科领域的论文（计算机科学、生物学、物理学、社会科学等）

## 何时使用本技能（When to Use This Skill）

**在以下场景中应始终加载本技能：**

- 用户提供论文 URL（arXiv、DOI、会议论文集、期刊链接）
- 用户上传研究论文或预印本的 PDF
- 用户提出"评审（review）""分析（analyze）""批评（critique）""评估（assess）""总结（summarize）"研究论文的请求
- 用户希望了解一项研究的优势与不足
- 用户请求以同行评审风格对学术工作进行评价
- 用户在为会议或期刊投稿准备评审意见时寻求帮助

## 评审方法论（Review Methodology）

### 阶段 1：论文理解（Paper Comprehension）

在任何判断形成之前，先彻底阅读并理解论文。

#### 步骤 1.1：识别论文元数据（Identify Paper Metadata）

抽取并记录：

| 字段 | 描述 |
|-------|-------------|
| **Title** | 论文完整标题 |
| **Authors** | 作者列表与所属机构 |
| **Venue / Status** | 发表会议/期刊、预印本平台、投稿状态 |
| **Year** | 发表或投稿年份 |
| **Domain** | 研究领域与子领域 |
| **Paper Type** | 实证型、理论型、综述型、立场型、系统型等 |

#### 步骤 1.2：深入阅读（Deep Reading Pass）

按以下顺序系统地阅读论文：

1. **摘要与引言** —— 识别作者声称的贡献与研究动机
2. **相关工作** —— 记录作者如何将自身工作定位到已有研究之中
3. **方法论** —— 详细理解所提出的方法、模型或框架
4. **实验与结果** —— 检视数据集、基线、评估指标与所报告的结果
5. **讨论与局限** —— 记录作者自己指出的局限
6. **结论** —— 将结论性主张与论文实际呈现的证据进行比对

#### 步骤 1.3：关键主张抽取（Key Claims Extraction）

显式列出论文的核心主张：

```
Claim 1: [关于贡献或结论的具体主张]
Evidence: [论文中支持该主张的证据]
Strength: [强 / 中 / 弱]

Claim 2: [...]
...
```

### 阶段 2：批判性分析（Critical Analysis）

#### 步骤 2.1：文献背景检索（Literature Context Search）

使用 Web 检索理解研究领域全景：

```
Search queries:
- "[paper topic] state of the art [current year]"
- "[key method name] comparison benchmark"
- "[authors] previous work [topic]"
- "[specific technique] limitations criticism"
- "survey [research area] recent advances"
```

对关键的相关论文或综述使用 `web_fetch`，以理解本文工作在领域中的位置。

#### 步骤 2.2：方法论评估（Methodology Assessment）

按以下框架评价方法论：

| 评估维度 | 应提出的问题 | 评分 |
|-----------|-----------------|--------|
| **Soundness（合理性）** | 方法在技术层面是否正确？是否存在逻辑缺陷？ | 1-5 |
| **Novelty（新颖性）** | 哪些是真正的新颖之处，哪些只是渐进式改进？ | 1-5 |
| **Reproducibility（可复现性）** | 细节是否足以复现？代码/数据是否公开？ | 1-5 |
| **Experimental Design（实验设计）** | 基线是否公平？消融是否充分？数据集是否合适？ | 1-5 |
| **Statistical Rigor（统计严谨性）** | 结果是否具备统计显著性？是否报告误差棒？是否多次运行？ | 1-5 |
| **Scalability（可扩展性）** | 方法能否扩展？是否讨论了计算成本？ | 1-5 |

#### 步骤 2.3：贡献重要性评估（Contribution Significance Assessment）

按以下分级评估贡献的重要程度：

| 级别 | 描述 | 标准 |
|-------|-------------|----------|
| **Landmark（里程碑级）** | 根本性地改变该领域 | 全新范式、广泛适用的突破 |
| **Significant（重要级）** | 强有力的贡献，显著推进现有最佳水平 | 在扎实证据支撑下的明显进步 |
| **Moderate（中等）** | 有用的贡献，但存在一定局限 | 渐进式但有效的改进 |
| **Marginal（边缘）** | 较已有工作仅有微弱推进 | 提升幅度小、适用面窄 |
| **Below threshold（不达标）** | 未达发表标准 | 存在根本性缺陷，证据不足 |

#### 步骤 2.4：优缺点分析（Strengths and Weaknesses Analysis）

对每条优点或缺点，需给出：

- **What（现象）**：具体的观察
- **Where（出处）**：所在的章节/图表/公式编号
- **Why it matters（意义）**：对论文主张或实用性的影响

### 阶段 3：评审综合（Review Synthesis）

#### 步骤 3.1：组装结构化评审（Assemble the Structured Review）

按以下模板输出最终评审。

## 评审输出模板（Review Output Template）

```markdown
# Paper Review: [Paper Title]

## Paper Metadata
- **Authors**: [Author list]
- **Venue**: [Publication venue or preprint server]
- **Year**: [Year]
- **Domain**: [Research field]
- **Paper Type**: [Empirical / Theoretical / Survey / Systems / Position]

## Executive Summary

[2-3 段概述论文的核心贡献、方法与主要发现。
开门见山地给出总体判断：论文擅长之处、薄弱之处，以及其贡献是否足以支撑所声称的发表层次/影响水平。]

## Summary of Contributions

1. [第一条声称的贡献 —— 一句话]
2. [第二条声称的贡献 —— 一句话]
3. [如有其他贡献，继续列出]

## Strengths

### S1: [简洁的优点标题]
[详细说明，并具体引用论文中的章节、图表或公式。解释为何构成优点以及其意义。]

### S2: [简洁的优点标题]
[...]

### S3: [简洁的优点标题]
[...]

## Weaknesses

### W1: [简洁的缺点标题]
[详细说明并具体引用。解释此缺点对论文主张的影响，并提出可改进的方向。]

### W2: [简洁的缺点标题]
[...]

### W3: [简洁的缺点标题]
[...]

## Methodology Assessment

| Criterion | Rating (1-5) | Assessment |
|-----------|:---:|------------|
| Soundness | X | [简要说明] |
| Novelty | X | [简要说明] |
| Reproducibility | X | [简要说明] |
| Experimental Design | X | [简要说明] |
| Statistical Rigor | X | [简要说明] |
| Scalability | X | [简要说明] |

## Questions for the Authors

1. [可澄清某项疑虑或模糊点的具体问题]
2. [关于方法选择或替代方案的问题]
3. [关于可推广性或实际适用性的问题]

## Minor Issues

- [笔误、格式问题、不清晰的图表、记法不一致]
- [应当引用却缺失的文献]
- [提升清晰度的建议]

## Literature Positioning

[本文工作与当前领域研究前沿的关系如何？
关键的相关工作是否被引用？对比是否公平、完整？
有哪些重要的相关工作被遗漏？]

## Recommendations

**Overall Assessment**: [Accept / Weak Accept / Borderline / Weak Reject / Reject]

**Confidence**: [High / Medium / Low] —— [对信心水平的说明]

**Contribution Level**: [Landmark / Significant / Moderate / Marginal / Below threshold]

### Actionable Suggestions for Improvement
1. [具体且具有建设性的改进建议]
2. [具体且具有建设性的改进建议]
3. [具体且具有建设性的改进建议]
```

## 评审原则（Review Principles）

### 建设性批评（Constructive Criticism）
- **始终给出修复建议** —— 仅仅指出问题不够，要提出解决方案
- **应肯定则肯定** —— 即便论文存在不足，也要承认其真正有价值的贡献
- **具体而非笼统** —— 引用具体的章节、公式、图表与表格
- **区分主次** —— 把致命缺陷与可修复的小问题分开

### 客观性标准（Objectivity Standards）
- ❌ "This paper is poorly written"（笼统、毫无帮助）
- ✅ "Section 3.2 introduces notation X without formal definition, making the proof in Theorem 1 difficult to follow. Consider adding a notation table after the problem formulation."（具体、可操作）

### 合乎伦理的评审实践（Ethical Review Practices）
- 不因作者声誉或所属机构而否定其工作
- 仅依据工作本身的优劣进行评价
- 以建设性的方式标记潜在伦理问题（数据集偏差、双重用途风险）
- 对未公开发表的工作保持保密

## 按论文类型适配（Adaptation by Paper Type）

| 论文类型 | 关注重点 |
|------------|-------------|
| **Empirical（实证型）** | 实验设计、基线、统计显著性、消融、可复现性 |
| **Theoretical（理论型）** | 证明正确性、假设合理性、界的紧致性、与实践的关联 |
| **Survey（综述型）** | 全面性、分类体系质量、最新工作覆盖、综合洞见 |
| **Systems（系统型）** | 架构决策、可扩展性证据、真实部署、工程贡献 |
| **Position（立场型）** | 论证连贯性、主张证据、潜在影响、表述公正性 |

## 应避免的常见误区（Common Pitfalls to Avoid）

- ❌ 评审你"希望写出的论文"，而非实际提交的论文
- ❌ 要求作者进行超出合理范围的额外实验
- ❌ 因论文未解决另一个问题而扣分
- ❌ 被写作质量过度影响而忽略技术贡献
- ❌ 把"未与自己的工作对比"当作缺点
- ❌ 只给总结、不做批判性分析

## 质量清单（Quality Checklist）

在定稿之前，请逐项确认：

- [ ] 已通读全文（不仅摘要与引言）
- [ ] 识别了全部主要主张，并对照证据进行评估
- [ ] 至少给出 3 条优点与 3 条缺点，且每条都附带具体引用
- [ ] 方法论评估表填写完整，含评分与说明
- [ ] 给作者的问题针对真正的疑点，而非修辞式批评
- [ ] 已进行文献检索以定位本文贡献
- [ ] 评审建议具有可操作性与建设性
- [ ] 总体评价与所列优缺点保持一致
- [ ] 评审语气专业、尊重
- [ ] 次要问题与主要问题已分开

## 输出格式（Output Format）

- 以 **Markdown** 格式输出完整评审
- 在沙箱环境中将评审保存到 `/mnt/user-data/outputs/review-{paper-topic}.md`
- 使用 `present_files` 工具将评审文件展示给用户

## 备注（Notes）

- 本技能与 `deep-research` 技能互补 —— 当用户希望在更广领域背景下评审论文时，请同时加载两者
- 对于付费墙后的论文，使用可访问到的任何内容（摘要、公开版本、预印本镜像）
- 评审深度应根据用户需求调整：用于快速分诊的简短评估 vs. 用于投稿准备的完整评审
- 在对多篇论文做对比评审时，请保持各篇评审标准一致
- 始终披露评审的局限（例如"我未能详细核实 Appendix B 中的证明"）
