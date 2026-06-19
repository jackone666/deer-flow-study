# 任务工具改进

## 概述

任务工具已得到改进，以消除浪费的 LLM 轮询。以前，在使用后台任务时，LLM 必须重复调用 `task_status` 来轮询是否完成，从而导致不必要的 API 请求。

## 所做的改变

### 1. 删除了 `run_in_background` 参数

`run_in_background`参数已从`task` 工具中删除。所有子代理任务现在默认情况下都是异步运行的，但该工具会自动处理完成。

**之前：**
```python
# LLM had to manage polling
task_id = task(
    subagent_type="bash",
    prompt="Run tests",
    description="Run tests",
    run_in_background=True
)
# Then LLM had to poll repeatedly:
while True:
    status = task_status(task_id)
    if completed:
        break
```

**之后：**
```python
# Tool blocks until complete, polling happens in backend
result = task(
    subagent_type="bash",
    prompt="Run tests",
    description="Run tests"
)
# Result is available immediately after the call returns
```

### 2. 后端轮询

现在的`task_tool`：
- 异步启动子代理任务
- 在后端轮询完成情况（每 2 秒一次）
- 阻止工具调用直到完成
- 直接返回最终结果

这意味着：
- ✅ LLM 仅进行 ONE 工具调用
- ✅ 没有浪费的 LLM 轮询请求
- ✅ 后端处理所有状态检查
- ✅ 超时保护（最多 5 分钟）

### 3. 从 LLM 工具中删除了 `task_status`

`task_status_tool` 不再暴露于 LLM。它保留在代码库中以供潜在的 internal/debugging 使用，但 LLM 无法调用它。

### 4.更新文档

- 更新了 `prompt.py`中的`SUBAGENT_SECTION` 以删除所有对后台任务和轮询的引用
- 简化使用示例
- 明确表示该工具自动等待完成

## 实施细节

### 轮询逻辑

位于`packages/harness/deerflow/tools/builtins/task_tool.py`：

```python
# Start background execution
task_id = executor.execute_async(prompt)

# Poll for task completion in backend
while True:
    result = get_background_task_result(task_id)

    # Check if task completed or failed
    if result.status == SubagentStatus.COMPLETED:
        return f"[Subagent: {subagent_type}]\n\n{result.result}"
    elif result.status == SubagentStatus.FAILED:
        return f"[Subagent: {subagent_type}] Task failed: {result.error}"

    # Wait before next poll
    time.sleep(2)

    # Timeout protection (5 minutes)
    if poll_count > 150:
        return "Task timed out after 5 minutes"
```

### 执行超时

除了轮询超时之外，子代理执行现在还有一个内置的超时机制：

**配置** (`packages/harness/deerflow/subagents/config.py`)：
```python
@dataclass
class SubagentConfig:
    # ...
    timeout_seconds: int = 300  # 5 minutes default
```

**线程池架构**：

为了避免嵌套线程池和资源浪费，我们使用两个专用线程池：

1. **调度程序池** (`_scheduler_pool`)：
   - 最大worker数：4
   - 目的：协调后台任务执行
   - 运行管理任务生命周期的 `run_task()` 函数

2. **执行池** (`_execution_pool`):
   - 最大worker数：8（更大以避免阻塞）
   - 目的：具有超时支持的实际子代理执行
   - 运行调用代理的 `execute()` 方法

**它是如何工作的**：
```python
# In execute_async():
_scheduler_pool.submit(run_task)  # Submit orchestration task

# In run_task():
future = _execution_pool.submit(self.execute, task)  # Submit execution
exec_result = future.result(timeout=timeout_seconds)  # Wait with timeout
```

**好处**：
- ✅ 关注点的清晰分离（调度与执行）
- ✅ 无嵌套线程池
- ✅ 在正确的级别执行超时
- ✅ 更好的资源利用率

**两级超时保护**：
1. **执行超时**：子代理执行本身有5分钟的超时（可在SubagentConfig中配置）
2. **轮询超时**：工具轮询有 5 分钟超时（30 次轮询 × 10 秒）

这确保即使子代理执行挂起，系统也不会无限期地等待。

### 好处

1. **降低 API 成本**：不再重复 LLM 轮询请求
2. **更简单的UX**：LLM不需要管理轮询逻辑
3. **更好的可靠性**：后端一致地处理所有状态检查
4. **超时保护**：两级超时防止无限等待（执行+轮询）

## 测试

要验证更改是否正常工作：

1. 启动需要几秒钟的子代理任务
2. 验证工具调用块直至完成
3. 验证结果直接返回
4. 验证没有进行 `task_status` 调用

示例测试场景：
```python
# This should block for ~10 seconds then return result
result = task(
    subagent_type="bash",
    prompt="sleep 10 && echo 'Done'",
    description="Test task"
)
# result should contain "Done"
```

## 迁移说明

对于之前使用 `run_in_background=True` 的 users/code：
- 只需删除参数
- 删除任何轮询逻辑
- 该工具将自动等待完成

无需其他更改 - API 向后兼容（减去删除的参数）。
