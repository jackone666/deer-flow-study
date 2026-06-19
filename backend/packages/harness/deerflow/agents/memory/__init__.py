"""DeerFlow 记忆模块。

提供全局记忆机制：
- 在 ``memory.json`` 中存储用户上下文与对话历史；
- 使用 LLM 对对话进行摘要与事实抽取；
- 将相关记忆注入系统提示，实现个性化回复。

子模块一览：
- ``prompt.py`` — LLM 提示模板（记忆更新 + 格式化）
- ``message_processing.py`` — 消息过滤与纠正/强化信号检测
- ``queue.py`` — 带去抖合并的记忆更新队列
- ``storage.py`` — 基于文件的原子写入存储
- ``summarization_hook.py`` — 摘要压缩前的记忆紧急冲入钩子
- ``updater.py`` — 核心更新引擎（LLM 调用 → JSON 解析 → 原子保存）
"""

# ── 提示模板与格式化工具 ──
from deerflow.agents.memory.prompt import (
    FACT_EXTRACTION_PROMPT,
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
    format_memory_for_injection,
)
# ── 更新队列 ──
from deerflow.agents.memory.queue import (
    ConversationContext,
    MemoryUpdateQueue,
    get_memory_queue,
    reset_memory_queue,
)
# ── 存储层 ──
from deerflow.agents.memory.storage import (
    FileMemoryStorage,
    MemoryStorage,
    get_memory_storage,
)
# ── 更新引擎 ──
from deerflow.agents.memory.updater import (
    MemoryUpdater,
    clear_memory_data,
    delete_memory_fact,
    get_memory_data,
    reload_memory_data,
    update_memory_from_conversation,
)

__all__ = [
    # Prompt utilities
    "MEMORY_UPDATE_PROMPT",
    "FACT_EXTRACTION_PROMPT",
    "format_memory_for_injection",
    "format_conversation_for_update",
    # Queue
    "ConversationContext",
    "MemoryUpdateQueue",
    "get_memory_queue",
    "reset_memory_queue",
    # Storage
    "MemoryStorage",
    "FileMemoryStorage",
    "get_memory_storage",
    # Updater
    "MemoryUpdater",
    "clear_memory_data",
    "delete_memory_fact",
    "get_memory_data",
    "reload_memory_data",
    "update_memory_from_conversation",
]
