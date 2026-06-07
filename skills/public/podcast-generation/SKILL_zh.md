---
name: podcast-generation
description: Use this skill when the user requests to generate, create, or produce podcasts from text content. Converts written content into a two-host conversational podcast audio format with natural dialogue.
---

# 播客生成技能（Podcast Generation Skill）

## 概述

本技能从文本内容生成高质量播客音频。工作流包括：先产出结构化的 JSON 脚本（对话稿），再通过文本转语音合成生成音频。

## 核心能力

- 将任意文本内容（文章、报告、文档）转写为播客脚本
- 生成自然的男女双主持对话
- 通过 TTS 合成语音音频
- 将多个音频片段混音为最终播客 MP3 文件
- 同时支持英文和中文内容

## 工作流

### 第 1 步：理解需求

当用户请求生成播客时，先明确以下信息：

- 源内容：要转为播客的文本 / 文章 / 报告
- 语言：英文或中文（依据内容）
- 输出位置：保存生成结果的位置
- 无需检查 `/mnt/user-data` 下的目录

### 第 2 步：生成结构化脚本 JSON

在 `/mnt/user-data/workspace/` 下生成结构化 JSON 脚本文件，命名格式：`{descriptive-name}-script.json`

JSON 结构：

```json
{
  "locale": "en",
  "lines": [
    {"speaker": "male", "paragraph": "dialogue text"},
    {"speaker": "female", "paragraph": "dialogue text"}
  ]
}
```

### 第 3 步：执行生成

调用 Python 脚本：

```bash
python /mnt/skills/public/podcast-generation/scripts/generate.py \
  --script-file /mnt/user-data/workspace/script-file.json \
  --output-file /mnt/user-data/outputs/generated-podcast.mp3 \
  --transcript-file /mnt/user-data/outputs/generated-podcast-transcript.md
```

参数说明：

- `--script-file`：JSON 脚本文件的绝对路径（必填）
- `--output-file`：输出 MP3 文件的绝对路径（必填）
- `--transcript-file`：输出文字稿 Markdown 文件的绝对路径（可选，但建议提供）

> [!IMPORTANT]
> - 一次调用完整执行脚本。不要将工作流拆分为多个步骤。
> - 脚本会在内部完成所有 TTS API 调用和音频生成。
> - 不要阅读 Python 文件，直接带参数调用即可。
> - 始终带上 `--transcript-file` 以生成可供阅读的文字稿。

## 脚本 JSON 格式

脚本 JSON 文件必须遵循以下结构：

```json
{
  "title": "The History of Artificial Intelligence",
  "locale": "en",
  "lines": [
    {"speaker": "male", "paragraph": "Hello Deer! Welcome back to another episode."},
    {"speaker": "female", "paragraph": "Hey everyone! Today we have an exciting topic to discuss."},
    {"speaker": "male", "paragraph": "That's right! We're going to talk about..."}
  ]
}
```

字段说明：

- `title`：播客节目标题（可选，会作为文字稿的标题）
- `locale`：语言代码 —— "en" 代表英文，"zh" 代表中文
- `lines`：对话行数组
  - `speaker`：取值为 "male" 或 "female"
  - `paragraph`：当前说话人的对话文本

## 脚本编写指南

编写脚本 JSON 时，请遵循以下准则：

### 格式要求
- 只设置两位主持：男、女，自然交替
- 目标时长：约 10 分钟对话（约 40-60 行）
- 开头由男主持说出一句包含 "Hello Deer" 的问候

### 语气与风格
- 自然、口语化的对话 —— 像两个朋友在聊天
- 使用口语化表达和自然的承接词
- 避免过于正式或学术化的语气
- 加入反应、追问和自然的感叹

### 内容要求
- 两位主持频繁一来一回地交流
- 句子短小、易于口语跟读
- 仅使用纯文本 —— 输出中不要带 Markdown 格式
- 将技术概念转写为通俗语言
- 不出现数学公式、代码或复杂符号
- 让内容对纯音频听众有吸引力且易于理解
- 不要包含日期、作者名、文档结构等元信息

## 播客生成示例

用户请求："Generate a podcast about the history of artificial intelligence"

第 1 步：创建脚本文件 `/mnt/user-data/workspace/ai-history-script.json`：

```json
{
  "title": "The History of Artificial Intelligence",
  "locale": "en",
  "lines": [
    {"speaker": "male", "paragraph": "Hello Deer! Welcome back to another fascinating episode. Today we're diving into something that's literally shaping our future - the history of artificial intelligence."},
    {"speaker": "female", "paragraph": "Oh, I love this topic! You know, AI feels so modern, but it actually has roots going back over seventy years."},
    {"speaker": "male", "paragraph": "Exactly! It all started back in the 1950s. The term artificial intelligence was actually coined by John McCarthy in 1956 at a famous conference at Dartmouth."},
    {"speaker": "female", "paragraph": "Wait, so they were already thinking about machines that could think back then? That's incredible!"},
    {"speaker": "male", "paragraph": "Right? The early pioneers were so optimistic. They thought we'd have human-level AI within a generation."},
    {"speaker": "female", "paragraph": "But things didn't quite work out that way, did they?"},
    {"speaker": "male", "paragraph": "No, not at all. The 1970s brought what's called the first AI winter..."}
  ]
}
```

第 2 步：执行生成：

```bash
python /mnt/skills/public/podcast-generation/scripts/generate.py \
  --script-file /mnt/user-data/workspace/ai-history-script.json \
  --output-file /mnt/user-data/outputs/ai-history-podcast.mp3 \
  --transcript-file /mnt/user-data/outputs/ai-history-transcript.md
```

执行后会产生：

- `ai-history-podcast.mp3`：播客音频文件
- `ai-history-transcript.md`：可阅读的 Markdown 文字稿

## 特定模板

仅在匹配用户请求时读取以下模板文件。

- [Tech Explainer](templates/tech-explainer.md) —— 用于将技术文档和教程转为播客

## 输出格式

生成的播客采用 "Hello Deer" 格式：

- 两位主持：男、女各一
- 自然口语化对话
- 以 "Hello Deer" 问候开场
- 目标时长：约 10 分钟
- 主持交替发言，节奏流畅

## 输出处理

生成完成后：

- 播客和文字稿保存在 `/mnt/user-data/outputs/`
- 用 `present_files` 工具将播客 MP3 和文字稿 MD 一并交给用户
- 简要描述生成结果（主题、时长、主持数量）
- 主动询问是否需要调整重生成

## 环境要求

必须设置以下环境变量：

- `VOLCENGINE_TTS_APPID`：火山引擎 TTS 应用 ID
- `VOLCENGINE_TTS_ACCESS_TOKEN`：火山引擎 TTS 访问令牌
- `VOLCENGINE_TTS_CLUSTER`：火山引擎 TTS 集群（可选，默认 `volcano_tts`）

## 注意事项

- **始终在一次调用中执行完整流水线** —— 不必单步调试或担心超时
- 脚本 JSON 的语言应与内容语言一致（en 或 zh）
- 脚本中的技术内容应做简化以适配音频媒介
- 复杂符号（公式、代码）应在脚本中转为平实语言
- 内容较长时播客也会相应变长
