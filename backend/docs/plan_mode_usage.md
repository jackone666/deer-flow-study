# 带有 TodoList 中间件的计划模式

本文档介绍如何通过 DeerFlow 2.0 中的 TodoList 中间件启用和使用计划模式功能。

## 概述

Plan Mode为Agent添加了一个TodoList中间件，它提供了一个`write_todos`工具来帮助Agent：
- 将复杂的任务分解为更小的、可管理的步骤
- 随着工作的进展跟踪进度
- 向用户提供正在执行的操作的可见性

TodoList 中间件构建于 LangChain 的 `TodoListMiddleware` 之上。

## 配置

### 启用计划模式

计划模式通过 **运行时配置** 通过 `RunnableConfig`的`configurable`部分中的`is_plan_mode` 参数进行控制。这允许您根据每个请求动态启用或禁用计划模式。

```python
from langchain_core.runnables import RunnableConfig
from deerflow.agents.lead_agent.agent import make_lead_agent

# Enable plan mode via runtime configuration
config = RunnableConfig(
    configurable={
        "thread_id": "example-thread",
        "thinking_enabled": True,
        "is_plan_mode": True,  # Enable plan mode
    }
)

# Create agent with plan mode enabled
agent = make_lead_agent(config)
```

### 配置选项

- **is_plan_mode** (bool)：是否使用 TodoList 中间件启用计划模式。默认值：`False`
  - 通过 `config.get("configurable", {}).get("is_plan_mode", False)`
  - 可以为每个代理调用动态设置
  - 无需全局配置

## 默认行为

当使用默认设置启用计划模式时，代理将可以访问具有以下行为的 `write_todos` 工具：

### 何时使用 TodoList

代理将使用待办事项列表：
1. 复杂的多步骤任务（3+个不同的步骤）
2. 需要仔细规划的重要任务
3. 当用户明确请求待办事项列表时
4. 当用户提供多个任务时

### 当 NOT 使用 TodoList 时

代理将跳过使用待办事项列表：
1. 单一、简单的任务
2. 琐碎任务（< 3 步骤）
3. 纯粹的对话或信息请求

### 任务状态

- **待处理**：任务尚未开始
- **in_progress**：当前正在处理（可以有多个并行任务）
- **完成**：任务成功完成

## 使用示例

### 基本用法

```python
from langchain_core.runnables import RunnableConfig
from deerflow.agents.lead_agent.agent import make_lead_agent

# Create agent with plan mode ENABLED
config_with_plan_mode = RunnableConfig(
    configurable={
        "thread_id": "example-thread",
        "thinking_enabled": True,
        "is_plan_mode": True,  # TodoList middleware will be added
    }
)
agent_with_todos = make_lead_agent(config_with_plan_mode)

# Create agent with plan mode DISABLED (default)
config_without_plan_mode = RunnableConfig(
    configurable={
        "thread_id": "another-thread",
        "thinking_enabled": True,
        "is_plan_mode": False,  # No TodoList middleware
    }
)
agent_without_todos = make_lead_agent(config_without_plan_mode)
```

### 每个请求的动态计划模式

您可以 enable/disable 为不同的对话或任务动态规划模式：

```python
from langchain_core.runnables import RunnableConfig
from deerflow.agents.lead_agent.agent import make_lead_agent

def create_agent_for_task(task_complexity: str):
    """Create agent with plan mode based on task complexity."""
    is_complex = task_complexity in ["high", "very_high"]

    config = RunnableConfig(
        configurable={
            "thread_id": f"task-{task_complexity}",
            "thinking_enabled": True,
            "is_plan_mode": is_complex,  # Enable only for complex tasks
        }
    )

    return make_lead_agent(config)

# Simple task - no TodoList needed
simple_agent = create_agent_for_task("low")

# Complex task - TodoList enabled for better tracking
complex_agent = create_agent_for_task("high")
```

## 它是如何工作的

1. 当调用 `make_lead_agent(config)`时，它从`config.configurable`中提取`is_plan_mode`
2. 配置传递到 `_build_middlewares(config)`
3. `_build_middlewares()`读取`is_plan_mode`并调用`_create_todo_list_middleware(is_plan_mode)`
4. 如果是 `is_plan_mode=True`，则创建 `TodoListMiddleware` 实例并将其添加到中间件链中
5. 中间件自动将 `write_todos` 工具添加到代理的工具集中
6. 代理可以使用此工具来管理执行过程中的任务
7. 中间件处理待办事项列表状态并将其提供给代理

## 建筑

```
make_lead_agent(config)
  │
  ├─> Extracts: is_plan_mode = config.configurable.get("is_plan_mode", False)
  │
  └─> _build_middlewares(config)
        │
        ├─> ThreadDataMiddleware
        ├─> SandboxMiddleware
        ├─> SummarizationMiddleware (if enabled via global config)
        ├─> TodoListMiddleware (if is_plan_mode=True) ← NEW
        ├─> TitleMiddleware
        └─> ClarificationMiddleware
```

## 实施细节

### 代理模块
- **位置**：`packages/harness/deerflow/agents/lead_agent/agent.py`
- **函数**：`_create_todo_list_middleware(is_plan_mode: bool)` - 如果启用计划模式，则创建 TodoListMiddleware
- **功能**：`_build_middlewares(config: RunnableConfig)` - 基于运行时配置构建中间件链
- **功能**：`make_lead_agent(config: RunnableConfig)` - 使用适当的中间件创建代理

### 运行时配置
计划模式通过 `RunnableConfig.configurable`中的`is_plan_mode` 参数控制：
```python
config = RunnableConfig(
    configurable={
        "is_plan_mode": True,  # Enable plan mode
        # ... other configurable options
    }
)
```

## 主要优点

1. **动态控制**：Enable/disable 每个请求的计划模式，无全局状态
2. **灵活性**：不同的对话可以有不同的计划模式设置
3. **简单**：无需全局配置管理
4. **上下文感知**：计划模式决策可以基于任务复杂性、用户偏好等。

## 自定义提示

DeerFlow 对 TodoListMiddleware 使用自定义 `system_prompt`和`tool_description` 来匹配整体 DeerFlow 提示样式：

### 系统提示功能
- 使用 XML 标签 (`<todo_list_system>`) 与 DeerFlow 的主提示符保持结构一致性
- 强调 CRITICAL 规则和最佳实践
- 明确“何时使用”与“何时使用 NOT”指南
- 注重实时更新和立即完成任务

### 工具说明功能
- 详细使用场景及示例
- 强烈强调 NOT 用于简单任务
- 清除任务状态定义（待处理、进行中、已完成）
- 综合最佳实践部分
- 任务完成要求以防止过早标记

自定义提示在 `/Users/hetao/workspace/deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py:57`的`_create_todo_list_middleware()` 中定义。

## 注释

- TodoList 中间件使用 LangChain 的内置 `TodoListMiddleware` 和 **自定义 DeerFlow 风格的提示**
- 计划模式 **默认禁用** (`is_plan_mode=False`) 以保持向后兼容性
- 中间件位于 `ClarificationMiddleware` 之前，以允许在澄清流程期间进行待办事项管理
- 自定义提示强调与 DeerFlow 主系统提示相同的原则（清晰、面向行动、关键规则）
