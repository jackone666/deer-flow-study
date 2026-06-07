---
name: find-skills
description: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill.
---

# 发现技能

本技能用于在开放的 agent 技能生态中帮助用户发现并安装可用的技能。

## 何时使用本技能

在以下情况使用本技能：

- 用户提问 "how do I do X"（"我要怎么完成 X"），而 X 可能是一个有现成技能的常见任务
- 用户说 "find a skill for X" 或 "is there a skill for X"（"找一个能……的技能"）
- 用户问 "can you do X"，而 X 是一项专门的能力
- 用户表达出希望扩展 agent 能力的意愿
- 用户希望搜索工具、模板或工作流
- 用户提到希望能在某个特定领域（设计、测试、部署等）获得帮助

## 什么是 Skills CLI？

Skills CLI（`npx skills`）是开放 agent 技能生态的包管理器。技能以模块化方式扩展 agent 的能力，提供专门的知识、工作流和工具。

**关键命令：**

- `npx skills find [query]` —— 通过关键词交互式地搜索技能
- `npx skills check` —— 检查技能是否有更新
- `npx skills update` —— 更新所有已安装的技能

**浏览技能：** https://skills.sh/

## 如何帮助用户发现技能

### 步骤 1：理解用户需求

当用户请求帮助时，需要明确：

1. 所属领域（例如 React、测试、设计、部署）
2. 具体任务（例如编写测试、创建动画、审查 PR）
3. 这是否是一个足够常见、大概率已有对应技能的任务

### 步骤 2：搜索技能

使用相关关键词运行 find 命令：

```bash
npx skills find [query]
```

例如：

- 用户问 "how do I make my React app faster?"（"我如何让 React 应用更快？"）→ `npx skills find react performance`
- 用户问 "can you help me with PR reviews?"（"你能帮我审查 PR 吗？"）→ `npx skills find pr review`
- 用户问 "I need to create a changelog"（"我要写一份更新日志"）→ `npx skills find changelog`

命令的返回结果形如：

```
Install with bash /path/to/skill/scripts/install-skill.sh vercel-labs/agent-skills@vercel-react-best-practices

vercel-labs/agent-skills@vercel-react-best-practices
└ https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices
```

### 步骤 3：向用户呈现候选

当找到相关技能时，向用户展示以下信息：

1. 技能名称与功能简介
2. 用户可运行的安装命令
3. 访问 skills.sh 查看更多信息的链接

回复示例：

```
I found a skill that might help! The "vercel-react-best-practices" skill provides
React and Next.js performance optimization guidelines from Vercel Engineering.

To install it:
bash /path/to/skill/scripts/install-skill.sh vercel-labs/agent-skills@vercel-react-best-practices

Learn more: https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices
```

### 步骤 4：安装技能

如果用户希望继续，使用 `install-skill.sh` 脚本安装技能，并自动将其链接到当前项目：

```bash
bash /path/to/skill/scripts/install-skill.sh <owner/repo@skill-name>
```

例如，用户希望安装 `vercel-react-best-practices`：

```bash
bash /path/to/skill/scripts/install-skill.sh vercel-labs/agent-skills@vercel-react-best-practices
```

脚本会将技能全局安装到 `skills/custom/` 目录。

## 常见技能分类

搜索时，可以参考以下常见分类：

| 分类 | 示例查询 |
| --------------- | ---------------------------------------- |
| Web 开发 | react、nextjs、typescript、css、tailwind |
| 测试 | testing、jest、playwright、e2e |
| 运维 | deploy、docker、kubernetes、ci-cd |
| 文档 | docs、readme、changelog、api-docs |
| 代码质量 | review、lint、refactor、best-practices |
| 设计 | ui、ux、design-system、accessibility |
| 生产力 | workflow、automation、git |

## 高效搜索的小贴士

1. **使用具体的关键词**："react testing" 优于单用 "testing"
2. **尝试不同说法**：如果 "deploy" 没有结果，试试 "deployment" 或 "ci-cd"
3. **关注热门来源**：很多技能来自 `vercel-labs/agent-skills` 或 `ComposioHQ/awesome-claude-skills`

## 找不到技能时

如果没有找到相关技能：

1. 坦诚告知没有找到现成技能
2. 主动表示可以凭借通用能力直接帮助完成此任务
3. 建议用户可以用 `npx skills init` 创建自己的技能

示例：

```
I searched for skills related to "xyz" but didn't find any matches.
I can still help you with this task directly! Would you like me to proceed?

If this is something you do often, you could create your own skill:
npx skills init my-xyz-skill
```
