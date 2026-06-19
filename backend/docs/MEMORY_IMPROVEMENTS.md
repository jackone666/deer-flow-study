# 内存系统改进

该文档跟踪内存注入行为和路线图状态。

## 状态（截至2026年3月10日）

在 `main` 中实现：
- 通过 `format_memory_for_injection`中的`tiktoken` 进行准确的令牌计数。
- 事实被注入到提示内存上下文中。
- 事实按置信度排名（降序）。
- 注入遵循 `max_injection_tokens` 预算。

计划/尚未合并：
- TF-IDF 基于相似性的事实检索。
- `current_context` 输入用于上下文感知评分。
- 可配置 similarity/confidence 权重（`similarity_weight`、`confidence_weight`）。
- Middleware/runtime 连接，用于在每次模型调用之前进行上下文感知检索。

## 当前行为

今天的功能：

```python
def format_memory_for_injection(memory_data: dict[str, Any], max_tokens: int = 2000) -> str:
```

当前注入格式：
- `user.*.summary`的`User Context` 部分
- `history.*.summary`的`History` 部分
- `facts[]`中的`Facts` 部分，按置信度排序，附加到达到token预算

token计数：
- 在可用时使用 `tiktoken` (`cl100k_base`)
- 回退到 `len(text) // 4`

## 已知差距

本文档的先前版本把 TF-IDF/context-aware 检索描述成已经发布。
这对于 `main` 来说不准确并引起混乱。

问题参考：`#1059`

## 路线图（计划中）

计划评分策略：

```text
final_score = (similarity * 0.6) + (confidence * 0.4)
```

计划集成形态：
1. 从过滤的 user/final-assistant 回合中提取最近的对话上下文。
2. 计算每个事实与当前上下文之间的 TF-IDF 余弦相似度。
3. 按加权分数排名并根据token预算注入。
4. 如果上下文不可用，则退回到仅置信度排名。

## 验证

当前回归覆盖范围包括：
- 事实包含在内存注入输出中
- 置信度排序
- token 预算受限时的事实包含

测试：
- `backend/tests/test_memory_prompt_injection.py`
