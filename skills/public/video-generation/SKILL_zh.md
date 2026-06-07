---
name: video-generation
description: Use this skill when the user requests to generate, create, or imagine videos. Supports structured prompts and reference image for guided generation.
---

# 视频生成技能

## 概述

本技能使用结构化提示词（prompt）和 Python 脚本生成高质量视频。工作流包括创建 JSON 格式的提示词，以及在可选的参考图引导下执行视频生成。

## 核心能力

- 为 AIGC 视频生成创建结构化的 JSON 提示词
- 支持使用参考图作为引导，或作为视频的首帧 / 末帧
- 通过自动化的 Python 脚本执行视频生成

## 工作流程

### 步骤 1：理解需求

当用户请求视频生成时，需要明确：

- 主体 / 内容：画面里应该有什么
- 风格偏好：美术风格、情绪、配色
- 技术规格：宽高比、构图、光影
- 参考图：是否有用于引导生成的图像
- 无需检查 `/mnt/user-data` 下的文件夹

### 步骤 2：创建结构化提示词

在 `/mnt/user-data/workspace/` 下生成一个结构化 JSON 文件，命名遵循 `{描述性名称}.json` 的模式。

### 步骤 3：创建参考图（当具备 image-generation 技能时可选）

为视频生成准备参考图。

- 如果只提供 1 张图片，则将其作为视频的引导帧

### 步骤 3：执行生成

调用 Python 脚本：
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --reference-images /path/to/ref1.jpg \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --aspect-ratio 16:9
```

参数：

- `--prompt-file`：JSON 提示词文件的绝对路径（必填）
- `--reference-images`：参考图的绝对路径（选填）
- `--output-file`：输出视频文件的绝对路径（必填）
- `--aspect-ratio`：生成视频的宽高比（选填，默认为 16:9）

[!NOTE]
**不要**直接阅读该 Python 文件，只需用参数调用即可。

## 视频生成示例

用户请求："Generate a short video clip depicting the opening scene from 'The Chronicles of Narnia: The Lion, the Witch and the Wardrobe'"（"生成一段简短的视频，描绘《纳尼亚传奇：狮子、女巫与魔衣橱》的开场场景"）

步骤 1：在网上搜索《纳尼亚传奇：狮子、女巫与魔衣橱》的开场场景

步骤 2：创建如下内容的 JSON 提示词文件：

```json
{
  "title": "The Chronicles of Narnia - Train Station Farewell",
  "background": {
    "description": "World War II evacuation scene at a crowded London train station. Steam and smoke fill the air as children are being sent to the countryside to escape the Blitz.",
    "era": "1940s wartime Britain",
    "location": "London railway station platform"
  },
  "characters": ["Mrs. Pevensie", "Lucy Pevensie"],
  "camera": {
    "type": "Close-up two-shot",
    "movement": "Static with subtle handheld movement",
    "angle": "Profile view, intimate framing",
    "focus": "Both faces in focus, background soft bokeh"
  },
  "dialogue": [
    {
      "character": "Mrs. Pevensie",
      "text": "You must be brave for me, darling. I'll come for you... I promise."
    },
    {
      "character": "Lucy Pevensie",
      "text": "I will be, mother. I promise."
    }
  ],
  "audio": [
    {
      "type": "Train whistle blows (signaling departure)",
      "volume": 1
    },
    {
      "type": "Strings swell emotionally, then fade",
      "volume": 0.5
    },
    {
      "type": "Ambient sound of the train station",
      "volume": 0.5
    }
  ]
}
```

步骤 3：使用 image-generation 技能生成参考图

加载 image-generation 技能，并按其规范生成单张参考图 `narnia-farewell-scene-01.jpg`。

步骤 4：使用 `generate.py` 脚本生成视频
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/narnia-farewell-scene.json \
  --reference-images /mnt/user-data/outputs/narnia-farewell-scene-01.jpg \
  --output-file /mnt/user-data/outputs/narnia-farewell-scene-01.mp4 \
  --aspect-ratio 16:9
```
> **不要**直接阅读该 Python 文件，只需用参数调用即可。

## 输出处理

生成完成后：

- 视频通常保存在 `/mnt/user-data/outputs/`
- 使用 `present_files` 工具与用户分享生成的视频（优先）以及参考图（如适用）
- 简要说明生成结果
- 在需要时主动提出迭代修改

## 注意事项

- 提示词始终使用英文，与用户的语言无关
- 使用 JSON 格式可保证提示词结构化、可解析
- 参考图能显著提升生成质量
- 为获得最佳效果，迭代式调整是常规做法
