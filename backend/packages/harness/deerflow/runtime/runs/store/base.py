"""Run 元数据持久化抽象接口。

``RunManager`` 依赖此接口进行 Run 元数据的读写。当前已实现：
- :class:`MemoryRunStore`：纯内存 dict（开发与测试场景）。
- 未来扩展：基于 SQLAlchemy ORM 的 ``RunRepository``（数据库后端）。

所有方法接受可选的 ``user_id``，用于多租户隔离。当 ``user_id`` 为
``None`` 时不应用用户过滤（单用户模式）。
"""

from __future__ import annotations

import abc
from typing import Any


class RunStore(abc.ABC):
    """Run 元数据存储的抽象接口。"""

    @abc.abstractmethod
    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        assistant_id: str | None = None,
        user_id: str | None = None,
        model_name: str | None = None,
        status: str = "pending",
        multitask_strategy: str = "reject",
        metadata: dict[str, Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        error: str | None = None,
        created_at: str | None = None,
    ) -> None:
        """插入或替换一个 Run 记录。

        Args:
            run_id: Run 唯一标识。
            thread_id: 所属线程 ID。
            assistant_id: 助手 ID（可空）。
            user_id: 用户 ID（可空）。
            model_name: 实际使用的模型名称（可空）。
            status: 初始状态，默认 ``"pending"``。
            multitask_strategy: 多任务策略，默认 ``"reject"``。
            metadata: 元数据字典。
            kwargs: Run 调用参数。
            error: 错误信息（可空）。
            created_at: 创建时间 ISO 字符串（可空）。
        """

    @abc.abstractmethod
    async def get(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """按 run_id 读取单条记录。

        Args:
            run_id: Run 唯一标识。
            user_id: 可选用户 ID，用于过滤；不匹配则返回 ``None``。

        Returns:
            找到时返回 dict 格式的记录，未找到返回 ``None``。
        """

    @abc.abstractmethod
    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出指定线程下的 Run 记录（按 ``created_at`` 降序，最多 ``limit`` 条）。

        Args:
            thread_id: 线程 ID。
            user_id: 可选用户 ID 过滤。
            limit: 返回的最大记录数。
        """

    @abc.abstractmethod
    async def update_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> bool | None:
        """更新一个 Run 的状态。

        Args:
            run_id: Run 唯一标识。
            status: 目标状态字符串。
            error: 可选错误信息。

        Returns:
            存储可以证明未更新任何行时返回 ``False``；轻量级实现无法
            报告受影响行数时可返回 ``None``。
        """

    @abc.abstractmethod
    async def delete(self, run_id: str) -> None:
        """删除指定 run_id 的记录。"""

    @abc.abstractmethod
    async def update_model_name(
        self,
        run_id: str,
        model_name: str | None,
    ) -> None:
        """更新一条 Run 记录的 ``model_name`` 字段。"""

    @abc.abstractmethod
    async def update_run_completion(
        self,
        run_id: str,
        *,
        status: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        lead_agent_tokens: int = 0,
        subagent_tokens: int = 0,
        middleware_tokens: int = 0,
        message_count: int = 0,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
        error: str | None = None,
    ) -> bool | None:
        """持久化 Run 完成的最终字段（状态、token 累计、首尾消息等）。

        Returns:
            存储可以证明未更新任何行时返回 ``False``。
        """

    async def update_run_progress(
        self,
        run_id: str,
        *,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_tokens: int | None = None,
        llm_call_count: int | None = None,
        lead_agent_tokens: int | None = None,
        subagent_tokens: int | None = None,
        middleware_tokens: int | None = None,
        message_count: int | None = None,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
    ) -> None:
        """持久化一个尽力而为的运行中快照，不改变 Run 状态。"""
        return None

    @abc.abstractmethod
    async def list_pending(self, *, before: str | None = None) -> list[dict[str, Any]]:
        """列出处于 ``pending`` 状态且 ``created_at <= before`` 的持久化记录。"""

    @abc.abstractmethod
    async def list_inflight(self, *, before: str | None = None) -> list[dict[str, Any]]:
        """列出仍处于 ``pending`` 或 ``running`` 状态的持久化记录。"""

    @abc.abstractmethod
    async def aggregate_tokens_by_thread(self, thread_id: str, *, include_active: bool = False) -> dict[str, Any]:
        """聚合某个线程下已完成 Run 的 token 用量。

        Args:
            thread_id: 线程 ID。
            include_active: 是否将 ``running`` 状态也纳入聚合（默认只算
                ``success``/``error``）。

        Returns:
            包含 ``total_tokens``、``total_input_tokens``、
            ``total_output_tokens``、``total_runs``、``by_model``
            （model_name → {tokens, runs}）、``by_caller``
            （{lead_agent, subagent, middleware}）字段的字典。
        """
