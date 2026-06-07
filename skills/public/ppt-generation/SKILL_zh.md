---
name: ppt-generation
description: Use this skill when the user requests to generate, create, or make presentations (PPT/PPTX). Creates visually rich slides by generating images for each slide and composing them into a PowerPoint file.
---

# PPT 生成技能（PPT Generation Skill）

## 概述（Overview）

本技能通过为每张幻灯片生成 AI 图像，再将这些图像合成为 PowerPoint 文件，从而产出专业的演示文稿。工作流包括：先以统一的视觉风格规划演示文稿结构，再依序生成幻灯片图像（以相邻上一张为风格参考），最后将所有图像合成为最终演示文稿。

## 核心能力（Core Capabilities）

- 规划多页演示文稿结构，确保视觉风格统一
- 支持多种演示风格：商务（Business）、学术（Academic）、极简（Minimal）、苹果 Keynote、创意（Creative）
- 使用 image-generation 技能为每张幻灯片生成专属 AI 图像
- 通过将上一张幻灯片作为参考图来保持视觉一致性
- 将图像合成为专业的 PPTX 文件

## 演示风格（Presentation Styles）

创建演示文稿方案时，从以下风格中择一：

| 风格 | 描述 | 最适用场景 |
|-------|-------------|----------|
| **glassmorphism（玻璃拟态）** | 磨砂玻璃面板、模糊效果、漂浮半透明卡片、鲜艳的渐变背景、通过层次营造深度感 | 科技产品、AI/SaaS 演示、未来感路演 |
| **dark-premium（深色高级）** | 浓郁黑色背景（#0a0a0a）、发光的强调色、细腻光晕、奢华品牌美学 | 高端产品、高管演示、奢侈品牌 |
| **gradient-modern（渐变现代）** | 大胆的网格渐变、流畅的色彩过渡、当代字体排印、鲜活而不失雅致 | 创业公司、创意机构、品牌发布会 |
| **neo-brutalist（新粗野主义）** | 粗犷醒目的字体、高对比度、刻意的"丑"美学、反设计即设计、孟菲斯风格 | 锐意进取的品牌、面向 Z 世代、颠覆式创业 |
| **3d-isometric（3D 等距）** | 干净的等距插画、漂浮的 3D 元素、柔和阴影、科技感美学 | 科技讲解、产品功能、SaaS 演示 |
| **editorial（编辑设计）** | 杂志级版面、考究的字体层级、戏剧性摄影、Vogue/Bloomberg 美学 | 年报、奢侈品牌、思想领导力内容 |
| **minimal-swiss（极简瑞士）** | 网格化精准、Helvetica 风字体、大胆留白、永恒的现代主义 | 建筑、设计公司、高端咨询 |
| **keynote（苹果 Keynote）** | 苹果风格美学、醒目的字体、戏剧性图像、高对比度、影院级观感 | 主题演讲、产品发布、励志演讲 |

## 工作流（Workflow）

### 步骤 1：理解需求（Understand Requirements）

当用户请求生成演示文稿时，需明确：

- 主题：演示文稿关于什么
- 张数：需要多少张（默认：5-10 张）
- **风格**：business / academic / minimal / keynote / creative
- 宽高比：标准（16:9）或经典（4:3）
- 内容大纲：每张幻灯片的关键要点
- 无需检查 `/mnt/user-data` 下的文件夹

### 步骤 2：创建演示方案（Create Presentation Plan）

在 `/mnt/user-data/workspace/` 下创建一个 JSON 文件描述演示结构。**重要**：务必包含 `style` 字段以定义整体视觉一致性。

```json
{
  "title": "Presentation Title",
  "style": "keynote",
  "style_guidelines": {
    "color_palette": "Deep black backgrounds, white text, single accent color (blue or orange)",
    "typography": "Bold sans-serif headlines, clean body text, dramatic size contrast",
    "imagery": "High-quality photography, full-bleed images, cinematic composition",
    "layout": "Generous whitespace, centered focus, minimal elements per slide"
  },
  "aspect_ratio": "16:9",
  "slides": [
    {
      "slide_number": 1,
      "type": "title",
      "title": "Main Title",
      "subtitle": "Subtitle or tagline",
      "visual_description": "Detailed description for image generation"
    },
    {
      "slide_number": 2,
      "type": "content",
      "title": "Slide Title",
      "key_points": ["Point 1", "Point 2", "Point 3"],
      "visual_description": "Detailed description for image generation"
    }
  ]
}
```

### 步骤 3：依序生成幻灯片图像（Generate Slide Images Sequentially）

**IMPORTANT**：必须**严格按顺序逐张**生成幻灯片。切勿并行或批量生成图像。每张幻灯片都依赖上一张的输出作为参考图。并行生成会破坏视觉一致性，属于不允许的操作。

1. 读取 image-generation 技能：`/mnt/skills/public/image-generation/SKILL.md`

2. **对于第一张幻灯片（slide 1）**，需构造一个能奠定视觉风格的提示词：

```json
{
  "prompt": "Professional presentation slide. [style_guidelines from plan]. Title: 'Your Title'. [visual_description]. This slide establishes the visual language for the entire presentation.",
  "style": "[Based on chosen style - e.g., Apple Keynote aesthetic, dramatic lighting, cinematic]",
  "composition": "Clean layout with clear text hierarchy, [style-specific composition]",
  "color_palette": "[From style_guidelines]",
  "typography": "[From style_guidelines]"
}
```

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/slide-01-prompt.json \
  --output-file /mnt/user-data/outputs/slide-01.jpg \
  --aspect-ratio 16:9
```

3. **对于后续幻灯片（slide 2+）**，将**上一张**作为参考图：

```json
{
  "prompt": "Professional presentation slide continuing the visual style from the reference image. Maintain the same color palette, typography style, and overall aesthetic. Title: 'Slide Title'. [visual_description]. Keep visual consistency with the reference.",
  "style": "Match the style of the reference image exactly",
  "composition": "Similar layout principles as reference, adapted for this content",
  "color_palette": "Same as reference image",
  "consistency_note": "This slide must look like it belongs in the same presentation as the reference image"
}
```

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/slide-02-prompt.json \
  --reference-images /mnt/user-data/outputs/slide-01.jpg \
  --output-file /mnt/user-data/outputs/slide-02.jpg \
  --aspect-ratio 16:9
```

4. **继续生成剩余幻灯片**，始终以前一张为参考：

```bash
# Slide 3 references slide 2
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/slide-03-prompt.json \
  --reference-images /mnt/user-data/outputs/slide-02.jpg \
  --output-file /mnt/user-data/outputs/slide-03.jpg \
  --aspect-ratio 16:9

# Slide 4 references slide 3
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/slide-04-prompt.json \
  --reference-images /mnt/user-data/outputs/slide-03.jpg \
  --output-file /mnt/user-data/outputs/slide-04.jpg \
  --aspect-ratio 16:9
```

### 步骤 4：合成 PPT（Compose PPT）

所有幻灯片图像生成完毕后，调用合成脚本：

```bash
python /mnt/skills/public/ppt-generation/scripts/generate.py \
  --plan-file /mnt/user-data/workspace/presentation-plan.json \
  --slide-images /mnt/user-data/outputs/slide-01.jpg /mnt/user-data/outputs/slide-02.jpg /mnt/user-data/outputs/slide-03.jpg \
  --output-file /mnt/user-data/outputs/presentation.pptx
```

参数：

- `--plan-file`：演示方案 JSON 文件的绝对路径（必填）
- `--slide-images`：按顺序排列的幻灯片图像绝对路径（必填，空格分隔）
- `--output-file`：输出 PPTX 文件的绝对路径（必填）

[!NOTE]
不要阅读 python 文件本身，只需按参数调用即可。

## 完整示例：玻璃拟态风格（最现代前卫）

用户请求："Create a presentation about AI product launch"

### 步骤 1：创建演示方案

创建 `/mnt/user-data/workspace/ai-product-plan.json`：
```json
{
  "title": "Introducing Nova AI",
  "style": "glassmorphism",
  "style_guidelines": {
    "color_palette": "Vibrant purple-to-cyan gradient background (#667eea→#00d4ff), frosted glass panels with 15-20% white opacity, electric accents",
    "typography": "SF Pro Display style, bold 700 weight white titles with subtle text-shadow, clean 400 weight body text, excellent contrast on glass",
    "imagery": "Abstract 3D glass spheres, floating translucent geometric shapes, soft luminous orbs, depth through layered transparency",
    "layout": "Centered frosted glass cards with 32px rounded corners, 48-64px padding, floating above gradient, layered depth with soft shadows",
    "effects": "Backdrop blur 20-40px on glass panels, subtle white border glow, soft colored shadows matching gradient, light refraction effects",
    "visual_language": "Apple Vision Pro / visionOS aesthetic, premium depth through transparency, futuristic yet approachable, 2024 design trends"
  },
  "aspect_ratio": "16:9",
  "slides": [
    {
      "slide_number": 1,
      "type": "title",
      "title": "Introducing Nova AI",
      "subtitle": "Intelligence, Reimagined",
      "visual_description": "Stunning gradient background flowing from deep purple (#667eea) through magenta to cyan (#00d4ff). Center: large frosted glass panel with strong backdrop blur, containing bold white title 'Introducing Nova AI' and lighter subtitle. Floating 3D glass spheres and abstract shapes around the card creating depth. Soft glow emanating from behind the glass panel. Premium visionOS aesthetic. The glass card has subtle white border (1px rgba 255,255,255,0.3) and soft purple-tinted shadow."
    },
    {
      "slide_number": 2,
      "type": "content",
      "title": "Why Nova?",
      "key_points": ["10x faster processing", "Human-like understanding", "Enterprise-grade security"],
      "visual_description": "Same purple-cyan gradient background. Left side: floating frosted glass card with title 'Why Nova?' in bold white, three key points below with subtle glass pill badges. Right side: abstract 3D visualization of neural network as interconnected glass nodes with soft glow. Floating translucent geometric shapes (icosahedrons, tori) adding depth. Consistent glassmorphism aesthetic with previous slide."
    },
    {
      "slide_number": 3,
      "type": "content",
      "title": "How It Works",
      "key_points": ["Natural language input", "Multi-modal processing", "Instant insights"],
      "visual_description": "Gradient background consistent with previous slides. Central composition: three stacked frosted glass cards at slight angles showing the workflow steps, connected by soft glowing lines. Each card has an abstract icon. Floating glass orbs and light particles around the composition. Title 'How It Works' in bold white at top. Depth created through card layering and transparency."
    },
    {
      "slide_number": 4,
      "type": "content",
      "title": "Built for Scale",
      "key_points": ["1M+ concurrent users", "99.99% uptime", "Global infrastructure"],
      "visual_description": "Same gradient background. Asymmetric layout: right side features large frosted glass panel with metrics displayed in bold typography. Left side: abstract 3D globe made of glass panels and connection lines, representing global scale. Floating data visualization elements as small glass cards with numbers. Soft ambient glow throughout. Premium tech aesthetic."
    },
    {
      "slide_number": 5,
      "type": "conclusion",
      "title": "The Future Starts Now",
      "subtitle": "Join the waitlist",
      "visual_description": "Dramatic finale slide. Gradient background with slightly increased vibrancy. Central frosted glass card with bold title 'The Future Starts Now' and call-to-action subtitle. Behind the card: burst of soft light rays and floating glass particles creating celebration effect. Multiple layered glass shapes creating depth. The most visually impactful slide while maintaining style consistency."
    }
  ]
}
```

### 步骤 2：读取 image-generation 技能

读取 `/mnt/skills/public/image-generation/SKILL.md` 以了解如何生成图像。

### 步骤 3：依序生成幻灯片图像并串联参考

**Slide 1 —— 标题（奠定视觉语言）：**

创建 `/mnt/user-data/workspace/nova-slide-01.json`：
```json
{
  "prompt": "Ultra-premium presentation title slide with glassmorphism design. Background: smooth flowing gradient from deep purple (#667eea) through magenta (#f093fb) to cyan (#00d4ff), soft and vibrant. Center: large frosted glass panel with strong backdrop blur effect, rounded corners 32px, containing bold white sans-serif title 'Introducing Nova AI' (72pt, SF Pro Display style, font-weight 700) with subtle text shadow, subtitle 'Intelligence, Reimagined' below in lighter weight. The glass panel has subtle white border (1px rgba 255,255,255,0.25) and soft purple-tinted drop shadow. Floating around the card: 3D glass spheres with refraction, translucent geometric shapes (icosahedrons, abstract blobs), creating depth and dimension. Soft luminous glow emanating from behind the glass panel. Small floating particles of light. Apple Vision Pro / visionOS UI aesthetic. Professional presentation slide, 16:9 aspect ratio. Hyper-modern, premium tech product launch feel.",
  "style": "Glassmorphism, visionOS aesthetic, Apple Vision Pro UI style, premium tech, 2024 design trends",
  "composition": "Centered glass card as focal point, floating 3D elements creating depth at edges, 40% negative space, clear visual hierarchy",
  "lighting": "Soft ambient glow from gradient, light refraction through glass elements, subtle rim lighting on 3D shapes",
  "color_palette": "Purple gradient #667eea, magenta #f093fb, cyan #00d4ff, frosted white rgba(255,255,255,0.15), pure white text #ffffff",
  "effects": "Backdrop blur on glass panels, soft drop shadows with color tint, light refraction, subtle noise texture on glass, floating particles"
}
```

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/nova-slide-01.json \
  --output-file /mnt/user-data/outputs/nova-slide-01.jpg \
  --aspect-ratio 16:9
```

**Slide 2 —— 内容（必须以 slide 1 为参考以保持一致）：**

创建 `/mnt/user-data/workspace/nova-slide-02.json`：
```json
{
  "prompt": "Presentation slide continuing EXACT visual style from reference image. SAME purple-to-cyan gradient background, SAME glassmorphism aesthetic, SAME typography style. Left side: frosted glass card with backdrop blur containing title 'Why Nova?' in bold white (matching reference font style), three feature points as subtle glass pill badges below. Right side: abstract 3D neural network visualization made of interconnected glass nodes with soft cyan glow, floating in space. Floating translucent geometric shapes (matching style from reference) adding depth. The frosted glass has identical treatment: white border, purple-tinted shadow, same blur intensity. CRITICAL: This slide must look like it belongs in the exact same presentation as the reference image - same colors, same glass treatment, same overall aesthetic.",
  "style": "MATCH REFERENCE EXACTLY - Glassmorphism, visionOS aesthetic, same visual language",
  "composition": "Asymmetric split: glass card left (40%), 3D visualization right (40%), breathing room between elements",
  "color_palette": "EXACTLY match reference: purple #667eea, cyan #00d4ff gradient, same frosted white treatment, same text white",
  "consistency_note": "CRITICAL: Must be visually identical in style to reference image. Same gradient colors, same glass blur intensity, same shadow treatment, same typography weight and style. Viewer should immediately recognize this as the same presentation."
}
```

```bash
python /mnt/skills/public/image-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/nova-slide-02.json \
  --reference-images /mnt/user-data/outputs/nova-slide-01.jpg \
  --output-file /mnt/user-data/outputs/nova-slide-02.jpg \
  --aspect-ratio 16:9
```

**Slide 3-5：继续沿用同一模式，每张以前一张为参考**

后续幻灯片的关键一致性规则：

- 在提示词中始终包含 "continuing EXACT visual style from reference image"
- 明确指出 "SAME gradient background""SAME glass treatment""SAME typography"
- 附带 `consistency_note` 字段强调风格匹配
- 引用紧邻的上一张幻灯片图像

### 步骤 4：合成最终 PPT

```bash
python /mnt/skills/public/ppt-generation/scripts/generate.py \
  --plan-file /mnt/user-data/workspace/nova-plan.json \
  --slide-images /mnt/user-data/outputs/nova-slide-01.jpg /mnt/user-data/outputs/nova-slide-02.jpg /mnt/user-data/outputs/nova-slide-03.jpg /mnt/user-data/outputs/nova-slide-04.jpg /mnt/user-data/outputs/nova-slide-05.jpg \
  --output-file /mnt/user-data/outputs/nova-presentation.pptx
```

## 风格专属指南（Style-Specific Guidelines）

### 玻璃拟态（Glassmorphism，推荐 —— 最现代前卫）
```json
{
  "style": "glassmorphism",
  "style_guidelines": {
    "color_palette": "Vibrant gradient backgrounds (purple #667eea to pink #f093fb, or cyan #4facfe to blue #00f2fe), frosted white panels with 20% opacity, accent colors that pop against the gradient",
    "typography": "SF Pro Display or Inter font style, bold 600-700 weight titles, clean 400 weight body, white text with subtle drop shadow for readability on glass",
    "imagery": "Abstract 3D shapes floating in space, soft blurred orbs, geometric primitives with glass material, depth through overlapping translucent layers",
    "layout": "Floating card panels with backdrop-blur effect, generous padding (48-64px), rounded corners (24-32px radius), layered depth with subtle shadows",
    "effects": "Frosted glass blur (backdrop-filter: blur 20px), subtle white border (1px rgba 255,255,255,0.2), soft glow behind panels, floating elements with drop shadows",
    "visual_language": "Premium tech aesthetic like Apple Vision Pro UI, depth through transparency, light refracting through glass surfaces"
  }
}
```

### 深色高级（Dark Premium）
```json
{
  "style": "dark-premium",
  "style_guidelines": {
    "color_palette": "Deep black base (#0a0a0a to #121212), luminous accent color (electric blue #00d4ff, neon purple #bf5af2, or gold #ffd700), subtle gray gradients for depth (#1a1a1a to #0a0a0a)",
    "typography": "Elegant sans-serif (Neue Haas Grotesk or Suisse Int'l style), dramatic size contrast (72pt+ headlines, 18pt body), letter-spacing -0.02em for headlines, pure white (#ffffff) text",
    "imagery": "Dramatic studio lighting, rim lights and edge glow, cinematic product shots, abstract light trails, premium material textures (brushed metal, matte surfaces)",
    "layout": "Generous negative space (60%+), asymmetric balance, content anchored to grid but with breathing room, single focal point per slide",
    "effects": "Subtle ambient glow behind key elements, light bloom effects, grain texture overlay (2-3% opacity), vignette on edges",
    "visual_language": "Luxury tech brand aesthetic (Bang & Olufsen, Porsche Design), sophistication through restraint, every element intentional"
  }
}
```

### 渐变现代（Gradient Modern）
```json
{
  "style": "gradient-modern",
  "style_guidelines": {
    "color_palette": "Bold mesh gradients (Stripe/Linear style: purple-pink-orange #7c3aed→#ec4899→#f97316, or cool tones: cyan-blue-purple #06b6d4→#3b82f6→#8b5cf6), white or dark text depending on background intensity",
    "typography": "Modern geometric sans-serif (Satoshi, General Sans, or Clash Display style), variable font weights, oversized bold headlines (80pt+), comfortable body text (20pt)",
    "imagery": "Abstract fluid shapes, morphing gradients, 3D rendered abstract objects, soft organic forms, floating geometric primitives",
    "layout": "Dynamic asymmetric compositions, overlapping elements with blend modes, text integrated with gradient flows, full-bleed backgrounds",
    "effects": "Smooth gradient transitions, subtle noise texture (3-5% for depth), soft shadows with color tint matching gradient, motion blur suggesting movement",
    "visual_language": "Contemporary SaaS aesthetic (Stripe, Linear, Vercel), energetic yet professional, forward-thinking tech vibes"
  }
}
```

### 新粗野主义（Neo-Brutalist）
```json
{
  "style": "neo-brutalist",
  "style_guidelines": {
    "color_palette": "High contrast primaries: stark black, pure white, with bold accent (hot pink #ff0080, electric yellow #ffff00, or raw red #ff0000), optional: Memphis-inspired pastels as secondary",
    "typography": "Ultra-bold condensed type (Impact, Druk, or Bebas Neue style), UPPERCASE headlines, extreme size contrast, intentionally tight or overlapping letter-spacing",
    "imagery": "Raw unfiltered photography, intentional visual noise, halftone patterns, cut-out collage aesthetic, hand-drawn elements, stickers and stamps",
    "layout": "Broken grid, overlapping elements, thick black borders (4-8px), visible structure, anti-whitespace (dense but organized chaos)",
    "effects": "Hard shadows (no blur, offset 8-12px), pixelation accents, scan lines, CRT screen effects, intentional 'mistakes'",
    "visual_language": "Anti-corporate rebellion, DIY zine aesthetic meets digital, raw authenticity, memorable through boldness"
  }
}
```

### 3D 等距（3D Isometric）
```json
{
  "style": "3d-isometric",
  "style_guidelines": {
    "color_palette": "Soft contemporary palette: muted purples (#8b5cf6), teals (#14b8a6), warm corals (#fb7185), with cream or light gray backgrounds (#fafafa), consistent saturation across elements",
    "typography": "Friendly geometric sans-serif (Circular, Gilroy, or Quicksand style), medium weight headlines, excellent readability, comfortable 24pt body text",
    "imagery": "Clean isometric 3D illustrations, consistent 30° isometric angle, soft clay-render aesthetic, floating platforms and devices, cute simplified objects",
    "layout": "Central isometric scene as hero, text balanced around 3D elements, clear visual hierarchy, comfortable margins (64px+)",
    "effects": "Soft drop shadows (20px blur, 30% opacity), ambient occlusion on 3D objects, subtle gradients on surfaces, consistent light source (top-left)",
    "visual_language": "Friendly tech illustration (Slack, Notion, Asana style), approachable complexity, clarity through simplification"
  }
}
```

### 编辑设计（Editorial）
```json
{
  "style": "editorial",
  "style_guidelines": {
    "color_palette": "Sophisticated neutrals: off-white (#f5f5f0), charcoal (#2d2d2d), with single accent color (burgundy #7c2d12, forest #14532d, or navy #1e3a5f), occasional full-color photography",
    "typography": "Refined serif for headlines (Playfair Display, Freight, or Editorial New style), clean sans-serif for body (Söhne, Graphik), dramatic size hierarchy (96pt headlines, 16pt body), generous line-height 1.6",
    "imagery": "Magazine-quality photography, dramatic crops, full-bleed images, portraits with intentional negative space, editorial lighting (Vogue, Bloomberg Businessweek style)",
    "layout": "Sophisticated grid system (12-column), intentional asymmetry, pull quotes as design elements, text wrapping around images, elegant margins",
    "effects": "Minimal effects - let photography and typography shine, subtle image treatments (slight desaturation, film grain), elegant borders and rules",
    "visual_language": "High-end magazine aesthetic, intellectual sophistication, content elevated through design restraint"
  }
}
```

### 极简瑞士（Minimal Swiss）
```json
{
  "style": "minimal-swiss",
  "style_guidelines": {
    "color_palette": "Pure white (#ffffff) or off-white (#fafaf9) backgrounds, true black (#000000) text, single bold accent (Swiss red #ff0000, Klein blue #002fa7, or signal yellow #ffcc00)",
    "typography": "Helvetica Neue or Aktiv Grotesk, strict type scale (12/16/24/48/96), medium weight for body, bold for emphasis only, flush-left ragged-right alignment",
    "imagery": "Objective photography, geometric shapes, clean iconography, mathematical precision, intentional empty space as compositional element",
    "layout": "Strict grid adherence (baseline grid visible in spirit), modular compositions, generous whitespace (40%+ of slide), content aligned to invisible grid lines",
    "effects": "None - purity of form, no shadows, no gradients, no decorative elements, occasional single hairline rules",
    "visual_language": "International Typographic Style, form follows function, timeless modernism, Dieter Rams-inspired restraint"
  }
}
```

### 苹果 Keynote 风格
```json
{
  "style": "keynote",
  "style_guidelines": {
    "color_palette": "Deep blacks (#000000 to #1d1d1f), pure white text, signature blue (#0071e3) or gradient accents (purple-pink for creative, blue-teal for tech)",
    "typography": "San Francisco Pro Display, extreme weight contrast (bold 80pt+ titles, light 24pt body), negative letter-spacing on headlines (-0.03em), optical alignment",
    "imagery": "Cinematic photography, shallow depth of field, dramatic lighting (rim lights, spot lighting), product hero shots with reflections, full-bleed imagery",
    "layout": "Maximum negative space, single powerful image or statement per slide, content centered or dramatically offset, no clutter",
    "effects": "Subtle gradient overlays, light bloom and glow on key elements, reflection on surfaces, smooth gradient backgrounds",
    "visual_language": "Apple WWDC keynote aesthetic, confidence through simplicity, every pixel considered, theatrical presentation"
  }
}
```

## 输出处理（Output Handling）

生成完成后：

- PPTX 文件保存到 `/mnt/user-data/outputs/`
- 使用 `present_files` 工具将生成的演示文稿分享给用户
- 如有需要，可一并分享各张幻灯片的图像
- 提供对演示文稿的简要说明
- 主动提出可针对特定幻灯片迭代或重新生成

## 备注（Notes）

### 关键质量准则

**面向专业结果的提示词工程（Prompt Engineering for Professional Results）：**
- 无论用户使用何种语言，图像提示词一律使用英语
- 描述视觉细节必须极其具体 —— 含糊的提示词只会得到泛泛的结果
- 给出精确的十六进制色值（例如用 #667eea 而非 "purple"）
- 指明字体细节：字重（400/700）、字号层级、字间距
- 精确描述效果："backdrop blur 20px"、"drop shadow 8px blur 30% opacity"
- 引用真实的设计系统："visionOS aesthetic"、"Stripe website style"、"Bloomberg Businessweek layout"

**视觉一致性（最重要）：**
- **依序生成幻灯片** —— 每张幻灯片都必须以相邻上一张为参考
- 第一张幻灯片至关重要 —— 它奠定整份演示的视觉语言
- 在每张后续幻灯片的提示词中显式说明："continuing EXACT visual style from reference image"
- 在提示词中刻意反复使用 SAME、EXACT、MATCH 等关键词以强化一致性
- 自第 2 张起，每张 JSON 提示词都包含 `consistency_note` 字段
- 若某张幻灯片出现不一致，重新生成并强化对参考图的强调

**现代美学的设计原则：**
- 善用留白 —— 40-60% 的空白能营造高端感
- 每张幻灯片元素克制 —— 一个焦点、一条信息
- 通过层次（阴影、透明、纵深）表达深度
- 字体层级：超大的标题（72pt 以上）、舒适的正文（18-24pt）
- 色彩克制：单一主色调，最多 1-2 个强调色

**应避免的常见错误：**
- ❌ 使用 "professional slide" 这类泛泛的提示词 —— 务必具体
- ❌ 单张幻灯片堆砌过多元素/文字 —— 杂乱等于不专业
- ❌ 幻灯片之间色彩不一致 —— 务必引用上一张
- ❌ 漏掉参考图参数 —— 这会破坏视觉一致性
- ❌ 同一份演示中混用不同设计风格
- ❌ 并行生成幻灯片 —— 必须按顺序逐张生成（slide 1 → 2 → 3 ...），绝不能并发

**不同场景下的推荐风格：**
- 科技产品发布 → `glassmorphism` 或 `gradient-modern`
- 奢侈/高端品牌 → `dark-premium` 或 `editorial`
- 创业路演 → `gradient-modern` 或 `minimal-swiss`
- 高管演示 → `dark-premium` 或 `keynote`
- 创意机构 → `neo-brutalist` 或 `gradient-modern`
- 数据/分析 → `minimal-swiss` 或 `3d-isometric`
