"""当提供方对补全做了安全终止时，跳过工具执行。

    背景——参见 issue ``bytedance/deer-flow#3028``。

    部分提供方（OpenAI ``finish_reason='content_filter'``、Anthropic
    ``stop_reason='refusal'``、Gemini ``finish_reason='SAFETY'`` 等）会在中途停止生成，
    但仍会返回半成品的 ``tool_calls``。LangChain 的 tool 路由会把任何
    ``tool_calls`` 字段非空的 AIMessage 视为「去执行这些」，于是被截断一半的参数
    （例如停在句中的 markdown ``write_file``）也会被当成完整的请求派发出去。
    Agent 随后看到被截断的文件、尝试修复、再被过滤、然后陷入循环。

    该中间件位于 ``after_model`` 阶段并对这种行为进行拦截：当配置的
    ``SafetyTerminationDetector`` 触发 *且* AIMessage 携带 tool call 时，
    我们剥离 tool call（同时清除结构化与原始提供方负载），追加一条面向用户的解释，
    并将可观测字段存放在 ``additional_kwargs.safety_termination`` 中，
    便于日志、追踪与 SSE 消费者查看发生了什么。

    钩子选择：``after_model``（而非 ``wrap_model_call``），因为响应是 *正常* 返回
    （不是异常），并且希望与 ``LoopDetectionMiddleware`` 处在同一条 after-model
    链上——两者复用 tool-call 抑制机制但触发条件不同。

    注册位置：在中间件列表中放在 ``LoopDetectionMiddleware`` *之后*。
    LangChain 工厂以反向列表顺序连接 ``after_model`` 边
    （``langchain/agents/factory.py:add_edge("model", middleware_w_after_model[-1])``
    然后 ``range(len-1, 0, -1)``），因此 *最后* 注册的中间件是 *最先* 观察模型输出的。
    把 Safety 注册在 Loop 之后，意味着 Safety 先看到原始响应，按需清除 tool call，
    随后 Loop 在清理后的消息上做判断。
"""


from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.safety_termination_detectors import (
    SafetyTermination,
    SafetyTerminationDetector,
    default_detectors,
)
from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls

if TYPE_CHECKING:
    from deerflow.config.safety_finish_reason_config import SafetyFinishReasonConfig

logger = logging.getLogger(__name__)


_USER_FACING_MESSAGE = (
    "The model provider stopped this response with a safety-related signal "
    "({reason_field}={reason_value!r}, detector={detector!r}). Any tool "
    "calls produced in this turn were suppressed because their arguments "
    "may be truncated and unsafe to execute. Please rephrase the request "
    "or ask for a narrower output."
)


class SafetyFinishReasonMiddleware(AgentMiddleware[AgentState]):
    """剥离被 ``SafetyTerminationDetector`` 标记的 AIMessage 的 ``tool_calls``。"""

    def __init__(self, detectors: list[SafetyTerminationDetector] | None = None) -> None:
        """初始化中间件。

        Args:
            detectors: 可选的检测器列表；为 ``None`` 时使用默认检测器集合。
        """
        super().__init__()
        # Copy so caller mutations after construction don't leak into us.
        self._detectors: list[SafetyTerminationDetector] = list(detectors) if detectors else default_detectors()

    @classmethod
    def from_config(cls, config: SafetyFinishReasonConfig) -> SafetyFinishReasonMiddleware:
        """从已校验的 Pydantic 配置构造中间件。

        在提供 ``detectors`` 时通过反射加载对应的检测器实例。显式空列表会
        被刻意拒绝——它会在保留中间件的同时静默禁用检测，是最差情况。
        请使用 ``enabled: false`` 完全关闭。
        """
        if config.detectors is None:
            return cls()

        if not config.detectors:
            raise ValueError("safety_finish_reason.detectors must be omitted (use built-ins) or contain at least one entry; use enabled=false to disable the middleware entirely.")

        from deerflow.reflection import resolve_variable

        detectors: list[SafetyTerminationDetector] = []
        for entry in config.detectors:
            detector_cls = resolve_variable(entry.use)
            kwargs = dict(entry.config) if entry.config else {}
            detector = detector_cls(**kwargs)
            if not isinstance(detector, SafetyTerminationDetector):
                raise TypeError(f"{entry.use} did not produce a SafetyTerminationDetector (got {type(detector).__name__}); ensure it has a `name` attribute and a `detect(message)` method")
            detectors.append(detector)
        return cls(detectors=detectors)

    # ----- detection -------------------------------------------------------

    def _detect(self, message: AIMessage) -> SafetyTermination | None:
        """依次运行所有检测器，返回首次命中的安全终止信息。"""
        for detector in self._detectors:
            try:
                hit = detector.detect(message)
            except Exception:  # noqa: BLE001 - never let a buggy detector break the agent run
                logger.exception("SafetyTerminationDetector %r raised; treating as no-match", getattr(detector, "name", type(detector).__name__))
                continue
            if hit is not None:
                return hit
        return None

    # ----- message rewriting ----------------------------------------------

    @staticmethod
    def _append_user_message(content: object, text: str) -> str | list:
        """向 AIMessage 的 content 追加纯文本说明。

        与 ``LoopDetectionMiddleware._append_text`` 行为一致：list 形式
        （Anthropic 思考块、vLLM 推理分段）保持结构，避免被强制转成
        字符串而触发 ``TypeError``。
        """
        if content is None or content == "":
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        return str(content) + f"\n\n{text}"

    def _build_suppressed_message(
        self,
        message: AIMessage,
        termination: SafetyTermination,
    ) -> AIMessage:
        """执行赋值。"""
        suppressed_names = [tc.get("name") or "unknown" for tc in (message.tool_calls or [])]
        explanation = _USER_FACING_MESSAGE.format(
            reason_field=termination.reason_field,
            reason_value=termination.reason_value,
            detector=termination.detector,
        )
        new_content = self._append_user_message(message.content, explanation)

        # clone_ai_message_with_tool_calls handles structured tool_calls,
        # raw additional_kwargs.tool_calls, and function_call in one shot.
        # It only rewrites finish_reason when the old value was "tool_calls",
        # which is not our case — content_filter / refusal / SAFETY stay put
        # so downstream SSE / converters keep seeing the real provider reason.
        cleared = clone_ai_message_with_tool_calls(message, [], content=new_content)

        # Re-clone additional_kwargs so we don't accidentally mutate the
        # dict returned by clone_ai_message_with_tool_calls (which already
        # made a shallow copy, but downstream model_copy still references
        # it). Then stamp the observability record.
        kwargs = dict(getattr(cleared, "additional_kwargs", None) or {})
        kwargs["safety_termination"] = {
            "detector": termination.detector,
            "reason_field": termination.reason_field,
            "reason_value": termination.reason_value,
            "suppressed_tool_call_count": len(suppressed_names),
            "suppressed_tool_call_names": suppressed_names,
            "extras": dict(termination.extras) if termination.extras else {},
        }
        return cleared.model_copy(update={"additional_kwargs": kwargs})

    # ----- observability ---------------------------------------------------

    def _emit_event(
        self,
        termination: SafetyTermination,
        suppressed_names: list[str],
        runtime: Runtime,
    ) -> None:
        """向 SSE 消费者（例如 Web UI）通知工具轮次被抑制的信号。

        便于其协调已流式推送给用户的“工具开始…”占位。失败仅在 debug 级别
        记录并忽略——这是尽力而为的信号。
        """
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        except Exception:  # noqa: BLE001
            logger.debug("get_stream_writer unavailable; skipping safety_termination event", exc_info=True)
            return

        thread_id = None
        if runtime is not None and getattr(runtime, "context", None):
            thread_id = runtime.context.get("thread_id") if isinstance(runtime.context, dict) else None

        try:
            writer(
                {
                    "type": "safety_termination",
                    "detector": termination.detector,
                    "reason_field": termination.reason_field,
                    "reason_value": termination.reason_value,
                    "suppressed_tool_call_count": len(suppressed_names),
                    "suppressed_tool_call_names": suppressed_names,
                    "thread_id": thread_id,
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to emit safety_termination stream event", exc_info=True)

    def _record_audit_event(
        self,
        termination: SafetyTermination,
        message,
        tool_calls: list[dict],
        runtime: Runtime,
    ) -> None:
        """向 ``RunEventStore`` 写入 ``middleware:safety_termination`` 事件用于事后审计。

        ``_emit_event`` 中的自定义流事件由活跃 SSE 客户端消费、运行结束后
        即丢弃；该事件则被持久化，运维可凭单条 SQL 查询回答“今天哪些 run
        被安全抑制”，无需联结消息体。Worker 通过 ``runtime.context["__run_journal"]``
        暴露 run 级 ``RunJournal``；在单元测试、子代理、无事件存储路径下
        缺省时静默跳过。

        工具 **参数** 故意不被记录——这正是被提供方过滤的内容；持久化它们
        会让安全过滤失去意义。仅记录名称/计数/ID 已足够审计与排障。
        """
        journal = None
        if runtime is not None and getattr(runtime, "context", None):
            context = runtime.context
            if isinstance(context, dict):
                journal = context.get("__run_journal")
        if journal is None:
            return

        suppressed_names = [tc.get("name") or "unknown" for tc in tool_calls]
        suppressed_ids = [tc.get("id") for tc in tool_calls if tc.get("id")]

        changes = {
            "detector": termination.detector,
            "reason_field": termination.reason_field,
            "reason_value": termination.reason_value,
            "suppressed_tool_call_count": len(tool_calls),
            "suppressed_tool_call_names": suppressed_names,
            "suppressed_tool_call_ids": suppressed_ids,
            "message_id": getattr(message, "id", None),
            "extras": dict(termination.extras) if termination.extras else {},
        }

        try:
            journal.record_middleware(
                tag="safety_termination",
                name=type(self).__name__,
                hook="after_model",
                action="suppress_tool_calls",
                changes=changes,
            )
        except Exception:  # noqa: BLE001
            # Audit-event persistence must never break agent execution.
            logger.debug("Failed to record middleware:safety_termination event", exc_info=True)

    # ----- main apply ------------------------------------------------------

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        """执行赋值。"""
        messages = state.get("messages", [])
        if not messages:
            return None

        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        # Issue scope: only intervene when there's something to suppress.
        # ``content_filter`` without tool_calls is allowed through unchanged
        # so the partial text response (if any) reaches the user naturally.
        tool_calls = last.tool_calls
        if not tool_calls:
            return None

        termination = self._detect(last)
        if termination is None:
            return None

        patched = self._build_suppressed_message(last, termination)

        thread_id = None
        if runtime is not None and getattr(runtime, "context", None):
            thread_id = runtime.context.get("thread_id") if isinstance(runtime.context, dict) else None

        logger.warning(
            "Provider safety termination detected — suppressed %d tool call(s)",
            len(tool_calls),
            extra={
                "thread_id": thread_id,
                "detector": termination.detector,
                "reason_field": termination.reason_field,
                "reason_value": termination.reason_value,
                "suppressed_tool_call_names": [tc.get("name") for tc in tool_calls],
            },
        )

        self._emit_event(termination, [tc.get("name") or "unknown" for tc in tool_calls], runtime)
        self._record_audit_event(termination, last, list(tool_calls), runtime)

        return {"messages": [patched]}

    # ----- hooks -----------------------------------------------------------

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """模型调用后同步钩子。"""
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """模型调用后异步钩子。"""
        return self._apply(state, runtime)
