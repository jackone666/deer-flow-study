# 11 沙箱系统设计

这一篇专门用于回答：

> “为什么 Agent 平台需要沙箱？”
> “你的沙箱系统怎么和 Harness 接起来？”
> “远程 Sandbox Backend 和本地执行有什么区别？”
> “Sandbox、Guardrails、工具权限三者怎么分工？”

## 一句话总述

> 我把沙箱设计成 Agent Harness 的工具执行隔离层：模型仍然通过工具接口表达意图，但 bash、文件读写、产物生成等有副作用操作不会直接落到宿主机，而是通过 `SandboxProvider -> AioSandboxProvider -> RemoteSandboxBackend -> HTTP provisioner` 进入线程级远程沙箱执行。

## 为什么需要沙箱

Agent 和普通后端接口最大的差异是：模型会动态决定要不要调用工具，以及怎么组合工具。

没有沙箱时：

```text
模型生成 bash / 文件写入
  -> Agent 进程直接执行
  -> 宿主机文件、环境变量、进程、网络都可能暴露
```

这会带来几个问题：

| 风险 | 例子 | 沙箱要解决什么 |
| --- | --- | --- |
| 文件越权 | 模型读写非项目目录 | 限定 workspace/uploads/outputs |
| 命令副作用 | `rm -rf`、后台进程、改系统配置 | 隔离执行环境，配合审计拦截 |
| 依赖污染 | `pip install` 改宿主机环境 | 把依赖安装限制在线程沙箱 |
| 多租户串扰 | A 用户看到 B 用户文件 | 按 thread/user 隔离工作目录 |
| 产物不可追踪 | 文件散落宿主机 | 统一 outputs 目录和 artifact 管理 |

面试回答：

> 沙箱解决的是“工具执行在哪里发生”的问题。模型可以决定调用 bash 或写文件，但真正执行必须进入受控环境，避免 Agent 进程直接碰宿主机。

## 学习版：沙箱和容器的关系

沙箱不是一定等于 Docker，但 Docker / Pod 是常见实现方式。

从抽象看：

```text
Sandbox = 受控执行环境
Container/Pod = Sandbox 的一种实现载体
Remote Backend = 负责创建和管理执行环境的服务
```

Agent 侧最重要的是抽象边界：

```text
Agent 不关心底层是 Docker、k3s、Firecracker 还是远程 VM
Agent 只依赖 Sandbox 接口：execute/read/write/list
```

面试回答：

> 我把 Sandbox 做成接口，而不是把 Docker 命令写死在工具里。这样底层执行环境可以换成本地容器、远程 Pod 或其他隔离技术，上层工具不用改。

## 生产级沙箱需要哪些能力

| 能力 | 说明 |
| --- | --- |
| 隔离 | 文件系统、进程、网络、用户权限隔离 |
| 生命周期 | 创建、发现、复用、销毁 |
| 资源限制 | CPU、内存、磁盘、运行时长 |
| 文件同步 | workspace/uploads/outputs 映射或同步 |
| 安全审计 | bash 命令、文件访问、网络访问记录 |
| 空闲回收 | 避免资源泄漏 |
| 多租户 | user/thread 级隔离 |
| 可观测 | acquire、execute、destroy 都有 trace/metrics |

当前项目已经重点覆盖：

```text
远程 backend
线程级复用
懒加载
确定性 sandbox_id
warm pool
idle cleanup
startup reconciliation
SandboxAudit
```

可继续增强：

```text
CPU/memory/disk quota
network policy
egress allowlist
per-tool timeout
artifact sync protocol
human approval for critical actions
```

## 从 0 到 1 落地路线

### 阶段 1：接口抽象

先定义上层工具依赖的能力：

```python
class Sandbox:
    def execute_command(self, command: str) -> str: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def list_dir(self, path: str) -> list[str]: ...
```

再定义生命周期：

```python
class SandboxProvider:
    def acquire(self, thread_id: str) -> str: ...
    def get(self, sandbox_id: str) -> Sandbox | None: ...
    def release(self, sandbox_id: str) -> None: ...
```

### 阶段 2：接入 Harness

```text
ThreadDataMiddleware 准备目录
SandboxMiddleware 管生命周期
工具调用 ensure_sandbox_initialized
ThreadState 保存 sandbox_id
```

### 阶段 3：远程化

```text
AioSandboxProvider
  -> RemoteSandboxBackend
  -> provisioner
  -> sandbox HTTP service
```

缺 `provisioner_url` 直接 fail-fast。

### 阶段 4：安全审计

```text
bash command
  -> SandboxAuditMiddleware
  -> block / warn / pass
  -> audit log
```

### 阶段 5：观测和回收

```text
trace: sandbox.acquire / discover / create / execute
metrics: latency / error / reuse / idle_destroy
cleanup: idle checker / startup reconciliation
```

## 沙箱在 Harness 里的位置

沙箱不是一个普通工具，而是工具执行环境。

完整链路：

```text
用户请求
  -> ThreadDataMiddleware 准备线程目录
  -> SandboxMiddleware 注册沙箱生命周期
  -> 模型决定调用 bash/read_file/write_file
  -> sandbox tool 调 ensure_sandbox_initialized()
  -> get_sandbox_provider()
  -> AioSandboxProvider.acquire(thread_id)
  -> RemoteSandboxBackend 调 provisioner
  -> provisioner 创建/发现远程 sandbox
  -> 工具在 sandbox 中执行
  -> ToolMessage 返回模型
```

一句话：

> Harness 负责把 thread_id、thread_data、sandbox_id 串起来；工具层负责在第一次真实需要执行时懒加载沙箱。

## 核心对象分层

| 层 | 对象 | 职责 |
| --- | --- | --- |
| 抽象接口 | `Sandbox` | 定义执行命令、读文件、写文件、列目录等能力 |
| 生命周期 | `SandboxProvider` | 获取、查询、释放沙箱 |
| 中间件 | `SandboxMiddleware` | 在 Agent 生命周期里接入 sandbox state |
| 远程 provider | `AioSandboxProvider` | 管理远程 sandbox 的创建、复用、发现、回收 |
| 后端 | `RemoteSandboxBackend` | 通过 HTTP 调 provisioner 创建/销毁/发现 sandbox |
| 工具层 | `bash_tool`、`read_file_tool` 等 | 模型真正调用的工具入口 |
| 审计层 | `SandboxAuditMiddleware` | 对 bash 命令做 block/warn/pass 判定 |

## 当前项目的远程沙箱架构

当前项目运行时已经收敛为远程 HTTP backend：

```text
Agent Process
  -> AioSandboxProvider
  -> RemoteSandboxBackend
  -> HTTP provisioner
  -> k3s / container runtime
  -> sandbox service HTTP API
```

配置形态：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
  idle_timeout: 600
  replicas: 3
```

关键设计：

- `provisioner_url` 必填，缺失就启动失败。
- Agent 进程只作为 client，不直接启动本地 Docker/Apple Container。
- provisioner 负责创建 Pod/Service 或远程执行单元。
- sandbox service 暴露 HTTP API，Agent 通过 `AioSandbox` 适配到统一 `Sandbox` 接口。
- 本地实现可以作为历史参考或测试参考，但不作为生产运行时 fallback。

面试回答：

> 我没有让 Agent 进程自己执行命令，也没有在缺配置时 fallback 到本地执行。远程沙箱必须显式配置 provisioner_url，配置缺失直接 fail-fast，这样安全边界不会因为环境差异被悄悄绕开。

## 为什么要远程 Backend / HTTP Backend

本地沙箱适合开发便利，但生产里更推荐远程 backend。

| 维度 | 本地执行/本地容器 | 远程 HTTP Backend |
| --- | --- | --- |
| 安全边界 | 容易和 Agent 进程共主机 | 执行面从 Agent 进程剥离 |
| 多租户 | 需要本机做复杂隔离 | 可以交给 k8s/容器平台 |
| 扩缩容 | 受单机资源限制 | provisioner 统一调度 |
| 故障隔离 | 容器/命令问题影响本机 | sandbox 故障可独立回收 |
| 运维观测 | 分散在本机 | provisioner/sandbox 统一上报 |

取舍：

- 远程 backend 有网络开销和 provisioner 可用性要求。
- 但它让安全边界、扩缩容、资源回收、多租户隔离更清楚。

## 懒加载机制

`SandboxMiddleware` 默认 `lazy_init=True`。

意思是：

```text
Agent 开始
  -> 不立即创建 sandbox
  -> 只有模型真的调用 bash/read/write 等工具
  -> ensure_sandbox_initialized() 才创建或复用 sandbox
```

好处：

- 纯问答不浪费 sandbox 启动成本。
- 避免每轮对话都创建容器。
- 工具调用路径更自然：需要执行时才拿执行环境。

简化代码：

```python
def ensure_sandbox_initialized(runtime):
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state:
        sandbox = get_sandbox_provider().get(sandbox_state["sandbox_id"])
        if sandbox:
            runtime.context["sandbox_id"] = sandbox_state["sandbox_id"]
            return sandbox

    thread_id = runtime.context.get("thread_id")
    if not thread_id:
        raise SandboxRuntimeError("Thread ID not available")

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire(thread_id)
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}
    runtime.context["sandbox_id"] = sandbox_id

    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition")
    return sandbox
```

面试回答：

> 沙箱是懒加载的。SandboxMiddleware 把生命周期接进 Harness，但真正创建发生在第一个 sandbox tool 调用时，这样既保留统一状态，又避免无工具请求浪费资源。

## 线程级复用

沙箱和 `thread_id` 绑定。

```text
thread_id
  -> deterministic sandbox_id
  -> provider cache
  -> discover existing sandbox
  -> create if missing
```

为什么这样设计：

- 同一线程多轮对话可以复用 workspace。
- 代码修改、下载依赖、生成文件能跨轮保留。
- 多进程部署时可以通过确定性 `sandbox_id` 发现已有沙箱。
- 避免同一个线程并发创建多个执行环境。

当前 provider 里做了几类保护：

- 进程内 `_thread_sandboxes` 缓存。
- 基于 `thread_id` 的锁，防止同线程并发创建。
- 确定性 `sandbox_id`，支持跨进程发现。
- warm pool 复用，降低冷启动成本。
- idle checker，清理空闲沙箱。
- startup reconciliation，接管旧进程遗留的远程沙箱。

面试回答：

> 我用 thread_id 派生确定性 sandbox_id，同一线程多轮会复用同一个 sandbox；如果进程重启，也可以通过 provisioner discover 找回已有 sandbox，避免状态丢失或重复创建。

## RemoteSandboxBackend 做什么

`RemoteSandboxBackend` 是一个轻量 HTTP client。

它不直接关心模型，也不关心工具，只负责 sandbox 生命周期：

| 方法 | HTTP 行为 | 作用 |
| --- | --- | --- |
| `create` | `POST /api/sandboxes` | 创建 sandbox |
| `destroy` | `DELETE /api/sandboxes/{id}` | 销毁 sandbox |
| `discover` | `GET /api/sandboxes/{id}` | 查找已存在 sandbox |
| `is_alive` | `GET /api/sandboxes/{id}` | 判断是否 Running |
| `list_running` | `GET /api/sandboxes` | 启动时接管遗留 sandbox |

简化代码：

```python
class RemoteSandboxBackend(SandboxBackend):
    def create(self, thread_id, sandbox_id, extra_mounts=None):
        resp = requests.post(
            f"{self.provisioner_url}/api/sandboxes",
            json={
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "user_id": get_effective_user_id(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=data["sandbox_url"],
        )

    def discover(self, sandbox_id):
        resp = requests.get(
            f"{self.provisioner_url}/api/sandboxes/{sandbox_id}",
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return SandboxInfo(sandbox_id=sandbox_id, sandbox_url=data["sandbox_url"])
```

面试回答：

> RemoteSandboxBackend 的职责很窄，只负责跟 provisioner 交互。这样 Agent Harness 不需要知道底层是 k3s、Docker 还是其他执行平台，只依赖 create/discover/destroy/is_alive 这些抽象生命周期能力。

## 工具如何进入沙箱

模型看到的是工具：

```text
bash
ls
glob
grep
read_file
write_file
str_replace
```

但这些工具内部都会先拿 sandbox：

```text
bash_tool()
  -> ensure_sandbox_initialized(runtime)
  -> sandbox.execute_command(command)

read_file_tool()
  -> ensure_sandbox_initialized(runtime)
  -> sandbox.read_file(path)

write_file_tool()
  -> ensure_sandbox_initialized(runtime)
  -> sandbox.write_file(path, content)
```

所以模型不是直接操作文件系统，而是通过工具协议进入 sandbox。

## SandboxAuditMiddleware

沙箱解决“在哪里执行”，审计解决“这条命令能不能执行”。

`SandboxAuditMiddleware` 对 bash 做三档分类：

| 判定 | 行为 | 例子 |
| --- | --- | --- |
| `block` | 直接返回 error ToolMessage，不调用真实工具 | `rm -rf /`、fork bomb、`curl | bash` |
| `warn` | 允许执行，但在结果里追加警告 | `pip install`、`chmod 777` |
| `pass` | 正常执行 | `ls`、`pytest`、`python script.py` |

简化代码：

```python
def wrap_tool_call(self, request, handler):
    if request.tool_call["name"] != "bash":
        return handler(request)

    command = request.tool_call["args"].get("command", "")
    verdict = classify_command(command)

    if verdict == "block":
        return ToolMessage(
            content="Command blocked: security violation detected",
            tool_call_id=request.tool_call["id"],
            name="bash",
            status="error",
        )

    result = handler(request)
    if verdict == "warn":
        result.content += "\n\nWarning: medium-risk command."
    return result
```

面试回答：

> 沙箱不是万能安全策略，所以我又加了一层 SandboxAuditMiddleware。它在 bash 工具真正执行前做确定性规则判断，高危命令直接拦截，中危命令允许但提示，所有命令都记录审计日志。

## Sandbox、Guardrails、工具权限的区别

这三层容易混在一起，面试要讲清楚。

| 层 | 回答的问题 | 例子 |
| --- | --- | --- |
| 工具权限 | 这个 Agent 能不能看到/调用这个工具 | allowed-tools、deferred tools |
| Guardrails | 这次工具调用按策略允不允许 | allow/deny、fail-closed、拒绝原因 |
| Sandbox | 如果允许执行，在哪里执行 | 远程隔离环境、workspace/uploads/outputs |
| SandboxAudit | bash 命令内容是否危险 | block/warn/pass |

一句话：

> 工具权限管“有没有入口”，Guardrails 管“这次能不能用”，Sandbox 管“在哪里执行”，SandboxAudit 管“bash 内容是否危险”。

## 安全边界

沙箱系统的安全边界可以这样讲：

- Agent 进程不直接执行用户/模型生成的 bash。
- 文件操作通过 sandbox API 进入受控目录。
- 线程维度隔离 workspace/uploads/outputs。
- skills 挂载只读，避免运行时污染能力定义。
- 高危 bash 在工具执行前被审计拦截。
- sandbox 缺配置 fail-fast，不静默降级到本地执行。
- 空闲 sandbox 会被清理，避免资源泄漏。

需要补一句诚实边界：

> 沙箱不是替代所有安全策略。它主要提供执行隔离，仍然需要 Guardrails、工具权限、命令审计、路径校验、资源限额和可观测性一起工作。

## 可观测性指标

沙箱相关指标建议记录：

| 指标 | 含义 |
| --- | --- |
| `sandbox_acquire_latency_ms` | 创建/发现沙箱耗时 |
| `sandbox_acquire_fail_count` | 沙箱获取失败次数 |
| `sandbox_reuse_count` | 复用已有沙箱次数 |
| `sandbox_discover_count` | 通过 provisioner 发现已有沙箱次数 |
| `sandbox_idle_destroy_count` | 空闲回收数量 |
| `sandbox_command_latency_ms` | 命令执行耗时 |
| `sandbox_command_block_count` | 审计拦截次数 |
| `sandbox_provider_error_count` | provisioner/backend 异常 |

Trace 上建议拆成：

```text
tool.call:bash
  -> sandbox.ensure_initialized
  -> sandbox.provider.acquire
  -> sandbox.backend.discover/create
  -> sandbox.audit
  -> sandbox.execute_command
```

## 常见故障和排查

| 现象 | 优先看哪里 | 可能原因 |
| --- | --- | --- |
| 工具报没有 sandbox | runtime context/state | 缺 `thread_id` 或 middleware 未接入 |
| 首次 bash 很慢 | acquire trace | sandbox 冷启动、provisioner 慢 |
| 同线程状态丢失 | sandbox_id / discover | 没有复用同一 thread_id |
| provisioner 创建失败 | backend error log | provisioner 不可达或资源不足 |
| 命令被拒绝 | SandboxAudit log | 命中高危规则 |
| 文件找不到 | workspace/uploads/outputs | 路径不在约定目录或同步失败 |

## 面试 2 分钟讲法

> 沙箱在我的 Agent Harness 里是工具执行隔离层，不是普通工具。ThreadDataMiddleware 先准备线程级 workspace/uploads/outputs，SandboxMiddleware 把 sandbox 生命周期接进 Harness，但默认懒加载，只有 bash、read_file、write_file 这类工具第一次被调用时才通过 ensure_sandbox_initialized 创建或复用 sandbox。运行时走 AioSandboxProvider，它根据 thread_id 派生确定性 sandbox_id，先查进程内缓存和 warm pool，再通过 RemoteSandboxBackend 调 HTTP provisioner discover 或 create。Agent 进程不直接执行宿主机命令，真正执行发生在远程 sandbox HTTP API 后面。安全上，工具权限决定模型能不能看到工具，Guardrails 决定这次工具调用允不允许，Sandbox 决定允许后在哪里执行，SandboxAudit 决定 bash 命令内容是否危险。缺 provisioner_url 会 fail-fast，不会静默 fallback 到本地执行。

## 高频追问

### 1. 为什么不用本地 Docker 作为 fallback？

因为 fallback 会让安全边界变得不确定。生产里如果远程 provisioner 没配置，系统应该显式失败，而不是悄悄把命令放到本机或本地容器执行。

### 2. 为什么沙箱要懒加载？

很多对话不需要文件或 bash。如果每轮都创建 sandbox，会增加冷启动成本和资源占用。懒加载能让纯问答保持轻量，只有工具真正需要执行时才付出成本。

### 3. 为什么要按 thread_id 复用？

Agent 任务通常跨多轮。用户上一轮写的文件、安装的依赖、生成的产物，下一轮还可能继续用。按 thread_id 复用可以保留任务上下文，同时又不会跨线程串数据。

### 4. Sandbox 和 Guardrails 谁更重要？

两者解决的问题不同，不能互相替代。Guardrails 是执行前策略判断，Sandbox 是执行时环境隔离。高风险工具应该先经过 Guardrails，再进入 Sandbox。

### 5. 沙箱怎么防止资源泄漏？

Provider 维护 active sandboxes 和 warm pool，通过 idle checker 清理空闲实例；启动时还会 list_running，把旧进程遗留的 sandbox 纳入管理，避免远程资源一直运行。

### 6. 面试官问“这个系统还有什么可以优化”怎么答？

可以说三点：

- 资源限额：为每个 sandbox 加 CPU、内存、磁盘、网络访问策略。
- 观测增强：把 acquire/create/discover/execute 都做成 trace span。
- 数据闭环：把高频被拦截命令、超时命令、路径错误沉淀成评测 case 和工具说明优化。
