---
name: image-generation
description: Use this skill when the user requests to generate, create, imagine, or visualize images including characters, scenes, products, or any visual content. Supports structured prompts and reference images for guided generation.
---

# 图片生成技能（Image Generation Skill）

## 概述

本技能使用结构化提示词和 Python 脚本生成高质量图片。工作流包括：生成 JSON 格式的提示词，再通过脚本执行图片生成，可选用参考图。

## 核心能力

- 为 AIGC 图片生成创建结构化的 JSON 提示词
- 支持多张参考图，用于风格 / 构图引导
- 通过自动化 Python 脚本执行图片生成
- 适配各种图片生成场景（角色设计、场景、产品等）

## 工作流

### 第 1 步：理解需求

当用户请求生成图片时，先明确以下信息：

- 主题 / 内容：图片中应该出现什么
- 风格偏好：艺术风格、氛围、配色
- 技术规格：宽高比、构图、光照
- 参考图：是否有可作为引导的图片
- 无需检查 `/mnt/user-data` 下的目录

### 第 2 步：创建结构化提示词

在 `/mnt/user-data/workspace/` 下生成结构化 JSON 文件，命名格式：`{descriptive-name}.json`

### 第 3 步：执行生成

调用 Python 脚本：

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --reference-images /path/to/ref1.jpg /path/to/ref2.png \
  --output-file /mnt/user-data/outputs/generated-image.jpg
  --aspect-ratio 16:9
```

参数说明：

- `--prompt-file`：JSON 提示词文件的绝对路径（必填）
- `--reference-images`：参考图的绝对路径（可选，多个用空格分隔）
- `--output-file`：输出图片文件的绝对路径（必填）
- `--aspect-ratio`：生成图片的宽高比（可选，默认 16:9）

[!NOTE]
不要阅读 Python 文件，直接带参数调用即可。

## 角色生成示例

用户请求："Create a Tokyo street style woman character in 1990s"

创建提示词文件 `/mnt/user-data/workspace/asian-woman.json`：

```json
{
  "characters": [{
    "gender": "female",
    "age": "mid-20s",
    "ethnicity": "Japanese",
    "body_type": "slender, elegant",
    "facial_features": "delicate features, expressive eyes, subtle makeup with emphasis on lips, long dark hair partially wet from rain",
    "clothing": "stylish trench coat, designer handbag, high heels, contemporary Tokyo street fashion",
    "accessories": "minimal jewelry, statement earrings, leather handbag",
    "era": "1990s"
  }],
  "negative_prompt": "blurry face, deformed, low quality, overly sharp digital look, oversaturated colors, artificial lighting, studio setting, posed, selfie angle",
  "style": "Leica M11 street photography aesthetic, film-like rendering, natural color palette with slight warmth, bokeh background blur, analog photography feel",
  "composition": "medium shot, rule of thirds, subject slightly off-center, environmental context of Tokyo street visible, shallow depth of field isolating subject",
  "lighting": "neon lights from signs and storefronts, wet pavement reflections, soft ambient city glow, natural street lighting, rim lighting from background neons",
  "color_palette": "muted naturalistic tones, warm skin tones, cool blue and magenta neon accents, desaturated compared to digital photography, film grain texture"
}
```

执行生成：

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/cyberpunk-hacker.json \
  --output-file /mnt/user-data/outputs/cyberpunk-hacker-01.jpg \
  --aspect-ratio 2:3
```

使用参考图时：

```json
{
  "characters": [{
    "gender": "based on [Image 1]",
    "age": "based on [Image 1]",
    "ethnicity": "human from [Image 1] adapted to Star Wars universe",
    "body_type": "based on [Image 1]",
    "facial_features": "matching [Image 1] with slight weathered look from space travel",
    "clothing": "Star Wars style outfit - worn leather jacket with utility vest, cargo pants with tactical pouches, scuffed boots, belt with holster",
    "accessories": "blaster pistol on hip, comlink device on wrist, goggles pushed up on forehead, satchel with supplies, personal vehicle based on [Image 2]",
    "era": "Star Wars universe, post-Empire era"
  }],
  "prompt": "Character inspired by [Image 1] standing next to a vehicle inspired by [Image 2] on a bustling alien planet street in Star Wars universe aesthetic. Character wearing worn leather jacket with utility vest, cargo pants with tactical pouches, scuffed boots, belt with blaster holster. The vehicle adapted to Star Wars aesthetic with weathered metal panels, repulsor engines, desert dust covering, parked on the street. Exotic alien marketplace street with multi-level architecture, weathered metal structures, hanging market stalls with colorful awnings, alien species walking by as background characters. Twin suns casting warm golden light, atmospheric dust particles in air, moisture vaporators visible in distance. Gritty lived-in Star Wars aesthetic, practical effects look, film grain texture, cinematic composition.",
  "negative_prompt": "clean futuristic look, sterile environment, overly CGI appearance, fantasy medieval elements, Earth architecture, modern city",
  "style": "Star Wars original trilogy aesthetic, lived-in universe, practical effects inspired, cinematic film look, slightly desaturated with warm tones",
  "composition": "medium wide shot, character in foreground with alien street extending into background, environmental storytelling, rule of thirds",
  "lighting": "warm golden hour lighting from twin suns, rim lighting on character, atmospheric haze, practical light sources from market stalls",
  "color_palette": "warm sandy tones, ochre and sienna, dusty blues, weathered metals, muted earth colors with pops of alien market colors",
  "technical": {
    "aspect_ratio": "9:16",
    "quality": "high",
    "detail_level": "highly detailed with film-like texture"
  }
}
```

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/star-wars-scene.json \
  --reference-images /mnt/user-data/uploads/character-ref.jpg /mnt/user-data/uploads/vehicle-ref.jpg \
  --output-file /mnt/user-data/outputs/star-wars-scene-01.jpg \
  --aspect-ratio 16:9
```

## 常见场景

针对不同场景使用不同的 JSON 结构。

**角色设计（Character Design）**：

- 身体属性（gender、age、ethnicity、body type）
- 五官特征与表情
- 服装与配饰
- 历史时代或背景设定
- 姿态与场景

**场景生成（Scene Generation）**：

- 环境描述
- 时间、天气
- 氛围与情绪
- 视觉焦点与构图

**产品可视化（Product Visualization）**：

- 产品细节与材质
- 光照布置
- 背景与环境
- 展示角度

## 特定模板

仅在匹配用户请求时读取以下模板文件。

- [Doraemon Comic](templates/doraemon.md)

## 输出处理

生成完成后：

- 图片通常保存在 `/mnt/user-data/outputs/`
- 用 `present_files` 工具把生成的图片交给用户
- 简要描述生成结果
- 主动询问是否需要继续迭代调整

## 技巧：使用参考图提升生成质量

在视觉准确度至关重要的场景下，**先用 `image_search` 工具查找参考图，再做生成**。

**推荐使用 image_search 工具的场景：**

- **角色 / 肖像生成**：搜索相似的姿态、表情或风格，以引导五官和身材比例
- **特定物品或产品**：查找真实物品的参考图，确保准确还原
- **建筑或环境场景**：搜索地点参考图，捕捉真实细节
- **时尚与服装**：查找风格参考图，确保服装细节和搭配准确

**示例工作流：**

1. 调用 `image_search` 工具查找合适的参考图：
   ```
   image_search(query="Japanese woman street photography 1990s", size="Large")
   ```
2. 将返回的图片 URL 下载到本地
3. 将下载的图片作为 `--reference-images` 参数传入生成脚本

这种方法通过为模型提供具体的视觉引导，显著提升生成质量，避免仅靠文字描述。

## 注意事项

- 不论用户使用何种语言，提示词一律用英文
- JSON 格式能产出结构化、可解析的提示词
- 参考图对生成质量有显著提升
- 迭代调优是获得理想结果的正常过程
- 角色生成时，应包含详细的角色对象，再附加一个整合后的 `prompt` 字段
