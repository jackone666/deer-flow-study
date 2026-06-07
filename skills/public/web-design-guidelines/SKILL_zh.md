---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web 界面规范审查

审查文件是否符合 Web Interface Guidelines（Web 界面规范）。

## 工作流程

1. 从下方源 URL（统一资源定位符）拉取最新规范
2. 读取指定文件（或向用户询问文件 / 模式）
3. 逐条对照拉取到的规范进行检查
4. 以简洁的 `file:line` 格式输出发现的问题

## 规范来源

每次审查前重新拉取最新规范：

```
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
```

使用 WebFetch 工具获取最新规则。拉取到的内容中包含全部规则以及输出格式说明。

## 使用方法

当用户提供文件或匹配模式（pattern）参数时：

1. 从上方源 URL 拉取规范
2. 读取指定文件
3. 应用拉取到的所有规则
4. 按规范中指定的格式输出审查结果

如果用户未指定文件，向其询问需要审查哪些文件。
