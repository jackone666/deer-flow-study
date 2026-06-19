# API 参考

本文档提供了 DeerFlow 后端 APIs 的完整参考。

## 概述

DeerFlow 后端公开两组 APIs：

1. **LangGraph 兼容 API** - 代理交互、线程和流 (`/api/langgraph/*`)
2. **网关 API** - 模型、MCP、技能、上传和工件 (`/api/*`)

所有APIs都是通过Nginx反向代理在端口2026访问的。

## LangGraph 兼容 API

基础 URL: `/api/langgraph`

公共 LangGraph 兼容 API 遵循 LangGraph SDK 约定。在统一的 nginx 部署中，网关拥有 `/api/langgraph/*`并将这些路径转换为其本机`/api/*` 运行、线程和流路由器。

### 话题

#### 创建线程

```http
POST /api/langgraph/threads
Content-Type: application/json
```

**请求正文：**
```json
{
  "metadata": {}
}
```

**回复：**
```json
{
  "thread_id": "abc123",
  "created_at": "2024-01-15T10:30:00Z",
  "metadata": {}
}
```

#### 获取线程状态

```http
GET /api/langgraph/threads/{thread_id}/state
```

**回复：**
```json
{
  "values": {
    "messages": [...],
    "sandbox": {...},
    "artifacts": [...],
    "thread_data": {...},
    "title": "Conversation Title"
  },
  "next": [],
  "config": {...}
}
```

### 运行

#### 创建运行

使用输入执行代理。

```http
POST /api/langgraph/threads/{thread_id}/runs
Content-Type: application/json
```

**请求正文：**
```json
{
  "input": {
    "messages": [
      {
        "role": "user",
        "content": "Hello, can you help me?"
      }
    ]
  },
  "config": {
    "recursion_limit": 100,
    "configurable": {
      "model_name": "gpt-4",
      "thinking_enabled": false,
      "is_plan_mode": false
    }
  },
  "stream_mode": ["values", "messages-tuple", "custom"]
}
```

**流模式兼容性：**
- 使用：`values`、`messages-tuple`、`custom`、`updates`、`events`、`debug`、`tasks`、`checkpoints`
- 不要使用：`tools`（deprecated/invalid 在当前 `langgraph-api` 中，将触发架构验证错误）

**递归限制：**

`config.recursion_limit` 限制 LangGraph 将执行的图形步骤数
在一次运行中。统一网关路径默认为`100`
`build_run_config`（参见`backend/app/gateway/services.py`），这是一个更安全的
计划模式或子代理大量运行的起点。客户仍然可以设置
`recursion_limit` 明确在请求正文中；如果你跑得很深，就增加它
嵌套子代理图。

**可配置选项：**
- `model_name` (string): 覆盖默认模型
- `thinking_enabled` （布尔值）：为支持的模型启用扩展思维
- `is_plan_mode` (boolean): 启用 TodoList 中间件进行任务跟踪

**响应：** 服务器发送的事件 (SSE) 流

```
event: values
data: {"messages": [...], "title": "..."}

event: messages
data: {"content": "Hello! I'd be happy to help.", "role": "assistant"}

event: end
data: {}
```

#### 获取运行历史记录

```http
GET /api/langgraph/threads/{thread_id}/runs
```

**回复：**
```json
{
  "runs": [
    {
      "run_id": "run123",
      "status": "success",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

#### 流运行

实时传输响应。

```http
POST /api/langgraph/threads/{thread_id}/runs/stream
Content-Type: application/json
```

与 Create Run 相同的请求正文。返回 SSE 流。

---

## 网关 API

基础 URL: `/api`

### 模型

#### 列出型号

从配置中获取所有可用的 LLM 模型。

```http
GET /api/models
```

**回复：**
```json
{
  "models": [
    {
      "name": "gpt-4",
      "display_name": "GPT-4",
      "supports_thinking": false,
      "supports_vision": true
    },
    {
      "name": "claude-3-opus",
      "display_name": "Claude 3 Opus",
      "supports_thinking": false,
      "supports_vision": true
    },
    {
      "name": "deepseek-v3",
      "display_name": "DeepSeek V3",
      "supports_thinking": true,
      "supports_vision": false
    }
  ]
}
```

#### 获取型号详细信息

```http
GET /api/models/{model_name}
```

**回复：**
```json
{
  "name": "gpt-4",
  "display_name": "GPT-4",
  "model": "gpt-4",
  "max_tokens": 4096,
  "supports_thinking": false,
  "supports_vision": true
}
```

### MCP 配置

#### 获取 MCP 配置

获取当前 MCP 服务器配置。

```http
GET /api/mcp/config
```

**回复：**
```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "***"
      },
      "description": "GitHub operations"
    }
  }
}
```

#### 更新 MCP 配置

更新 MCP 服务器配置。

```http
PUT /api/mcp/config
Content-Type: application/json
```

**请求正文：**
```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "$GITHUB_TOKEN"
      },
      "description": "GitHub operations"
    }
  }
}
```

**回复：**
```json
{
  "success": true,
  "message": "MCP configuration updated"
}
```

### 技能

#### 列出技能

获取所有可用技能。

```http
GET /api/skills
```

**回复：**
```json
{
  "skills": [
    {
      "name": "pdf-processing",
      "display_name": "PDF Processing",
      "description": "Handle PDF documents efficiently",
      "enabled": true,
      "license": "MIT",
      "path": "public/pdf-processing"
    },
    {
      "name": "frontend-design",
      "display_name": "Frontend Design",
      "description": "Design and build frontend interfaces",
      "enabled": false,
      "license": "MIT",
      "path": "public/frontend-design"
    }
  ]
}
```

#### 获取技能详情

```http
GET /api/skills/{skill_name}
```

**回复：**
```json
{
  "name": "pdf-processing",
  "display_name": "PDF Processing",
  "description": "Handle PDF documents efficiently",
  "enabled": true,
  "license": "MIT",
  "path": "public/pdf-processing",
  "allowed_tools": ["read_file", "write_file", "bash"],
  "content": "# PDF Processing\n\nInstructions for the agent..."
}
```

#### 启用技能

```http
POST /api/skills/{skill_name}/enable
```

**回复：**
```json
{
  "success": true,
  "message": "Skill 'pdf-processing' enabled"
}
```

#### 禁用技能

```http
POST /api/skills/{skill_name}/disable
```

**回复：**
```json
{
  "success": true,
  "message": "Skill 'pdf-processing' disabled"
}
```

#### 安装技巧

从 `.skill` 文件安装技能。

```http
POST /api/skills/install
Content-Type: multipart/form-data
```

**请求正文：**
- `file`：要安装的 `.skill` 文件

**回复：**
```json
{
  "success": true,
  "message": "Skill 'my-skill' installed successfully",
  "skill": {
    "name": "my-skill",
    "display_name": "My Skill",
    "path": "custom/my-skill"
  }
}
```

### 文件上传

#### 上传文件

将一个或多个文件上传到一个线程。

```http
POST /api/threads/{thread_id}/uploads
Content-Type: multipart/form-data
```

**请求正文：**
- `files`：要上传的一个或多个文件

**回复：**
```json
{
  "success": true,
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".deer-flow/threads/abc123/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/abc123/artifacts/mnt/user-data/uploads/document.pdf",
      "markdown_file": "document.md",
      "markdown_path": ".deer-flow/threads/abc123/user-data/uploads/document.md",
      "markdown_virtual_path": "/mnt/user-data/uploads/document.md",
      "markdown_artifact_url": "/api/threads/abc123/artifacts/mnt/user-data/uploads/document.md"
    }
  ],
  "message": "Successfully uploaded 1 file(s)"
}
```

**支持的文档格式**（自动转换为 Markdown）：
- PDF (`.pdf`)
- PowerPoint (`.ppt`, `.pptx`)
- Excel（`.xls`，`.xlsx`）
- 字（`.doc`，`.docx`）

#### 列出上传的文件

```http
GET /api/threads/{thread_id}/uploads/list
```

**回复：**
```json
{
  "files": [
    {
      "filename": "document.pdf",
      "size": 1234567,
      "path": ".deer-flow/threads/abc123/user-data/uploads/document.pdf",
      "virtual_path": "/mnt/user-data/uploads/document.pdf",
      "artifact_url": "/api/threads/abc123/artifacts/mnt/user-data/uploads/document.pdf",
      "extension": ".pdf",
      "modified": 1705997600.0
    }
  ],
  "count": 1
}
```

#### 删除文件

```http
DELETE /api/threads/{thread_id}/uploads/{filename}
```

**回复：**
```json
{
  "success": true,
  "message": "Deleted document.pdf"
}
```

### 线程清理

在删除 LangGraph 线程本身后，删除 `.deer-flow/threads/{thread_id}` 下的 DeerFlow 管理的本地线程文件。

```http
DELETE /api/threads/{thread_id}
```

**回复：**
```json
{
  "success": true,
  "message": "Deleted local thread data for abc123"
}
```

**错误行为：**
- `422` 对于无效线程 IDs
- `500`返回通用`{"detail": "Failed to delete local thread data."}` 响应，而完整的异常详细信息保留在服务器日志中

### 工件

#### 获取工件

下载或查看代理生成的工件。

```http
GET /api/threads/{thread_id}/artifacts/{path}
```

**路径示例：**
- `/api/threads/abc123/artifacts/mnt/user-data/outputs/result.txt`
- `/api/threads/abc123/artifacts/mnt/user-data/uploads/document.pdf`

**查询参数：**
- `download`（布尔值）：如果`true`，则使用 Content-Disposition 标头强制下载

**响应：** 具有适当 Content-Type 的文件内容

---

## 错误响应

所有 APIs 以一致的格式返回错误：

```json
{
  "detail": "Error message describing what went wrong"
}
```

**HTTP 状态代码：**
- `400` - 错误请求：输入无效
- `404` - 未找到：未找到资源
- `422` - 验证错误：请求验证失败
- `500` - 内部服务器错误：服务器端错误

---

## 身份验证

DeerFlow 对所有非公共 HTTP 路由强制进行身份验证。公共路由仅限于 health/docs 元数据和这些公共身份验证端点：

- `POST /api/v1/auth/initialize` 在不存在管理员时创建第一个管理员帐户。
- `POST /api/v1/auth/login/local`使用 email/password 登录并设置 HttpOnly`access_token` cookie。
- `POST /api/v1/auth/register`创建常规`user` 帐户并设置会话 cookie。
- `POST /api/v1/auth/logout` 清除会话 cookie。
- `GET /api/v1/auth/setup-status` 报告是否仍需要创建第一个管理员。

经过身份验证的身份验证端点是：

- `GET /api/v1/auth/me` 返回当前用户。
- `POST /api/v1/auth/change-password`更改密码，可以选择在设置期间更改电子邮件，递增`token_version`，并重新发出 cookie。

受保护的状态更改请求还需要 CSRF 双提交令牌：将 `csrf_token`cookie 值作为`X-CSRF-Token`标头发送。 Login/register/initialize/logout 是引导身份验证端点：它们不受双重提交令牌的影响，但仍然拒绝恶意浏览器`Origin` 标头。

从经过身份验证的用户上下文强制执行用户隔离：

- 线程元数据的范围为 `threads_meta.user_id`； search/read/write/delete APIs 仅公开当前用户的线程。
- 线程文件位于 `{base_dir}/users/{user_id}/threads/{thread_id}/user-data/`下，并在沙箱内以`/mnt/user-data/` 形式公开。
- 内存和自定义代理存储在 `{base_dir}/users/{user_id}/...` 下。

注意：MCP 出站连接仍然可以对已配置的 HTTP/SSE MCP 服务器使用 OAuth；这与 DeerFlow API 身份验证是分开的。

---

## 速率限制

默认情况下不实施速率限制。对于生产部署，在 Nginx 中配置速率限制：

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://backend;
}
```

---

## 流媒体支持

网关的 LangGraph 兼容 API 流通过服务器发送的事件 (SSE) 运行事件：

```http
POST /api/langgraph/threads/{thread_id}/runs/stream
Accept: text/event-stream
```

---

## SDK 用法

### Python (LangGraph SDK)

```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2026/api/langgraph")

# Create thread
thread = await client.threads.create()

# Run agent
async for event in client.runs.stream(
    thread["thread_id"],
    "lead_agent",
    input={"messages": [{"role": "user", "content": "Hello"}]},
    config={"configurable": {"model_name": "gpt-4"}},
    stream_mode=["values", "messages-tuple", "custom"],
):
    print(event)
```

### JavaScript/TypeScript

```typescript
// Using fetch for Gateway API
const response = await fetch('/api/models');
const data = await response.json();
console.log(data.models);

// Create a run and stream SSE events
const streamResponse = await fetch(`/api/langgraph/threads/${threadId}/runs/stream`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  },
  body: JSON.stringify({
    input: { messages: [{ role: "user", content: "Hello" }] },
    stream_mode: ["values", "messages-tuple", "custom"],
  }),
});

const reader = streamResponse.body?.getReader();
// Decode and parse SSE frames from reader in your client code.
```

### cURL 示例

```bash
# List models
curl http://localhost:2026/api/models

# Get MCP config
curl http://localhost:2026/api/mcp/config

# Upload file
curl -X POST http://localhost:2026/api/threads/abc123/uploads \
  -F "files=@document.pdf"

# Enable skill
curl -X POST http://localhost:2026/api/skills/pdf-processing/enable

# Create thread and run agent
curl -X POST http://localhost:2026/api/langgraph/threads \
  -H "Content-Type: application/json" \
  -d '{}'

curl -X POST http://localhost:2026/api/langgraph/threads/abc123/runs \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"messages": [{"role": "user", "content": "Hello"}]},
    "config": {
      "recursion_limit": 100,
      "configurable": {"model_name": "gpt-4"}
    }
  }'
```

> 统一网关路径默认`config.recursion_limit`为100
> 计划模式和子代理重运行。客户仍可设置
> `config.recursion_limit` 明确 — 请参阅 [Create Run](#create-run)
> 部分了解详细信息。
