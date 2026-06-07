---
name: chart-visualization
description: This skill should be used when the user wants to visualize data. It intelligently selects the most suitable chart type from 26 available options, extracts parameters based on detailed specifications, and generates a chart image using a JavaScript script.
compatibility:
  nodejs: ">=18.0.0"
---

# 图表可视化技能

本技能提供了一套完整的工作流，用于把数据转化为可视化图表。它涵盖图表选型、参数提取以及图片生成。

## 工作流程

可视化数据的步骤如下：

### 1. 智能图表选型

分析用户数据的特征，确定最合适的图表类型。参考以下指南（具体规范可查阅 `references/` 目录）：

- **时间序列（Time Series）**：使用 `generate_line_chart`（趋势）或 `generate_area_chart`（累计趋势）。当存在两种不同量纲时，使用 `generate_dualaxes_chart`。
- **比较（Comparisons）**：使用 `generate_bar_chart`（分类）或 `generate_column_chart`。频次分布使用 `generate_histogram_chart`。
- **部分与整体（Part-to-Whole）**：使用 `generate_pie_chart` 或 `generate_treemap_chart`（层级关系）。
- **关系与流向（Relationships & Flow）**：使用 `generate_scatter_chart`（相关性）、`generate_sankey_chart`（流向）或 `generate_venn_chart`（重叠）。
- **地图（Maps）**：使用 `generate_district_map`（区域）、`generate_pin_map`（点位）或 `generate_path_map`（路径）。
- **层级与树形（Hierarchies & Trees）**：使用 `generate_organization_chart` 或 `generate_mind_map`。
- **专用图**：
    - `generate_radar_chart`：多维对比。
    - `generate_funnel_chart`：流程阶段。
    - `generate_liquid_chart`：百分比 / 进度。
    - `generate_word_cloud_chart`：词频。
    - `generate_boxplot_chart` 或 `generate_violin_chart`：统计分布。
    - `generate_network_graph`：复杂的节点-边关系。
    - `generate_fishbone_diagram`：因果分析。
    - `generate_flow_diagram`：流程图。
    - `generate_spreadsheet`：表格数据或透视表，用于结构化数据的展示和交叉分析。

### 2. 参数提取

选定图表类型后，读取 `references/` 目录下对应的文件（例如 `references/generate_line_chart.md`），明确必填与可选字段。

从用户的输入中抽取数据，并映射到 `args` 所要求的格式。

### 3. 生成图表

以 JSON 负载（payload）调用 `scripts/generate.js` 脚本。

**负载格式：**
```json
{
  "tool": "generate_chart_type_name",
  "args": {
    "data": [...],
    "title": "...",
    "theme": "...",
    "style": { ... }
  }
}
```

**执行命令：**
```bash
node ./scripts/generate.js '<payload_json>'
```

### 4. 反馈结果

脚本会输出生成图表的图片 URL。

向用户返回以下内容：
- 图片 URL。
- 生成时使用的完整 `args`（即规范定义）。

## 参考资料

每种图表类型的详细规范位于 `references/` 目录。请查阅这些文件，确保传给脚本的 `args` 与预期模式（schema）一致。

## 许可证

本 `SKILL.md` 由 [antvis/chart-visualization-skills](https://github.com/antvis/chart-visualization-skills) 提供。

基于 [MIT 许可证](https://github.com/antvis/chart-visualization-skills/blob/master/LICENSE) 发布。
