"""带去抖机制的记忆更新队列。

核心设计：
- **去抖**：在 ``debounce_seconds``（默认 30s）内同一线程的多次入队会被合并为一次处理，避免高频对话触发大量 LLM 调用
- **线程归属**：每个 ``(thread_id, user_id, agent_name)`` 三元组作为合并键，同键后入队覆盖先入队
- **双模式**：``add()`` 按去抖延迟处理；``add_nowait()`` 立即处理（摘要丢弃前的紧急冲入）

入队 → 处理流程：
```
MemoryMiddleware → queue.add(thread_id, messages)
      ↓ (重置去抖定时器)
30s 内同 thread 再次入队 → 合并并重置定时器
      ↓ (定时器到期)
_process_queue() → MemoryUpdater.update_memory() × N → memory.json
```"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from deerflow.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """待处理记忆更新的会话上下文。

    每个实例代表一个线程中需要被记忆更新的对话上下文。
    通过 ``(thread_id, user_id, agent_name)`` 三元组在队列中去重合并。

    示例：
    ```python
    ctx = ConversationContext(
        thread_id="thread_abc",
        messages=[HumanMessage("..."), AIMessage("...")],
        agent_name="my-agent",
        user_id="user_123",
        correction_detected=True,
        reinforcement_detected=False,
    )
    # ctx.timestamp → 2026-06-08T10:30:00Z（入队时自动生成）
    ```
    """

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_name: str | None = None
    user_id: str | None = None  # 入队时捕获（非 ContextVar），跨越 Timer 线程边界
    correction_detected: bool = False
    reinforcement_detected: bool = False


class MemoryUpdateQueue:
    """带去抖机制的记忆更新队列。

    该队列收集会话上下文，并在可配置的去抖时长后处理它们。
    在去抖窗口内收到的多条会话将被合并处理。
    """

    def __init__(self):
        """初始化记忆更新队列。"""
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    @staticmethod
    def _queue_key(
        thread_id: str,
        user_id: str | None,
        agent_name: str | None,
    ) -> tuple[str, str | None, str | None]:
        """返回记忆更新目标在去抖窗口内的合并键。"""
        return (thread_id, user_id, agent_name)

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """向更新队列添加一条会话（带去抖合并）。

        同 ``(thread_id, user_id, agent_name)`` 在去抖窗口内多次调用会被合并：
        后入队的消息**覆盖**先入队的消息（而非追加）。合并时 correction/reinforcement
        标记取**逻辑或**（任一为真则保留）。

        入队后自动重置去抖定时器——每次新消息到达都会把处理推迟 ``debounce_seconds`` 秒。

        调用示例：
        ```python
        queue = get_memory_queue()
        queue.add(
            thread_id="thread_abc",
            messages=filtered_msgs,
            agent_name="default",
            user_id="user_123",
            correction_detected=True,
        )
        # → 30s 后自动调用 MemoryUpdater.update_memory()
        ```

        Args:
            thread_id: 线程 ID。
            messages: 筛选后的会话消息列表（通常由 ``filter_messages_for_memory()`` 产出）。
            agent_name: 若提供则按 Agent 隔离存储记忆；为 ``None`` 时使用全局记忆。
            user_id: 入队时捕获的用户 ID（存入 ``ConversationContext`` 以跨越
                ``threading.Timer`` 边界，ContextVar 不会跨原生线程传播）。
            correction_detected: 最近的对话轮次中是否出现显式纠正信号。
            reinforcement_detected: 最近的对话轮次中是否出现正向强化信号。
        """
        config = get_memory_config()
        if not config.enabled:
            return

        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            self._reset_timer()

        logger.info("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def add_nowait(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """添加会话并立即在后台开始处理。"""
        config = get_memory_config()
        if not config.enabled:
            return

        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
            )
            self._schedule_timer(0)

        logger.info("Memory update queued for immediate processing on thread %s, queue size: %d", thread_id, len(self._queue))

    def _enqueue_locked(
        self,
        *,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None,
        user_id: str | None,
        correction_detected: bool,
        reinforcement_detected: bool,
    ) -> None:
        """执行赋值。"""
        queue_key = self._queue_key(thread_id, user_id, agent_name)
        existing_context = next(
            (context for context in self._queue if self._queue_key(context.thread_id, context.user_id, context.agent_name) == queue_key),
            None,
        )
        merged_correction_detected = correction_detected or (existing_context.correction_detected if existing_context is not None else False)
        merged_reinforcement_detected = reinforcement_detected or (existing_context.reinforcement_detected if existing_context is not None else False)
        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            agent_name=agent_name,
            user_id=user_id,
            correction_detected=merged_correction_detected,
            reinforcement_detected=merged_reinforcement_detected,
        )

        self._queue = [context for context in self._queue if self._queue_key(context.thread_id, context.user_id, context.agent_name) != queue_key]
        self._queue.append(context)

    def _reset_timer(self) -> None:
        """重置去抖定时器。"""
        config = get_memory_config()
        self._schedule_timer(config.debounce_seconds)

        logger.debug("Memory update timer set for %ss", config.debounce_seconds)

    def _schedule_timer(self, delay_seconds: float) -> None:
        """在给定延迟后调度队列处理。"""
        # Cancel existing timer if any
        if self._timer is not None:
            self._timer.cancel()

        self._timer = threading.Timer(
            delay_seconds,
            self._process_queue,
        )
        self._timer.daemon = True
        self._timer.start()

    def _process_queue(self) -> None:
        """处理所有已入队的会话上下文。"""
        # Import here to avoid circular dependency
        from deerflow.agents.memory.updater import MemoryUpdater

        with self._lock:
            if self._processing:
                # Preserve immediate flush semantics even if another worker is active.
                self._schedule_timer(0)
                return

            if not self._queue:
                return

            self._processing = True
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        try:
            updater = MemoryUpdater()

            for context in contexts_to_process:
                try:
                    logger.info("Updating memory for thread %s", context.thread_id)
                    success = updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                        correction_detected=context.correction_detected,
                        reinforcement_detected=context.reinforcement_detected,
                        user_id=context.user_id,
                    )
                    if success:
                        logger.info("Memory updated successfully for thread %s", context.thread_id)
                    else:
                        logger.warning("Memory update skipped/failed for thread %s", context.thread_id)
                except Exception as e:
                    logger.error("Error updating memory for thread %s: %s", context.thread_id, e)

                # Small delay between updates to avoid rate limiting
                if len(contexts_to_process) > 1:
                    time.sleep(0.5)

        finally:
            with self._lock:
                self._processing = False

    def flush(self) -> None:
        """立即强制处理队列。

        常用于测试或优雅关闭场景。
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        self._process_queue()

    def flush_nowait(self) -> None:
        """在后台线程中立即开始处理队列。"""
        with self._lock:
            # Daemon thread: queued messages may be lost if the process exits
            # before _process_queue completes. Acceptable for best-effort memory updates.
            self._schedule_timer(0)

    def clear(self) -> None:
        """清空队列而不处理。

        常用于测试场景。
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        """获取待处理更新数。"""
        with self._lock:
            return len(self._queue)

    @property
    def is_processing(self) -> bool:
        """检查队列是否正在被处理。"""
        with self._lock:
            return self._processing


# Global singleton instance
_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    """获取全局记忆更新队列单例。

    Returns:
        记忆更新队列实例。
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue


def reset_memory_queue() -> None:
    """重置全局记忆队列。

    常用于测试场景。
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is not None:
            _memory_queue.clear()
        _memory_queue = None
