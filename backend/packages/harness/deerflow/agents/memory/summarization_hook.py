"""在摘要从状态中移除消息前触发的钩子。"""


from __future__ import annotations

from deerflow.agents.memory.message_processing import detect_correction, detect_reinforcement, filter_messages_for_memory
from deerflow.agents.memory.queue import get_memory_queue
from deerflow.agents.middlewares.summarization_middleware import SummarizationEvent
from deerflow.config.memory_config import get_memory_config
from deerflow.runtime.user_context import resolve_runtime_user_id


def memory_flush_hook(event: SummarizationEvent) -> None:
    """在摘要中间件丢弃消息前，将被压缩的消息紧急冲入记忆更新队列。

    这是记忆系统的"最后防线"：当对话被摘要中间件压缩时，被移除的旧消息
    将永久丢失。本钩子在压缩前将这些消息送入记忆更新队列，确保其中包含的
    用户偏好、上下文等信息被 LLM 抽取并写入 memory.json。

    处理流程：
    ```
    SummarizationMiddleware 即将压缩
          ↓
    memory_flush_hook(event)  ← 本函数
          ↓
    1. 检查记忆是否启用 + thread_id 是否存在
    2. filter_messages_for_memory() → 筛选有价值消息
    3. 检查是否同时包含用户消息和 AI 回复（缺一则无意义）
    4. detect_correction() / detect_reinforcement() → 信号检测
    5. queue.add_nowait() → 立即入队处理（不用去抖，因为消息即将被丢弃）
    ```

    关键细节：
    - 使用 ``add_nowait()`` 而非 ``add()``：消息即将被摘要移除，不能等去抖
    - ``reinforcement_detected`` 仅在未检测到纠正时才检查（避免纠偏后的确认被误判）

    Args:
        event: 摘要事件上下文（包含待压缩消息列表、thread_id、agent_name、runtime）。
    """
    if not get_memory_config().enabled or not event.thread_id:
        return

    # 从待压缩消息中筛选对记忆有价值的内容
    filtered_messages = filter_messages_for_memory(list(event.messages_to_summarize))
    # 必须同时存在用户消息和助手回复，否则无有效对话可抽取
    user_messages = [message for message in filtered_messages if getattr(message, "type", None) == "human"]
    assistant_messages = [message for message in filtered_messages if getattr(message, "type", None) == "ai"]
    if not user_messages or not assistant_messages:
        return

    # 检测纠偏信号；仅在无纠偏时才检测正向强化（避免"对，就是这���"被同时标记）
    correction_detected = detect_correction(filtered_messages)
    reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)
    user_id = resolve_runtime_user_id(event.runtime)
    # 立即入队处理（不用去抖），因为消息即将被摘要移除
    queue = get_memory_queue()
    queue.add_nowait(
        thread_id=event.thread_id,
        messages=filtered_messages,
        agent_name=event.agent_name,
        user_id=user_id,
        correction_detected=correction_detected,
        reinforcement_detected=reinforcement_detected,
    )
