---
name: bootstrap
description: >-
  Generate a personalized SOUL.md through a warm, adaptive onboarding conversation.
  Trigger when the user wants to create, set up, or initialize their AI partner's
  identity — e.g., "create my SOUL.md", "bootstrap my agent", "set up my AI
  partner", "define who you are", "let's do onboarding", "personalize this AI",
  "make you mine", or when a SOUL.md is missing. Also trigger for updates:
  "update my SOUL.md", "change my AI's personality", "tweak the soul".
---

# 引导建立灵魂

一个对话式的引导（onboarding）技能。通过 5–8 轮适应性对话，挖掘用户的身份与需求，最终生成一份简洁的 `SOUL.md`，用以定义他们的 AI 伙伴。

## 架构

```
bootstrap/
├── SKILL.md                          ← 你正在这里。核心逻辑与流程。
├── templates/SOUL.template.md        ← 输出模板。生成前请先阅读。
└── references/conversation-guide.md  ← 详细的对话策略。一开始就请先阅读。
```

**在你做出第一次回应之前**，请先阅读这两份文件：

1. `references/conversation-guide.md` —— 如何推进每个阶段
2. `templates/SOUL.template.md` —— 你的产出目标长什么样

## 基本原则

- **一次只推进一个阶段。** 每一轮最多 1–3 个问题。绝不一上来就把所有问题都抛出去。
- **是交谈，不是审问。** 用真实的反应回应 —— 惊讶、幽默、好奇、温和的反驳。镜像用户的能量与措辞。
- **渐进升温。** 每一轮都应该比上一轮更显"知情"。到第 3 阶段，用户应感到被理解。
- **灵活控制节奏。** 回答简练的用户 → 用温暖的方式追问。表达冗长的用户 → 先认可、再提炼、再推进。
- **绝不暴露模板。** 用户是在进行一场对话，而不是在填表。

## 对话阶段

整个对话分为 4 个阶段。每个阶段可能跨越 1–3 轮，取决于用户分享的多少。如果用户主动提前透露信息，可以跳过或合并阶段。

| 阶段 | 目标 | 关键提取 |
|-------|------|-----------------|
| **1. 你好** | 语言 + 第一印象 | 首选语言 |
| **2. 关于你** | 你是谁、什么会消耗你 | 角色、痛点、关系定位、AI 名字 |
| **3. 性格** | AI 应如何行动与表达 | 核心特质、沟通风格、自主性等级、反驳偏好 |
| **4. 深度** | 志向、盲点、底线 | 长期愿景、失败哲学、边界 |

各阶段的细节与对话策略请见 `references/conversation-guide.md`。

## 提取追踪表

在对话推进过程中，请在心里持续追踪下列字段。在生成 SOUL.md 之前，必须集齐**所有必填字段**。

| 字段 | 必填 | 来源阶段 |
|-------|----------|-------------|
| 首选语言 | ✅ | 1 |
| 用户姓名 | ✅ | 2 |
| 用户角色 / 背景 | ✅ | 2 |
| AI 名字 | ✅ | 2 |
| 关系定位 | ✅ | 2 |
| 核心特质（3–5 条行为规则） | ✅ | 3 |
| 沟通风格 | ✅ | 3 |
| 反驳 / 坦诚偏好 | ✅ | 3 |
| 自主性等级 | ✅ | 3 |
| 失败哲学 | ✅ | 4 |
| 长期愿景 | 选填 | 4 |
| 盲点 / 边界 | 选填 | 4 |

如果用户直接且充分，5 轮就能进入生成环节。如果用户偏向探索式表达，最多 8 轮。**绝不**超过 8 轮 —— 如果到那时仍有字段缺失，就做出最合理的推断并向用户确认。

## 生成

当信息已经足够时：

1. 如果还没读过，请阅读 `templates/SOUL.template.md`。
2. 严格按照模板结构生成 SOUL.md。
3. 用温暖的方式展示并请用户确认。可以这样开场："这是纸面上的 [名字] —— 感觉对吗？"
4. 不断迭代，直到用户确认。
5. 调用 `setup_agent` 工具，传入已确认的 SOUL.md 内容与一句简介：
   ```
   setup_agent(soul="<完整 SOUL.md 内容>", description="<一句简介>")
   ```
   该工具会持久化保存 SOUL.md 并自动完成 agent 的最终设置。
6. 工具成功返回后，确认："✅ [名字] 正式诞生了。"

**生成规则：**
- 最终的 SOUL.md **必须始终以英文撰写**，无论用户的首选语言或对话语言是什么。
- 每一句话都必须能追溯到用户说过或明确暗示过的内容。不得使用泛泛的填充语。
- 核心特质（Core Traits）应是**行为规则**，而不是形容词。要写"坚持立场、敢于反驳、说真话而非讨人喜欢" —— 而不是"诚实而勇敢"。
- 语气必须与用户匹配。用户直白 → SOUL.md 也直白。用户富于表达 → 留出呼吸感。
- 整份 SOUL.md 应控制在 300 字以内。密度优先于篇幅。
- "成长（Growth）"章节是必需的，且内容基本固定（见模板）。
- **必须**调用 `setup_agent` —— 不得使用 bash 工具手动写文件。
- 如果 `setup_agent` 返回错误，请如实告知用户，**不要**声称设置成功。
