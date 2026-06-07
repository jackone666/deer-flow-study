---
name: claude-to-deerflow
description: "Interact with DeerFlow AI agent platform via its HTTP API. Use this skill when the user wants to send messages or questions to DeerFlow for research/analysis, start a DeerFlow conversation thread, check DeerFlow status or health, list available models/skills/agents in DeerFlow, manage DeerFlow memory, upload files to DeerFlow threads, or delegate complex research tasks to DeerFlow. Also use when the user mentions deerflow, deer flow, or wants to run a deep research task that DeerFlow can handle."
---

# DeerFlow 技能（DeerFlow Skill）

通过 HTTP API 与运行中的 DeerFlow 实例通信。DeerFlow 是构建在 LangGraph 之上的 AI 智能体平台，用于编排多个子智能体完成研究、代码执行、网页浏览等任务。

## 架构

DeerFlow 在 Nginx 反向代理之后暴露两类 API 接口：

| 服务        | 直连端口 | 通过代理                        | 用途                          |
|----------------|-------------|----------------------------------|----------------------------------|
| Gateway API    | 8001        | `$DEERFLOW_GATEWAY_URL`          | REST 端点与内嵌的智能体运行时 |
| LangGraph 兼容 API | 8001 | `$DEERFLOW_LANGGRAPH_URL`       | 智能体线程、运行、流式输出 |

## 环境变量

所有 URL 都通过环境变量配置。**发起任何请求前，请先读取以下环境变量。**

| 变量                | 默认值                                  | 说明                        |
|-------------------------|------------------------------------------|------------------------------------|
| `DEERFLOW_URL`          | `http://localhost:2026`                  | 统一的代理基地址             |
| `DEERFLOW_GATEWAY_URL`  | `${DEERFLOW_URL}`                        | Gateway API 基地址（models、skills、memory、uploads） |
| `DEERFLOW_LANGGRAPH_URL`| `${DEERFLOW_URL}/api/langgraph`          | LangGraph API 基地址（threads、runs） |

调用 curl 时，务必按以下方式解析 URL：

```bash
# 先从环境变量解析基地址（在任何 API 调用之前）
DEERFLOW_URL="${DEERFLOW_URL:-http://localhost:2026}"
DEERFLOW_GATEWAY_URL="${DEERFLOW_GATEWAY_URL:-$DEERFLOW_URL}"
DEERFLOW_LANGGRAPH_URL="${DEERFLOW_LANGGRAPH_URL:-$DEERFLOW_URL/api/langgraph}"
```

## 可用操作

### 1. 健康检查

确认 DeerFlow 是否在运行：

```bash
curl -s "$DEERFLOW_GATEWAY_URL/health"
```

### 2. 发送消息（流式）

这是最常用的操作。它会创建一个线程，并流式返回智能体的响应。

**第 1 步：创建线程**

```bash
curl -s -X POST "$DEERFLOW_LANGGRAPH_URL/threads" \
  -H "Content-Type: application/json" \
  -d '{}'
```

响应：`{"thread_id": "<uuid>", ...}`

**第 2 步：流式运行**

```bash
curl -s -N -X POST "$DEERFLOW_LANGGRAPH_URL/threads/<thread_id>/runs/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "lead_agent",
    "input": {
      "messages": [
        {
          "type": "human",
          "content": [{"type": "text", "text": "YOUR MESSAGE HERE"}]
        }
      ]
    },
    "stream_mode": ["values", "messages-tuple"],
    "stream_subgraphs": true,
    "config": {
      "recursion_limit": 1000
    },
    "context": {
      "thinking_enabled": true,
      "is_plan_mode": true,
      "subagent_enabled": true,
      "thread_id": "<thread_id>"
    }
  }'
```

响应是 SSE 流。每个事件的格式为：

```
event: <event_type>
data: <json_data>
```

关键事件类型：

- `metadata` —— 包含 `run_id` 的运行元信息
- `values` —— 完整状态快照，含 `messages` 数组
- `messages-tuple` —— 增量消息更新（AI 文本片段、工具调用、工具结果）
- `end` —— 流结束

**Context 模式**（通过 `context` 设置）：

- Flash 模式：`thinking_enabled: false, is_plan_mode: false, subagent_enabled: false`
- 标准模式：`thinking_enabled: true, is_plan_mode: false, subagent_enabled: false`
- Pro 模式：`thinking_enabled: true, is_plan_mode: true, subagent_enabled: false`
- Ultra 模式：`thinking_enabled: true, is_plan_mode: true, subagent_enabled: true`

### 3. 继续对话

要发送追问消息，请复用第 2 步拿到的 `thread_id`，再用新消息 POST 一次 run。

### 4. 列出模型

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/models"
```

返回：`{"models": [{"name": "...", "provider": "...", ...}, ...]}`

### 5. 列出技能

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/skills"
```

返回：`{"skills": [{"name": "...", "enabled": true, ...}, ...]}`

### 6. 启用 / 禁用技能

```bash
curl -s -X PUT "$DEERFLOW_GATEWAY_URL/api/skills/<skill_name>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

### 7. 列出智能体

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/agents"
```

返回：`{"agents": [{"name": "...", ...}, ...]}`

### 8. 获取记忆

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/memory"
```

返回用户上下文、事实、对话历史摘要。

### 9. 上传文件到线程

```bash
curl -s -X POST "$DEERFLOW_GATEWAY_URL/api/threads/<thread_id>/uploads" \
  -F "files=@/path/to/file.pdf"
```

支持 PDF、PPTX、XLSX、DOCX —— 自动转换为 Markdown。

### 10. 列出已上传文件

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/threads/<thread_id>/uploads/list"
```

### 11. 获取线程历史

```bash
curl -s "$DEERFLOW_LANGGRAPH_URL/threads/<thread_id>/history"
```

### 12. 列出线程

```bash
curl -s -X POST "$DEERFLOW_LANGGRAPH_URL/threads/search" \
  -H "Content-Type: application/json" \
  -d '{"limit": 20, "sort_by": "updated_at", "sort_order": "desc"}'
```

## 使用脚本

如需发送消息并收集完整回复，可使用辅助脚本：

```bash
bash /path/to/skills/claude-to-deerflow/scripts/chat.sh "Your question here"
```

具体实现见 `scripts/chat.sh`。脚本流程：

1. 检查健康状态
2. 创建线程
3. 流式运行并收集最终的 AI 回复
4. 打印结果

## 解析 SSE 输出

流返回的是 SSE 事件。要从 `values` 事件中提取最终 AI 回复：

- 找到最后一个 `event: values` 块
- 解析它的 `data` JSON
- `messages` 数组包含全部消息；最后一条 `type: "ai"` 的就是回复
- 该消息的 `content` 字段即为 AI 的文本回复

## 错误处理

- 健康检查失败：DeerFlow 未运行，告知用户需要先启动
- 流返回错误事件：提取并展示错误信息
- 常见问题：端口未开放、服务尚未启动完成、配置错误

## 使用建议

- 简单问题用 Flash 模式（最快，不做规划）
- 研究类任务用 Pro 或 Ultra 模式（启用规划和子智能体）
- 可以先上传文件，再在消息中引用
- 线程 ID 持久化 —— 之后可以回到同一会话继续
