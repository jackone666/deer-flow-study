# 04 工具治理体系

对应简历表述：

> 设计工具治理体系，支持工具分组、延迟工具加载、工具 hash 校验、TF-IDF 检索召回、工具权限控制和大规模工具集下的上下文压缩。

## 面试官想听什么

工具治理是 Agent 平台的核心工程问题。面试官会关心：

1. 工具多了为什么不能全部绑定给模型？
2. 工具权限怎么控制？
3. 延迟工具加载怎么保证模型知道有哪些工具？
4. tool hash 为什么必要？
5. TF-IDF 怎么用于工具检索？
6. 怎么评估工具检索效果？

## 问题背景

Agent 工具数量少时，可以直接把所有工具 schema 绑定到模型。

但当工具很多时会出现问题：

1. **上下文膨胀**：每个工具 schema 都占 token。
2. **选择干扰**：模型面对大量相似工具容易选错。
3. **权限风险**：不是每个场景都应该暴露所有工具。
4. **版本漂移**：工具目录变化后，旧的提升状态可能失效。

所以工具治理要解决：

```text
哪些工具可用
哪些工具现在绑定
哪些工具可被检索
哪些工具被提升
工具目录变化后如何失效旧状态
```

## 工具装配流程

关键代码：

- `backend/packages/harness/deerflow/tools/tools.py`
- `backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- `backend/packages/harness/deerflow/tools/builtins/tool_search.py`
- `backend/packages/harness/deerflow/agents/middlewares/deferred_tool_filter_middleware.py`
- `backend/packages/harness/deerflow/skills/tool_policy.py`

典型流程：

```text
读取 config.tools
  -> 按 group 过滤
  -> resolve_variable 加载工具对象
  -> 追加内置工具 / 子 Agent 工具 / MCP 工具
  -> 按 skill allowed-tools 过滤权限
  -> 将 MCP 等大量工具组装为 deferred tools
  -> 只绑定基础工具 + tool_search
  -> 模型需要时调用 tool_search 检索
  -> 命中的工具写入 promoted 状态
  -> DeferredToolFilterMiddleware 允许被提升工具 schema 出现
```

## 学习版：工具治理是什么

工具治理解决的是 Agent 平台的“能力入口管理”。

工具少时：

```text
直接把所有 tools 绑定给模型
```

工具多时：

```text
几百上千个 MCP/内置/自定义工具
  -> tool schema 撑爆上下文
  -> 模型选错工具
  -> 权限边界不清
  -> 安全风险增加
```

工具治理要回答：

- 当前 Agent 能看到哪些工具？
- 当前 Skill 允许哪些工具？
- 哪些工具要延迟加载？
- 模型如何发现工具？
- 被提升的工具怎么记录？
- 工具目录变化后旧状态是否失效？
- 工具调用前还要经过哪些安全层？

## 成熟系统怎么做

成熟 Agent 平台一般拆成五层：

| 层 | 作用 | 当前项目对应 |
| --- | --- | --- |
| Tool Registry | 工具注册、schema、描述、版本 | `tools.py` |
| Policy | group、allowed-tools、denylist | `tool_policy.py` |
| Retrieval | 大规模工具检索召回 | `tool_search` |
| Promotion | 命中工具提升进上下文 | `ThreadState.promoted` |
| Runtime Guard | 调用前校验和安全拦截 | `DeferredToolFilter` / `Guardrails` |

面试回答：

> 工具治理不是简单工具列表，而是一套从注册、权限、检索、提升到调用时校验的闭环。否则工具越多，模型越容易选错，安全边界也越模糊。

## 工具生命周期

```text
register
  -> describe
  -> group
  -> permission filter
  -> defer or bind
  -> search
  -> promote
  -> call
  -> observe
  -> improve description
```

工具元数据建议：

```json
{
  "name": "read_file",
  "group": "sandbox",
  "description": "Read a file from sandbox workspace.",
  "risk_level": "medium",
  "requires_sandbox": true,
  "side_effect": false,
  "version": "v1"
}
```

## 简化版代码

```python
def get_available_tools(agent_config, skill):
    tools = load_builtin_tools() + load_configured_tools() + load_mcp_tools()
    tools = filter_by_group(tools, agent_config.groups)
    tools = filter_by_allowed_tools(tools, skill.allowed_tools)

    base_tools, deferred = split_deferred_tools(tools)
    catalog = build_tool_catalog(deferred)
    if deferred:
        base_tools.append(make_tool_search(catalog))
    return base_tools, catalog
```

运行时校验：

```python
def wrap_tool_call(request, handler):
    tool_name = request.tool_call["name"]
    if is_deferred_tool(tool_name):
        promoted = request.state.get("promoted", {})
        if tool_name not in promoted.tools:
            return ToolMessage(
                content="Tool is deferred. Please call tool_search first.",
                status="error",
                tool_call_id=request.tool_call["id"],
            )
    return handler(request)
```

## 工具描述怎么写

好的工具描述应该包含：

```text
工具做什么
什么时候用
什么时候不要用
关键参数
同义词/别名
风险等级
典型任务
```

例子：

```text
read_file:
Read text files from the sandbox workspace. Use when you need to inspect source code,
configuration, markdown, logs, or generated artifacts. Do not use for binary files.
Aliases: open file, inspect file, view source, read markdown.
```

## 评估和观测

离线指标：

| 指标 | 含义 |
| --- | --- |
| `Precision@5` | Top5 里有多少是相关工具 |
| `Recall@5` | 相关工具有多少被 Top5 召回 |
| `MRR` | 第一个正确工具排第几 |
| `tool_selection_accuracy` | 模型最终是否选对工具 |
| `unpromoted_call_block_rate` | 未提升工具拦截率 |
| `tool_schema_token_saved` | 延迟加载节省 token |

事件：

```text
tools.registry.loaded
tools.filtered.by_group
tools.filtered.by_allowed
tools.catalog.created
tool_search.called
tool_search.results
tool.promoted
tool.deferred.blocked
tool.call.failed
```

排障：

```text
模型说找不到工具
  -> 看工具是否注册
  -> 看 group/allowed-tools 是否过滤
  -> 看 tool_search 是否召回
  -> 看 promoted 是否写入
  -> 看 catalog_hash 是否变化
```

## 工具分组

工具分组解决“场景权限”和“上下文聚焦”。

例子：

```yaml
tools:
  - name: read_file
    group: file:read
  - name: write_file
    group: file:write
  - name: bash
    group: bash
  - name: web_search
    group: web
```

某个子 Agent 可以只拿：

```text
groups=["file:read", "web"]
```

这样不会暴露写文件或 bash。

面试回答：

> 我把工具先按 group 分层，解决不同 Agent 类型和不同任务场景的最小权限问题。比如分析型子 Agent 只需要读文件和搜索，执行型子 Agent 才需要 bash 或写文件。

## Skill allowed-tools 权限控制

Skill 可以声明：

```yaml
allowed-tools:
  - read_file
  - grep
  - web_search
```

聚合策略：

- 如果没有任何 skill 声明 `allowed-tools`，保持旧式全允许。
- 如果有 skill 声明，取允许工具集合。
- 空列表表示该 skill 不需要工具。

面试回答：

> group 是粗粒度运行时能力划分，allowed-tools 是 skill 级细粒度权限。这样某个技能只会拿到自己需要的工具，减少模型误用和越权调用。

## 延迟工具加载

为什么需要 deferred tools？

大规模 MCP 工具 schema 很长，如果全部绑定：

- system prompt 变长。
- 模型 tool choice 变差。
- 每轮调用成本增加。

延迟加载思路：

```text
不直接绑定全部 MCP schema
只在 system prompt 中列出可检索工具名
绑定一个 tool_search
模型先搜索，再提升具体工具
被提升工具才进入可调用 schema
```

面试回答：

> 延迟工具加载本质上是把“工具发现”和“工具调用”拆开。模型先通过 tool_search 找到相关工具，只有命中的工具才提升进当前上下文，这样可以管理大规模工具集下的 schema token 成本。

## promoted 状态

当 tool_search 命中工具后，会写入状态：

```json
{
  "promoted": {
    "catalog_hash": "...",
    "names": ["mcp_xxx"]
  }
}
```

后续 middleware 会根据 promoted 决定：

- 未提升工具：过滤掉 schema。
- 已提升工具：允许绑定给模型。

## tool hash 的作用

`catalog_hash` 表示当前工具目录版本。

为什么需要？

如果工具目录变化：

- 旧工具可能被删除。
- 同名工具 schema 可能变化。
- 旧 promoted 状态可能不再有效。

合并策略：

```text
catalog_hash 相同 -> promoted names 取并集
catalog_hash 不同 -> 整体替换，丢弃旧 promoted
```

面试回答：

> tool hash 是为了防止工具目录漂移。延迟工具一旦被提升，会留在状态里；如果工具目录后来变化，旧的提升结果可能引用不存在或 schema 已变的工具。用 catalog_hash 做作用域，hash 不一致就丢弃旧 promoted，避免 stale tool schema。

## TF-IDF 工具检索

TF-IDF 用于衡量“查询”和“工具描述”的文本相关性。

基本公式：

```text
TF-IDF(term, doc) = TF(term, doc) * IDF(term)
```

含义：

- TF：词在当前文档出现越多，越能代表该文档。
- IDF：词在全局越少见，区分度越高。

工具检索可以把每个工具看成一个文档：

```text
doc = tool.name + tool.description + args schema
query = 用户任务 + 当前模型意图
```

然后计算相似度，返回 Top-K 工具。

## 如果词没在文档中怎么办

如果一个词没有出现在某个工具文档里：

```text
TF = 0
TF-IDF = 0
```

这意味着这个词不能直接贡献该工具的重要性。

但工具仍可能因为其他词匹配被召回。

例子：

```text
query: "搜索 GitHub issue"
tool doc: "github_search repository pull request issue"
```

即使“搜索”没出现，只要 “GitHub / issue” 匹配，工具仍然可能排前。

## TF-IDF 的局限

优点：

- 实现简单。
- 可解释。
- 不依赖 embedding 模型。
- 对工具名、参数名这类短文本有效。

缺点：

- 不理解语义同义词。
- 对中文分词敏感。
- 对描述质量依赖高。
- 新词、缩写、别名需要额外处理。

可以优化：

- 加同义词表。
- 给 tool.name 更高权重。
- tool.description 与 args schema 分区加权。
- TF-IDF 初筛 + embedding rerank。

## 检索效果评估

常用指标：

```text
Precision@5 = Top 5 里相关工具数量 / 5
Recall@5 = Top 5 里召回的相关工具数量 / 该 query 全部相关工具数量
```

例子：

某 query 标注相关工具：

```text
{github_search, github_get_issue, web_search}
```

系统返回 Top 5：

```text
[github_search, read_file, github_get_issue, web_search, bash]
```

则：

```text
Precision@5 = 3 / 5 = 0.6
Recall@5 = 3 / 3 = 1.0
```

面试回答：

> 我会构造一组真实任务 query 和人工标注的相关工具集合，然后跑 tool_search 的 Top-K 结果，计算 Precision@5 和 Recall@5。Precision 看前 5 个工具有多少是对的，Recall 看应该召回的工具有没有被覆盖。

## 可讲的 trade-off

### 全量绑定 vs 延迟加载

全量绑定：

- 优点：一次模型调用就能直接选工具。
- 缺点：token 成本高，工具多时选择困难。

延迟加载：

- 优点：减少 schema token，提高工具选择聚焦度。
- 缺点：可能多一次 tool_search 调用；检索召回质量影响后续调用。

我的选择：

> 对少量核心工具直接绑定，对大量 MCP 工具延迟加载。这样保留常用路径效率，又控制大规模工具集的上下文成本。

### TF-IDF vs Embedding

TF-IDF：

- 优点：轻量、可解释、成本低。
- 缺点：语义理解弱。

Embedding：

- 优点：语义召回好。
- 缺点：依赖模型、成本更高、可解释性差。

我的选择：

> 初期用 TF-IDF 做轻量召回，后续可以加 embedding rerank。工具检索场景里工具名和参数名很关键，TF-IDF 反而有不错的可解释性。

## 高频追问

### 1. tool_search 召回错了怎么办？

可以让模型改写 query 重搜，也可以提高 Top-K，或者把关键工具作为 always-on 基础工具直接绑定。

### 2. 为什么工具权限不能只靠 prompt？

因为 prompt 不是安全边界。真正的权限要在工具绑定、middleware 过滤、guardrails 拦截处执行。

### 3. 延迟工具提升后如何避免一直污染上下文？

用 catalog_hash 作用域控制，并可结合轮次或任务结束清理 promoted 状态。

### 4. 如果多个 skill 的 allowed-tools 冲突怎么办？

可以取并集，让任务具备所需能力；但对高风险工具如 bash/write_file 可以再叠加 guardrails 或人工确认。

## 深挖补充：工具治理的完整生命周期

工具治理不是“注册工具”这么简单。一个工具从进入平台到被模型调用，至少经历下面几个阶段：

```text
tool registered
  -> schema normalization
  -> description quality check
  -> group / permission tagging
  -> catalog hashing
  -> always-on or deferred decision
  -> runtime filtering by agent / skill / user policy
  -> optional tool_search retrieval
  -> promoted into ThreadState
  -> tool call validation
  -> guardrails / sandbox
  -> result normalization
  -> metrics feedback
```

高分表达：

> 工具治理的目标不是让模型能用更多工具，而是让模型在当前任务里只看到合适的工具，并且每次调用都能被权限、安全和观测系统接住。

## 深挖补充：工具 schema 为什么是成本

工具 schema 会占模型上下文，而且会影响模型注意力。工具越多，问题越明显：

| 问题 | 表现 |
| --- | --- |
| token 成本高 | 用户任务内容被工具描述挤掉 |
| 相似工具混淆 | 模型在 `search_web`、`web_search`、`browser_search` 中乱选 |
| 参数误填 | schema 太长，模型忽略必填字段 |
| 权限过宽 | 当前技能不该用的工具也暴露给模型 |
| 版本漂移 | promoted 工具引用旧 schema |

可以这样解释 deferred tools：

> 我把工具 catalog 分成基础工具和长尾工具。基础工具直接绑定，长尾工具只暴露一个 tool_search 入口，命中后再提升具体工具。这样模型先表达“我需要什么能力”，平台再决定暴露哪个具体工具。

## 深挖补充：工具描述怎么写才可检索

工具描述要同时服务两件事：模型调用和检索召回。

好的描述应该包含：

- 这个工具解决什么任务。
- 什么时候应该用。
- 什么时候不应该用。
- 关键参数是什么意思。
- 输出是什么格式。
- 常见别名和用户会怎么说。

示例：

```text
bad: "Run command"

good: "Execute a shell command inside the configured sandbox. Use for
repository inspection, test runs, build commands, and file-system checks.
Do not use for network exfiltration, credential access, or destructive
host operations. Returns stdout, stderr, exit code, and timeout status."
```

面试里可以补一句：

> 工具描述不是文档装饰，它会直接影响 tool_search 召回和模型参数填写质量。

## 深挖补充：权限模型怎么分层

工具权限至少可以分四层：

| 层级 | 作用 |
| --- | --- |
| 全局配置 | 平台是否启用某类工具 |
| Agent 配置 | 某个 Agent 默认能用什么 |
| Skill allowed-tools | 某个技能执行时允许什么 |
| Runtime policy | 当前用户、线程、环境下还能不能用 |

例子：

```text
write_file 全局启用
  -> code agent 默认允许
  -> research skill 不允许
  -> 当前 run 是只读模式，再次禁止
  -> 即使允许，也要经过 Guardrails
```

高分回答：

> allowed-tools 不是唯一权限系统，它只是技能级白名单。真正的工具治理还要叠加全局配置、Agent 能力、用户权限、运行时策略和安全拦截。

## 深挖补充：tool_search 召回错了怎么定位

工具检索失败通常有四类原因：

| 类型 | 表现 | 修复 |
| --- | --- | --- |
| query 不好 | 用户意图没有转成工具词 | 让模型改写 query 或加同义词 |
| 描述不好 | 正确工具文档没有相关词 | 重写 description |
| ranking 不好 | 正确工具在 Top-K 后面 | 调整权重或加 rerank |
| 权限过滤 | 正确工具被提前过滤 | 检查 allowed-tools 和 group |

观测字段：

- `tool_search_query`
- `tool_search_top_k`
- `tool_search_results`
- `selected_tool`
- `tool_call_success`
- `tool_not_found_reason`
- `catalog_hash`

面试回答：

> 我不会只看最终有没有调到工具，而是看 query、召回列表、最终选择、调用结果和 catalog_hash。这样能区分是检索问题、权限问题、描述问题还是工具本身失败。

## 深挖补充：工具结果也要治理

工具治理不只发生在调用前，也发生在调用后。

工具结果可能有这些问题：

- 输出太长，撑爆上下文。
- 输出包含敏感信息。
- 输出格式不稳定，模型难以继续用。
- 错误信息不标准，模型无法决定是否重试。
- 工具返回外部不可信内容，可能包含 prompt injection。

所以工具结果应该做：

1. 截断和摘要。
2. 敏感信息脱敏。
3. 标准化错误结构。
4. 记录 stdout / stderr / exit code / duration。
5. 对外部网页、文件内容标记 untrusted source。

## 深挖补充：面试攻防

### Q：为什么不让模型自己判断能不能用工具？

模型可以判断“想用什么能力”，但不能作为权限边界。权限必须由平台执行，否则 prompt injection 可以诱导模型越权调用。

### Q：工具越多，是否 Agent 越强？

不是。工具越多，选择空间越大，误调用和 token 成本也越高。强 Agent 不是工具最多，而是工具暴露最合适。

### Q：怎么避免工具版本变化导致旧状态坏掉？

用 catalog_hash 或 tool version 绑定 promoted 状态。工具目录变化后，旧 promoted tools 要失效或重新校验，避免模型调用不存在或 schema 已变的工具。

### Q：为什么 TF-IDF 可以先用？

工具检索里工具名、参数名、领域关键词很重要，TF-IDF 轻量、便宜、可解释，适合作为第一版。后续可以叠加 embedding 和 rerank 提升语义召回。
