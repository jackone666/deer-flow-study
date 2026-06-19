# TODO 列表

## 已完成的功能

- [x] 仅在调用第一个文件系统或 bash 工具后启动沙箱
- [x] 为完整流程添加澄清机制
- [x] 实现上下文摘要机制，避免上下文爆炸
- [x] 集成 MCP （模型上下文协议）以用于可扩展工具
- [x] 添加文件上传支持和自动文档转换
- [x] 实现自动线程标题生成
- [x] 使用 TodoList 中间件添加计划模式
- [x] 使用 ViewImageMiddleware 添加视觉模型支持
- [x] 采用 `SKILL.md` 格式的 Skills 系统
- [x] 将 `packages/harness/deerflow/tools/builtins/task_tool.py`中的`time.sleep(5)`替换为`asyncio.sleep()` （子代理轮询）

## 计划的功能

- [ ] 池化沙箱资源，减少沙箱容器数量
- [ ] 添加认证/授权层
- [ ] 实施速率限制
- [ ] 添加指标和监控
- [ ] 支持更多文档格式上传
- [ ] 技能市场/远程技能安装
- [ ] 优化代理热路径中的异步并发（IM通道多任务场景）
- [ ] 将 `packages/harness/deerflow/sandbox/local/local_sandbox.py`中的`subprocess.run()`替换为`asyncio.create_subprocess_shell()`
  - 在社区工具（tavily、jina_ai、firecrawl、infoquest、image_search）中将同步 `requests`替换为`httpx.AsyncClient`
  - [x] 在 title_middleware 和内存更新程序中将同步 `model.invoke()`替换为异步`model.ainvoke()`
  - 考虑使用 `asyncio.to_thread()` 包装剩余的阻塞文件 I/O
  - 对于生产：调整网关 worker/runtime 设置以适应长时间运行的代理工作负载

## 已解决的问题

- [x] 确保 `state.artifacts` 中没有重复的文件
- [x] 思考很长但内容很空（在思考过程中回答）
