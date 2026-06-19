"""记忆更新器：负责读取、写入与通过 LLM 更新记忆数据。

完整更新流水线：
```
对话完成 (MemoryMiddleware / SummarizationHook)
       ↓
update_memory(messages)                     ← 入口
       ↓
_prepare_update_prompt()                    ← 加载当前记忆 + 构建提示词
  ├─ get_memory_data()                      ← 从 storage 加载 memory.json
  ├─ format_conversation_for_update()       ← 格式化对话为纯文本
  └─ MEMORY_UPDATE_PROMPT.format(...)       ← 渲染提示模板
       ↓
model.invoke(prompt)                        ← 调用 LLM
       ↓
_finalize_update()
  ├─ _parse_memory_update_response()        ← 从 LLM 输出中提取 JSON
  ├─ _apply_updates()                       ← 应用 user/history/facts 更新
  ├─ _strip_upload_mentions_from_memory()   ← 清除上传相关引用
  └─ storage.save()                         ← 原子写入 memory.json
```

线程安全：同步/异步双路径。
- **异步路径**（``aupdate_memory``）：通过 ``asyncio.to_thread`` 委托给同步路径
- **同步路径**（``update_memory``）：检测到运行中的事件循环时卸载到专用线程池
   ``_SYNC_MEMORY_UPDATER_EXECUTOR``，避免跨循环连接复用（issue #2615）"""

import asyncio
import atexit
import concurrent.futures
import copy
import json
import logging
import math
import re
import uuid
from typing import Any

from deerflow.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from deerflow.agents.memory.storage import (
    create_empty_memory,
    get_memory_storage,
    utc_now_iso_z,
)
from deerflow.config.memory_config import get_memory_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)


# Thread pool for offloading sync memory updates when called from an async
# context.  Unlike the previous asyncio.run() approach, this runs *sync*
# model.invoke() calls — no event loop is created, so the langchain async
# httpx client pool (globally cached via @lru_cache) is never touched and
# cross-loop connection reuse is impossible.
_SYNC_MEMORY_UPDATER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="memory-updater-sync",
)
atexit.register(lambda: _SYNC_MEMORY_UPDATER_EXECUTOR.shutdown(wait=False))


def _create_empty_memory() -> dict[str, Any]:
    """存储层空白记忆工厂的向后兼容封装。"""
    return create_empty_memory()


def _save_memory_to_file(memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
    """已配置记忆存储 save 路径的向后兼容封装。"""
    return get_memory_storage().save(memory_data, agent_name, user_id=user_id)


def get_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """通过存储提供者获取当前记忆数据。"""
    return get_memory_storage().load(agent_name, user_id=user_id)


def reload_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """通过存储提供者重新加载记忆数据。"""
    return get_memory_storage().reload(agent_name, user_id=user_id)


def import_memory_data(memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """通过存储提供者持久化导入的记忆数据。

    Args:
        memory_data: 待持久化的完整记忆载荷。
        agent_name: 若提供则导入到对应的 Agent 记忆。
        user_id: 若提供则按用户隔离记忆。

    Returns:
        存储归一化后保存的记忆数据。

    Raises:
        OSError: 当导入记忆持久化失败时抛出。
    """
    storage = get_memory_storage()
    if not storage.save(memory_data, agent_name, user_id=user_id):
        raise OSError("Failed to save imported memory data")
    return storage.load(agent_name, user_id=user_id)


def clear_memory_data(agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """清空所有已存储记忆数据并持久化一份空白结构。"""
    cleared_memory = create_empty_memory()
    if not _save_memory_to_file(cleared_memory, agent_name, user_id=user_id):
        raise OSError("Failed to save cleared memory data")
    return cleared_memory


def _validate_confidence(confidence: float) -> float:
    """校验持久化事实的置信度，使存储的 JSON 保持符合规范。"""
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise ValueError("confidence")
    return confidence


def create_memory_fact(
    content: str,
    category: str = "context",
    confidence: float = 0.5,
    agent_name: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """创建一条新事实并持久化更新后的记忆数据。"""
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("content")

    normalized_category = category.strip() or "context"
    validated_confidence = _validate_confidence(confidence)
    now = utc_now_iso_z()
    memory_data = get_memory_data(agent_name, user_id=user_id)
    updated_memory = dict(memory_data)
    facts = list(memory_data.get("facts", []))
    facts.append(
        {
            "id": f"fact_{uuid.uuid4().hex[:8]}",
            "content": normalized_content,
            "category": normalized_category,
            "confidence": validated_confidence,
            "createdAt": now,
            "source": "manual",
        }
    )
    updated_memory["facts"] = facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError("Failed to save memory data after creating fact")

    return updated_memory


def delete_memory_fact(fact_id: str, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
    """按 ID 删除一条事实并持久化更新后的记忆数据。"""
    memory_data = get_memory_data(agent_name, user_id=user_id)
    facts = memory_data.get("facts", [])
    updated_facts = [fact for fact in facts if fact.get("id") != fact_id]
    if len(updated_facts) == len(facts):
        raise KeyError(fact_id)

    updated_memory = dict(memory_data)
    updated_memory["facts"] = updated_facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError(f"Failed to save memory data after deleting fact '{fact_id}'")

    return updated_memory


def update_memory_fact(
    fact_id: str,
    content: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    agent_name: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """更新一条已存在的事实并持久化更新后的记忆数据。"""
    memory_data = get_memory_data(agent_name, user_id=user_id)
    updated_memory = dict(memory_data)
    updated_facts: list[dict[str, Any]] = []
    found = False

    for fact in memory_data.get("facts", []):
        if fact.get("id") == fact_id:
            found = True
            updated_fact = dict(fact)
            if content is not None:
                normalized_content = content.strip()
                if not normalized_content:
                    raise ValueError("content")
                updated_fact["content"] = normalized_content
            if category is not None:
                updated_fact["category"] = category.strip() or "context"
            if confidence is not None:
                updated_fact["confidence"] = _validate_confidence(confidence)
            updated_facts.append(updated_fact)
        else:
            updated_facts.append(fact)

    if not found:
        raise KeyError(fact_id)

    updated_memory["facts"] = updated_facts

    if not _save_memory_to_file(updated_memory, agent_name, user_id=user_id):
        raise OSError(f"Failed to save memory data after updating fact '{fact_id}'")

    return updated_memory


def _extract_text(content: Any) -> str:
    """从 LLM 响应内容中提取纯文本（str 或内容块列表）。

    现代 LLM 可能以结构化内容块列表（而非纯字符串）返回结果，例如
    ``[{"type": "text", "text": "..."}]``。若直接对这种内容使用 ``str()``，
    会得到 Python repr 而非真实文本，导致下游 JSON 解析失败。

    字符串片段之间不带分隔符地拼接，避免破坏分块 JSON/文本负载。
    基于 dict 的文本块作为完整文本块处理，并以换行符连接以提高可读性。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            """执行相应操作。
            
                    Returns:
                        None。
            """
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)


_REQUIRED_MEMORY_UPDATE_TOP_LEVEL_KEYS = frozenset({"user", "history", "newFacts", "factsToRemove"})


def _normalize_memory_update_fact(fact: Any) -> dict[str, Any] | None:
    """对模型生成的记忆更新中单个事实条目进行归一化。"""
    if not isinstance(fact, dict):
        return None

    raw_content = fact.get("content")
    if not isinstance(raw_content, str):
        return None
    content = raw_content.strip()
    if not content:
        return None

    raw_category = fact.get("category")
    category = raw_category.strip() if isinstance(raw_category, str) and raw_category.strip() else "context"

    raw_confidence = fact.get("confidence", 0.5)
    if isinstance(raw_confidence, bool):
        return None
    if isinstance(raw_confidence, str):
        raw_confidence = raw_confidence.strip()
        if not raw_confidence:
            return None
        try:
            raw_confidence = float(raw_confidence)
        except ValueError:
            return None
    elif isinstance(raw_confidence, (int, float)):
        raw_confidence = float(raw_confidence)
    else:
        return None

    if not math.isfinite(raw_confidence):
        return None

    normalized_fact = {
        "content": content,
        "category": category,
        "confidence": raw_confidence,
    }
    source_error = fact.get("sourceError")
    if isinstance(source_error, str):
        normalized_source_error = source_error.strip()
        if normalized_source_error:
            normalized_fact["sourceError"] = normalized_source_error

    return normalized_fact


def _normalize_memory_update_data(update_data: dict[str, Any]) -> dict[str, Any]:
    """将 LLM 输出的原始字典归一化为 ``_apply_updates`` 期望的标准形状。

    安全策略（fail-safe）：
    - ``newFacts`` 中个别格式错误的事实会被**跳过**（不阻塞整体更新）
    - 但如果 ``factsToRemove`` 非空且 ``newFacts`` 中存在格式错误 → 抛异常拒绝
      （理由：删除了事实但新增失败 = 数据丢失，宁可不更新）

    不接受的输入（返回空结构或不完整的会被拒绝）：
    ```python
    # 1. 置信度为 bool → 丢弃
    {"content": "...", "confidence": True}

    # 2. content 为空 → 丢弃
    {"content": "", "category": "preference"}

    # 3. confidence 无法解析为 float → 丢弃
    {"content": "...", "confidence": "high"}
    ```

    Args:
        update_data: 从 LLM 响应中解析出的原始字典。

    Returns:
        归一化后的标准字典（``{"user": {}, "history": {}, "newFacts": [...], "factsToRemove": [...]}``）。

    Raises:
        json.JSONDecodeError: factsToRemove 非空但 newFacts 有格式错误 → 拒绝部分更新。
    """
    user = update_data.get("user")
    history = update_data.get("history")
    new_facts = update_data.get("newFacts")
    facts_to_remove = update_data.get("factsToRemove")
    normalized_facts_to_remove = [fact_id for fact_id in facts_to_remove if isinstance(fact_id, str)] if isinstance(facts_to_remove, list) else []
    normalized_new_facts = []
    dropped_new_fact = not isinstance(new_facts, list)
    if isinstance(new_facts, list):
        for fact in new_facts:
            normalized_fact = _normalize_memory_update_fact(fact)
            if normalized_fact is not None:
                normalized_new_facts.append(normalized_fact)
            else:
                dropped_new_fact = True

    if normalized_facts_to_remove and dropped_new_fact:
        raise json.JSONDecodeError(
            "Unsafe partial memory update: factsToRemove with malformed newFacts",
            json.dumps(update_data, ensure_ascii=False),
            0,
        )

    return {
        "user": user if isinstance(user, dict) else {},
        "history": history if isinstance(history, dict) else {},
        "newFacts": normalized_new_facts,
        "factsToRemove": normalized_facts_to_remove,
    }


def _parse_memory_update_response(response_content: Any) -> dict[str, Any]:
    """从 LLM 响应中提取第一个合法的记忆更新 JSON 对象。

    LLM 输出可能被包裹在思考过程、散文或 Markdown 围栏中——
    本函数用 ``re.finditer(r"\\{", ...)`` 扫描每个 ``{`` 位置并尝试 ``raw_decode``，
    直到找到同时包含 ``user``/``history``/``newFacts``/``factsToRemove`` 四个顶层键的
    合法 JSON 对象。

    输入示例（LLM 原始输出）：
    ```
    好的，我来分析对话并更新记忆。
    {
      "user": { ... },
      "history": { ... },
      "newFacts": [ ... ],
      "factsToRemove": []
    }
    更新完成。
    ```
    → 从第一个 ``{`` 开始 raw_decode，命中后返回 ``_normalize_memory_update_data(parsed)``

    不会被修复的情况：
    - JSON 被截断（``{"user": {...}`` 缺 ``}``）→ 跳过该 ``{`` 继续扫描
    - JSON 不包含四个必需键 → 跳过继续扫描
    - 整个响应无合法 JSON → 抛出 ``json.JSONDecodeError``

    Args:
        response_content: LLM 响应的原始内容（str 或内容块列表）。

    Returns:
        归一化后的记忆更新数据字典。

    Raises:
        json.JSONDecodeError: 未找到包含四个必需顶层键的合法 JSON 对象。
    """
    response_text = _extract_text(response_content).strip()
    decoder = json.JSONDecoder()

    for match in re.finditer(r"\{", response_text):
        try:
            parsed, _end = decoder.raw_decode(response_text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and _REQUIRED_MEMORY_UPDATE_TOP_LEVEL_KEYS.issubset(parsed):
            return _normalize_memory_update_data(parsed)

    raise json.JSONDecodeError("No valid memory update JSON object found", response_text, 0)


# Matches sentences that describe a file-upload *event* rather than general
# file-related work.  Deliberately narrow to avoid removing legitimate facts
# such as "User works with CSV files" or "prefers PDF export".
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """从所有记忆摘要与事实中移除与文件上传相关的句子。

    上传文件是会话范围的；将上传事件写入长期记忆会导致 Agent 在未来会话中
    试图查找实际不存在的文件。
    """
    # Scrub summaries in user/history sections
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    # Also remove any facts that describe upload events
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]

    return memory_data


def _fact_content_key(content: Any) -> str | None:
    """内部辅助方法。"""
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped.casefold()


class MemoryUpdater:
    """基于 LLM 与会话上下文更新记忆。"""

    def __init__(self, model_name: str | None = None):
        """初始化记忆更新器。

        Args:
            model_name: 可选的模型名；为 ``None`` 时使用配置或默认模型。
        """
        self._model_name = model_name

    def _get_model(self):
        """获取用于记忆更新的模型。"""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)

    def _build_correction_hint(
        self,
        correction_detected: bool,
        reinforcement_detected: bool,
    ) -> str:
        """为纠正与强化信号构建可选的提示词补充说明。"""
        correction_hint = ""
        if correction_detected:
            correction_hint = (
                "IMPORTANT: Explicit correction signals were detected in this conversation. "
                "Pay special attention to what the agent got wrong, what the user corrected, "
                "and record the correct approach as a fact with category "
                '"correction" and confidence >= 0.95 when appropriate.'
            )
        if reinforcement_detected:
            reinforcement_hint = (
                "IMPORTANT: Positive reinforcement signals were detected in this conversation. "
                "The user explicitly confirmed the agent's approach was correct or helpful. "
                "Record the confirmed approach, style, or preference as a fact with category "
                '"preference" or "behavior" and confidence >= 0.9 when appropriate.'
            )
            correction_hint = (correction_hint + "\n" + reinforcement_hint).strip() if correction_hint else reinforcement_hint

        return correction_hint

    def _prepare_update_prompt(
        self,
        messages: list[Any],
        agent_name: str | None,
        correction_detected: bool,
        reinforcement_detected: bool,
        user_id: str | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """加载记忆并为指定会话构建更新提示。"""
        config = get_memory_config()
        if not config.enabled or not messages:
            return None

        current_memory = get_memory_data(agent_name, user_id=user_id)
        conversation_text = format_conversation_for_update(messages)
        if not conversation_text.strip():
            return None

        correction_hint = self._build_correction_hint(
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
        )
        prompt = MEMORY_UPDATE_PROMPT.format(
            current_memory=json.dumps(current_memory, indent=2, ensure_ascii=False),
            conversation=conversation_text,
            correction_hint=correction_hint,
        )
        return current_memory, prompt

    def _finalize_update(
        self,
        current_memory: dict[str, Any],
        response_content: Any,
        thread_id: str | None,
        agent_name: str | None,
        user_id: str | None = None,
    ) -> bool:
        """解析模型响应、应用更新并持久化记忆。"""
        update_data = _parse_memory_update_response(response_content)
        # Deep-copy before in-place mutation so a subsequent save() failure
        # cannot corrupt the still-cached original object reference.
        updated_memory = self._apply_updates(copy.deepcopy(current_memory), update_data, thread_id)
        updated_memory = _strip_upload_mentions_from_memory(updated_memory)
        return get_memory_storage().save(updated_memory, agent_name, user_id=user_id)

    async def aupdate_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """异步更新记忆：委托给同步路径执行。

        使用 ``asyncio.to_thread`` 在工作线程中执行同步的 ``model.invoke()``，
        既不创建第二个事件循环，也绝不触碰 LangChain 的异步 httpx 客户端连接池
        （与 Lead Agent 共享）。该方案消除了 issue #2615 中描述的
        跨事件循环连接复用问题。
        """
        return await asyncio.to_thread(
            self._do_update_memory_sync,
            messages=messages,
            thread_id=thread_id,
            agent_name=agent_name,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
            user_id=user_id,
        )

    def _do_update_memory_sync(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """使用 ``model.invoke()`` 的纯同步记忆更新。

        通过同步 LLM 调用路径执行，不会创建新的事件循环，从而保证 LangChain
        提供方全局缓存的异步 httpx ``AsyncClient``/连接池（与 Lead Agent 共享）
        永远不会被触碰——杜绝跨循环连接复用。
        """
        try:
            prepared = self._prepare_update_prompt(
                messages=messages,
                agent_name=agent_name,
                correction_detected=correction_detected,
                reinforcement_detected=reinforcement_detected,
                user_id=user_id,
            )
            if prepared is None:
                return False

            current_memory, prompt = prepared
            model = self._get_model()
            response = model.invoke(prompt, config={"run_name": "memory_agent"})
            return self._finalize_update(
                current_memory=current_memory,
                response_content=response.content,
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=user_id,
            )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response for memory update: %s", e)
            return False
        except Exception as e:
            logger.exception("Memory update failed: %s", e)
            return False

    def update_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """通过同步 LLM 路径同步更新记忆。

        使用 ``model.invoke()``（同步 HTTP），与 Lead Agent 共享的异步
        ``AsyncClient`` 连接池完全隔离，从而消除了 issue #2615 中描述的
        跨循环连接复用问题。

        若在运行中的事件循环内调用（例如从 LangGraph 节点调用），
        会将阻塞的同步调用卸载到线程池，避免阻塞调用方循环。

        Args:
            messages: 会话消息列表。
            thread_id: 可选的线程 ID，用于追踪来源。
            agent_name: 若提供则按 Agent 隔离更新记忆；为 ``None`` 时更新全局记忆。
            correction_detected: 最近的对话轮次中是否包含显式纠正信号。
            reinforcement_detected: 最近的对话轮次中是否包含正向强化信号。
            user_id: 若提供则按用户隔离记忆。

        Returns:
            更新成功返回 ``True``，否则返回 ``False``。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            try:
                future = _SYNC_MEMORY_UPDATER_EXECUTOR.submit(
                    self._do_update_memory_sync,
                    messages=messages,
                    thread_id=thread_id,
                    agent_name=agent_name,
                    correction_detected=correction_detected,
                    reinforcement_detected=reinforcement_detected,
                    user_id=user_id,
                )
                return future.result()
            except Exception:
                logger.exception("Failed to offload memory update to executor")
                return False

        return self._do_update_memory_sync(
            messages=messages,
            thread_id=thread_id,
            agent_name=agent_name,
            correction_detected=correction_detected,
            reinforcement_detected=reinforcement_detected,
            user_id=user_id,
        )

    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """将 LLM 生成的更新应用到记忆数据结构中（原地修改深拷贝）。

        更新步骤：
        1. **User 段**：遍历 ``workContext/personalContext/topOfMind``，仅当
           ``shouldUpdate=true`` 且 ``summary`` 非空时才覆盖
        2. **History 段**：同上逻辑，遍历 ``recentMonths/earlierContext/longTermBackground``
        3. **删除事实**：按 ``factsToRemove`` 中的 ID 列表移除
        4. **新增事实**：置信度 ≥ ``fact_confidence_threshold``（默认 0.7）且内容不重复才追加
        5. **截断**：按置信度降序保留前 ``max_facts``（默认 100）条

        输入/输出示例：
        ```python
        current_memory = {
            "user": {"workContext": {"summary": "旧摘要", "updatedAt": "..."}},
            "history": {"recentMonths": {"summary": "", "updatedAt": ""}},
            "facts": [{"id":"f1", "content":"偏好 Python", "confidence": 0.9}],
        }
        update_data = {
            "user": {"workContext": {"summary": "新摘要", "shouldUpdate": True}},
            "history": {},
            "newFacts": [{"content": "正在学 Rust", "category": "goal", "confidence": 0.8}],
            "factsToRemove": [],
        }
        # → 返回:
        # {
        #     "user": {"workContext": {"summary": "新摘要", "updatedAt": "2026-..."}},
        #     "history": {"recentMonths": {"summary": "", "updatedAt": ""}},
        #     "facts": [
        #         {"id":"f1", "content":"偏好 Python", "confidence":0.9},
        #         {"id":"f2", "content":"正在学 Rust", "category":"goal", "confidence":0.8},
        #     ],
        # }
        ```

        Args:
            current_memory: 当前记忆数据（会被深拷贝，原对象不受影响）。
            update_data: LLM 生成的归一化更新数据。
            thread_id: 可选的线程 ID，用于 ``source`` 字段追踪来源。

        Returns:
            更新后的记忆数据字典。
        """
        config = get_memory_config()
        now = utc_now_iso_z()

        # Update user sections
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Update history sections
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Remove facts
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # Add new facts
        existing_fact_keys = {fact_key for fact_key in (_fact_content_key(fact.get("content")) for fact in current_memory.get("facts", [])) if fact_key is not None}
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:
                raw_content = fact.get("content", "")
                if not isinstance(raw_content, str):
                    continue
                normalized_content = raw_content.strip()
                fact_key = _fact_content_key(normalized_content)
                if fact_key is not None and fact_key in existing_fact_keys:
                    continue

                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized_content,
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                source_error = fact.get("sourceError")
                if isinstance(source_error, str):
                    normalized_source_error = source_error.strip()
                    if normalized_source_error:
                        fact_entry["sourceError"] = normalized_source_error
                current_memory["facts"].append(fact_entry)
                if fact_key is not None:
                    existing_fact_keys.add(fact_key)

        # Enforce max facts limit
        if len(current_memory["facts"]) > config.max_facts:
            # Sort by confidence and keep top ones
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory


def update_memory_from_conversation(
    messages: list[Any],
    thread_id: str | None = None,
    agent_name: str | None = None,
    correction_detected: bool = False,
    reinforcement_detected: bool = False,
    user_id: str | None = None,
) -> bool:
    """根据会话更新记忆的便捷函数（创建临时 ``MemoryUpdater`` 实例）。

    一行调用完成完整更新流水线：
    ```python
    ok = update_memory_from_conversation(
        messages=filtered_msgs,
        thread_id="thread_abc",
        agent_name="my-agent",
        user_id="user_123",
        correction_detected=True,
    )
    if ok:
        print("记忆已更新")
    ```

    Args:
        messages: 会话消息列表（建议先用 ``filter_messages_for_memory()`` 筛选）。
        thread_id: 可选的线程 ID，写入事实的 ``source`` 字段。
        agent_name: 若提供则按 Agent 隔离更新记忆；为 ``None`` 时更新全局记忆。
        correction_detected: 最近的对话轮次中是否包含显式纠正信号。
        reinforcement_detected: 最近的对话轮次中是否包含正向强化信号。
        user_id: 若提供则按用户隔离记忆。

    Returns:
        成功返回 ``True``，LLM 调用失败、JSON 解析失败等返回 ``False``。
    """
    updater = MemoryUpdater()
    return updater.update_memory(messages, thread_id, agent_name, correction_detected, reinforcement_detected, user_id=user_id)
