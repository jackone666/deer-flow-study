# Guardrails：工具调用前授权

> **上下文：** [Issue #1213](https://github.com/bytedance/deer-flow/issues/1213) — DeerFlow 通过 `ask_clarification` 具有 Docker 沙箱和人工批准，但没有用于工具调用的确定性、策略驱动的授权层。运行自主多步骤任务的代理可以使用任何参数执行任何加载的工具。 Guardrails 添加了一个中间件，该中间件在执行之前根据策略评估每个工具调用。

## 为什么要护栏

```
Without guardrails:                      With guardrails:

  Agent                                    Agent
    │                                        │
    ▼                                        ▼
  ┌──────────┐                             ┌──────────┐
  │ bash     │──▶ executes immediately     │ bash     │──▶ GuardrailMiddleware
  │ rm -rf / │                             │ rm -rf / │        │
  └──────────┘                             └──────────┘        ▼
                                                         ┌──────────────┐
                                                         │  Provider    │
                                                         │  evaluates   │
                                                         │  against     │
                                                         │  policy      │
                                                         └──────┬───────┘
                                                                │
                                                          ┌─────┴─────┐
                                                          │           │
                                                        ALLOW       DENY
                                                          │           │
                                                          ▼           ▼
                                                      Tool runs   Agent sees:
                                                      normally    "Guardrail denied:
                                                                   rm -rf blocked"
```

- **沙盒**提供进程隔离，但不提供语义授权。沙盒 `bash`仍然可以`curl` 数据输出。
- **人工批准** (`ask_clarification`) 需要有人参与每个操作。对于自主工作流程不可行。
- **Guardrails** 提供确定性、策略驱动的授权，无需人工干预即可工作。

## 建筑

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Middleware Chain                               │
│                                                                      │
│  1. ThreadDataMiddleware     ─── per-thread dirs                     │
│  2. UploadsMiddleware        ─── file upload tracking                │
│  3. SandboxMiddleware        ─── sandbox acquisition                 │
│  4. DanglingToolCallMiddleware ── fix incomplete tool calls           │
│  5. GuardrailMiddleware ◄──── EVALUATES EVERY TOOL CALL             │
│  6. ToolErrorHandlingMiddleware ── convert exceptions to messages     │
│  7-12. (Summarization, Title, Memory, Vision, Subagent, Clarify)    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                         │
                         ▼
           ┌──────────────────────────┐
           │    GuardrailProvider     │  ◄── pluggable: any class
           │    (configured in YAML)  │      with evaluate/aevaluate
           └────────────┬─────────────┘
                        │
              ┌─────────┼──────────────┐
              │         │              │
              ▼         ▼              ▼
         Built-in   OAP Passport    Custom
         Allowlist  Provider        Provider
         (zero dep) (open standard) (your code)
                        │
                  Any implementation
                  (e.g. APort, or
                   your own evaluator)
```

`GuardrailMiddleware`实现`wrap_tool_call`/`awrap_tool_call`（与`ToolErrorHandlingMiddleware`使用的`AgentMiddleware` 模式相同）。它：

1. 使用工具名称、参数和护照参考构建 `GuardrailRequest`
2. 在配置的任何提供程序上调用 `provider.evaluate(request)`
3. 如果 **拒绝**：返回 `ToolMessage(status="error")` 及其原因 -- 代理看到拒绝并进行调整
4. 如果**允许**：传递到实际的工具处理程序
5. 如果 **提供者错误** 且 `fail_closed=true` （默认）：阻止调用
6. `GraphBubbleUp` 异常（LangGraph 控制信号）始终传播，从不捕获

## 三个提供商选项

### 选项 1：内置 AllowlistProvider（零依赖）

最简单的选择。随 DeerFlow 一起提供。按名称阻止或允许工具。无需外部包、无需 passport、无需网络。

**config.yaml:**
```yaml
guardrails:
  enabled: true
  provider:
    use: deerflow.guardrails.builtin:AllowlistProvider
    config:
      denied_tools: ["bash", "write_file"]
```

这会阻止所有请求的 `bash`和`write_file`。所有其他工具都会通过。

您还可以使用白名单（仅允许使用以下工具）：
```yaml
guardrails:
  enabled: true
  provider:
    use: deerflow.guardrails.builtin:AllowlistProvider
    config:
      allowed_tools: ["web_search", "read_file", "ls"]
```

**尝试一下：**
1. 将上面的配置添加到您的 `config.yaml`
2. 开始 DeerFlow: `make dev`
3. 询问代理：“使用 bash 运行 echo hello”
4. 代理看到：`Guardrail denied: tool 'bash' was blocked (oap.tool_not_allowed)`

### 选项 2：OAP 护照提供商（基于策略）

用于基于 [Open Agent Passport (OAP)](https://github.com/aporthq/aport-spec) 开放标准的策略执行。 OAP 护照是 JSON 文件，声明代理人的身份、能力和操作限制。任何读取 OAP 护照并返回 OAP 合规决定的提供商均与 DeerFlow 合作。

```
┌─────────────────────────────────────────────────────────────┐
│                    OAP Passport (JSON)                        │
│                   (open standard, any provider)              │
│  {                                                           │
│    "spec_version": "oap/1.0",                                │
│    "status": "active",                                       │
│    "capabilities": [                                         │
│      {"id": "system.command.execute"},                       │
│      {"id": "data.file.read"},                               │
│      {"id": "data.file.write"},                              │
│      {"id": "web.fetch"},                                    │
│      {"id": "mcp.tool.execute"}                              │
│    ],                                                        │
│    "limits": {                                               │
│      "system.command.execute": {                             │
│        "allowed_commands": ["git", "npm", "node", "ls"],     │
│        "blocked_patterns": ["rm -rf", "sudo", "chmod 777"]   │
│      }                                                       │
│    }                                                         │
│  }                                                           │
└──────────────────────────┬──────────────────────────────────┘
                           │
               Any OAP-compliant provider
          ┌────────────────┼────────────────┐
          │                │                │
     Your own         APort (ref.      Other future
     evaluator        implementation)  implementations
```

**手动创建护照：**

OAP 护照只是一个 JSON 文件。您可以按照 [OAP specification](https://github.com/aporthq/aport-spec/blob/main/oap/oap-spec.md) 手动创建一个并根据 [JSON schema](https://github.com/aporthq/aport-spec/blob/main/oap/passport-schema.json) 对其进行验证。请参阅 [examples](https://github.com/aporthq/aport-spec/tree/main/oap/examples) 目录以获取模板。

**使用 APort 作为参考实现：**

[APort Agent Guardrails](https://github.com/aporthq/aport-agent-guardrails) 是 OAP 提供程序的一种开源 (Apache 2.0) 实现。它处理护照创建、本地评估和可选的托管 API 评估。

```bash
pip install aport-agent-guardrails
aport setup --framework deerflow
```

这会创建：
- `~/.aport/deerflow/config.yaml` -- 评估器配置（本地或 API 模式）
- `~/.aport/deerflow/aport/passport.json` -- OAP 护照，具有能力和限制

**config.yaml （使用 APort 作为提供者）：**
```yaml
guardrails:
  enabled: true
  provider:
    use: aport_guardrails.providers.generic:OAPGuardrailProvider
```

**config.yaml （使用您自己的 OAP 提供商）：**
```yaml
guardrails:
  enabled: true
  provider:
    use: my_oap_provider:MyOAPProvider
    config:
      passport_path: ./my-passport.json
```

任何接受 `framework` 作为 kwarg 并实现 `evaluate`/`aevaluate` 的提供者都可以工作。OAP 标准定义了护照格式和决策代码；DeerFlow 不关心由哪个提供者读取它们。

**护照控制什么：**

| 护照字段 | 它的作用 | 示例 |
|---|---|---|
| `capabilities[].id` | 代理可以使用哪些工具类别 | `system.command.execute`, `data.file.write` |
| `limits.*.allowed_commands` | 允许哪些命令 | `["git", "npm", "node"]`或`["*"]` 对于所有人 |
| `limits.*.blocked_patterns` | 模式总是被拒绝 | `["rm -rf", "sudo", "chmod 777"]` |
| `status` | 终止开关 | `active`, `suspended`, `revoked` |

**评估模式（取决于提供商）：**

OAP 提供商可能支持不同的评估模式。例如，APort 参考实现支持：

| 模式 | 它是如何工作的 | 网络 | 延迟 |
|---|---|---|---|
| **本地** | 在本地评估护照（bash 脚本）。 | 无 | ~300ms |
| **API** | 将 passport + 上下文发送给托管评估者。签署决策。 | 是 | ~65ms |

自定义 OAP 提供程序可以实现任何评估策略 - DeerFlow 中间件不关心提供程序如何做出决定。

**尝试一下：**
1. 如上安装和设置
2. 启动 DeerFlow 并询问：“创建一个名为 test.txt 的文件，内容为 hello”
3. 然后询问：“现在使用 bash rm -rf 删除它”
4. 护栏挡住了它：`oap.blocked_pattern: Command contains blocked pattern: rm -rf`

### 选项 3：自定义提供商（自带）

任何具有 `evaluate(request)`和`aevaluate(request)` 方法的 Python 类都可以。不需要基类或继承——它是一个结构协议。

```python
# my_guardrail.py

class MyGuardrailProvider:
    name = "my-company"

    def evaluate(self, request):
        from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason

        # Example: block any bash command containing "delete"
        if request.tool_name == "bash" and "delete" in str(request.tool_input):
            return GuardrailDecision(
                allow=False,
                reasons=[GuardrailReason(code="custom.blocked", message="delete not allowed")],
                policy_id="custom.v1",
            )
        return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.allowed")])

    async def aevaluate(self, request):
        return self.evaluate(request)
```

**config.yaml:**
```yaml
guardrails:
  enabled: true
  provider:
    use: my_guardrail:MyGuardrailProvider
```

确保 `my_guardrail.py` 位于 Python 路径上（例如，在后端目录中或作为包安装）。

**尝试一下：**
1. 在后端目录创建`my_guardrail.py`
2. 添加配置
3. 启动 DeerFlow 并询问：“使用 bash 删除 test.txt”
4. 您的提供商阻止了它

## 实施提供者

### 所需接口

```
┌──────────────────────────────────────────────────┐
│              GuardrailProvider Protocol            │
│                                                   │
│  name: str                                        │
│                                                   │
│  evaluate(request: GuardrailRequest)              │
│      -> GuardrailDecision                         │
│                                                   │
│  aevaluate(request: GuardrailRequest)   (async)   │
│      -> GuardrailDecision                         │
└──────────────────────────────────────────────────┘

┌──────────────────────────┐    ┌──────────────────────────┐
│     GuardrailRequest      │    │    GuardrailDecision      │
│                           │    │                           │
│  tool_name: str           │    │  allow: bool              │
│  tool_input: dict         │    │  reasons: [GuardrailReason]│
│  agent_id: str | None     │    │  policy_id: str | None    │
│  thread_id: str | None    │    │  metadata: dict           │
│  is_subagent: bool        │    │                           │
│  timestamp: str           │    │  GuardrailReason:         │
│                           │    │    code: str              │
└──────────────────────────┘    │    message: str           │
                                └──────────────────────────┘
```

### DeerFlow 工具名称

这些是您的提供商将在 `request.tool_name` 中看到的工具名称：

| 工具 | 它的作用 |
|---|---|
| `bash` | Shell命令执行 |
| `write_file` | Create/overwrite 一个文件 |
| `str_replace` | 编辑文件（查找和替换） |
| `read_file` | 读取文件内容 |
| `ls` | 列出目录 |
| `web_search` | 网络搜索查询 |
| `web_fetch` | 获取 URL 内容 |
| `image_search` | 图片搜索 |
| `present_files` | 向用户呈现文件 |
| `view_image` | 显示图像 |
| `ask_clarification` | 向用户提问 |
| `task` | 委托给子代理 |
| `mcp__*` | MCP 工具（动态） |

### OAP 原因代码

[OAP specification](https://github.com/aporthq/aport-spec) 使用的标准代码：

| 代码 | 含义 |
|---|---|
| `oap.allowed` | 工具调用授权 |
| `oap.tool_not_allowed` | 工具不在白名单中 |
| `oap.command_not_allowed` | 命令不在 allowed_commands 中 |
| `oap.blocked_pattern` | 命令与阻止的模式匹配 |
| `oap.limit_exceeded` | 操作超出限制 |
| `oap.passport_suspended` | 护照状态为 suspended/revoked |
| `oap.evaluator_error` | 提供商崩溃（失败关闭） |

### 提供商加载

DeerFlow 通过 `resolve_variable()`加载提供程序——与模型、工具和沙箱提供程序使用的机制相同。`use:` 字段是 Python 类路径：`package.module:ClassName`。

如果设置了 `config:`，则提供程序将使用 `**config`kwargs 进行实例化，并且始终注入`framework="deerflow"`。接受 `**kwargs` 以保持向前兼容：

```python
class YourProvider:
    def __init__(self, framework: str = "generic", **kwargs):
        # framework="deerflow" tells you which config dir to use
        ...
```

## 配置参考

```yaml
guardrails:
  # Enable/disable guardrail middleware (default: false)
  enabled: true

  # Block tool calls if provider raises an exception (default: true)
  fail_closed: true

  # Passport reference -- passed as request.agent_id to the provider.
  # File path, hosted agent ID, or null (provider resolves from its config).
  passport: null

  # Provider: loaded by class path via resolve_variable
  provider:
    use: deerflow.guardrails.builtin:AllowlistProvider
    config:  # optional kwargs passed to provider.__init__
      denied_tools: ["bash"]
```

## 测试

```bash
cd backend
uv run python -m pytest tests/test_guardrail_middleware.py -v
```

25 项测试涵盖：
- AllowlistProvider：允许、拒绝、白名单+拒绝名单、异步
- GuardrailMiddleware：允许通过，拒绝 OAP 代码，失败关闭，失败打开，护照转发，空原因回退，空工具名称，协议是实例检查
- 异步路径：awrap_tool_call 用于允许、拒绝、失败关闭、失败打开
- GraphBubbleUp：LangGraph 控制信号传播通过（未捕获）
- 配置：默认值，from_dict，单例 load/reset

## 文件

```
packages/harness/deerflow/guardrails/
    __init__.py              # Public exports
    provider.py              # GuardrailProvider protocol, GuardrailRequest, GuardrailDecision
    middleware.py             # GuardrailMiddleware (AgentMiddleware subclass)
    builtin.py               # AllowlistProvider (zero deps)

packages/harness/deerflow/config/
    guardrails_config.py     # GuardrailsConfig Pydantic model + singleton

packages/harness/deerflow/agents/middlewares/
    tool_error_handling_middleware.py  # Registers GuardrailMiddleware in chain

config.example.yaml          # Three provider options documented
tests/test_guardrail_middleware.py  # 25 tests
docs/GUARDRAILS.md           # 本文件
```
