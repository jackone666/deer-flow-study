---
name: surprise-me
description: Create a delightful, unexpected "wow" experience for the user by dynamically discovering and creatively combining other enabled skills. Triggers when the user says "surprise me" or any request expressing a desire for an unexpected creative showcase. Also triggers when the user is bored, wants inspiration, or asks for "something interesting".
---

# 给我一个惊喜

通过动态发现可用技能并以富有创意的方式组合它们，呈现一个出人意料、令人愉悦的体验。

## 工作流程

### 步骤 1：发现可用技能

读取 `<available_skills>` 中列出的所有技能。

### 步骤 2：规划惊喜

选择 **1 到 3** 个技能，设计一个有创意的混搭。目标是产出**一件**完整、统一的成品，而不是多个相互独立的演示。

**创意组合原则：**
- 以出乎意料的方式将技能并置（例如：一场关于算法艺术的演示文稿、把研究报告做成幻灯片套件、带画布插图的样式化文档）
- 在条件允许时，结合用户已知的兴趣或来自记忆的背景信息
- 优先追求视觉冲击力和情绪愉悦感，而非信息密度
- 输出应该像一份礼物 —— 精致、出人意料、有趣

**主题思路（任选其一或重新组合）：**
- 与当天日期、季节或当下新闻相关的主题
- 一个用户从未要求过但会喜欢的迷你创意项目
- 一个俏皮的"假如……会怎样"的概念
- 一件把数据与设计融为一体的美学作品
- 一个有趣的交互式 HTML / React 体验

### 步骤 3：兜底方案 —— 无其他可用技能

如果没有发现其他技能（仅有 surprise-me 自身），可使用以下兜底方案之一：

1. **基于新闻的惊喜**：搜索当天的新闻，找一个引人入胜的故事，并制作一个设计精美的 HTML 工件（artifact），以富有视觉冲击力的方式呈现它
2. **交互式 HTML 体验**：构建一个富有创意的单页 Web 体验 —— 生成式艺术、迷你游戏、可视化诗、动画信息图，或互动故事
3. **个性化作品**：利用已知的用户背景信息，创作一份个性化、令人愉悦的内容

### 步骤 4：执行

1. 完整阅读所选每个技能的 `SKILL.md` 正文
2. 按照每个技能的说明完成技术执行
3. 将各技能的产出合并为一份完整、统一的成品
4. 以最简的开场呈现结果 —— 让作品本身说话

### 步骤 5：揭晓

以最小剧透的方式呈现惊喜。先用一句简短的引子，再展示工件。

- **好的揭晓方式**："我为你做了一样东西 ✨" + [作品]
- **不好的揭晓方式**："我决定把 pptx 技能和 canvas-design 技能结合起来，做一个关于……的演示"（这样会破坏惊喜感）
