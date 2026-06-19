"""将会话消息清洗为记忆更新输入的共享辅助方法。

本模块负责三件事：
1. **消息过滤**：从完整会话中筛选出对记忆有价值的消息（仅保留人类输入 + 最终 AI 回复，过滤工具调用中间消息）
2. **内容提取**：将多模态内容块（list[dict]）展开为纯文本
3. **信号检测**：识别用户是否进行了"纠偏"或"正向强化"

典型调用链：
```
MemoryMiddleware → filter_messages_for_memory() → 入队
                                                    ↓
SummarizationHook → detect_correction() / detect_reinforcement() → 入队时标记
```"""



from __future__ import annotations

import re
from copy import copy
from typing import Any

_UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
_CORRECTION_PATTERNS = (
    re.compile(r"\bthat(?:'s| is) (?:wrong|incorrect)\b", re.IGNORECASE),
    re.compile(r"\byou misunderstood\b", re.IGNORECASE),
    re.compile(r"\btry again\b", re.IGNORECASE),
    re.compile(r"\bredo\b", re.IGNORECASE),
    re.compile(r"不对"),
    re.compile(r"你理解错了"),
    re.compile(r"你理解有误"),
    re.compile(r"重试"),
    re.compile(r"重新来"),
    re.compile(r"换一种"),
    re.compile(r"改用"),
)
_REINFORCEMENT_PATTERNS = (
    re.compile(r"\byes[,.]?\s+(?:exactly|perfect|that(?:'s| is) (?:right|correct|it))\b", re.IGNORECASE),
    re.compile(r"\bperfect(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bexactly\s+(?:right|correct)\b", re.IGNORECASE),
    re.compile(r"\bthat(?:'s| is)\s+(?:exactly\s+)?(?:right|correct|what i (?:wanted|needed|meant))\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+(?:doing\s+)?that\b", re.IGNORECASE),
    re.compile(r"\bjust\s+(?:like\s+)?(?:that|this)\b", re.IGNORECASE),
    re.compile(r"\bthis is (?:great|helpful)\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bthis is what i wanted\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"对[，,]?\s*就是这样(?:[。！？!?.]|$)"),
    re.compile(r"完全正确(?:[。！？!?.]|$)"),
    re.compile(r"(?:对[，,]?\s*)?就是这个意思(?:[。！？!?.]|$)"),
    re.compile(r"正是我想要的(?:[。！？!?.]|$)"),
    re.compile(r"继续保持(?:[。！？!?.]|$)"),
)


def extract_message_text(message: Any) -> str:
    """从消息内容中提取纯文本，用于过滤与信号检测。

    处理两种 content 格式：
    - **纯文本**：``AIMessage(content="你好")`` → 直接返回 ``"你好"``
    - **多模态块列表**（Anthropic 思考模式等）：
      ``AIMessage(content=[{"type":"text","text":"结论是..."}])``
      → 抽取所有 str 元素和 dict 中的 ``text`` 字段，用空格拼接

    输入示例：
    ```python
    # 纯文本消息
    msg1 = HumanMessage(content="帮我查一下")
    extract_message_text(msg1)  # → "帮我查一下"

    # 多模态块消息
    msg2 = AIMessage(content=[
        "这是思考过程...",
        {"type": "text", "text": "最终答案是42"}
    ])
    extract_message_text(msg2)  # → "这是思考过程... 最终答案是42"
    ```

    Args:
        message: LangChain 消息对象（需有 ``content`` 属性）。

    Returns:
        提取后的纯文本字符串。
    """
    content = getattr(message, "content", "")
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                text_val = part.get("text")
                if isinstance(text_val, str):
                    text_parts.append(text_val)
        return " ".join(text_parts)
    return str(content)


def filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """从完整会话中筛选对记忆有长期价值的消息子集。

    筛选规则：
    1. **人类消息**：始终保留（去除 ``<uploaded_files>`` 标签后的内容）
    2. **纯上传消息**：剥离上传标签后为空的消息 → 标记 ``skip_next_ai`` 跳过下一条 AI 回复
    3. **AI 消息**：仅保留**不含 tool_calls 的最终回复**（中间工具调用轮次被丢弃）
    4. **ToolMessage**：从不保留（工具执行结果是临时的）

    输入示例（6 条消息的会话）：
    ```python
    [
        HumanMessage(content="帮我查 DeepSeek 价格"),
        AIMessage(content="好的", tool_calls=[{"name":"web_search","args":{...}}]),  # ← 有 tool_call，丢弃
        ToolMessage(content="搜索结果...", tool_call_id="tc_1"),                      # ← 工具消息，丢弃
        AIMessage(content="DeepSeek 输入 1元/百万token"),                            # ← 无 tool_call，保留
        HumanMessage(content="<uploaded_files>\\n- a.pdf\\n</uploaded_files>翻译这个"), # ← 剥离后为 "翻译这个"
        AIMessage(content="翻译完成"),                                                # ← 保留
    ]
    ```
    输出：
    ```python
    [
        HumanMessage(content="帮我查 DeepSeek 价格"),
        AIMessage(content="DeepSeek 输入 1元/百万token"),
        HumanMessage(content="翻译这个"),
        AIMessage(content="翻译完成"),
    ]
    ```

    Args:
        messages: 完整会话消息列表。

    Returns:
        筛选后的消息列表（仅含人类输入 + 最终 AI 回复）。
    """
    filtered = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "human":
            content_str = extract_message_text(msg)
            if "<uploaded_files>" in content_str:
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:
                    skip_next_ai = True
                    continue
                clean_msg = copy(msg)
                clean_msg.content = stripped
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                filtered.append(msg)
                skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if skip_next_ai:
                    skip_next_ai = False
                    continue
                filtered.append(msg)

    return filtered


def detect_correction(messages: list[Any]) -> bool:
    """检测最近 6 条人类消息中是否出现用户显式纠偏信号。

    匹配中英文纠正模式，如：
    - 英文：``"that's wrong"``、``"you misunderstood"``、``"try again"``、``"redo"``
    - 中文：``"不对"``、``"你理解错了"``、``"重试"``、``"换一种"``、``"改用"``
    - 仅扫描最近 6 条人类消息（``messages[-6:]``），避免历史旧纠正被反复触发

    输入示例：
    ```python
    messages = [
        HumanMessage(content="帮我写一个排序函数"),
        AIMessage(content="def sort(): ..."),
        HumanMessage(content="不对，我要的是降序排列"),  # ← 命中 "不对"
    ]
    detect_correction(messages)  # → True
    ```

    Args:
        messages: 会话消息列表。

    Returns:
        检测到纠正信号时返回 ``True``。
    """
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _CORRECTION_PATTERNS):
            return True

    return False


def detect_reinforcement(messages: list[Any]) -> bool:
    """检测最近 6 条人类消息中是否出现用户显式正向强化信号。

    匹配中英文正面反馈模式，如：
    - 英文：``"yes, exactly"``、``"perfect"``、``"this is what I wanted"``、``"keep doing that"``
    - 中文：``"对，就是这样"``、``"完全正确"``、``"正是我想要的"``、``"继续保持"``

    仅在**未检测到纠正信号**时才调用此函数（``summarization_hook.py`` 第25行），
    因为一次纠偏后往往跟着"对了"的确认，但不应被同时记为正向强化。

    输入示例：
    ```python
    messages = [
        HumanMessage(content="帮我优化这段代码"),
        AIMessage(content="已优化：..."),
        HumanMessage(content="完全正确，这就是我要的效果"),  # ← 命中
    ]
    detect_reinforcement(messages)  # → True
    ```

    Args:
        messages: 会话消息列表。

    Returns:
        检测到正向强化信号时返回 ``True``。
    """
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _REINFORCEMENT_PATTERNS):
            return True

    return False
