# 内存系统改进 - 摘要

## 同步笔记 (2026-03-10)

此摘要与 `main` 分支实现同步。
TF-IDF/context-aware 检索已**计划**，尚未合并。

## 已实施

- 在内存注入中使用 `tiktoken` 进行准确的令牌计数。
- 事实被注入到 `<memory>` 提示内容中。
- 事实按置信度排序并以 `max_injection_tokens` 为界。

## 计划中（尚未合并）

- TF-IDF 基于最近对话上下文的余弦相似度回忆。
- `current_context`参数用于`format_memory_for_injection`。
- 加权排名（`similarity`+`confidence`）。
- 用于上下文感知事实选择的运行时 extraction/injection 流程。

## 为什么需要这种同步

早期文档描述了已实现的 TF-IDF 行为，该行为与 `main` 中的代码不匹配。
此不匹配在问题 `#1059` 中进行跟踪。

## 当前 API 形态

```python
def format_memory_for_injection(memory_data: dict[str, Any], max_tokens: int = 2000) -> str:
```

目前在 `main`中没有可用的`current_context` 参数。

## 验证指针

- 实施：`packages/harness/deerflow/agents/memory/prompt.py`
- 提示汇编：`packages/harness/deerflow/agents/lead_agent/prompt.py`
- 回归测试：`backend/tests/test_memory_prompt_injection.py`
