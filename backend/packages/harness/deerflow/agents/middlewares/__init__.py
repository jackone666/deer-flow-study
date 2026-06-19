"""Lead Agent 中间件集合：包含工具错误处理、沙箱审计、循环检测、记忆/上传等。

各中间件在 ``agent.py:_build_middlewares`` 中按固定顺序组装，三个拦截点覆盖完整生命周期：

```
                     before_agent           wrap_model_call /        wrap_tool_call
                    ┌─────────────┐         before/after_model      ┌──────────────┐
1.  ThreadData      │ 创建目录     │                                 │              │
2.  Uploads         │ 注入文件列表 │                                 │              │
3.  Sandbox         │ 获取沙箱     │                                 │              │
4.  DanglingToolCall│             │ 注入占位 ToolMessage            │              │
5.  LLMError        │             │ 重试/降级                       │              │
6.  Guardrail       │             │                                 │ 拒绝未授权工具│
7.  SandboxAudit    │             │                                 │ bash 安全检查 │
8.  ToolError       │             │                                 │ 异常→错误消息 │
9.  Summarization   │             │ 上下文压缩                       │              │
10. TodoList        │             │ todo 管理                        │              │
11. TokenUsage      │             │ 记录 token 用量                  │              │
12. Title           │             │ 自动生成标题                     │              │
13. Memory          │ after_agent→│ 记忆入队                         │              │
14. ViewImage       │             │ 注入图片 base64                  │              │
15. DeferredTool    │             │ 隐藏延迟工具 schema              │              │
16. SubagentLimit   │             │ 截断多余 task 调用               │              │
17. LoopDetection   │             │ 检测/中断循环                    │              │
18. SafetyFinish    │             │ 安全过滤后剥离 tool_call          │              │
19. Clarification   │             │ 拦截 ask_clarification → 中断     │              │
                    └─────────────┘                                 └──────────────┘
```
"""
