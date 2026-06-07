---
name: systematic-literature-review
description: Use this skill when the user wants a systematic literature review, survey, or synthesis across multiple academic papers on a topic. Also covers annotated bibliographies and cross-paper comparisons. Searches arXiv and outputs reports in APA, IEEE, or BibTeX format. Not for single-paper tasks — use academic-paper-review for reviewing one paper.
---

# 系统文献综述技能（Systematic Literature Review Skill）

## 概述

本技能围绕某一研究主题，对多篇学术论文产出结构化的 **系统文献综述（SLR, Systematic Literature Review）**。给定主题后，它会搜索 arXiv，并行抽取每篇论文的结构化元数据（研究问题、方法、关键发现、局限），跨论文综合主题，最终生成格式一致的引用报告。

**与 `academic-paper-review` 的区别：** 那个技能对单篇论文做深度评审。本技能做的是面向多篇论文的广度优先综合。若用户给你一篇论文的 URL 并说"评审这篇论文"，请改用 `academic-paper-review`。

## 何时使用本技能

当用户有以下任一需求时使用本技能：

- 针对某主题的文献调研（"survey transformer attention variants"、"review the literature on diffusion models"）
- 跨多篇论文的综合（"what do recent papers say about X"、"compare methodologies across papers on Y"）
- 引用格式统一的系统综述（"do an SLR on Z in APA format"）
- 某主题的注释书目（annotated bibliography）
- 某领域在某个时间窗内的研究趋势概览

以下场景**不要**使用本技能：

- 用户只给一篇论文并要求评审（改用 `academic-paper-review`）
- 用户问的是事实型问题、不需要综合多源（直接回答即可）
- 用户想要的是不要求学术严谨的通用网络研究（用标准网络搜索即可）

## 工作流

整个工作流分五个阶段，依次执行。

### 阶段 1：规划

在检索之前，先和用户确认以下事项。如有不清楚的，**一次性**问一个能覆盖所有缺失项的澄清问题，不要一次问一个问题。

- **主题（Topic）**：用普通英语描述的研究领域（例如 "transformer attention variants"）。
- **范围（Scope）**：论文数量（默认 20，上限 50），可选时间窗（例如 "last 2 years"），可选 arXiv 类目（例如 `cs.CL`、`cs.CV`）。
- **引用格式（Citation format）**：APA、IEEE 或 BibTeX（若用户未指定且看不出明显场景，默认 APA）。
- **输出位置（Output location）**：最终报告保存位置（默认 `/mnt/user-data/outputs/`）。

如果用户说"50+ papers"，礼貌地建议封顶 50 篇并解释：超过这个量之后综合质量会急剧下降，更大的调研最好按子主题拆分。

### 阶段 2：搜索 arXiv

调用内置的搜索脚本。**不要**用其他方式抓 arXiv，**不要**自己写 HTTP 客户端 —— 该脚本已正确处理 URL 编码、Atom XML 解析和 id 归一化。

```bash
python /mnt/skills/public/systematic-literature-review/scripts/arxiv_search.py \
  "<topic>" \
  --max-results <N> \
  [--category <cat>] \
  [--sort-by relevance] \
  [--start-date YYYY-MM-DD] \
  [--end-date YYYY-MM-DD]
```

**重要 —— 搜索前先提取 2-3 个核心关键词。** 不要把用户的整段主题描述直接当作 query。在调用脚本前，脑子里把主题压缩到 2-3 个最核心的词。像 "in computer vision"、"for NLP"、"variants"、"recent" 这类修饰语应该放进 `--category` 或 `--start-date`，而不是 query 字符串里。

**Query 措辞 —— 保持简短。** 脚本会用双引号把多词 query 包起来做 arXiv 上的短语匹配。意思是：

- `"diffusion models"` → 搜索这个精确短语 → 较好，能返回相关论文
- `"diffusion models in computer vision"` → 搜索这个精确的 5 词短语 → **过于具体，几乎肯定返回 0 篇**，因为很少有论文包含这个完整的字符串

用 **2-3 个核心关键词**做 query，用 `--category` 限定领域，而不是把领域名塞进 query。例如：

| 用户说 | 好的 query | 不好的 query |
|---|---|---|
| "diffusion models in computer vision" | `"diffusion models" --category cs.CV` | `"diffusion models in computer vision"` |
| "transformer attention variants" | `"transformer attention"` | `"transformer attention variants in NLP"` |
| "graph neural networks for molecules" | `"graph neural networks" --category cs.LG` | `"graph neural networks for molecular property prediction"` |

脚本会把 JSON 数组打印到 stdout。每篇论文包含字段：`id`、`title`、`authors`、`abstract`、`published`、`updated`、`categories`、`pdf_url`、`abs_url`。

**排序策略**：

- **始终使用 `relevance` 排序** —— arXiv 的 BM25 风格打分能保证结果确实与用户主题相关。`submittedDate` 排序返回的是某类目下最近提交的论文，与主题无关，结果多半跑偏
- 当用户要求 "recent" 论文或给定时间窗时，**同时**使用 `--sort-by relevance` 和 `--start-date`，以在保证相关性的同时限定时间范围。例如 "recent diffusion model papers" → `--sort-by relevance --start-date 2024-01-01`，而不是 `--sort-by submittedDate`
- 只有当用户明确要按时间顺序展示（例如 "show me papers in the order they were published"）时才用 `submittedDate`，这很罕见
- `lastUpdatedDate` 很少用，除非用户主动要求

**只跑一次搜索。** 不要因为结果看起来不够完美就换 query 重试 —— arXiv 的相关性排序就是这样。改 query 重试既浪费工具调用，又会撞到递归上限。如果结果真的为空（0 篇），告诉用户并建议他/她放宽主题或去掉类目过滤。

**如果脚本返回的论文数少于请求数**，那就是该 query 在 arXiv 上真实的命中数。不要凑数 —— 把实际数量告知用户并继续。

**如果脚本失败**（网络错误、arXiv 返回非 200），把具体错误告诉用户并停止。不要编造论文元数据。

**不要把搜索结果保存到文件** —— JSON 留在你上下文中供阶段 3 使用。整个工作流中唯一会落盘的文件是阶段 5 产出的最终报告。

### 阶段 3：并行抽取元数据

**你必须通过 `task` 工具把抽取工作委托给子智能体 —— 不要自己抽。** 这是硬性要求。具体来说：

- ❌ 不要写 `python -c "papers = [...]"` 或任何 Python / bash 脚本来处理论文
- ❌ 不要在自己的上下文里逐条读取摘要抽取元数据
- ❌ 不要用 `task` 以外的任何工具来跑这一阶段

相反，你必须调用 `task` 工具派生子智能体。原因是：在自己的上下文里抽取 10-50 篇论文会消耗大量 token，并降低阶段 4 的综合质量。每个子智能体在隔离的上下文里只处理自己那批论文，产出更干净的抽取结果。

将论文按每批约 5 篇切分，然后为每批调用 `task` 工具，`subagent_type: "general-purpose"`。每个子智能体接收论文摘要文本，返回结构化 JSON。

**并发上限：每轮最多 3 个子智能体。** DeerFlow 运行时硬限 `MAX_CONCURRENT_SUBAGENTS = 3`，同一轮里多出的派发会被静默丢弃 —— LLM 不会收到任何通知，所以请严格遵守下面的轮次策略。

**轮次策略 —— 用下面这张决策表，不要自己算怎么分**：

| 论文数 | 约 5 篇一批的批数 | 轮次 | 每轮子智能体数 |
|---|---|---|---|
| 1–5 | 1 批 | 1 轮 | 1 个 |
| 6–10 | 2 批 | 1 轮 | 2 个 |
| 11–15 | 3 批 | 1 轮 | 3 个 |
| 16–20 | 4 批 | 2 轮 | 3 + 1 |
| 21–25 | 5 批 | 2 轮 | 3 + 2 |
| 26–30 | 6 批 | 2 轮 | 3 + 3 |
| 31–35 | 7 批 | 3 轮 | 3 + 3 + 1 |
| 36–40 | 8 批 | 3 轮 | 3 + 3 + 2 |
| 41–45 | 9 批 | 3 轮 | 3 + 3 + 3 |
| 46–50 | 10 批 | 4 轮 | 3 + 3 + 3 + 1 |

**同一轮内派发的子智能体绝不能超过 3 个。** 当某行写 "2 轮 (3 + 1)" 时，意思是：第一轮并行派发 3 个子智能体，等全部完成后第二轮再派发 1 个。轮次在主智能体层面严格串行。

如果论文数落在两行之间（例如 23 篇），按下一行的布局来安排，但实际只派发需要的批数 —— 决策表给出的是形状，不是死规定。

**在主智能体层面完成切批**：阶段 2 已经拿到每篇论文的摘要，所以每个子智能体接收的都是纯文本。子智能体不需要访问网络或沙箱 —— 它们的全部工作就是读文本、返回 JSON。不要让子智能体重跑 `arxiv_search.py`，那样既浪费 token 又可能被限流。

**每个子智能体接收的内容**应该是这样的结构化 prompt：

```
Execute this task: extract structured metadata and key findings from the
following arXiv papers.

Papers:
[Paper 1]
arxiv_id: 1706.03762
title: Attention Is All You Need
authors: Ashish Vaswani, Noam Shazeer, ...
published: 2017-06-12
abstract: <full abstract text>

[Paper 2]
arxiv_id: ...
...

For each paper, return a JSON object with these fields:
- arxiv_id (string)
- title (string)
- authors (list of strings)
- published_date (string, YYYY-MM-DD)
- research_question (1 sentence, what problem the paper tackles)
- methodology (1-2 sentences, how they tackle it)
- key_findings (3-5 bullet points, what they actually found)
- limitations (1-2 sentences, what they acknowledge or what is obviously missing)

Return the result as a JSON array, one object per paper, in the same
order as the input. Do not include any text outside the JSON — no
preamble, no markdown fences, just the array.
```

**解析子智能体结果**：task 工具返回的是带固定前缀的字符串，形如 `Task Succeeded. Result: [...JSON...]`。在解析 JSON 之前先剥掉 `Task Succeeded. Result: `（或 `Task failed.` / `Task timed out.`）前缀。如果某一批失败或返回的 JSON 无法解析，记录下来、注明涉及哪些论文，然后继续处理剩余批 —— 不要因为一批坏掉就让整个综合失败。

所有轮次完成后，将每批的数组合并成一份论文元数据列表，保留顺序。

### 阶段 4：综合与排版

接下来产出最终的 SLR 报告。这里要做两件事：跨论文综合（主题分析）以及引用排版。

**跨论文综合**：报告不能只是罗列论文。至少要识别：

- **主题（Themes）**：3-6 个反复出现的研究方向、方法或问题框架
- **共识（Convergences）**：多篇论文一致认同的发现
- **分歧（Disagreements）**：论文结论不同或方法互不兼容之处
- **空白（Gaps）**：整个文献尚未覆盖的问题（经常出现在 "limitations" 字段里）

如果论文集太小或太异质，无法支持主题综合（例如 5 篇论文各自讲完全不同的子方向），请在报告里明确说明 —— 不要硬凑主题。

**引用排版**：具体格式依用户偏好而定。**只读**与用户请求格式对应的模板，不要三个全读：

- [templates/apa.md](templates/apa.md) —— APA 第 7 版。社会科学和多数 CS 期刊的默认格式。用户要求 APA 或未指定时使用
- [templates/ieee.md](templates/ieee.md) —— IEEE 数字引用。用户面向 IEEE 会议或期刊、或明确要求 IEEE 时使用
- [templates/bibtex.md](templates/bibtex.md) —— BibTeX 条目。用户提到 BibTeX、LaTeX 或需要机器可读引用时使用。**重要**：arXiv 论文应作为 `@misc` 而非 `@article` 引用 —— BibTeX 模板里有专门说明

每个模板既包含引用规则，也包含完整的报告结构（执行摘要、主题、单篇注释、参考文献、方法论章节）。报告正文严格按模板结构组织，再用阶段 3 抽出的元数据填充内容。

### 阶段 5：保存与展示

将完整报告保存到 `/mnt/user-data/outputs/slr-<topic-slug>-<YYYYMMDD>.md`，其中 `<topic-slug>` 是主题的小写连字符形式（例如 `transformer-attention`）。然后用 `present_files` 工具传入该路径，方便用户下载。

**在聊天消息中**，给出一段简短的预览，让用户能立即看到价值而不必打开文件：

1. **执行摘要** —— 报告顶部的 3-5 段，逐字摘出
2. **主题列表** —— 阶段 4 综合出的主题清单（只列主题名 + 一行说明，不必展开整节）
3. **论文数 + 文件指引** —— 例如 "Full report with 20 papers, per-paper annotations, and formatted references saved to `slr-transformer-attention-20260409.md`."

**不要**把 2000+ 字的整份报告直接贴到对话里 —— 单篇注释、参考文献、方法论都放在文件里。预览的作用是让用户一眼判断报告价值，决定是否打开。

## 示例

**示例 1：典型 SLR 请求**

用户："Do a systematic literature review of recent transformer attention variants, 20 papers, APA format."

你的流程：

1. 阶段 1：确认主题（transformer attention variants）、范围（20 篇，默认时间窗）、格式（APA）。如缺少某项只问 **一个**澄清问题（例如 "Any particular time window, or should I default to the last 3 years?"）。
2. 阶段 2：`arxiv_search.py "transformer attention" --max-results 20 --sort-by relevance --start-date 2023-01-01`。
3. 阶段 3：20 篇 → 第 1 轮 3 个子智能体 × 5 篇 = 15，第 2 轮 1 个子智能体 × 5 篇 = 5。汇总。
4. 阶段 4：读 `templates/apa.md`，按其结构写报告，填入主题和单篇注释。
5. 阶段 5：保存为 `slr-transformer-attention-20260409.md`，调用 `present_files`。

**示例 2：小集合 + 模糊请求**

用户："Survey a few papers on diffusion models for me."

你的流程：

1. 阶段 1："a few" 很模糊。一次性问一个问题："How many papers would you like — 10, 20, or 30? And any citation format preference (APA is the default)?"
2. 用户回答 "10, BibTeX"。
3. 阶段 2：`arxiv_search.py "diffusion models" --max-results 10 --category cs.CV`。
4. 阶段 3：10 篇 → 单轮 2 个子智能体 × 5 篇。
5. 阶段 4：读 `templates/bibtex.md`，用 `@misc`（不是 `@article`）排版。
6. 阶段 5：保存并展示。

**示例 3：超出范围**

用户："Here's one paper (https://arxiv.org/abs/1706.03762). Can you review it?"

这是单篇论文评审，不是文献综述。**不要**用本技能。改用 `academic-paper-review`。

## 注意事项

- **前置条件：`subagent_enabled` 必须为 `true`**。阶段 3 需要 `task` 工具做并行抽取。该工具只有在运行时配置 `config.configurable.subagent_enabled` 为 `true` 时才会出现在可用工具列表中，缺它阶段 3 就跑不起来
- **只搜 arXiv，这是设计选择**。本技能不查 Semantic Scholar、PubMed 或 Google Scholar。arXiv 覆盖了 CS / ML / 物理 / 数学的预印本主体，这正是 DeerFlow 用户最常要调研的。多源学术搜索应放在专门的 MCP 服务里，不放在本技能内
- **硬上限 50 篇**。这与阶段 3 的并发策略挂钩（每轮最多 3 个子智能体，每批约 5 篇，最多约 3 轮）。超过 50 篇会显著降低综合质量，更大的调研应按子主题拆开
- **阶段 3 必须有可用的子智能体**。本技能的并行抽取步骤强依赖 `task` 工具，该工具仅在 `subagent_enabled=true` 时可用。如果子智能体不可用，不要声称执行了阶段 3 的并行方案；请直接告诉用户必须先开启子智能体才能跑完整工作流，或建议把请求缩小 / 拆成更小的人工综述
- **子智能体的返回是字符串而非对象**。解析 JSON 之前先剥掉 `Task Succeeded. Result: ` / `Task failed.` / `Task timed out.` 前缀
- **`id` 字段是裸的 arXiv id**（如 `1706.03762`），不是 URL，也不带版本后缀。需要完整 URL 时用 `abs_url` / `pdf_url`
- **要的是综合，不是罗列**。最终报告必须识别主题并跨论文比较发现。一份只把论文一条条列下来的报告是失败模式 —— 找不到主题时请明确说明，不要硬编主题
