"""内存版 RunStore。

用于 ``database.backend=memory``（默认）以及测试场景。行为等价于
``RunManager._runs`` 字典的原始实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from deerflow.runtime.runs.store.base import RunStore


class MemoryRunStore(RunStore):
    """基于 ``dict`` 的 RunStore 实现，所有数据只活在进程内。"""

    def __init__(self) -> None:
        """初始化 self。"""
        self._runs: dict[str, dict[str, Any]] = {}

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id=None,
        model_name=None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
    ):
        """插入或替换一条 Run 记录。

        Args:
            run_id: Run 唯一标识。
            thread_id: 所属线程 ID。
            assistant_id: 助手 ID。
            user_id: 用户 ID。
            model_name: 模型名称。
            status: 状态。
            multitask_strategy: 多任务策略。
            metadata: 元数据。
            kwargs: Run 调用参数。
            error: 错误信息。
            created_at: 创建时间 ISO 字符串。
        """
        now = datetime.now(UTC).isoformat()
        self._runs[run_id] = {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": user_id,
            "model_name": model_name,
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata": metadata or {},
            "kwargs": kwargs or {},
            "error": error,
            "created_at": created_at or now,
            "updated_at": now,
        }

    async def get(self, run_id, *, user_id=None):
        """读取单条记录，可选按 ``user_id`` 过滤。

        Args:
            run_id: Run 唯一标识。
            user_id: 用户 ID 过滤；不匹配返回 ``None``。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        if user_id is not None and run.get("user_id") != user_id:
            return None
        return run

    async def list_by_thread(self, thread_id, *, user_id=None, limit=100):
        """列出线程下的 Run 记录（按 ``created_at`` 降序，最多 ``limit`` 条）。"""
        results = [r for r in self._runs.values() if r["thread_id"] == thread_id and (user_id is None or r.get("user_id") == user_id)]
        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results[:limit]

    async def update_status(self, run_id, status, *, error=None):
        """更新指定 Run 的状态（同时刷新 ``updated_at``）。"""
        if run_id in self._runs:
            self._runs[run_id]["status"] = status
            if error is not None:
                self._runs[run_id]["error"] = error
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()
            return True
        return False

    async def update_model_name(self, run_id, model_name):
        """更新指定 Run 的 ``model_name`` 字段。"""
        if run_id in self._runs:
            self._runs[run_id]["model_name"] = model_name
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def delete(self, run_id):
        """删除指定 Run 记录。"""
        self._runs.pop(run_id, None)

    async def update_run_completion(self, run_id, *, status, **kwargs):
        """写入 Run 完成时的最终字段（跳过 ``None`` 值字段）。"""
        if run_id in self._runs:
            self._runs[run_id]["status"] = status
            for key, value in kwargs.items():
                if value is not None:
                    self._runs[run_id][key] = value
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()
            return True
        return False

    async def update_run_progress(self, run_id, **kwargs):
        """更新一个运行中（``status == "running"``）快照，不改变状态。"""
        if run_id in self._runs and self._runs[run_id].get("status") == "running":
            for key, value in kwargs.items():
                if value is not None:
                    self._runs[run_id][key] = value
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def list_pending(self, *, before=None):
        """列出 ``created_at <= before`` 的 ``pending`` Run。"""
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if r["status"] == "pending" and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def list_inflight(self, *, before=None):
        """列出 ``created_at <= before`` 的 ``pending``/``running`` Run。"""
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if r["status"] in ("pending", "running") and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def aggregate_tokens_by_thread(self, thread_id: str, *, include_active: bool = False) -> dict[str, Any]:
        """聚合线程下已完成（或可选包含活跃）Run 的 token 用量。

        Args:
            thread_id: 线程 ID。
            include_active: 是否将 ``running`` 状态纳入聚合。

        Returns:
            包含 ``total_tokens``、``total_input_tokens``、
            ``total_output_tokens``、``total_runs``、``by_model``、
            ``by_caller`` 字段的字典。
        """
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        completed = [r for r in self._runs.values() if r["thread_id"] == thread_id and r.get("status") in statuses]
        by_model: dict[str, dict] = {}
        for r in completed:
            model = r.get("model_name") or "unknown"
            entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
            entry["tokens"] += r.get("total_tokens", 0)
            entry["runs"] += 1
        return {
            "total_tokens": sum(r.get("total_tokens", 0) for r in completed),
            "total_input_tokens": sum(r.get("total_input_tokens", 0) for r in completed),
            "total_output_tokens": sum(r.get("total_output_tokens", 0) for r in completed),
            "total_runs": len(completed),
            "by_model": by_model,
            "by_caller": {
                "lead_agent": sum(r.get("lead_agent_tokens", 0) for r in completed),
                "subagent": sum(r.get("subagent_tokens", 0) for r in completed),
                "middleware": sum(r.get("middleware_tokens", 0) for r in completed),
            },
        }
