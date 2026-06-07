"""DeerFlowClient —— DeerFlow Agent 系统的嵌入式 Python 客户端。

提供对 DeerFlow Agent 能力的直接编程式访问，无需启动 LangGraph Server
或 Gateway API 进程。

用法::

    from deerflow.client import DeerFlowClient

    client = DeerFlowClient()
    response = client.chat("Analyze this paper for me", thread_id="my-thread")
    print(response)

    # 流式
    for event in client.stream("hello"):
        print(event)
"""

import asyncio
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import uuid
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.agent import _assemble_deferred, _build_middlewares
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import AGENT_NAME_PATTERN
from deerflow.config.app_config import get_app_config, reload_app_config
from deerflow.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from deerflow.config.paths import get_paths
from deerflow.models import create_chat_model
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.tracing import build_tracing_callbacks, inject_langfuse_metadata
from deerflow.uploads.manager import (
    claim_unique_filename,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    upload_artifact_url,
    upload_virtual_path,
)

logger = logging.getLogger(__name__)


StreamEventType = Literal["values", "messages-tuple", "custom", "end"]


@dataclass
class StreamEvent:
    """流式 agent 响应中的单个事件。

    事件类型与 LangGraph SSE 协议保持一致：

    - ``"values"``：完整状态快照（title、messages、artifacts）。
    - ``"messages-tuple"``：单条消息更新（AI 文本、工具调用、工具结果）。
    - ``"end"``：流结束。

    Attributes:
        type: 事件类型。
        data: 事件负载，结构随类型变化。
    """

    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)


class DeerFlowClient:
    """DeerFlow Agent 系统的嵌入式 Python 客户端。

    提供对 DeerFlow Agent 能力的直接编程式访问，无需启动 LangGraph Server
    或 Gateway API 进程。

    Note:
        多轮对话需要传入 ``checkpointer``。未传入时，每次 ``stream()`` /
        ``chat()`` 调用都是无状态的——``thread_id`` 仅用于文件隔离
        （uploads / artifacts）。

        系统提示（包含日期、记忆、技能上下文）会在首次创建内部 agent 时
        生成并缓存，直到相关配置键发生变化。长时间运行进程中如需强制
        刷新，可调用 :meth:`reset_agent`。

    示例::

        from deerflow.client import DeerFlowClient

        client = DeerFlowClient()

        # 简单一次性调用
        print(client.chat("hello"))

        # 流式
        for event in client.stream("hello"):
            print(event.type, event.data)

        # 配置查询
        print(client.list_models())
        print(client.list_skills())
    """

    def __init__(
        self,
        config_path: str | None = None,
        checkpointer=None,
        *,
        model_name: str | None = None,
        thinking_enabled: bool = True,
        subagent_enabled: bool = False,
        plan_mode: bool = False,
        agent_name: str | None = None,
        available_skills: set[str] | None = None,
        middlewares: Sequence[AgentMiddleware] | None = None,
        environment: str | None = None,
    ):
        """初始化客户端。

        加载配置但延迟到首次使用时再创建 agent。

        Args:
            config_path: ``config.yaml`` 路径；为 ``None`` 时走默认解析。
            checkpointer: 用于状态持久化的 LangGraph checkpointer。在同一
                ``thread_id`` 上进行多轮对话时必须提供；未提供时每次调用
                都是无状态的。
            model_name: 覆盖配置中的默认模型名。
            thinking_enabled: 是否启用模型的扩展思考。
            subagent_enabled: 是否启用 subagent 委派。
            plan_mode: 是否启用 plan mode 的 TodoList 中间件。
            agent_name: 要使用的 agent 名称。
            available_skills: 可选的技能名集合；为 ``None``（默认）时所有
                已扫描技能均可用。
            middlewares: 要注入到 agent 中的自定义中间件列表。
            environment: 部署环境标签，会写入 ``langfuse_tags``（如
                ``"production"`` / ``"staging"``）。为 ``None`` 时回退到
                ``DEER_FLOW_ENV`` 或 ``ENVIRONMENT`` 环境变量。程序化调用
                者可显式传值以避免与 env 变量耦合。
        """
        if config_path is not None:
            reload_app_config(config_path)
        self._app_config = get_app_config()

        if agent_name is not None and not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name '{agent_name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")

        self._checkpointer = checkpointer
        self._model_name = model_name
        self._thinking_enabled = thinking_enabled
        self._subagent_enabled = subagent_enabled
        self._plan_mode = plan_mode
        self._agent_name = agent_name
        self._available_skills = set(available_skills) if available_skills is not None else None
        self._middlewares = list(middlewares) if middlewares else []
        self._environment = environment

        # Lazy agent — created on first call, recreated when config changes.
        self._agent = None
        self._agent_config_key: tuple | None = None

    def reset_agent(self) -> None:
        """强制在下次调用时重新创建内部 agent。

        在外部发生变化（例如记忆更新、技能安装）后，希望这些变化反映到
        系统提示或工具集时使用。
        """
        self._agent = None
        self._agent_config_key = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write_json(path: Path, data: dict) -> None:
        """以「临时文件 + 替换」的方式原子地写入 JSON 到 ``path``。

        Args:
            path: 目标文件路径。
            data: 待写入的字典对象。

        Raises:
            OSError: 写入或替换失败时。
        """
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    def _get_runnable_config(self, thread_id: str, **overrides) -> RunnableConfig:
        """为 agent 调用构造一个 :class:`RunnableConfig`。

        Args:
            thread_id: 当前对话的 thread ID。
            **overrides: 可选覆盖项，支持 ``model_name``、``thinking_enabled``、
                ``plan_mode``、``subagent_enabled``、``recursion_limit``。

        Returns:
            可直接传给 ``agent.stream`` / ``agent.invoke`` 的 :class:`RunnableConfig`。
        """
        configurable = {
            "thread_id": thread_id,
            "model_name": overrides.get("model_name", self._model_name),
            "thinking_enabled": overrides.get("thinking_enabled", self._thinking_enabled),
            "is_plan_mode": overrides.get("plan_mode", self._plan_mode),
            "subagent_enabled": overrides.get("subagent_enabled", self._subagent_enabled),
        }
        return RunnableConfig(
            configurable=configurable,
            recursion_limit=overrides.get("recursion_limit", 100),
        )

    def _ensure_agent(self, config: RunnableConfig):
        """当依赖配置的参数发生变化时创建（或重建）内部 agent。

        通过 ``_agent_config_key`` 对当前 ``configurable`` 与客户端
        默认值做哈希；不匹配时调用 ``create_agent`` 重建。

        Args:
            config: 由 :meth:`_get_runnable_config` 生成的 :class:`RunnableConfig`。
        """
        cfg = config.get("configurable", {})
        key = (
            cfg.get("model_name"),
            cfg.get("thinking_enabled"),
            cfg.get("is_plan_mode"),
            cfg.get("subagent_enabled"),
            self._agent_name,
            frozenset(self._available_skills) if self._available_skills is not None else None,
        )

        if self._agent is not None and self._agent_config_key == key:
            return

        thinking_enabled = cfg.get("thinking_enabled", True)
        model_name = cfg.get("model_name")
        subagent_enabled = cfg.get("subagent_enabled", False)
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)

        tools = self._get_tools(model_name=model_name, subagent_enabled=subagent_enabled)
        final_tools, deferred_setup = _assemble_deferred(tools, enabled=self._app_config.tool_search.enabled)
        kwargs: dict[str, Any] = {
            # attach_tracing=False because ``stream()`` injects tracing
            # callbacks at the graph invocation root so a single embedded run
            # produces one trace with correct session_id / user_id propagation.
            # Attaching them again on the model would emit duplicate spans.
            "model": create_chat_model(name=model_name, thinking_enabled=thinking_enabled, attach_tracing=False),
            "tools": final_tools,
            "middleware": _build_middlewares(config, model_name=model_name, agent_name=self._agent_name, custom_middlewares=self._middlewares, deferred_setup=deferred_setup),
            "system_prompt": apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                agent_name=self._agent_name,
                available_skills=self._available_skills,
                deferred_names=deferred_setup.deferred_names,
            ),
            "state_schema": ThreadState,
        }
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer import get_checkpointer

            checkpointer = get_checkpointer()
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer

        self._agent = create_agent(**kwargs)
        self._agent_config_key = key
        logger.info("Agent created: agent_name=%s, model=%s, thinking=%s", self._agent_name, model_name, thinking_enabled)

    @staticmethod
    def _get_tools(*, model_name: str | None, subagent_enabled: bool):
        """延迟导入工具注册中心以避免模块级循环依赖。

        Args:
            model_name: 模型名称，会传给工具注册中心用于过滤模型相关工具。
            subagent_enabled: 是否启用 subagent 相关工具。

        Returns:
            当前可用的工具列表。
        """
        from deerflow.tools import get_available_tools

        return get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled)

    @staticmethod
    def _serialize_tool_calls(tool_calls) -> list[dict]:
        """将 LangChain 的 ``tool_calls`` 重组为事件使用的 wire 格式。

        Args:
            tool_calls: LangChain 消息上的 ``tool_calls`` 字段。

        Returns:
            每个工具调用包含 ``name``、``args``、``id`` 三个键的字典列表。
        """
        return [{"name": tc["name"], "args": tc["args"], "id": tc.get("id")} for tc in tool_calls]

    @staticmethod
    def _serialize_additional_kwargs(msg) -> dict[str, Any] | None:
        """在 ``additional_kwargs`` 存在时返回其浅拷贝。

        Args:
            msg: LangChain 消息对象。

        Returns:
            若 ``additional_kwargs`` 是非空字典则返回浅拷贝，否则返回 ``None``。
        """
        additional_kwargs = getattr(msg, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict) and additional_kwargs:
            return dict(additional_kwargs)
        return None

    @staticmethod
    def _ai_text_event(msg_id: str | None, text: str, usage: dict | None, additional_kwargs: dict[str, Any] | None = None) -> "StreamEvent":
        """构造一个 ``messages-tuple`` 类型的 AI 文本事件。"""
        data: dict[str, Any] = {"type": "ai", "content": text, "id": msg_id}
        if usage:
            data["usage_metadata"] = usage
        if additional_kwargs:
            data["additional_kwargs"] = additional_kwargs
        return StreamEvent(type="messages-tuple", data=data)

    @staticmethod
    def _ai_tool_calls_event(msg_id: str | None, tool_calls, additional_kwargs: dict[str, Any] | None = None) -> "StreamEvent":
        """构造一个 ``messages-tuple`` 类型的 AI 工具调用事件。"""
        data: dict[str, Any] = {
            "type": "ai",
            "content": "",
            "id": msg_id,
            "tool_calls": DeerFlowClient._serialize_tool_calls(tool_calls),
        }
        if additional_kwargs:
            data["additional_kwargs"] = additional_kwargs
        return StreamEvent(type="messages-tuple", data=data)

    @staticmethod
    def _tool_message_event(msg: ToolMessage) -> "StreamEvent":
        """从 :class:`ToolMessage` 构造 ``messages-tuple`` 工具结果事件。"""
        return StreamEvent(
            type="messages-tuple",
            data={
                "type": "tool",
                "content": DeerFlowClient._extract_text(msg.content),
                "name": msg.name,
                "tool_call_id": msg.tool_call_id,
                "id": msg.id,
            },
        )

    @staticmethod
    def _serialize_message(msg) -> dict:
        """将 LangChain 消息序列化为 ``values`` 事件使用的纯字典。

        支持 ``AIMessage``、``ToolMessage``、``HumanMessage``、``SystemMessage``，
        其他类型会落到 ``{"type": "unknown", ...}`` 分支。

        Args:
            msg: 待序列化的 LangChain 消息对象。

        Returns:
            纯 Python 字典，可被 JSON 序列化。
        """
        if isinstance(msg, AIMessage):
            d: dict[str, Any] = {"type": "ai", "content": msg.content, "id": getattr(msg, "id", None)}
            if msg.tool_calls:
                d["tool_calls"] = DeerFlowClient._serialize_tool_calls(msg.tool_calls)
            if getattr(msg, "usage_metadata", None):
                d["usage_metadata"] = msg.usage_metadata
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, ToolMessage):
            d = {
                "type": "tool",
                "content": DeerFlowClient._extract_text(msg.content),
                "name": getattr(msg, "name", None),
                "tool_call_id": getattr(msg, "tool_call_id", None),
                "id": getattr(msg, "id", None),
            }
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, HumanMessage):
            d = {"type": "human", "content": msg.content, "id": getattr(msg, "id", None)}
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        if isinstance(msg, SystemMessage):
            d = {"type": "system", "content": msg.content, "id": getattr(msg, "id", None)}
            if additional_kwargs := DeerFlowClient._serialize_additional_kwargs(msg):
                d["additional_kwargs"] = additional_kwargs
            return d
        return {"type": "unknown", "content": str(msg), "id": getattr(msg, "id", None)}

    @staticmethod
    def _extract_text(content) -> str:
        """从 ``AIMessage.content`` 中抽取纯文本。

        ``content`` 可能是 ``str``、``list``（块数组）或其他类型：

        - 字符串直接返回；
        - 列表中的字符串片段直接拼接（避免破坏 token/字符增量或分块 JSON 负载）；
          基于字典的 ``text`` 块以换行符连接，便于阅读。
        - 其他类型回退为 ``str(content)``。

        Args:
            content: ``AIMessage.content`` 字段。

        Returns:
            抽取得到的纯文本字符串。
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            if content and all(isinstance(block, str) for block in content):
                chunk_like = len(content) > 1 and all(isinstance(block, str) and len(block) <= 20 and any(ch in block for ch in '{}[]":,') for block in content)
                return "".join(content) if chunk_like else "\n".join(content)

            pieces: list[str] = []
            pending_str_parts: list[str] = []

            def flush_pending_str_parts() -> None:
                """把累积的连续字符串片段合并为一段后追加到 pieces 列表。

                仅当 ``pending_str_parts`` 非空时执行；执行后会清空
                ``pending_str_parts``，便于下一批字符串片段重新开始累积。
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
            return "\n".join(pieces) if pieces else ""
        return str(content)

    # ------------------------------------------------------------------
    # Public API — threads
    # ------------------------------------------------------------------

    def list_threads(self, limit: int = 10) -> dict:
        """列出最近的 N 条 thread。

        Args:
            limit: 返回的 thread 最大数量，默认 10。

        Returns:
            形如 ``{"thread_list": [...]}`` 的字典，列表按 ``created_at``
            降序排列。
        """
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer.provider import get_checkpointer

            checkpointer = get_checkpointer()

        thread_info_map = {}

        for cp in checkpointer.list(config=None, limit=limit):
            cfg = cp.config.get("configurable", {})
            thread_id = cfg.get("thread_id")
            if not thread_id:
                continue

            ts = cp.checkpoint.get("ts")
            checkpoint_id = cfg.get("checkpoint_id")

            if thread_id not in thread_info_map:
                channel_values = cp.checkpoint.get("channel_values", {})
                thread_info_map[thread_id] = {
                    "thread_id": thread_id,
                    "created_at": ts,
                    "updated_at": ts,
                    "latest_checkpoint_id": checkpoint_id,
                    "title": channel_values.get("title"),
                }
            else:
                # Explicitly compare timestamps to ensure accuracy when iterating over unordered namespaces.
                # Treat None as "missing" and only compare when existing values are non-None.
                if ts is not None:
                    current_created = thread_info_map[thread_id]["created_at"]
                    if current_created is None or ts < current_created:
                        thread_info_map[thread_id]["created_at"] = ts

                    current_updated = thread_info_map[thread_id]["updated_at"]
                    if current_updated is None or ts > current_updated:
                        thread_info_map[thread_id]["updated_at"] = ts
                        thread_info_map[thread_id]["latest_checkpoint_id"] = checkpoint_id
                        channel_values = cp.checkpoint.get("channel_values", {})
                        thread_info_map[thread_id]["title"] = channel_values.get("title")

        threads = list(thread_info_map.values())
        threads.sort(key=lambda x: x.get("created_at") or "", reverse=True)

        return {"thread_list": threads[:limit]}

    def get_thread(self, thread_id: str) -> dict:
        """获取指定 thread 的完整记录，包含所有 checkpoint。

        Args:
            thread_id: 目标 thread 的 ID。

        Returns:
            包含该 thread 全部 checkpoint 历史的字典。
        """
        checkpointer = self._checkpointer
        if checkpointer is None:
            from deerflow.runtime.checkpointer.provider import get_checkpointer

            checkpointer = get_checkpointer()

        config = {"configurable": {"thread_id": thread_id}}
        checkpoints = []

        for cp in checkpointer.list(config):
            channel_values = dict(cp.checkpoint.get("channel_values", {}))
            if "messages" in channel_values:
                channel_values["messages"] = [self._serialize_message(m) if hasattr(m, "content") else m for m in channel_values["messages"]]

            cfg = cp.config.get("configurable", {})
            parent_cfg = cp.parent_config.get("configurable", {}) if cp.parent_config else {}

            checkpoints.append(
                {
                    "checkpoint_id": cfg.get("checkpoint_id"),
                    "parent_checkpoint_id": parent_cfg.get("checkpoint_id"),
                    "ts": cp.checkpoint.get("ts"),
                    "metadata": cp.metadata,
                    "values": channel_values,
                    "pending_writes": [{"task_id": w[0], "channel": w[1], "value": w[2]} for w in getattr(cp, "pending_writes", [])],
                }
            )

        # Sort globally by timestamp to prevent partial ordering issues caused by different namespaces (e.g., subgraphs)
        checkpoints.sort(key=lambda x: x["ts"] if x["ts"] else "")

        return {"thread_id": thread_id, "checkpoints": checkpoints}

    # ------------------------------------------------------------------
    # Public API — conversation
    # ------------------------------------------------------------------

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs,
    ) -> Generator[StreamEvent, None, None]:
        """流式推进一轮对话，增量产出事件。

        每次调用发送一条用户消息，并持续产出事件直到 agent 完成本轮。
        若要在多次调用之间保留多轮上下文，必须在初始化时提供
        ``checkpointer``。

        事件类型与 LangGraph SSE 协议对齐，便于消费者在 HTTP 流式与
        嵌入式模式间切换而无需调整事件处理逻辑。

        Token 级流式
        ~~~~~~~~~~~~
        本方法订阅 LangGraph 的 ``messages`` 流模式，因此 AI 文本对应的
        ``messages-tuple`` 事件以**增量**形式随模型 token 生成而发出，
        并非节点结束时的累计 dump。每个增量带有稳定的 ``id``，需要完整
        文本的消费者必须按 ``id`` 累加 ``content``。``chat()`` 已代为处理。

        工具调用与工具结果仍然按逻辑消息一次性发出。``values`` 事件继续
        在每个图节点完成后提供完整状态快照；已通过 ``messages`` 流送出的
        AI 文本**不会**从快照中再次合成，以避免重复发送。

        为什么不直接复用 Gateway 的 ``run_agent``？
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Gateway（``runtime/runs/worker.py``）拥有一套完整的流式管线：
        ``run_agent`` → ``StreamBridge`` → ``sse_consumer``。表面上看本
        客户端重复了这些工作，但两条链路面向不同消费者，**不能**共享
        执行：

        - ``run_agent`` 是 ``async def`` 并使用 ``agent.astream()``；本方法
          是基于 ``agent.stream()`` 的同步生成器，调用方可直接写出
          ``for event in client.stream(...)`` 而无需接触 asyncio。若要
          在两条路径间桥接，每次调用都需要新启事件循环 + 线程。
        - Gateway 事件由 ``serialize()`` 做 JSON 序列化以走 SSE wire 传输；
          本客户端直接以 Python 数据结构（``StreamEvent`` 携带纯 ``dict``
          的 ``data``）产出内存中的流事件，不经过 HTTP 投递所用的
          JSON/SSE 序列化层。
        - ``StreamBridge`` 是用于跨 HTTP 边界解耦生产与消费者的
          asyncio 队列（``Last-Event-ID`` 回放、心跳、多订阅者扇出）。
          单进程内直接迭代的调用者不需要这些。

        因此 ``DeerFlowClient.stream()`` 是同一 ``create_agent()`` 工厂的
        平行、同步、进程内消费者——而不是 Gateway 的封装。两条路径**应当**
        在订阅的 LangGraph stream 模式上保持一致；该不变式由
        ``tests/test_client.py::test_messages_mode_emits_token_deltas``
        强制，而不是通过共享常量，因为三层（图、Platform SDK、HTTP）各
        自使用不同的命名（``messages`` 与 ``messages-tuple``），无法直接
        共享字符串。

        Args:
            message: 用户消息文本。
            thread_id: 对话上下文使用的 thread ID；为 ``None`` 时自动生成。
            **kwargs: 覆盖客户端默认值的可选参数（``model_name``、
                ``thinking_enabled``、``plan_mode``、``subagent_enabled``、
                ``recursion_limit``）。

        Yields:
            :class:`StreamEvent`，``type`` 取值之一：

            - ``type="values"``         ``data={"title": str|None, "messages": [...], "artifacts": [...]}``
            - ``type="custom"``         ``data={...}``
            - ``type="messages-tuple"`` ``data={"type": "ai", "content": <delta>, "id": str}``
            - ``type="messages-tuple"`` ``data={"type": "ai", "content": <delta>, "id": str, "usage_metadata": {...}}``
            - ``type="messages-tuple"`` ``data={"type": "ai", "content": "", "id": str, "tool_calls": [...]}``
            - ``type="messages-tuple"`` ``data={"type": "ai", "content": "", "id": str, "additional_kwargs": {...}}``
            - ``type="messages-tuple"`` ``data={"type": "tool", "content": str, "name": str, "tool_call_id": str, "id": str}``
            - ``type="end"``            ``data={"usage": {"input_tokens": int, "output_tokens": int, "total_tokens": int}}``
        """
        if thread_id is None:
            thread_id = str(uuid.uuid4())

        config = self._get_runnable_config(thread_id, **kwargs)

        # Inject tracing callbacks and Langfuse trace metadata at the graph
        # invocation root so the embedded client matches the gateway worker's
        # behaviour: a single ``stream()`` produces one trace with all node /
        # LLM / tool calls nested under it, and the trace carries the reserved
        # ``langfuse_session_id`` / ``langfuse_user_id`` keys that the Langfuse
        # CallbackHandler lifts onto the root trace's ``sessionId`` / ``userId``.
        tracing_callbacks = build_tracing_callbacks()
        if tracing_callbacks:
            existing_callbacks = list(config.get("callbacks") or [])
            config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

        configurable = config.get("configurable") or {}
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=self._agent_name or "lead-agent",
            model_name=configurable.get("model_name") or self._model_name,
            environment=self._environment or os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        self._ensure_agent(config)

        state: dict[str, Any] = {"messages": [HumanMessage(content=message)]}
        context = {"thread_id": thread_id}
        if self._agent_name:
            context["agent_name"] = self._agent_name

        seen_ids: set[str] = set()
        # Cross-mode handoff: ids already streamed via LangGraph ``messages``
        # mode so the ``values`` path skips re-synthesis of the same message.
        streamed_ids: set[str] = set()
        # The same message id carries identical cumulative ``usage_metadata``
        # in both the final ``messages`` chunk and the values snapshot —
        # count it only on whichever arrives first.
        counted_usage_ids: set[str] = set()
        sent_additional_kwargs_by_id: dict[str, dict[str, Any]] = {}
        cumulative_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        def _account_usage(msg_id: str | None, usage: Any) -> dict | None:
            """若该 ``id`` 尚未计费，则把 ``usage`` 累加到累计统计中。

            ``usage`` 是 ``langchain_core.messages.UsageMetadata`` TypedDict
            或 ``None``；由于 TypedDict 在严格类型检查下不能结构化地
            赋给普通 ``dict``，因此以 ``Any`` 标注。被接受的 usage 在
            返回值中以归一化字典形式给出，便于附加到事件中；未接受则
            返回 ``None``。

            Args:
                msg_id: 消息 ID。
                usage: 用量元数据，可能为 ``None``。

            Returns:
                接受时返回归一化后的 usage 字典，否则返回 ``None``。
            """
            if not usage:
                return None
            if msg_id and msg_id in counted_usage_ids:
                return None
            if msg_id:
                counted_usage_ids.add(msg_id)
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0
            cumulative_usage["input_tokens"] += input_tokens
            cumulative_usage["output_tokens"] += output_tokens
            cumulative_usage["total_tokens"] += total_tokens
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }

        def _unsent_additional_kwargs(msg_id: str | None, additional_kwargs: dict[str, Any] | None) -> dict[str, Any] | None:
            """计算本轮流式事件中尚未发送过的 ``additional_kwargs`` 增量。

            用于在流式聊天补全过程中跟踪每个 ``msg_id`` 已下发的额外键值，
            避免重复推送相同字段。每次返回后会更新内部的
            ``sent_additional_kwargs_by_id`` 记录。

            Args:
                msg_id: 消息标识；为 ``None`` 时表示无去重上下文，直接返回原值。
                additional_kwargs: 候选的额外参数字典；为 ``None`` 或空时无增量。

            Returns:
                需要在本轮下发的增量字典；若全部键值都已在历史中发送过则返回
                ``None``，若 ``msg_id`` 为空则原样返回 ``additional_kwargs``。
            """
            if not additional_kwargs:
                return None
            if not msg_id:
                return additional_kwargs

            sent = sent_additional_kwargs_by_id.setdefault(msg_id, {})
            delta = {key: value for key, value in additional_kwargs.items() if sent.get(key) != value}
            if not delta:
                return None

            sent.update(delta)
            return delta

        for item in self._agent.stream(
            state,
            config=config,
            context=context,
            stream_mode=["values", "messages", "custom"],
        ):
            if isinstance(item, tuple) and len(item) == 2:
                mode, chunk = item
                mode = str(mode)
            else:
                mode, chunk = "values", item

            if mode == "custom":
                yield StreamEvent(type="custom", data=chunk)
                continue

            if mode == "messages":
                # LangGraph ``messages`` mode emits ``(message_chunk, metadata)``.
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    msg_chunk, _metadata = chunk
                else:
                    msg_chunk = chunk

                msg_id = getattr(msg_chunk, "id", None)

                if isinstance(msg_chunk, AIMessage):
                    text = self._extract_text(msg_chunk.content)
                    additional_kwargs = self._serialize_additional_kwargs(msg_chunk)
                    counted_usage = _account_usage(msg_id, msg_chunk.usage_metadata)
                    sent_additional_kwargs = False

                    if text:
                        if msg_id:
                            streamed_ids.add(msg_id)
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_text_event(
                            msg_id,
                            text,
                            counted_usage,
                            additional_kwargs_delta,
                        )
                        sent_additional_kwargs = bool(additional_kwargs_delta)

                    if msg_chunk.tool_calls:
                        if msg_id:
                            streamed_ids.add(msg_id)
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_tool_calls_event(
                            msg_id,
                            msg_chunk.tool_calls,
                            additional_kwargs_delta,
                        )

                elif isinstance(msg_chunk, ToolMessage):
                    if msg_id:
                        streamed_ids.add(msg_id)
                    yield self._tool_message_event(msg_chunk)
                continue

            # mode == "values"
            messages = chunk.get("messages", [])

            for msg in messages:
                msg_id = getattr(msg, "id", None)
                if msg_id and msg_id in seen_ids:
                    continue
                if msg_id:
                    seen_ids.add(msg_id)

                # Already streamed via ``messages`` mode; only (defensively)
                # capture usage here and skip re-synthesizing the event.
                if msg_id and msg_id in streamed_ids:
                    if isinstance(msg, AIMessage):
                        _account_usage(msg_id, getattr(msg, "usage_metadata", None))
                        additional_kwargs = self._serialize_additional_kwargs(msg)
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        if additional_kwargs_delta:
                            # Metadata-only follow-up: ``messages-tuple`` has no
                            # dedicated attribution event, so clients should
                            # merge this empty-content AI event by message id
                            # and ignore it for text rendering.
                            yield self._ai_text_event(msg_id, "", None, additional_kwargs_delta)
                    continue

                if isinstance(msg, AIMessage):
                    counted_usage = _account_usage(msg_id, msg.usage_metadata)
                    additional_kwargs = self._serialize_additional_kwargs(msg)
                    sent_additional_kwargs = False

                    if msg.tool_calls:
                        additional_kwargs_delta = _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_tool_calls_event(
                            msg_id,
                            msg.tool_calls,
                            additional_kwargs_delta,
                        )
                        sent_additional_kwargs = bool(additional_kwargs_delta)

                    text = self._extract_text(msg.content)
                    if text:
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        yield self._ai_text_event(
                            msg_id,
                            text,
                            counted_usage,
                            additional_kwargs_delta,
                        )
                    elif msg_id:
                        additional_kwargs_delta = None if sent_additional_kwargs else _unsent_additional_kwargs(msg_id, additional_kwargs)
                        if not additional_kwargs_delta:
                            continue
                        # See the metadata-only follow-up convention above.
                        yield self._ai_text_event(msg_id, "", None, additional_kwargs_delta)

                elif isinstance(msg, ToolMessage):
                    yield self._tool_message_event(msg)

            # Emit a values event for each state snapshot
            yield StreamEvent(
                type="values",
                data={
                    "title": chunk.get("title"),
                    "messages": [self._serialize_message(m) for m in messages],
                    "artifacts": chunk.get("artifacts", []),
                },
            )

        yield StreamEvent(type="end", data={"usage": cumulative_usage})

    def chat(self, message: str, *, thread_id: str | None = None, **kwargs) -> str:
        """发送一条消息并返回最终的文本响应。

        :meth:`stream` 的便捷包装：按 ``id`` 累积 ``messages-tuple`` 增量
        事件，返回**最后**一条完成生成的 AI 消息的文本。中间的 AI 消息
        （如 planner 草稿）会被丢弃——只有最后一个 ``id`` 的累计文本被
        返回。如需逐个增量请直接使用 :meth:`stream`。

        Args:
            message: 用户消息文本。
            thread_id: 对话上下文使用的 thread ID；为 ``None`` 时自动生成。
            **kwargs: 覆盖客户端默认值（与 :meth:`stream` 相同）。

        Returns:
            最后一条 AI 消息的累计文本；若没有 AI 文本则返回空字符串。
        """
        # Per-id delta lists joined once at the end — avoids the O(n²) cost
        # of repeated ``str + str`` on a growing buffer for long responses.
        chunks: dict[str, list[str]] = {}
        last_id: str = ""
        for event in self.stream(message, thread_id=thread_id, **kwargs):
            if event.type == "messages-tuple" and event.data.get("type") == "ai":
                msg_id = event.data.get("id") or ""
                delta = event.data.get("content", "")
                if delta:
                    chunks.setdefault(msg_id, []).append(delta)
                    last_id = msg_id
        return "".join(chunks.get(last_id, ()))

    # ------------------------------------------------------------------
    # Public API — configuration queries
    # ------------------------------------------------------------------

    def list_models(self) -> dict:
        """列出当前可用的模型配置。

        Returns:
            形如 ``{"models": [...], "token_usage": {"enabled": bool}}`` 的
            字典，结构与 Gateway API 的 ``ModelsListResponse`` schema 对齐。
        """
        token_usage_enabled = getattr(getattr(self._app_config, "token_usage", None), "enabled", False)
        if not isinstance(token_usage_enabled, bool):
            token_usage_enabled = False

        return {
            "models": [
                {
                    "name": model.name,
                    "model": getattr(model, "model", None),
                    "display_name": getattr(model, "display_name", None),
                    "description": getattr(model, "description", None),
                    "supports_thinking": getattr(model, "supports_thinking", False),
                    "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
                }
                for model in self._app_config.models
            ],
            "token_usage": {"enabled": token_usage_enabled},
        }

    def list_skills(self, enabled_only: bool = False) -> dict:
        """列出可用的技能。

        Args:
            enabled_only: 若为 ``True``，仅返回已启用的技能。

        Returns:
            形如 ``{"skills": [...]}`` 的字典，结构与 Gateway API 的
            ``SkillsListResponse`` schema 对齐。
        """
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "license": s.license,
                    "category": s.category,
                    "enabled": s.enabled,
                }
                for s in get_or_new_skill_storage().load_skills(enabled_only=enabled_only)
            ]
        }

    def get_memory(self) -> dict:
        """获取当前记忆数据。

        Returns:
            记忆数据字典（结构参见 ``src/agents/memory/updater.py``）。
        """
        from deerflow.agents.memory.updater import get_memory_data

        return get_memory_data(user_id=get_effective_user_id())

    def export_memory(self) -> dict:
        """导出当前记忆数据，用于备份或迁移。"""

    def import_memory(self, memory_data: dict) -> dict:
        """导入并持久化完整的记忆数据。"""
        from deerflow.agents.memory.updater import import_memory_data

        return import_memory_data(memory_data, user_id=get_effective_user_id())

    def get_model(self, name: str) -> dict | None:
        """按名称获取某个模型的配置。

        Args:
            name: 模型名称。

        Returns:
            与 Gateway API ``ModelResponse`` schema 对齐的模型信息字典；
            未找到时返回 ``None``。
        """
        model = self._app_config.get_model_config(name)
        if model is None:
            return None
        return {
            "name": model.name,
            "model": getattr(model, "model", None),
            "display_name": getattr(model, "display_name", None),
            "description": getattr(model, "description", None),
            "supports_thinking": getattr(model, "supports_thinking", False),
            "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
        }

    # ------------------------------------------------------------------
    # Public API — MCP configuration
    # ------------------------------------------------------------------

    def get_mcp_config(self) -> dict:
        """获取 MCP server 配置。

        Returns:
            形如 ``{"mcp_servers": {name: config, ...}}`` 的字典，结构与
            Gateway API 的 ``McpConfigResponse`` schema 对齐。
        """
        config = get_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in config.mcp_servers.items()}}

    def update_mcp_config(self, mcp_servers: dict[str, dict]) -> dict:
        """更新 MCP server 配置。

        会写入 ``extensions_config.json`` 并重新加载缓存。

        Args:
            mcp_servers: 形如 ``{server_name: config_dict, ...}`` 的字典。
                每个值应包含 ``enabled``、``type``、``command``、``args``、
                ``env``、``url`` 等键。

        Returns:
            形如 ``{"mcp_servers": ...}`` 的字典，结构与 Gateway API 的
            ``McpConfigResponse`` schema 对齐。

        Raises:
            OSError: 当配置文件无法写入时。
        """
        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set DEER_FLOW_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        current_config = get_extensions_config()

        config_data = {
            "mcpServers": mcp_servers,
            "skills": {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        self._agent_config_key = None
        reloaded = reload_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in reloaded.mcp_servers.items()}}

    # ------------------------------------------------------------------
    # Public API — skills management
    # ------------------------------------------------------------------

    def get_skill(self, name: str) -> dict | None:
        """按名称获取某个技能。

        Args:
            name: 技能名称。

        Returns:
            技能信息字典；未找到时返回 ``None``。
        """
        from deerflow.skills.storage import get_or_new_skill_storage

        skill = next((s for s in get_or_new_skill_storage().load_skills(enabled_only=False) if s.name == name), None)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "license": skill.license,
            "category": skill.category,
            "enabled": skill.enabled,
        }

    def update_skill(self, name: str, *, enabled: bool) -> dict:
        """更新某个技能的启用状态。

        Args:
            name: 技能名称。
            enabled: 新的启用状态。

        Returns:
            更新后的技能信息字典。

        Raises:
            ValueError: 当技能未找到时。
            OSError: 当配置文件无法写入时。
        """
        from deerflow.skills.storage import get_or_new_skill_storage

        skills = get_or_new_skill_storage().load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            raise ValueError(f"Skill '{name}' not found")

        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set DEER_FLOW_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        extensions_config = get_extensions_config()
        extensions_config.skills[name] = SkillStateConfig(enabled=enabled)

        config_data = {
            "mcpServers": {n: s.model_dump() for n, s in extensions_config.mcp_servers.items()},
            "skills": {n: {"enabled": sc.enabled} for n, sc in extensions_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        self._agent_config_key = None
        reload_extensions_config()

        updated = next((s for s in get_or_new_skill_storage().load_skills(enabled_only=False) if s.name == name), None)
        if updated is None:
            raise RuntimeError(f"Skill '{name}' disappeared after update")
        return {
            "name": updated.name,
            "description": updated.description,
            "license": updated.license,
            "category": updated.category,
            "enabled": updated.enabled,
        }

    def install_skill(self, skill_path: str | Path) -> dict:
        """从一个 ``.skill``（ZIP）归档中安装技能。

        Args:
            skill_path: ``.skill`` 文件路径。

        Returns:
            包含 ``success``、``skill_name``、``message`` 的字典。

        Raises:
            FileNotFoundError: 当文件不存在时。
            ValueError: 当文件无效时。
        """
        return get_or_new_skill_storage().install_skill_from_archive(skill_path)

    # ------------------------------------------------------------------
    # Public API — memory management
    # ------------------------------------------------------------------

    def reload_memory(self) -> dict:
        """从文件重新加载记忆数据，强制使缓存失效。

        Returns:
            重新加载后的记忆数据字典。
        """
        from deerflow.agents.memory.updater import reload_memory_data

        return reload_memory_data(user_id=get_effective_user_id())

    def clear_memory(self) -> dict:
        """清空所有已持久化的记忆数据。"""
        from deerflow.agents.memory.updater import clear_memory_data

        return clear_memory_data(user_id=get_effective_user_id())

    def create_memory_fact(self, content: str, category: str = "context", confidence: float = 0.5) -> dict:
        """手动创建一条事实记录。"""
        from deerflow.agents.memory.updater import create_memory_fact

        return create_memory_fact(content=content, category=category, confidence=confidence)

    def delete_memory_fact(self, fact_id: str) -> dict:
        """按 fact_id 删除记忆中的一条事实。"""
        from deerflow.agents.memory.updater import delete_memory_fact

        return delete_memory_fact(fact_id)

    def update_memory_fact(
        self,
        fact_id: str,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
    ) -> dict:
        """手动更新一条事实，未传值的字段保持原样。"""
        from deerflow.agents.memory.updater import update_memory_fact

        return update_memory_fact(
            fact_id=fact_id,
            content=content,
            category=category,
            confidence=confidence,
        )

    def get_memory_config(self) -> dict:
        """获取记忆系统配置。

        Returns:
            记忆配置字典。
        """
        from deerflow.config.memory_config import get_memory_config

        config = get_memory_config()
        return {
            "enabled": config.enabled,
            "storage_path": config.storage_path,
            "debounce_seconds": config.debounce_seconds,
            "max_facts": config.max_facts,
            "fact_confidence_threshold": config.fact_confidence_threshold,
            "injection_enabled": config.injection_enabled,
            "max_injection_tokens": config.max_injection_tokens,
        }

    def get_memory_status(self) -> dict:
        """获取记忆状态：配置 + 当前数据。

        Returns:
            包含 ``config`` 与 ``data`` 键的字典。
        """
        return {
            "config": self.get_memory_config(),
            "data": self.get_memory(),
        }

    # ------------------------------------------------------------------
    # Public API — file uploads
    # ------------------------------------------------------------------

    def upload_files(self, thread_id: str, files: list[str | Path]) -> dict:
        """把本地文件上传到指定 thread 的 uploads 目录。

        对 PDF、PPT、Excel、Word 文件还会额外生成 Markdown 副本。

        Args:
            thread_id: 目标 thread ID。
            files: 待上传的本地文件路径列表。

        Returns:
            包含 ``success``、``files``、``message`` 的字典，结构与
            Gateway API 的 ``UploadResponse`` schema 对齐。

        Raises:
            FileNotFoundError: 当任一文件不存在时。
            ValueError: 当任一路径存在但不是常规文件时。
        """
        from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

        # Validate all files upfront to avoid partial uploads.
        resolved_files = []
        seen_names: set[str] = set()
        has_convertible_file = False
        for f in files:
            p = Path(f)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {f}")
            if not p.is_file():
                raise ValueError(f"Path is not a file: {f}")
            dest_name = claim_unique_filename(p.name, seen_names)
            resolved_files.append((p, dest_name))
            if not has_convertible_file and p.suffix.lower() in CONVERTIBLE_EXTENSIONS:
                has_convertible_file = True

        uploads_dir = ensure_uploads_dir(thread_id)
        uploaded_files: list[dict] = []

        conversion_pool = None
        if has_convertible_file:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                conversion_pool = None
            else:
                import concurrent.futures

                # Reuse one worker when already inside an event loop to avoid
                # creating a new ThreadPoolExecutor per converted file.
                conversion_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _convert_in_thread(path: Path):
            """在线程池中把单个文件转换为 Markdown。

            通过 ``asyncio.run`` 启动一个独立事件循环执行
            ``convert_file_to_markdown``，避免与外层事件循环冲突。

            Args:
                path: 待转换文件的绝对路径。

            Returns:
                ``convert_file_to_markdown`` 的返回结果。
            """
            return asyncio.run(convert_file_to_markdown(path))

        try:
            for src_path, dest_name in resolved_files:
                dest = uploads_dir / dest_name
                shutil.copy2(src_path, dest)

                info: dict[str, Any] = {
                    "filename": dest_name,
                    "size": dest.stat().st_size,
                    "path": str(dest),
                    "virtual_path": upload_virtual_path(dest_name),
                    "artifact_url": upload_artifact_url(thread_id, dest_name),
                }
                if dest_name != src_path.name:
                    info["original_filename"] = src_path.name

                if src_path.suffix.lower() in CONVERTIBLE_EXTENSIONS:
                    try:
                        if conversion_pool is not None:
                            md_path = conversion_pool.submit(_convert_in_thread, dest).result()
                        else:
                            md_path = asyncio.run(convert_file_to_markdown(dest))
                    except Exception:
                        logger.warning(
                            "Failed to convert %s to markdown",
                            src_path.name,
                            exc_info=True,
                        )
                        md_path = None

                    if md_path is not None:
                        info["markdown_file"] = md_path.name
                        info["markdown_path"] = str(uploads_dir / md_path.name)
                        info["markdown_virtual_path"] = upload_virtual_path(md_path.name)
                        info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_path.name)

                uploaded_files.append(info)
        finally:
            if conversion_pool is not None:
                conversion_pool.shutdown(wait=True)

        return {
            "success": True,
            "files": uploaded_files,
            "message": f"Successfully uploaded {len(uploaded_files)} file(s)",
        }

    def list_uploads(self, thread_id: str) -> dict:
        """列出指定 thread uploads 目录中的文件。

        Args:
            thread_id: 目标 thread ID。

        Returns:
            包含 ``files`` 与 ``count`` 键的字典，结构与 Gateway API 的
            ``list_uploaded_files`` 响应对齐。
        """
        uploads_dir = get_uploads_dir(thread_id)
        result = list_files_in_dir(uploads_dir)
        return enrich_file_listing(result, thread_id)

    def delete_upload(self, thread_id: str, filename: str) -> dict:
        """从指定 thread 的 uploads 目录中删除一个文件。

        Args:
            thread_id: 目标 thread ID。
            filename: 待删除的文件名。

        Returns:
            包含 ``success`` 与 ``message`` 的字典，结构与 Gateway API 的
            ``delete_uploaded_file`` 响应对齐。

        Raises:
            FileNotFoundError: 当文件不存在时。
            PermissionError: 当检测到路径穿越时。
        """
        from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS

        uploads_dir = get_uploads_dir(thread_id)
        return delete_file_safe(uploads_dir, filename, convertible_extensions=CONVERTIBLE_EXTENSIONS)

    # ------------------------------------------------------------------
    # Public API — artifacts
    # ------------------------------------------------------------------

    def get_artifact(self, thread_id: str, path: str) -> tuple[bytes, str]:
        """读取 agent 生成的 artifact 文件。

        Args:
            thread_id: 目标 thread ID。
            path: 虚拟路径（如 ``"mnt/user-data/outputs/file.txt"``）。

        Returns:
            ``(文件字节内容, MIME 类型)`` 元组。

        Raises:
            FileNotFoundError: 当 artifact 不存在时。
            ValueError: 当路径无效时。
        """
        try:
            actual = get_paths().resolve_virtual_path(thread_id, path, user_id=get_effective_user_id())
        except ValueError as exc:
            if "traversal" in str(exc):
                from deerflow.uploads.manager import PathTraversalError

                raise PathTraversalError("Path traversal detected") from exc
            raise
        if not actual.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        if not actual.is_file():
            raise ValueError(f"Path is not a file: {path}")

        mime_type, _ = mimetypes.guess_type(actual)
        return actual.read_bytes(), mime_type or "application/octet-stream"
