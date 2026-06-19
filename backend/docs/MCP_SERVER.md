# MCP（模型上下文协议）配置

DeerFlow 支持可配置的 MCP 服务器和技能来扩展其功能，这些服务器和技能是从项目根目录中的专用 `extensions_config.json` 文件加载的。

## 设置

1. 将 `extensions_config.example.json` 复制到项目根目录下的 `extensions_config.json` 中。
   ```bash
   # Copy example configuration
   cp extensions_config.example.json extensions_config.json
   ```
   
2. 通过设置 `"enabled": true` 启用所需的 MCP 服务器或技能。
3. 根据需要配置每个服务器的命令、参数和环境变量。
4. 重启应用以加载并注册 MCP 工具。

## 文件系统 MCP 服务器

DeerFlow 已经提供了用于线程范围工作区访问的内置文件工具。
不要为同一个 DeerFlow 工作区添加 MCP 文件系统服务器。
重叠的文件工具使用不同的路径语义，会让 LLM 的工具选择和文件访问行为变得不稳定。

DeerFlow 目前不适配文件系统服务器的 MCP roots 模式。
尤其是，它不会发布每个线程的 MCP root，也不会把 DeerFlow 沙箱路径
（例如 `/mnt/user-data/...`）映射到 `@modelcontextprotocol/server-filesystem`
接受的路径。处理 DeerFlow 工作区文件时，请使用 DeerFlow 的内置文件工具。

## OAuth 支持（HTTP/SSE MCP 服务器）

对于 `http`和`sse` MCP 服务器，DeerFlow 支持 OAuth 令牌获取和自动令牌刷新。

- 支持的授权类型：`client_credentials`、`refresh_token`
- 在 `extensions_config.json`中配置每个服务器`oauth` 块
- 秘密应通过环境变量提供（例如：`$MCP_OAUTH_CLIENT_SECRET`）

示例：

```json
{
   "mcpServers": {
      "secure-http-server": {
         "enabled": true,
         "type": "http",
         "url": "https://api.example.com/mcp",
         "oauth": {
            "enabled": true,
            "token_url": "https://auth.example.com/oauth/token",
            "grant_type": "client_credentials",
            "client_id": "$MCP_OAUTH_CLIENT_ID",
            "client_secret": "$MCP_OAUTH_CLIENT_SECRET",
            "scope": "mcp.read",
            "refresh_skew_seconds": 60
         }
      }
   }
}
```

## 自定义工具拦截器

您可以注册在每个 MCP 工具调用之前运行的自定义拦截器。这对于注入每个请求标头（例如，来自 LangGraph 执行上下文的用户身份验证令牌）、日志记录或指标非常有用。

使用 `mcpInterceptors`字段在`extensions_config.json` 中声明拦截器：

```json
{
  "mcpInterceptors": [
    "my_package.mcp.auth:build_auth_interceptor"
  ],
  "mcpServers": { ... }
}
```

每个条目都是 `module:variable`格式的 Python 导入路径（通过`resolve_variable`解析）。该变量必须是一个**无参数构建器函数**，它返回与`MultiServerMCPClient`的`tool_interceptors`接口兼容的异步拦截器，或者要跳过的`None` 。

从 LangGraph 元数据注入身份验证标头的示例拦截器：

```python
def build_auth_interceptor():
    async def interceptor(request, handler):
        from langgraph.config import get_config
        metadata = get_config().get("metadata", {})
        headers = dict(request.headers or {})
        if token := metadata.get("auth_token"):
            headers["X-Auth-Token"] = token
        return await handler(request.override(headers=headers))
    return interceptor
```

- 接受单个字符串值并将其标准化为单元素列表。
- 无效路径或构建器失败将被记录为警告，而不会阻止其他拦截器。
- 构建器返回值必须是 `callable`；不可调用的值将被跳过并带有警告。

## 工作原理

MCP 服务器公开了在运行时自动发现并集成到 DeerFlow 代理系统中的工具。启用后，代理即可使用这些工具，无需进行额外的代码更改。

## 示例功能

MCP 服务器可以提供对：

- **数据库**（例如，PostgreSQL）
- **外部 APIs** （例如，GitHub、Brave Search）
- **浏览器自动化**（例如，Puppeteer）
- **自定义 MCP 服务器实现**

## 了解更多信息

有关模型上下文协议的详细文档，请访问：
https://modelcontextprotocol.io
