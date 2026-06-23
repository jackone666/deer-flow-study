# 05 Guardrails 安全拦截中间件

对应简历表述：

> 实现 Guardrails 安全拦截中间件，在工具调用前执行 allow/deny 决策，支持 fail-closed 策略、拒绝原因返回和工具错误消息标准化。

## 面试官想听什么

Guardrails 是 Agent 安全能力，面试官会重点看：

1. 为什么要在工具调用前拦截？
2. guardrail 和 prompt 安全提示有什么区别？
3. allow/deny 决策怎么表达？
4. provider 异常时为什么 fail-closed？
5. 拒绝后怎么反馈给模型？
6. 怎么扩展不同安全策略？

## 设计目标

Agent 工具调用有真实副作用：

- 写文件。
- 执行 bash。
- 调外部 API。
- 访问用户数据。
- 创建/删除资源。

只靠 prompt 让模型“不要做危险事”是不够的，因为模型可能误判或被提示注入影响。

所以要在工具执行前加确定性拦截：

```text
模型发起 tool call
  -> GuardrailMiddleware.wrap_tool_call
  -> 构造 GuardrailRequest
  -> provider.evaluate(request)
  -> allow: 调用原工具 handler
  -> deny: 返回 ToolMessage(status="error")
```

关键代码：

- `backend/packages/harness/deerflow/guardrails/middleware.py`
- `backend/packages/harness/deerflow/guardrails/provider.py`
- `backend/packages/harness/deerflow/guardrails/builtin.py`
- `backend/packages/harness/deerflow/config/guardrails_config.py`

## 核心对象

### GuardrailRequest

表示一次待检查的工具调用。

典型字段：

```text
tool_name
tool_args
thread/user/runtime context
passport?
```

它是 provider 做安全判断的输入。

### GuardrailDecision

表示安全判断结果：

```text
allow: bool
reasons: list[GuardrailReason]
```

如果 `allow=False`，中间件不会执行真实工具。

### GuardrailReason

用于解释原因：

```text
code: "oap.tool_not_allowed"
message: "tool 'bash' is denied"
```

## 中间件执行流程

```text
wrap_tool_call(request, handler)
  -> build GuardrailRequest
  -> provider.evaluate(...)
  -> if allow:
        return handler(request)
     else:
        return denied ToolMessage(status="error")
  -> if provider raises:
        if fail_closed:
            return denied ToolMessage(status="error")
        else:
            return handler(request)
```

异步路径 `awrap_tool_call` 同理。

## 为什么是工具调用前

因为很多工具有副作用，一旦执行再拦截就晚了。

例子：

```text
bash: rm -rf ...
write_file: 覆盖用户文件
api_delete: 删除外部资源
```

面试回答：

> Guardrails 必须在工具调用前做，因为工具执行可能有不可逆副作用。它不是日志审计，而是执行前授权。模型可以提出调用意图，但真正执行前必须经过确定性策略判断。

## fail-closed 策略

`fail_closed=True` 表示：

```text
guardrail provider 出错 -> 默认拒绝工具调用
```

为什么？

如果 provider 超时、异常、不可用时默认放行，相当于安全系统故障时解除安全。

面试回答：

> 我默认 fail-closed，因为安全系统不可用时不能默认放行。尤其 Agent 工具可能写文件、执行命令或访问外部服务，provider 出错时应该返回标准化拒绝消息，而不是执行原工具。

## 拒绝后为什么返回 ToolMessage

模型已经发起了 tool call。为了维持消息协议一致，需要返回一个 tool response。

拒绝消息形态：

```text
ToolMessage(
  content="Guardrail denied tool call: ...",
  status="error",
  tool_call_id=...
)
```

好处：

1. 消息链路完整。
2. 模型能看到工具失败原因。
3. 后续模型可以换安全方案。
4. 调用方可以统一处理工具错误。

## 标准化错误消息

不要直接抛异常给上层。

更好的做法是：

```text
deny -> ToolMessage(status="error")
provider exception + fail_closed -> ToolMessage(status="error")
```

这样所有工具错误都能进入统一处理逻辑。

## Provider 可扩展设计

Provider 是策略接口，可以有多种实现：

1. **AllowlistProvider**：允许名单/拒绝名单。
2. **OAP Provider**：外部策略服务。
3. **自定义 Provider**：按企业规则、用户权限、工具参数判断。

面试回答：

> 我把策略判断抽象成 provider，而不是写死在 middleware。middleware 只负责拦截和执行控制，provider 负责安全策略。这样后续可以从内置 allowlist 切换到外部策略服务，而不改 Agent 主流程。

## Guardrails 和 Sandbox 的区别

这题很常见。

```text
Guardrails：执行前判断“能不能调用”。
Sandbox：执行时限制“能影响哪里”。
```

例子：

- Guardrails 拦截 `bash rm -rf /`。
- Sandbox 即使 bash 执行，也把影响限制在隔离环境。

面试回答：

> Guardrails 是准入控制，Sandbox 是执行隔离。两者不能互相替代。Guardrails 负责在工具执行前做 allow/deny，Sandbox 负责即使工具执行也把副作用限制在隔离边界内。

## Guardrails 和工具权限的区别

工具权限：

- 决定工具是否暴露给模型。
- 比如 skill allowed-tools 不给 bash。

Guardrails：

- 即使工具暴露了，也检查本次调用参数是否安全。
- 比如 bash 可用，但 `rm -rf` 被拒绝。

面试回答：

> 工具权限是静态能力控制，Guardrails 是动态调用控制。前者决定“有没有这个工具”，后者决定“这一次这样调用是否允许”。

## 可讲的 trade-off

### fail-open vs fail-closed

fail-open：

- 优点：可用性高。
- 缺点：安全服务故障时放行风险操作。

fail-closed：

- 优点：安全优先。
- 缺点：provider 故障会影响工具可用性。

我的选择：

> 默认 fail-closed，生产场景安全优先。对于纯只读工具可以按配置放宽，但写文件、bash、外部 API 这类高风险工具必须 fail-closed。

### 内置策略 vs 外部策略服务

内置策略：

- 优点：简单、低延迟。
- 缺点：规则更新不灵活。

外部策略服务：

- 优点：集中管理、可审计、多租户。
- 缺点：多一次网络调用，有可用性问题。

我的选择：

> middleware 保持 provider 抽象。开发和测试用内置 allowlist，生产可以接外部策略服务。

## 高频追问

### 1. 如果模型被 prompt injection 诱导调用危险工具怎么办？

prompt 只能影响模型输出，Guardrails 在模型之后、工具之前执行，是确定性控制层。即使模型发起危险 tool call，也会被拦截。

### 2. provider 判断需要哪些上下文？

至少包括工具名、参数、线程、用户、Agent、运行时 passport。复杂场景还可以加入用户权限、环境、资源标签。

### 3. Guardrails 会不会影响性能？

会增加一次策略判断。内置 allowlist 开销很小；外部 provider 有网络延迟。可以按工具风险分级：高风险强校验，低风险轻校验。

### 4. 拒绝后模型会不会无限重试？

可以在错误消息里给出明确原因，同时配合循环检测或工具错误处理。如果连续同类拒绝，提示模型换方案或向用户确认。
