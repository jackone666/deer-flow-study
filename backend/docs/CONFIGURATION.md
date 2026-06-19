# 配置指南

本指南说明如何为您的环境配置 DeerFlow。

## 配置版本控制

`config.example.yaml`包含一个`config_version`字段，用于跟踪架构更改。当示例版本高于本地`config.yaml` 时，应用程序会发出启动警告：

```
WARNING - Your config.yaml (version 0) is outdated — the latest version is 1.
Run `make config-upgrade` to merge new fields into your config.
```

- **配置中缺少 `config_version`** 将被视为版本 0。
- 运行 `make config-upgrade`自动合并缺失字段（保留现有值，创建`.bak` 备份）。
- 更改配置架构时，将 `config_version`更改为`config.example.yaml`。

## 配置部分

### 模型

配置代理可用的 LLM 模型：

```yaml
models:
  - name: gpt-4                    # Internal identifier
    display_name: GPT-4            # Human-readable name
    use: langchain_openai:ChatOpenAI  # LangChain class path
    model: gpt-4                   # Model identifier for API
    api_key: $OPENAI_API_KEY       # API key (use env var)
    max_tokens: 4096               # Max tokens per request
    temperature: 0.7               # Sampling temperature
```

**支持的提供商**：
- OpenAI (`langchain_openai:ChatOpenAI`)
- 人类 (`langchain_anthropic:ChatAnthropic`)
- DeepSeek (`langchain_deepseek:ChatDeepSeek`)
- 小米MiMo (`deerflow.models.patched_mimo:PatchedChatMiMo`)
- Claude Code OAuth (`deerflow.models.claude_provider:ClaudeChatModel`)
- 法典 CLI (`deerflow.models.openai_codex_provider:CodexChatModel`)
- 任何 LangChain 兼容的提供商

CLI 支持的提供商示例：

```yaml
models:
  - name: gpt-5.4
    display_name: GPT-5.4 (Codex CLI)
    use: deerflow.models.openai_codex_provider:CodexChatModel
    model: gpt-5.4
    supports_thinking: true
    supports_reasoning_effort: true

  - name: claude-sonnet-4.6
    display_name: Claude Sonnet 4.6 (Claude Code OAuth)
    use: deerflow.models.claude_provider:ClaudeChatModel
    model: claude-sonnet-4-6
    max_tokens: 4096
    supports_thinking: true
```

**CLI 支持的提供商的身份验证行为**：
- `CodexChatModel`从`~/.codex/auth.json` 加载 Codex CLI auth
- Codex 响应端点当前拒绝 `max_tokens`和`max_output_tokens`，因此 `CodexChatModel` 不会公开请求级别的令牌上限
- `ClaudeChatModel`接受`CLAUDE_CODE_OAUTH_TOKEN`、`ANTHROPIC_AUTH_TOKEN`、`CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`、`CLAUDE_CODE_CREDENTIALS_PATH`或明文`~/.claude/.credentials.json`
- 在 macOS 上，DeerFlow 不会自动探测钥匙串。需要时使用 `scripts/export_claude_code_oauth.py` 显式导出 Claude Code auth

要将 OpenAI 的 `/v1/responses`端点与 LangChain 一起使用，请继续使用`langchain_openai:ChatOpenAI` 并设置：

```yaml
models:
  - name: gpt-5-responses
    display_name: GPT-5 (Responses API)
    use: langchain_openai:ChatOpenAI
    model: gpt-5
    api_key: $OPENAI_API_KEY
    use_responses_api: true
    output_version: responses/v1
```

对于 OpenAI 兼容网关（例如 Novita 或 OpenRouter），继续使用 `langchain_openai:ChatOpenAI`并设置`base_url`：

```yaml
models:
  - name: novita-deepseek-v3.2
    display_name: Novita DeepSeek V3.2
    use: langchain_openai:ChatOpenAI
    model: deepseek/deepseek-v3.2
    api_key: $NOVITA_API_KEY
    base_url: https://api.novita.ai/openai
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled

  - name: minimax-m3
    display_name: MiniMax M3
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M3
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: true

  - name: minimax-m2.7
    display_name: MiniMax M2.7
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M2.7
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: true

  - name: minimax-m2.7-highspeed
    display_name: MiniMax M2.7 Highspeed
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M2.7-highspeed
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: true
  - name: openrouter-gemini-2.5-flash
    display_name: Gemini 2.5 Flash (OpenRouter)
    use: langchain_openai:ChatOpenAI
    model: google/gemini-2.5-flash-preview
    api_key: $OPENAI_API_KEY
    base_url: https://openrouter.ai/api/v1
```

如果您的 OpenRouter 键位于不同的环境变量名称中，请将 `api_key`显式指向该变量（例如`api_key: $OPENROUTER_API_KEY`）。

**思维模型**：
某些模型支持“思考”模式进行复杂推理：

```yaml
models:
  - name: deepseek-v3
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
```

**Gemini 通过 OpenAI 兼容网关进行思考**：

在启用思考的情况下通过 OpenAI 兼容代理（Vertex AI OpenAI 兼容端点、AI Studio 或第三方网关）路由 Gemini 时，API 将 `thought_signature` 附加到响应中返回的每个工具调用对象。  每个重播这些辅助消息的后续请求**必须**在工具调用条目上回显这些签名，否则 API 返回：

```
HTTP 400 INVALID_ARGUMENT: function call `<tool>` in the N. content block is
missing a `thought_signature`.
```

标准 `langchain_openai:ChatOpenAI`在序列化消息时默默地删除`thought_signature`。  使用 `deerflow.models.patched_openai:PatchedChatOpenAI`代替 — 它将工具调用签名（源自`AIMessage.additional_kwargs["tool_calls"]`）重新注入到每个传出的有效负载中：

```yaml
models:
  - name: gemini-2.5-pro-thinking
    display_name: Gemini 2.5 Pro (Thinking)
    use: deerflow.models.patched_openai:PatchedChatOpenAI
    model: google/gemini-2.5-pro-preview   # model name as expected by your gateway
    api_key: $GEMINI_API_KEY
    base_url: https://<your-openai-compat-gateway>/v1
    max_tokens: 16384
    supports_thinking: true
    supports_vision: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
```

对于**不启用 thinking** 的 Gemini 访问（例如通过 OpenRouter 且未激活 thinking），带有 `supports_thinking: false` 的普通 `langchain_openai:ChatOpenAI` 就足够了，不需要补丁。

**MiMo 通过 OpenAI 兼容 API** 进行思考：

MiMo 在思维模式下的辅助消息上返回 `reasoning_content`。在使用工具调用的多轮代理对话中，后续请求必须保留助理消息上的历史 `reasoning_content`，否则 MiMo API 可以返回 HTTP 400。标准 `langchain_openai:ChatOpenAI`会删除此特定于提供程序的字段，因此使用`deerflow.models.patched_mimo:PatchedChatMiMo`：

对于即用即付 API 密钥 (`sk-...`)，请使用 `https://api.xiaomimimo.com/v1`。对于令牌计划密钥 (`tp-...`)，请使用 MiMo 控制台中显示的区域令牌计划基础 URL，例如 `https://token-plan-cn.xiaomimimo.com/v1`。 MiMo 将这些密钥类型记录为单独且不可互换的。

`PatchedChatMiMo`与模型 ID 无关。将其用于您配置的每个 MiMo 思维模型条目，包括`subagents.*.model`覆盖引用的模型条目（例如`mimo-v2.5-pro`、`mimo-v2.5`、`mimo-v2-pro`、`mimo-v2-omni`或`mimo-v2-flash`）。

```yaml
models:
  - name: mimo-v2.5-pro
    display_name: MiMo V2.5 Pro
    use: deerflow.models.patched_mimo:PatchedChatMiMo
    model: mimo-v2.5-pro
    api_key: $MIMO_API_KEY
    base_url: https://api.xiaomimimo.com/v1
    max_tokens: 8192
    supports_thinking: true
    supports_vision: false
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled
    when_thinking_disabled:
      extra_body:
        thinking:
          type: disabled
```

`PatchedChatMiMo`保留 MiMo 的`choices[].message.reasoning_content`、流式 `delta.reasoning_content`和请求历史记录助手`reasoning_content` 字段。它不会重用 DeepSeek 提供程序。

### 工具组

将工具组织成逻辑组：

```yaml
tool_groups:
  - name: web          # Web browsing and search
  - name: file:read    # Read-only file operations
  - name: file:write   # Write file operations
  - name: bash         # Shell command execution
```

### 工具

配置代理可用的特定工具：

```yaml
tools:
  - name: web_search
    group: web
    use: deerflow.community.tavily.tools:web_search_tool
    max_results: 5
    # api_key: $TAVILY_API_KEY  # Optional
```

**内置工具**：
- `web_search` - 搜索网络（DuckDuckGo、Tavily、Exa、InfoQuest、Firecrawl）
- `web_fetch` - 获取网页（Jina AI、Exa、InfoQuest、Firecrawl）
- `ls` - 列出目录内容
- `read_file` - 读取文件内容
- `write_file` - 写入文件内容
- `str_replace` - 文件中的字符串替换
- `bash` - 执行 bash 命令

### 沙盒

DeerFlow 支持多种沙箱执行模式。在 `config.yaml` 中配置您的首选模式：

**本地执行**（直接在主机上运行沙箱代码）：
```yaml
sandbox:
   use: deerflow.sandbox.local:LocalSandboxProvider # Local execution
   allow_host_bash: false # default; host bash is disabled unless explicitly re-enabled
```

**Docker 执行**（在隔离的 Docker 容器中运行沙箱代码）：
```yaml
sandbox:
   use: deerflow.community.aio_sandbox:AioSandboxProvider # Docker-based sandbox
```

**使用 Kubernetes 进行 Docker 执行**（通过配置服务在 Kubernetes pod 中运行沙箱代码）：

此模式在 **主机集群** 上的隔离 Kubernetes Pod 中运行每个沙箱。需要 Docker Desktop K8s、OrbStack 或类似的本地 K8s 设置。

```yaml
sandbox:
   use: deerflow.community.aio_sandbox:AioSandboxProvider
   provisioner_url: http://provisioner:8002
```

使用 Docker 开发（`make docker-start`）时，仅当配置了此配置程序模式时，DeerFlow 才会启动 `provisioner`服务。在本地或普通 Docker 沙箱模式中，会跳过`provisioner`。

有关详细配置、先决条件和故障排除，请参阅 [Provisioner Setup Guide](../../docker/provisioner/README.md)。

选择本地执行或基于 Docker 的隔离：

**选项 1：本地沙箱**（默认，更简单的设置）：
```yaml
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: false
```

`allow_host_bash`有意为`false`。 DeerFlow的本地沙箱是主机端便利模式，而不是安全的外壳隔离边界。如果您需要 `bash`，请优先选择 `AioSandboxProvider`。仅针对完全可信的单用户本地工作流程设置 `allow_host_bash: true`。

**选项2：Docker Sandbox**（隔离，更安全）：
```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  port: 8080
  auto_start: true
  container_prefix: deer-flow-sandbox

  # Optional: Additional mounts
  mounts:
    - host_path: /path/on/host
      container_path: /path/in/container
      read_only: false
```

当您配置 `sandbox.mounts`时，DeerFlow 在代理提示中公开这些`container_path`值，以便代理可以直接发现并操作已安装的目录，而不是假设所有内容都必须位于`/mnt/user-data` 下。

对于使用 localhost 的裸机 Docker 沙箱运行，DeerFlow 默认将沙箱 HTTP 端口绑定到 `127.0.0.1`，因此它不会在每个主机接口上公开。通过 `host.docker.internal`连接的 Docker-outside-of-Docker 部署保留了广泛的传统绑定以实现兼容性。如果您的部署需要不同的绑定地址，请显式设置`DEER_FLOW_SANDBOX_BIND_HOST`。

### 技能

配置专门工作流程的技能目录：

```yaml
skills:
  # Host path (optional, default: ../skills)
  path: /custom/path/to/skills

  # Container mount path (default: /mnt/skills)
  container_path: /mnt/skills
```

**技能如何发挥作用**：
- 技能存储在 `deer-flow/skills/{public,custom}/` 中
- 每个技能都有一个包含元数据的 `SKILL.md` 文件
- 技能自动发现并加载
- 通过路径映射在本地和 Docker 沙箱中可用

**每座席技能过滤**：
自定义代理可以通过在其 `config.yaml`（位于 `workspace/agents/<agent_name>/config.yaml`）中定义 `skills` 字段来限制他们加载的技能：
- **省略或`null`**：加载所有全局启用的技能（默认后备）。
- **`[]`（空列表）**：禁用该特定代理的所有技能。
- **`["skill-name"]`**：仅加载明确指定的技能。

### 标题生成

自动对话标题生成：

```yaml
title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null  # Use first model in list
```

### GitHub API token（GitHub 深度研究技能可选）

默认的 GitHub API 速率限制非常严格。对于频繁的项目研究，我们建议配置具有只读权限的个人访问令牌（PAT）。

**配置步骤**：
1. 取消注释 `.env`文件中的`GITHUB_TOKEN` 行并添加您的个人访问令牌
2. 重新启动 DeerFlow 服务以应用更改

## 环境变量

DeerFlow 支持使用 `$` 前缀进行环境变量替换：

```yaml
models:
  - api_key: $OPENAI_API_KEY  # Reads from environment
```

**常用环境变量**：
- `OPENAI_API_KEY` - OpenAI API 键
- `ANTHROPIC_API_KEY` - 人择 API 键
- `DEEPSEEK_API_KEY` - DeepSeek API 键
- `MIMO_API_KEY` - 小米 MiMo API 键
- `NOVITA_API_KEY` - Novita API 密钥（OpenAI 兼容端点）
- `TAVILY_API_KEY` - 快速搜索 API 键
- `DEER_FLOW_PROJECT_ROOT` - 相对运行时路径的项目根
- `DEER_FLOW_CONFIG_PATH` - 自定义配置文件路径
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH` - 自定义扩展配置文件路径
- `DEER_FLOW_HOME` - 运行时状态目录（默认为项目根目录下的`.deer-flow`）
- `DEER_FLOW_SKILLS_PATH`- 省略`skills.path` 时的技能目录
- `GATEWAY_ENABLE_DOCS`- 设置为`false` 以禁用 Swagger UI (`/docs`)、ReDoc (`/redoc`) 和 OpenAPI 架构 (`/openapi.json`) 端点（默认值：`true`）

## 配置位置

配置文件应放置在**项目根目录**（`deer-flow/config.yaml`）中。当进程可能从另一个工作目录启动时设置 `DEER_FLOW_PROJECT_ROOT`，或设置`DEER_FLOW_CONFIG_PATH` 指向特定文件。

## 配置优先级

DeerFlow 按以下顺序搜索配置：

1. 通过 `config_path` 参数在代码中指定的路径
2. `DEER_FLOW_CONFIG_PATH` 环境变量的路径
3. `config.yaml`在`DEER_FLOW_PROJECT_ROOT`下，或者当`DEER_FLOW_PROJECT_ROOT` 未设置时在当前工作目录下
4. 传统 backend/repository-root 位置以实现单一存储库兼容性

## 最佳实践

1. **将 `config.yaml`放在项目根目录中** - 如果运行时在其他地方启动，则设置`DEER_FLOW_PROJECT_ROOT`
2. **永远不要提交 `config.yaml`** - 它已经在 `.gitignore` 中
3. **使用环境变量作为机密** - 不要硬编码 API 键
4. **保持 `config.example.yaml` 更新** - 记录所有新选项
5. **在本地测试配置更改** - 部署之前
6. **使用 Docker 沙箱进行生产** - 更好的隔离和安全性

## 故障排除

### “未找到配置文件”
- 确保 `config.yaml` 存在于 **项目根** 目录中 (`deer-flow/config.yaml`)
- 如果运行时在项目根目录之外启动，则设置 `DEER_FLOW_PROJECT_ROOT`
- 或者，将 `DEER_FLOW_CONFIG_PATH` 环境变量设置为自定义位置

### “无效的API密钥”
- 验证环境变量设置正确
- 检查 `$` 前缀是否用于环境变量引用

### “技能未加载”
- 检查 `deer-flow/skills/` 目录是否存在
- 验证技能是否具有有效的 `SKILL.md` 文件
- 如果使用自定义路径，请检查 `skills.path`或`DEER_FLOW_SKILLS_PATH`

### “Docker沙箱启动失败”
- 确保 Docker 正在运行
- 检查端口 8080（或配置的端口）是否可用
- 验证 Docker 镜像是否可访问

## 示例

有关所有配置选项的完整示例，请参阅 `config.example.yaml`。
