"""基于 SQLAlchemy 的 :class:`RunStore` 实现。

每个方法各自获取并释放短生命周期的 session。Run 状态更新可能由
存活数分钟的后台 worker 触发——我们不在长时间执行期间持有连接。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.run.model import RunRow
from deerflow.runtime.runs.store.base import RunStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso


class RunRepository(RunStore):
    """runs 表的 SQLAlchemy repository。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """构造 repository。

        Args:
            session_factory: 异步 session 工厂。
        """
        self._sf = session_factory

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """归一化 model_name：去空白并截断到 128 字符。"""
        if model_name is None:
            return None
        if not isinstance(model_name, str):
            model_name = str(model_name)
        normalized = model_name.strip()
        if len(normalized) > 128:
            normalized = normalized[:128]
        return normalized

    @staticmethod
    def _safe_json(obj: Any) -> Any:
        """确保对象可被 JSON 序列化；失败时回退到 ``model_dump()`` 或 ``str()``。

        递归处理 dict/list/tuple 等容器类型。对于 Pydantic 模型优先尝试
        ``model_dump()``，其次 ``dict()``。最终回退到 ``str()`` 以保证
        持久化写入不会因序列化失败而中断整个 run 生命周期。
        """
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: RunRepository._safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [RunRepository._safe_json(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return obj.dict()
            except Exception:
                pass
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    @staticmethod
    def _row_to_dict(row: RunRow) -> dict[str, Any]:
        """把 :class:`RunRow` 序列化为与 :class:`RunStore` 接口一致的 dict。"""
        d = row.to_dict()
        # 重命名 JSON 列以匹配 RunStore 接口
        d["metadata"] = d.pop("metadata_json", {})
        d["kwargs"] = d.pop("kwargs_json", {})
        # 将 datetime 转为 ISO 字符串，与 MemoryRunStore 保持一致。
        # SQLite 读出时尽管声明了 ``DateTime(timezone=True)`` 仍会丢 tzinfo——
        # ``coerce_iso`` 将 naive datetime 视为 UTC。
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = coerce_iso(val)
        return d

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id: str | None | _AutoSentinel = AUTO,
        model_name: str | None = None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
        follow_up_to_run_id=None,
    ):
        """插入或更新一行 run。

        ``RunManager`` 会在瞬时 SQLite 故障后重试 ``put``。把该操作
        实现为幂等，可以避免在「首次 commit 成功但未确认」时把重试
        演变成主键冲突。
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.put")
        now = datetime.now(UTC)
        created = datetime.fromisoformat(created_at) if created_at else now
        values = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "model_name": self._normalize_model_name(model_name),
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata_json": self._safe_json(metadata) or {},
            "kwargs_json": self._safe_json(kwargs) or {},
            "error": error,
            "follow_up_to_run_id": follow_up_to_run_id,
            "updated_at": now,
        }
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                session.add(RunRow(run_id=run_id, created_at=created, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await session.commit()

    async def get(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        """按 ID 获取 run；非所属用户返回 ``None``。"""
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.get")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return None
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def list_by_thread(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
        limit=100,
    ):
        """列出指定 thread 下的 run，按创建时间倒序。"""
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.list_by_thread")
        stmt = select(RunRow).where(RunRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(RunRow.user_id == resolved_user_id)
        stmt = stmt.order_by(RunRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def update_status(self, run_id, status, *, error=None) -> bool:
        """更新 run 状态；存在记录时返回 ``True``。"""
        values: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if error is not None:
            values["error"] = error
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()
            return result.rowcount != 0

    async def update_model_name(self, run_id, model_name):
        """更新 run 的 model_name（已归一化）。"""
        async with self._sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(model_name=self._normalize_model_name(model_name), updated_at=datetime.now(UTC)))
            await session.commit()

    async def delete(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        """按 ID 删除 run；非所属用户不删除。"""
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.delete")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()

    async def list_pending(self, *, before=None):
        """列出 ``status='pending'`` 且 ``created_at <= before`` 的 run。"""
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = select(RunRow).where(RunRow.status == "pending", RunRow.created_at <= before_dt).order_by(RunRow.created_at.asc())
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def list_inflight(self, *, before=None):
        """返回已持久化的活跃 run，供启动恢复使用。"""
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = (
            select(RunRow)
            .where(
                RunRow.status.in_(("pending", "running")),
                RunRow.created_at <= before_dt,
            )
            .order_by(RunRow.created_at.asc())
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

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
    ) -> bool:
        """在 run 完成时更新状态、token 用量与便利字段。

        Args:
            run_id: 目标 run ID。
            status: 新状态。
            total_input_tokens: 累计输入 token。
            total_output_tokens: 累计输出 token。
            total_tokens: 累计总 token。
            llm_call_count: 累计 LLM 调用次数。
            lead_agent_tokens / subagent_tokens / middleware_tokens: 按 caller 拆分的 token。
            message_count: 消息计数。
            last_ai_message / first_human_message: 末尾/首条消息预览（会被截断到 2000 字符）。
            error: 错误信息（如有）。

        Returns:
            bool: 没有匹配的 ``run_id`` 时返回 ``False``。
        """
        values: dict[str, Any] = {
            "status": status,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "lead_agent_tokens": lead_agent_tokens,
            "subagent_tokens": subagent_tokens,
            "middleware_tokens": middleware_tokens,
            "message_count": message_count,
            "updated_at": datetime.now(UTC),
        }
        if last_ai_message is not None:
            values["last_ai_message"] = last_ai_message[:2000]
        if first_human_message is not None:
            values["first_human_message"] = first_human_message[:2000]
        if error is not None:
            values["error"] = error
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()
            return result.rowcount != 0

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
        """在 run 仍处于运行中时，更新 token 用量与便利字段。

        只覆盖显式传入（非 ``None``）的字段；其余保持原值。仅在
        ``status == "running"`` 时才会落库，避免覆盖已经完结的 run。
        """
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        optional_counters = {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": llm_call_count,
            "lead_agent_tokens": lead_agent_tokens,
            "subagent_tokens": subagent_tokens,
            "middleware_tokens": middleware_tokens,
            "message_count": message_count,
        }
        for key, value in optional_counters.items():
            if value is not None:
                values[key] = value
        if last_ai_message is not None:
            values["last_ai_message"] = last_ai_message[:2000]
        if first_human_message is not None:
            values["first_human_message"] = first_human_message[:2000]
        async with self._sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id, RunRow.status == "running").values(**values))
            await session.commit()

    async def aggregate_tokens_by_thread(self, thread_id: str, *, include_active: bool = False) -> dict[str, Any]:
        """用单条 SQL ``GROUP BY`` 聚合 thread 下的 token 用量。

        Args:
            thread_id: 目标 thread ID。
            include_active: 是否包含 ``running`` 状态的 run。

        Returns:
            dict: 含总量、按 model 分组、按 caller 分组的统计结果。
        """
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        _completed = RunRow.status.in_(statuses)
        _thread = RunRow.thread_id == thread_id
        model_name = func.coalesce(RunRow.model_name, "unknown")

        stmt = (
            select(
                model_name.label("model"),
                func.count().label("runs"),
                func.coalesce(func.sum(RunRow.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(RunRow.total_input_tokens), 0).label("total_input_tokens"),
                func.coalesce(func.sum(RunRow.total_output_tokens), 0).label("total_output_tokens"),
                func.coalesce(func.sum(RunRow.lead_agent_tokens), 0).label("lead_agent"),
                func.coalesce(func.sum(RunRow.subagent_tokens), 0).label("subagent"),
                func.coalesce(func.sum(RunRow.middleware_tokens), 0).label("middleware"),
            )
            .where(_thread, _completed)
            .group_by(model_name)
        )

        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()

        total_tokens = total_input = total_output = total_runs = 0
        lead_agent = subagent = middleware = 0
        by_model: dict[str, dict] = {}
        for r in rows:
            by_model[r.model] = {"tokens": r.total_tokens, "runs": r.runs}
            total_tokens += r.total_tokens
            total_input += r.total_input_tokens
            total_output += r.total_output_tokens
            total_runs += r.runs
            lead_agent += r.lead_agent
            subagent += r.subagent
            middleware += r.middleware

        return {
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_runs": total_runs,
            "by_model": by_model,
            "by_caller": {
                "lead_agent": lead_agent,
                "subagent": subagent,
                "middleware": middleware,
            },
        }
