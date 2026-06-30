# 05 Guardrails 安全拦截中间件

对应简历表述：

> 实现 Guardrails 安全拦截中间件，在工具调用前执行 allow/deny 决策，支持 fail-closed 策略、拒绝原因返回和工具错误消息标准化。

## 相关源码跳转

- [GuardrailMiddleware：工具执行前 allow/deny 拦截](../../backend/packages/harness/deerflow/guardrails/middleware.py#L20)
- [GuardrailProvider：Request / Decision / Reason 数据结构](../../backend/packages/harness/deerflow/guardrails/provider.py#L9)
- [内置 Guardrail provider：本地策略实现](../../backend/packages/harness/deerflow/guardrails/builtin.py#L1)
- [GuardrailsConfig：provider 与 fail-closed 配置](../../backend/packages/harness/deerflow/config/guardrails_config.py#L1)
- [SandboxAuditMiddleware：bash 内容审计层](../../backend/packages/harness/deerflow/agents/middlewares/sandbox_audit_middleware.py#L222)

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

## 学习版：Guardrails 是什么

Guardrails 是 Agent 的“运行时安全网关”。

Prompt 安全提示是：

```text
告诉模型不要做危险事
```

Guardrails 是：

```text
模型已经决定调用工具
  -> 工具还没执行
  -> 中间件拦截请求
  -> 策略判断 allow / deny
  -> deny 时返回标准 ToolMessage
```

一句话：

> Prompt 是建议，Guardrails 是执行前强制门禁。

## 成熟系统怎么做安全分层

| 层 | 解决什么 |
| --- | --- |
| Prompt Policy | 告诉模型规则 |
| Tool Permission | 哪些工具可见 |
| Guardrails | 本次调用是否允许 |
| Sandbox | 允许后在哪里执行 |
| Audit Log | 事后审计和追责 |
| Human Approval | 高风险操作人工确认 |

当前项目的 Guardrails 位于：

```text
模型输出 tool_call
  -> DeferredToolFilter
  -> GuardrailMiddleware
  -> SandboxAuditMiddleware
  -> tool handler
```

面试回答：

> 我不会把安全完全交给 prompt。模型可能被 prompt injection 诱导，也可能误判工具风险，所以工具执行前必须有确定性中间件做 allow/deny。

## 策略类型和风险分级

| 策略类型 | 例子 | 特点 |
| --- | --- | --- |
| Allowlist | 只允许某些工具 | 简单稳妥 |
| Denylist | 禁止危险工具/参数 | 易落地但可能漏 |
| Rule-based | 路径、命令、域名、参数规则 | 可解释 |
| Policy Service | 外部安全服务判断 | 集中治理 |
| Human Approval | 高风险人工确认 | 安全但慢 |
| Risk Scoring | 按风险分层处理 | 灵活 |

| 风险 | 工具/行为 | 策略 |
| --- | --- | --- |
| Low | 读只读文档、列目录 | 默认放行 |
| Medium | 写 workspace 文件、安装依赖 | 审计记录 |
| High | bash、网络请求、删除文件 | Guardrails + SandboxAudit |
| Critical | 访问密钥、越权路径、生产操作 | 拒绝或人工审批 |

推荐演进路线：

```text
allowlist + rule provider + fail-closed
  -> policy service + risk scoring
  -> approval workflow
```

## 误杀、漏放和评估

| 错误 | 含义 | 风险 |
| --- | --- | --- |
| False Positive | 安全操作被拒绝 | 影响体验 |
| False Negative | 危险操作被放行 | 安全事故 |

高风险工具上宁可多一点 false positive，也不能 false negative。

指标：

| 指标 | 含义 |
| --- | --- |
| `dangerous_call_block_rate` | 危险调用拦截率 |
| `false_positive_rate` | 正常调用误杀率 |
| `false_negative_rate` | 危险调用漏放率 |
| `provider_error_rate` | provider 异常比例 |
| `fail_closed_count` | fail-closed 触发次数 |
| `deny_reason_distribution` | 拒绝原因分布 |

事件：

```text
guardrail.evaluate.started
guardrail.allowed
guardrail.denied
guardrail.provider.error
guardrail.fail_closed
guardrail.policy.timeout
```

排障：

```text
用户说工具不能用
  -> 看 allowed-tools 是否过滤
  -> 看 deferred tool 是否未提升
  -> 看 Guardrails deny reason
  -> 看 SandboxAudit 是否 block
```

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

## 简化版代码

面试时不用背完整项目代码，可以用下面这个极简版本说明实现思路。

### 1. 定义请求、原因和决策对象

```python
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class GuardrailRequest:
    tool_name: str
    tool_args: dict[str, Any]
    thread_id: str | None = None
    user_id: str | None = None
    agent_name: str | None = None
    passport: str | None = None


@dataclass
class GuardrailReason:
    code: str
    message: str = ""


@dataclass
class GuardrailDecision:
    allow: bool
    reasons: list[GuardrailReason] = field(default_factory=list)
```

这一层只做数据建模：

- `GuardrailRequest` 是“这次工具调用要不要放行”的输入。
- `GuardrailDecision` 是 provider 返回的判断结果。
- `GuardrailReason` 用于给模型和日志解释拒绝原因。

### 2. 定义 Provider 接口

```python
class GuardrailProvider(Protocol):
    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        ...

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        ...
```

Provider 只负责策略判断，不负责执行工具。

这样可以替换不同策略来源：

```text
AllowlistProvider
RemotePolicyProvider
UserPermissionProvider
RiskModelProvider
```

### 3. 一个最简单的 AllowlistProvider

```python
class AllowlistProvider:
    def __init__(
        self,
        *,
        allow: set[str] | None = None,
        deny: set[str] | None = None,
    ) -> None:
        self.allow = allow
        self.deny = deny or set()

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if self.allow is not None and request.tool_name not in self.allow:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="tool_not_allowed",
                        message=f"tool {request.tool_name!r} not in allowlist",
                    )
                ],
            )

        if request.tool_name in self.deny:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="tool_denied",
                        message=f"tool {request.tool_name!r} is denied",
                    )
                ],
            )

        return GuardrailDecision(
            allow=True,
            reasons=[GuardrailReason(code="allowed")],
        )

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        return self.evaluate(request)
```

这个 provider 的判断顺序是：

```text
如果配置了 allowlist，工具不在 allowlist -> 拒绝
如果工具在 denylist -> 拒绝
否则 -> 放行
```

### 4. 中间件核心逻辑

下面是同步路径的极简版本：

```python
class GuardrailMiddleware:
    def __init__(
        self,
        provider: GuardrailProvider,
        *,
        fail_closed: bool = True,
        passport: str | None = None,
    ) -> None:
        self.provider = provider
        self.fail_closed = fail_closed
        self.passport = passport

    def wrap_tool_call(self, request, handler):
        guardrail_request = GuardrailRequest(
            tool_name=request.tool_call["name"],
            tool_args=request.tool_call.get("args", {}),
            thread_id=request.runtime.context.get("thread_id"),
            user_id=request.runtime.context.get("user_id"),
            agent_name=request.runtime.context.get("agent_name"),
            passport=self.passport,
        )

        try:
            decision = self.provider.evaluate(guardrail_request)
        except Exception:
            if self.fail_closed:
                decision = GuardrailDecision(
                    allow=False,
                    reasons=[
                        GuardrailReason(
                            code="provider_error",
                            message="guardrail provider error (fail-closed)",
                        )
                    ],
                )
            else:
                return handler(request)

        if decision.allow:
            return handler(request)

        return self._denied_tool_message(request, decision)
```

这段代码的重点是：

1. 从真实 tool call 里提取工具名和参数。
2. 补充线程、用户、Agent 等运行时上下文。
3. 调 provider 做策略判断。
4. `allow=True` 才执行原始工具。
5. provider 异常时按 `fail_closed` 决定拒绝还是放行。
6. 拒绝时不抛异常，而是返回标准化 ToolMessage。

### 5. 拒绝时返回标准化 ToolMessage

```python
from langchain_core.messages import ToolMessage


def _format_reasons(decision: GuardrailDecision) -> str:
    if not decision.reasons:
        return "guardrail denied tool call"

    return "; ".join(
        f"{reason.code}: {reason.message}".strip(": ")
        for reason in decision.reasons
    )


def _denied_tool_message(request, decision: GuardrailDecision) -> ToolMessage:
    return ToolMessage(
        content=f"Guardrail denied tool call. {_format_reasons(decision)}",
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )
```

为什么不直接 `raise Exception`？

因为模型已经发起了一次 tool call。如果直接抛异常，上层消息链可能断掉；返回 `ToolMessage(status="error")` 可以保持协议完整，让模型看到拒绝原因并选择下一步。

### 6. 异步路径

异步路径基本一致，只是调用 `aevaluate` 和 `await handler(request)`：

```python
async def awrap_tool_call(self, request, handler):
    guardrail_request = GuardrailRequest(
        tool_name=request.tool_call["name"],
        tool_args=request.tool_call.get("args", {}),
        thread_id=request.runtime.context.get("thread_id"),
        user_id=request.runtime.context.get("user_id"),
        agent_name=request.runtime.context.get("agent_name"),
        passport=self.passport,
    )

    try:
        decision = await self.provider.aevaluate(guardrail_request)
    except Exception:
        if self.fail_closed:
            decision = GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="provider_error",
                        message="guardrail provider error (fail-closed)",
                    )
                ],
            )
        else:
            return await handler(request)

    if decision.allow:
        return await handler(request)

    return self._denied_tool_message(request, decision)
```

### 7. 面试讲代码的顺序

可以按这条线讲：

```text
模型发起工具调用
  -> 中间件拿到 tool_name/tool_args
  -> 构造 GuardrailRequest
  -> Provider 返回 GuardrailDecision
  -> allow 才调用 handler
  -> deny 返回 ToolMessage(status="error")
  -> provider 异常默认 fail-closed
```

一句话总结：

> Guardrails 的实现本质是一个工具调用前的授权中间件：它不执行策略细节，也不执行工具本身，只负责把工具调用转成标准请求、调用 provider 判断、按 allow/deny 控制 handler 是否继续执行。

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

## 深挖补充：Guardrails 的完整决策链路

面试时可以把 Guardrails 讲成工具调用前的策略引擎。

```text
model emits tool call
  -> middleware captures tool name and args
  -> build GuardrailRequest
  -> enrich with user / agent / thread / run context
  -> classify tool risk
  -> provider returns allow / deny / warn
  -> allow: continue tool execution
  -> deny: return standardized ToolMessage
  -> record trace and metrics
```

这里的关键是：Guardrails 不改变模型输出，而是在模型输出和真实工具执行之间插入确定性控制点。

高分表达：

> 模型可以提出动作，但不能直接执行动作。Guardrails 是动作进入真实世界前的策略门禁，尤其适合拦截 bash、文件写入、外部请求、凭证访问这类高风险操作。

## 深挖补充：风险分级怎么做

不是所有工具都需要同样强度的校验。可以按风险分层：

| 风险 | 工具示例 | 策略 |
| --- | --- | --- |
| 低风险 | list_dir、read_config_sample | 轻量 allowlist |
| 中风险 | read_file、web_search | 参数检查、路径限制、来源标记 |
| 高风险 | write_file、bash、delete_file | Guardrails 强校验 + Sandbox |
| 极高风险 | credential export、network exfiltration | 默认 deny 或人工确认 |

面试里可以补一句：

> 风险分级能平衡安全和性能。只读工具不需要每次走复杂外部策略，但有副作用的工具必须同步拦截。

## 深挖补充：误杀和漏放怎么处理

Guardrails 一定会面对两个问题：

- **误杀**：安全动作被拒绝，影响可用性。
- **漏放**：危险动作被允许，影响安全性。

处理思路：

| 问题 | 处理 |
| --- | --- |
| 误杀 | 记录 deny reason、policy version、tool args，人工复核后加白名单或细化规则 |
| 漏放 | 进入安全事故流程，补 eval case，升级策略，回放历史 trace |
| 模糊场景 | 返回 clarification，让模型或用户确认 |
| 高频拒绝 | 优化工具说明，避免模型反复尝试不可用路径 |

推荐回答：

> 我不会追求一次性规则完美，而是让每次 allow/deny 都可解释、可回放、可版本化。这样误杀能修，漏放能追责。

## 深挖补充：拒绝消息怎么设计

拒绝后不能只返回 “blocked”。模型需要知道下一步怎么调整。

好的拒绝消息包括：

```json
{
  "error": "guardrail_denied",
  "reason": "Command attempts to read credentials",
  "policy": "credential_access",
  "retryable": false,
  "suggestion": "Ask the user for a safe alternative or use read-only project files."
}
```

这样做有三个好处：

1. 模型知道不是工具崩了，而是策略拒绝。
2. 循环检测能识别连续同类拒绝。
3. 观测系统能按 policy 聚合误杀和攻击尝试。

## 深挖补充：Guardrails 和 prompt safety 的区别

| 维度 | prompt safety | Guardrails |
| --- | --- | --- |
| 位置 | 模型调用前 | 工具执行前 |
| 强度 | 软约束 | 硬边界 |
| 可审计性 | 弱 | 强 |
| 抵抗注入 | 容易被绕过 | 不依赖模型自觉 |
| 适合内容 | 行为规范、回答风格 | 工具权限、副作用动作、安全策略 |

面试回答：

> Prompt safety 告诉模型应该怎么做，Guardrails 决定某个工具调用能不能真的执行。前者是引导，后者是执行边界。

## 深挖补充：面试攻防

### Q：Guardrails provider 挂了怎么办？

高风险工具 fail-closed，低风险只读工具可以按配置降级。策略服务不可用时不能默认放行 bash、write_file、网络请求这类副作用动作。

### Q：Guardrails 会不会让 Agent 变笨？

会限制一部分行动，但这是必要边界。更好的做法是给拒绝消息提供替代建议，让模型能换安全路径完成任务。

### Q：怎么验证 Guardrails 有效？

准备一组 attack eval，包括读取凭证、越权路径、危险命令、删除文件、外传数据、prompt injection 诱导工具调用。P0 是危险动作不能放行，P1 是正常动作不要大量误杀。

### Q：Guardrails 能不能替代 Sandbox？

不能。Guardrails 判断应不应该执行，Sandbox 限制执行环境。即使 Guardrails 允许，Sandbox 也要限制路径、资源和网络。

## 面试补强：Guardrails 要讲 fail-closed、误杀漏放和策略版本

这题不要只答“工具执行前做安全检查”。更完整的说法是：

> Guardrails 是工具执行前的强制策略层，它不依赖模型自觉。模型可以提出 tool call，但真正执行前必须把 tool name、args、user/thread context 和风险等级交给策略层判断。低风险只读操作可以轻量放行，高风险副作用操作要 fail-closed、可审计、可回放，拒绝后还要给模型结构化原因，避免它误以为工具崩了。

### 回答骨架

```text
位置：
  model output -> tool call -> GuardrailMiddleware -> SandboxAudit -> real tool

输入：
  tool_name、tool_args、thread_id、user_id、runtime context、risk level

输出：
  allow / deny + reason code + retryable + suggestion

异常：
  高风险 fail-closed，低风险只读按配置降级，不能默认放行副作用工具。

观测：
  记录 policy_version、deny_reason、tool_args 摘要、provider latency、最终处理结果。
```

### 高频追问：provider 挂了怎么办

回答时不要一刀切说“全部拒绝”或“全部放行”。可以按风险分层：

| 风险 | provider 异常策略 | 原因 |
| --- | --- | --- |
| Low | 可配置降级放行 | 只读、无副作用，优先可用性 |
| Medium | 降级但加强审计 | 例如 workspace 内写入，需要留痕 |
| High | fail-closed | bash、删除、网络请求等有真实副作用 |
| Critical | fail-closed 或人工确认 | 凭证、越权路径、生产资源 |

面试回答：

> 这里我不会默认 allow。策略服务不可用本身就是风险信号，尤其对 bash、write、network、delete 这类工具必须 fail-closed。只读工具可以配置化降级，但要打 provider_error 和 degraded_allow 日志。

### 高频追问：Guardrails 会不会影响 Agent 效率

可以这样答：

> 会增加一次决策开销，所以不能所有工具都走同样重的策略。工程上我会做风险分层：只读工具走本地 allowlist，副作用工具走规则和审计，高危工具走外部 policy 或人工确认。这样把延迟花在风险真正高的地方。

关键点是把“安全”和“性能”放在同一套策略里，而不是二选一。

### 高频追问：误杀和漏放怎么闭环

Guardrails 最忌讳说成静态规则。生产系统要能修规则：

| 问题 | 需要记录 | 闭环动作 |
| --- | --- | --- |
| 误杀 | `deny_reason`、`policy_version`、`tool_name`、参数摘要、用户意图 | 人工复核，细化规则或加入条件白名单 |
| 漏放 | 完整 trace、执行结果、影响范围、对应策略版本 | 补 attack eval，升级策略，回放历史 trace |
| 高频拒绝 | 拒绝分布、重试次数、模型后续动作 | 优化工具描述或给替代建议 |
| provider 慢 | latency、timeout、risk level | 本地缓存低风险策略，高风险保持阻断 |

核心指标：

| 指标 | 说明 |
| --- | --- |
| `guardrail_deny_rate` | 拒绝比例 |
| `guardrail_provider_error_rate` | 策略服务异常比例 |
| `guardrail_latency_ms` | 决策延迟 |
| `false_positive_rate` | 正常动作误杀率 |
| `false_negative_incident_count` | 危险动作漏放事故数 |
| `deny_reason_distribution` | 拒绝原因分布 |
| `policy_version` | 策略版本 |
| `fail_closed_count` | fail-closed 次数 |

### 高频追问：拒绝后模型应该怎么办

不要只返回一段自然语言。更好的工具错误消息要结构化：

```json
{
  "error": "guardrail_denied",
  "reason_code": "credential_access_denied",
  "message": "The requested command attempts to read credential files.",
  "retryable": false,
  "suggestion": "Use project-local configuration files or ask the user to provide an approved credential path.",
  "policy_version": "guardrails-2026-06-30"
}
```

面试回答：

> 拒绝消息要让模型知道这是策略拒绝，不是工具故障。`retryable=false` 可以避免模型原地重试，`suggestion` 可以引导它换安全路径，`policy_version` 方便后续复盘误杀。

### 高频追问：你负责到什么程度

可以这样收束：

> 我负责的是 GuardrailRequest / Decision 数据结构、工具调用前的 middleware 拦截、fail-closed 行为、标准化 ToolMessage、deny reason 日志和基础评估集。具体策略 provider 可以是本地规则或外部服务，但调用边界、异常策略和观测字段必须由 Agent 运行时统一保证。
