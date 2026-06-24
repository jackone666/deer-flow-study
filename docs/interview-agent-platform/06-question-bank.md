# 06 高频面试问题与回答要点

这份题库按模块分类。建议先背“回答骨架”，不要逐字背答案。

## 一、项目总览

### Q1：你这个 Agent 平台主要解决什么问题？

回答要点：

- 长对话上下文越来越长。
- 用户偏好和纠错需要跨会话保留。
- 工具数量多，schema 不能全部塞给模型。
- 工具调用有副作用，需要安全拦截和沙箱隔离。
- 所以做了动态上下文、长期记忆、摘要压缩、工具治理、Guardrails。

推荐回答：

> 这个项目主要解决复杂 Agent 在长对话和多工具场景下不可控的问题。我把系统拆成上下文、记忆、摘要、工具治理和安全拦截五层：模型调用前动态注入当前上下文；对话后异步更新长期记忆；历史超 token 后做摘要压缩；大规模工具通过分组、权限和延迟加载治理；工具执行前经过 Guardrails 做 allow/deny 决策。

### Q2：这个项目最难的地方是什么？

回答要点：

- 不是单个功能难，而是多个中间件之间的顺序和边界难。
- 动态上下文不能被摘要错误吞掉。
- 记忆异步更新不能影响主链路。
- 工具权限、延迟加载、guardrails 要互相配合。

推荐回答：

> 最难的是上下文和工具状态的一致性。比如动态提醒是当前运行时上下文，摘要时不能把它压成历史；记忆更新要异步，不影响主链路，但用户纠偏信号又不能丢；工具延迟加载后还要用 catalog_hash 防止工具目录变化导致旧 schema 被错误复用。

## 二、动态上下文

### Q3：为什么要做动态上下文注入？

回答要点：

- system prompt 是静态全局规则。
- 长期记忆、日期、线程状态是动态的。
- 模型调用前注入能按当前用户/线程生成。

### Q4：为什么用隐藏消息？

回答要点：

- 模型可见，UI 不展示。
- 不污染真实用户消息。
- 用标签区分它是提醒，不是用户请求。

### Q5：为什么不直接放 system prompt？

回答要点：

- 系统规则优先级更高，不应该混用户上下文。
- 长期记忆按用户变化，不能全局写死。
- 日期和运行时状态需要实时生成。

### Q6：动态提醒被摘要了会怎样？

回答要点：

- 当前上下文变成历史内容。
- 日期/记忆可能失效。
- 和新 reminder 冲突。
- 所以要保护位置。

## 三、长期记忆

### Q7：长期记忆记录哪些内容？

回答要点：

- 用户偏好。
- 工作上下文。
- 当前关注点。
- 历史模式。
- 原子事实。
- 纠错事实。

### Q8：为什么记忆更新要异步？

回答要点：

- 避免额外 LLM 调用阻塞主链路。
- 支持去抖合并。
- 失败不影响当前对话。

### Q9：去抖合并怎么做？

回答要点：

- key 是 `(thread_id, user_id, agent_name)`。
- 最新 messages 覆盖。
- correction/reinforcement 做 OR。
- 防止重要信号丢失。

### Q10：纠错怎么更新？

回答要点：

- 新增 `category="correction"` 的 fact。
- 置信度高。
- 如果之前错误明确，写 `sourceError`。
- 删除冲突旧事实到 `factsToRemove`。

### Q11：什么不应该记？

回答要点：

- 文件上传事件。
- 临时路径。
- 一次性问题。
- 模型弱推断。
- 没有未来价值的寒暄。

## 四、摘要压缩

### Q12：什么时候触发摘要？

回答要点：

- token_counter 计算总量。
- 超过配置阈值。
- 找到合理 cutoff。

### Q13：压缩哪些，保留哪些？

回答要点：

- 旧消息压缩。
- 近期消息保留。
- 动态提醒保留。
- 关键 skill/tool 上下文保护。

### Q14：为什么 tool message 不全部保留？

回答要点：

- 工具输出很长。
- 多数是中间过程。
- 旧结果可以摘要成结论。
- 近期 tool call 链路需要保持完整。

### Q15：压缩后如何重建消息？

回答要点：

- `RemoveMessage(REMOVE_ALL_MESSAGES)` 清空旧消息。
- 插入 summary。
- 拼回 preserved messages。

### Q16：摘要和长期记忆有什么区别？

回答要点：

- 摘要服务当前线程。
- 长期记忆服务未来会话。
- 摘要保存任务进展，记忆保存用户画像和稳定事实。

## 五、工具治理

### Q17：为什么不能把所有工具都给模型？

回答要点：

- schema token 成本高。
- 工具选择干扰。
- 权限风险。
- 工具目录变化会导致状态漂移。

### Q18：工具分组解决什么问题？

回答要点：

- 粗粒度能力控制。
- 子 Agent 最小权限。
- 不同任务只暴露相关工具。

### Q19：allowed-tools 和 group 有什么区别？

回答要点：

- group 是工具配置层能力分类。
- allowed-tools 是 skill 级权限白名单。
- 一个粗，一个细。

### Q20：延迟工具加载怎么做？

回答要点：

- MCP 工具不直接绑定 schema。
- system prompt 只列可检索工具名。
- 绑定 tool_search。
- 搜索后写 promoted。
- middleware 根据 promoted 放行 schema。

### Q21：catalog_hash 有什么用？

回答要点：

- 标识当前工具目录版本。
- hash 相同，promoted names 合并。
- hash 不同，旧 promoted 失效。
- 防止 stale schema。

### Q22：TF-IDF 怎么用于工具检索？

回答要点：

- 工具描述是文档。
- 用户任务是 query。
- 计算 TF-IDF 向量相似度。
- 返回 Top-K。

### Q23：Precision@5 / Recall@5 怎么测？

回答要点：

- 准备 query 和人工标注相关工具集合。
- 跑 tool_search Top 5。
- Precision@5 看前 5 个有几个相关。
- Recall@5 看全部相关工具召回了几个。

## 六、Guardrails

### Q24：Guardrails 和 prompt 安全提示有什么区别？

回答要点：

- prompt 是软约束。
- Guardrails 是工具执行前的确定性拦截。
- prompt injection 影响模型，但不能绕过 middleware。

### Q25：为什么要 fail-closed？

回答要点：

- 安全系统异常时不能默认放行。
- 工具有副作用。
- provider 出错返回标准化 error。

### Q26：拒绝后为什么返回 ToolMessage？

回答要点：

- 保持 tool call 协议完整。
- 模型能看到失败原因。
- 后续可以换安全方案。
- 上层统一处理工具错误。

### Q27：Guardrails 和 Sandbox 区别？

回答要点：

- Guardrails：执行前授权。
- Sandbox：执行时隔离。
- 一个决定能不能做，一个限制能影响哪里。

### Q28：如果 guardrail provider 挂了怎么办？

回答要点：

- 默认 fail-closed。
- 返回 deny ToolMessage。
- 记录日志。
- 对低风险只读工具可配置放宽，但高风险工具不放行。

## 七、系统设计追问

### Q29：如果用户下一轮马上问，异步记忆还没更新怎么办？

回答要点：

- 这是异步一致性的 trade-off。
- 当前线程仍有消息上下文。
- 长期记忆用于跨会话，不强依赖本轮立刻可见。
- 可对 correction 做优先队列或同步短路优化。

### Q30：怎么防止摘要丢关键细节？

回答要点：

- 保留近期消息。
- 保护动态提醒。
- 摘要 prompt 要要求保留文件名、决策、错误、待办。
- 关键事实进入结构化状态或长期记忆。

### Q31：怎么做可观测性？

回答要点：

- 记录注入了哪些动态上下文。
- 记录摘要触发 token、cutoff、摘要长度。
- 记录记忆入队、合并、更新结果。
- 记录 tool_search query、Top-K、promoted。
- 记录 guardrail allow/deny reason。

### Q32：如果重新设计，你会怎么优化？

回答要点：

- 动态上下文按相关性检索，而不是固定拼接。
- 记忆加入 embedding + TF-IDF 混合召回。
- 摘要加入结构化 slot。
- 工具检索加入 rerank。
- Guardrails 增加风险分级和策略审计。

## 八、当前项目 Harness 案例追问

### Q33：为什么这个项目要做 Harness，而不是普通 Agent 封装？

回答要点：

- 普通 Agent 封装只管模型和工具。
- 当前项目还要管线程状态、上下文、摘要、记忆、工具权限、安全和沙箱。
- Harness 把这些横切能力做成运行时底座。
- 中间件链可以让不同能力独立演进。

推荐回答：

> 普通 Agent 封装通常只是模型加工具，但当前项目要解决长上下文、长期记忆、多工具治理、远程沙箱和 Guardrails 安全拦截。Harness 的价值是把这些能力统一成运行时：请求进来后有 thread_id、ThreadState、中间件链、工具过滤、sandbox、memory queue 和子 Agent 调度。

### Q34：Harness 运行时具体负责什么？

回答要点：

- 管理 `thread_id`、runtime context、线程数据目录。
- 注入长期偏好和动态上下文。
- 编排 Lead Agent 和子 Agent。
- 统一工具调用、中间件、安全拦截。
- 维护 ThreadState reducer 合并语义。

推荐回答：

> Harness 是 Agent 的运行时底座，不是单个功能。它负责把 HTTP 请求变成一个有 thread_id 的 Agent 任务，注入上下文和长期偏好，执行主 AgentLoop，调度工具和子 Agent，采集工具事件并推送给前端，同时挂上 Guardrails、Sandbox、摘要、记忆这些中间件。

### Q35：为什么要用 task 工具拆子 Agent？

回答要点：

- 子任务上下文隔离。
- 避免大量工具输出污染主对话。
- 子任务可独立读文件、搜索、执行命令。
- 主 Agent 保留任务主线和最终决策。

推荐回答：

> task 工具的核心价值是上下文隔离。主 Agent 负责整体目标和决策，子 Agent 负责探索型或命令型子任务。比如读大量代码、跑测试、分析日志都可能产生很多中间消息，放在子 Agent 里能避免污染主上下文。

### Q36：ThreadState 为什么不能简单用 dict 覆盖？

回答要点：

- 不同字段合并语义不同。
- messages 要支持 RemoveMessage。
- artifacts 要追加去重。
- promoted 要按 catalog_hash 作用域合并。
- viewed_images 是字典合并或清空。

### Q37：为什么沙箱要收敛到远程 HTTP backend？

回答要点：

- Agent 进程不直接执行宿主机命令。
- 安全边界更清晰。
- 多任务和生产部署更一致。
- 缺 `provisioner_url` 时显式失败，避免本地 fallback。

### Q38：工具治理为什么属于 Harness 能力？

回答要点：

- 工具来源多：内置、配置、MCP、Skill、Subagent、ACP。
- Harness 决定当前 Agent 能看到什么、调用什么。
- group 和 allowed-tools 做权限过滤。
- deferred tools 控制 schema token。
- catalog_hash 防止工具目录漂移。

### Q39：中间件链的顺序为什么重要？

回答要点：

- ThreadData 要在 Sandbox 前准备目录。
- DynamicContext 和 Summarization 都在模型调用前，但 reminder 不能被摘要压错位置。
- Guardrails 要在真实工具执行前。
- Memory 要在 Agent 响应后异步入队。

## 九、自进化追问

### Q40：你说的自进化具体是什么？

回答要点：

- 不是让 Agent 随便改自己。
- 分三层：长期记忆、Skill 工作流、评测训练。
- 用户纠偏进入 memory。
- 成功复杂流程沉淀成 Skill。
- Harness 评测可作为 SFT / RL 的数据基础，但当前主要强调 Memory 和 Skill 沉淀。

推荐回答：

> 自进化不是无约束自修改，而是有门禁的经验沉淀。短期是把用户偏好和纠错写入长期记忆，把重复成功的工具流程沉淀成 Skill；长期是记录 Agent 轨迹，用动态 Rubric 评分，高分轨迹进入训练数据，低分轨迹进入错误分析。

### Q41：Skill 自进化和长期记忆有什么区别？

回答要点：

- 长期记忆记录用户偏好和事实。
- Skill 记录任务流程和操作方法。
- “用户要求全程中文回复”是 memory。
- “生成面试文档时外部资料只参考格式，内容必须回到当前项目”是 Skill。

### Q42：Rubric 评测为什么要动态生成？

回答要点：

- 不同 query 的成功标准不同。
- 问 Guardrails 要检查 fail-closed 和 ToolMessage error。
- 问摘要压缩要检查动态提醒保护和 RemoveMessage 重建。
- 动态 Rubric 比固定模板更准确。

### Q43：怎么防止自进化以错训错？

回答要点：

- 来源门禁：只从明确纠偏、成功任务、高分轨迹提取。
- 格式门禁：Memory/Skill 必须 schema 校验。
- 评测门禁：更新后跑回归集。
- P0 fail 样本不能进训练集。

## 十、评估、观测与数据飞轮追问

### Q44：你怎么证明这个 Agent Harness 项目有效？

回答要点：

- 不能只看“有没有回答”。
- 分结果质量、过程质量、安全质量、成本效率。
- 用 P0/P1/P2 评估。
- 结合离线评测集和线上指标。

推荐回答：

> 我会从四类指标证明：结果上看任务成功率和用户纠偏率；过程上看工具调用是否合理、是否死循环、deferred tool 是否先提升；安全上看 Guardrail deny 和沙箱越权；成本上看 token、工具耗时、摘要压缩率和子 Agent 数量。

### Q45：P0/P1/P2 怎么设计？

回答要点：

- P0 是底线，触发就失败。
- P1 是关键过程，缺失重扣。
- P2 是质量评分。

例子：

- P0：Guardrails provider 异常时 fail-open。
- P1：deferred tool 没有先 tool_search。
- P2：回答结构、trade-off、验证说明。

### Q46：线上一次 Agent 运行慢了，你怎么排查？

回答要点：

- 先用 `thread_id/run_id` 找 trace。
- 看慢在 model、tool、sandbox acquire、subagent 还是 memory。
- 看 token 和工具输出大小。
- 看是否触发摘要或重试。

推荐回答：

> 我会把一次 run 拆成 trace：dynamic context、summarization、model.call、tool.call、guardrail.evaluate、sandbox、subagent、memory.enqueue。看每个 span 的 duration 和状态，就能定位慢在模型、工具、沙箱还是子 Agent。

### Q47：可观测性要记录哪些指标？

回答要点：

- 模型：token、latency、finish_reason。
- 上下文：摘要触发、压缩率、reminder 保护。
- 记忆：入队、合并、纠偏、失败。
- 工具：调用次数、错误率、耗时。
- 安全：guardrail deny、provider error、sandbox fail。

### Q48：数据飞轮怎么设计？

回答要点：

- Raw events 不直接进入长期系统。
- 清洗成 signals。
- 高质量数据进 eval/training。
- 通过门禁后进入 Memory / Skill / tool docs / guardrails。

推荐回答：

> 数据飞轮是：任务轨迹产生事件和指标，用户纠偏、工具错误、Guardrail 拒绝、评测分数被清洗成信号；可信信号进入 Memory 或 Skill，工具错误反哺工具描述和 Guardrails，最后用回归评测验证更新是否有效。

### Q49：怎么避免数据飞轮污染？

回答要点：

- 临时文件路径不入库。
- prompt injection 不入库。
- P0 fail 轨迹不入训练。
- 模型推断低置信。
- 更新后跑回归，必要时回滚。

### Q50：如果只能选一个最关键的观测字段，你选什么？

回答要点：

- `thread_id` / `run_id`。
- 没有它无法把模型、工具、沙箱、记忆、Guardrail 串成一条链。

## 十一、沙箱系统追问

### Q51：为什么 Agent 平台需要沙箱？

回答要点：

- Agent 会动态调用 bash、读写文件、生成产物。
- 这些操作有副作用，不能直接落到宿主机。
- 沙箱提供执行隔离、目录边界、多租户隔离和资源回收。

推荐回答：

> 沙箱解决的是“工具在哪里执行”的问题。模型可以决定调用 bash 或写文件，但真正执行应该进入受控环境，避免直接触碰宿主机文件、环境变量和进程。

### Q52：沙箱在 Harness 里怎么接入？

回答要点：

- `ThreadDataMiddleware` 准备线程目录。
- `SandboxMiddleware` 接入生命周期。
- 工具调用时 `ensure_sandbox_initialized()` 懒加载。
- `AioSandboxProvider` 获取或复用 sandbox。
- `RemoteSandboxBackend` 调 HTTP provisioner。

推荐回答：

> Harness 不把沙箱写死在 Lead Agent 里，而是通过 middleware 和 provider 抽象接入。模型调用工具时，工具内部先拿 sandbox，再执行真实命令或文件操作。

### Q53：为什么要远程 HTTP Backend？

回答要点：

- Agent 进程不直接执行宿主机命令。
- 执行面从 Agent 服务中剥离。
- provisioner 统一做创建、发现、销毁和资源调度。
- 更适合生产多租户隔离和扩缩容。

推荐回答：

> 远程 backend 让 Agent 进程只做 client，真正执行交给 provisioner 管理的 sandbox。这样安全边界更清楚，也能把资源隔离、回收、观测交给基础设施层。

### Q54：为什么缺 provisioner_url 要 fail-fast？

回答要点：

- 缺配置说明远程执行环境不可用。
- 不能静默 fallback 到本地执行。
- 否则不同环境安全边界不一致。

推荐回答：

> 沙箱配置错误属于安全边界错误，应该启动失败。静默 fallback 会让生产和开发环境行为不一致，严重时模型生成的命令会落到不该执行的地方。

### Q55：Sandbox、Guardrails、工具权限怎么区分？

回答要点：

- 工具权限：模型有没有入口。
- Guardrails：这一次调用允不允许。
- Sandbox：允许后在哪里执行。
- SandboxAudit：bash 内容是否危险。

推荐回答：

> 工具权限管可见性，Guardrails 管调用策略，Sandbox 管执行隔离，SandboxAudit 管 bash 命令审计。四层叠加，不能互相替代。

### Q56：为什么沙箱要按 thread_id 复用？

回答要点：

- 同一任务多轮需要保留 workspace。
- 文件、依赖、产物可以连续使用。
- 确定性 sandbox_id 支持跨进程 discover。
- 避免同线程重复创建执行环境。

推荐回答：

> Agent 任务往往不是一轮结束。按 thread_id 复用 sandbox，可以保留任务工作区，同时通过确定性 sandbox_id 在进程重启后找回已有执行环境。

### Q57：怎么做沙箱可观测性？

回答要点：

- 记录 acquire/create/discover/execute 的 trace span。
- 记录创建耗时、复用次数、失败次数、命令耗时。
- 记录 SandboxAudit block/warn/pass。
- 用 `thread_id/run_id/sandbox_id` 串联。

推荐回答：

> 我会把 sandbox.ensure_initialized、provider.acquire、backend.discover/create、sandbox.execute_command 都做成 trace span，并记录 sandbox_id、thread_id、耗时、错误类型和审计结果。
