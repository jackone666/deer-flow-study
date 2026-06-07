"""对单次工具结果强制大小预算的中间件。

    超大的工具结果会被持久化到磁盘，并替换为包含文件引用的精简预览。
    当磁盘持久化不可用时，中间件会回退到 head+tail 截断，
    避免单条巨大结果撑爆模型上下文。
"""


from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace as dc_replace
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.config.tool_output_config import ToolOutputConfig

logger = logging.getLogger(__name__)


def _default_config() -> ToolOutputConfig:
    """返回值。"""
    return ToolOutputConfig()


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _message_text(content: Any) -> str | None:
    """从 ``ToolMessage.content`` 中提取纯文本表示。

    对于非字符串/多模态内容（如图片、结构化块）返回 ``None``，
    调用方据此跳过预算限制。
    """
    if isinstance(content, str):
        return content
    if content is None:
        return None
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
            else:
                return None
        return "\n".join(pieces) if pieces else None
    return None


def _snap_to_line_boundary(text: str, pos: int) -> int:
    """返回 *pos* 或距离 *pos* 最近的前一个换行 +1，取更接近的。

    让预览与截断在可能的情况下以完整行结束。若 ``text[:pos]`` 的后半段
    找不到换行符，则原样返回 *pos*。
    """
    if pos <= 0 or pos >= len(text):
        return pos
    half = pos // 2
    nl = text.rfind("\n", half, pos)
    if nl >= 0:
        return nl + 1
    return pos


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------

_EXT_MAP: dict[str, str] = {
    "bash": "log",
    "bash_tool": "log",
    "web_fetch": "log",
}


def _sanitize_tool_name(name: str) -> str:
    """去除工具名中的路径分隔符与穿越组件。"""
    base = os.path.basename(name)
    safe = base.replace("..", "").replace("/", "_").replace("\\", "_")
    return safe or "unknown"


def _externalize(
    content: str,
    *,
    tool_name: str,
    tool_call_id: str,
    outputs_path: str,
    storage_subdir: str,
) -> str | None:
    """将 *content* 写入磁盘并返回虚拟路径，写入失败则返回 ``None``。"""
    if os.path.isabs(storage_subdir) or ".." in storage_subdir:
        return None
    storage_dir = os.path.join(outputs_path, storage_subdir)
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except OSError:
        return None

    safe_name = _sanitize_tool_name(tool_name)
    ext = _EXT_MAP.get(tool_name, "txt")
    short_id = uuid.uuid4().hex[:12]
    filename = f"{safe_name}-{short_id}.{ext}"
    filepath = os.path.join(storage_dir, filename)

    if not os.path.abspath(filepath).startswith(os.path.abspath(storage_dir)):
        return None

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return None

    virtual_base = "/mnt/user-data/outputs"
    return f"{virtual_base}/{storage_subdir}/{filename}"


# ---------------------------------------------------------------------------
# Preview / fallback builders
# ---------------------------------------------------------------------------


def _build_preview(
    content: str,
    *,
    tool_name: str,
    virtual_path: str,
    head_chars: int,
    tail_chars: int,
) -> str:
    """为外部化输出构造带有文件引用的预览片段。"""
    total = len(content)
    head_end = _snap_to_line_boundary(content, min(head_chars, total))
    tail_start = max(head_end, total - tail_chars)
    tail_start_snapped = _snap_to_line_boundary(content, tail_start)
    if tail_start_snapped > head_end:
        tail_start = tail_start_snapped

    head = content[:head_end]
    tail = content[tail_start:] if tail_start < total else ""

    omitted = total - len(head) - len(tail)
    ref = f"\n\n[Full {tool_name} output saved to {virtual_path} ({total} chars, ~{total // 4} tokens). Use read_file with start_line and end_line to access specific sections. {omitted} chars omitted from this preview.]\n\n"

    parts = [head, ref]
    if tail:
        parts.append(tail)
    return "".join(parts)


def _build_fallback(
    content: str,
    *,
    tool_name: str,
    max_chars: int,
    head_chars: int,
    tail_chars: int,
) -> str:
    """在磁盘持久化不可用时构造 head+tail 截断。

    返回的字符串长度保证不超过 *max_chars*。
    """
    total = len(content)
    if max_chars <= 0 or total <= max_chars:
        return content

    marker_template = "\n\n[... {n} chars omitted from {tn} output. Persistent storage unavailable. Consider narrowing the query or using more specific parameters.]\n\n"
    marker_overhead = len(marker_template.format(n=total, tn=tool_name))

    if marker_overhead >= max_chars:
        return content[:max_chars]

    budget = max_chars - marker_overhead
    effective_head = min(head_chars, budget)
    effective_tail = min(tail_chars, max(0, budget - effective_head))

    head_end = _snap_to_line_boundary(content, min(effective_head, total))
    tail_start = max(head_end, total - effective_tail)
    tail_start_snapped = _snap_to_line_boundary(content, tail_start)
    if tail_start_snapped > head_end:
        tail_start = tail_start_snapped

    head = content[:head_end]
    tail = content[tail_start:] if tail_start < total else ""
    omitted = total - len(head) - len(tail)

    marker = marker_template.format(n=omitted, tn=tool_name)

    parts = [head, marker]
    if tail:
        parts.append(tail)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core budget logic
# ---------------------------------------------------------------------------


def _resolve_outputs_path(request: ToolCallRequest) -> str | None:
    """尽力解析当前线程的 outputs 目录路径。"""
    runtime = getattr(request, "runtime", None)
    if runtime is None:
        return None
    state = getattr(runtime, "state", None)
    if state is None:
        return None
    thread_data = state.get("thread_data")
    if not isinstance(thread_data, dict):
        return None
    outputs_path = thread_data.get("outputs_path")
    return outputs_path if isinstance(outputs_path, str) else None


def _budget_content(
    content: str,
    *,
    tool_name: str,
    tool_call_id: str,
    outputs_path: str | None,
    config: ToolOutputConfig,
) -> str | None:
    """对 *content* 施加预算。无变化时返回 ``None``。"""
    threshold = config.tool_overrides.get(tool_name, config.externalize_min_chars)
    if threshold <= 0 and config.fallback_max_chars <= 0:
        return None
    if len(content) <= threshold and len(content) <= config.fallback_max_chars:
        return None

    if threshold > 0 and len(content) > threshold and outputs_path:
        virtual_path = _externalize(
            content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            outputs_path=outputs_path,
            storage_subdir=config.storage_subdir,
        )
        if virtual_path is not None:
            logger.info(
                "Externalized %s output (%d chars) to %s",
                tool_name,
                len(content),
                virtual_path,
            )
            return _build_preview(
                content,
                tool_name=tool_name,
                virtual_path=virtual_path,
                head_chars=config.preview_head_chars,
                tail_chars=config.preview_tail_chars,
            )

    if config.fallback_max_chars > 0 and len(content) > config.fallback_max_chars:
        logger.warning(
            "Fallback-truncating %s output: %d chars → %d max",
            tool_name,
            len(content),
            config.fallback_max_chars,
        )
        return _build_fallback(
            content,
            tool_name=tool_name,
            max_chars=config.fallback_max_chars,
            head_chars=config.fallback_head_chars,
            tail_chars=config.fallback_tail_chars,
        )

    return None


# ---------------------------------------------------------------------------
# Result patchers
# ---------------------------------------------------------------------------


def _patch_tool_message(msg: ToolMessage, config: ToolOutputConfig, outputs_path: str | None) -> ToolMessage:
    """对单个 ToolMessage 施加预算；未变化时返回原对象。"""
    tool_name = msg.name or "unknown"
    if tool_name in config.exempt_tools:
        return msg

    text = _message_text(msg.content)
    if text is None:
        return msg

    replacement = _budget_content(
        text,
        tool_name=tool_name,
        tool_call_id=msg.tool_call_id or "",
        outputs_path=outputs_path,
        config=config,
    )
    if replacement is None:
        return msg

    update: dict[str, Any] = {"content": replacement}
    if getattr(msg, "response_metadata", None):
        update["response_metadata"] = dict(msg.response_metadata)
    if getattr(msg, "additional_kwargs", None):
        update["additional_kwargs"] = dict(msg.additional_kwargs)
    return msg.model_copy(update=update)


def _effective_trigger(tool_name: str, config: ToolOutputConfig) -> int:
    """返回可能触发 *tool_name* 预算的最小内容长度。

    与 :func:`_budget_content` 中的触发条件（每工具外部化阈值或全局
    回退）保持一致，确保预扫描不会出现漏报；当无论如何都不会触发时
    返回 ``-1``。
    """
    candidates: list[int] = []
    externalize = config.tool_overrides.get(tool_name, config.externalize_min_chars)
    if externalize > 0:
        candidates.append(externalize)
    if config.fallback_max_chars > 0:
        candidates.append(config.fallback_max_chars)
    return min(candidates) if candidates else -1


def _tool_message_over_budget(msg: ToolMessage, config: ToolOutputConfig) -> bool:
    """廉价、按工具感知的检查：该 ToolMessage 是否非豁免且超出其触发阈值？"""
    if (msg.name or "") in config.exempt_tools:
        return False
    trigger = _effective_trigger(msg.name or "", config)
    if trigger < 0:
        return False
    text = _message_text(msg.content)
    return text is not None and len(text) > trigger


def _needs_budget(result: ToolMessage | Command, config: ToolOutputConfig) -> bool:
    """快速判断 *result* 是否可能需要预算（避免对小型输出做线程卸载）。"""
    if isinstance(result, ToolMessage):
        return _tool_message_over_budget(result, config)
    update = getattr(result, "update", None)
    if isinstance(update, dict):
        for msg in update.get("messages", []):
            if isinstance(msg, ToolMessage) and _tool_message_over_budget(msg, config):
                return True
    return False


def _patch_result(result: ToolMessage | Command, config: ToolOutputConfig, outputs_path: str | None) -> ToolMessage | Command:
    """对工具调用结果（``ToolMessage`` 或 ``Command``）施加预算。"""
    if isinstance(result, ToolMessage):
        return _patch_tool_message(result, config, outputs_path)

    update = getattr(result, "update", None)
    if not isinstance(update, dict):
        return result

    messages = update.get("messages")
    if not isinstance(messages, list):
        return result

    new_messages: list[Any] = []
    changed = False
    for msg in messages:
        if isinstance(msg, ToolMessage):
            patched = _patch_tool_message(msg, config, outputs_path)
            if patched is not msg:
                changed = True
            new_messages.append(patched)
        else:
            new_messages.append(msg)

    if not changed:
        return result

    return dc_replace(result, update={**update, "messages": new_messages})


def _patch_model_messages(messages: list[Any], config: ToolOutputConfig) -> list[Any] | None:
    """对模型请求中的历史 ``ToolMessage`` 施加预算，未变化则返回 ``None``。

    廉价的预扫描在所有历史 ``ToolMessage`` 都不超额时直接退出，避免分配
    新列表——这是工具调用阶段已完成预算后最常见的情况，因此不会在每次
    模型调用时重建长历史。
    """
    if not any(isinstance(msg, ToolMessage) and _tool_message_over_budget(msg, config) for msg in messages):
        return None

    updated: list[Any] = []
    changed = False
    for msg in messages:
        if isinstance(msg, ToolMessage):
            patched = _patch_tool_message(msg, config, outputs_path=None)
            if patched is not msg:
                changed = True
            updated.append(patched)
        else:
            updated.append(msg)
    return updated if changed else None


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class ToolOutputBudgetMiddleware(AgentMiddleware[AgentState]):
    """通过外部化或截断对工具输出施加单次结果的预算限制。"""

    def __init__(self, config: ToolOutputConfig | None = None) -> None:
        """初始化 self。"""
        super().__init__()
        self._config = config if config is not None else _default_config()

    @classmethod
    def from_app_config(cls, app_config: Any) -> ToolOutputBudgetMiddleware:
        """执行赋值。
        
                Args:
                    cls: 参数说明。
                    app_config: Any: 参数说明。
        
                Returns:
                    ToolOutputBudgetMiddleware。
        """
        tool_output = getattr(app_config, "tool_output", None)
        if isinstance(tool_output, ToolOutputConfig):
            return cls(config=tool_output)
        return cls()

    # -- tool call hooks ---------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步入口：拦截工具调用，按需修改 ``request`` 后调用 ``handler``。"""
        result = handler(request)
        if not self._config.enabled:
            return result
        if not _needs_budget(result, self._config):
            return result
        outputs_path = _resolve_outputs_path(request)
        return _patch_result(result, self._config, outputs_path)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步入口：拦截工具调用，按需修改 ``request`` 后 ``await handler``。"""
        result = await handler(request)
        if not self._config.enabled:
            return result
        if not _needs_budget(result, self._config):
            return result
        outputs_path = _resolve_outputs_path(request)
        return await asyncio.to_thread(_patch_result, result, self._config, outputs_path)

    # -- model call hooks (historical message truncation) ------------------

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """同步入口：拦截模型调用，必要时修改 ``request`` 后调用 ``handler``。"""
        if self._config.enabled:
            messages = getattr(request, "messages", None)
            if isinstance(messages, list):
                patched = _patch_model_messages(messages, self._config)
                if patched is not None:
                    request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """异步入口：拦截模型调用，必要时修改 ``request`` 后 ``await handler``。"""
        if self._config.enabled:
            messages = getattr(request, "messages", None)
            if isinstance(messages, list):
                patched = _patch_model_messages(messages, self._config)
                if patched is not None:
                    request = request.override(messages=patched)
        return await handler(request)
