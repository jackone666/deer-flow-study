"""内存版 Run 注册表，可选挂载持久化 RunStore 作为后端。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deerflow.utils.time import now_iso as _now_iso

from .schemas import DisconnectMode, RunStatus

if TYPE_CHECKING:
    from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

_RETRYABLE_SQLITE_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)

_RETRYABLE_SQLITE_ERROR_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
}


def _is_retryable_persistence_error(exc: BaseException) -> bool:
    """判断异常是否属于瞬时 SQLite 持久化失败。

    SQLite 锁竞争通常通过 ``sqlite3`` 异常或 SQLAlchemy 包装器抛上来。
    这里的短有限重试保护 Run 状态终结过程不会被瞬时写入压力击垮，
    同时不会永久掩盖真正的故障。

    Args:
        exc: 待检查的异常。

    Returns:
        属于可重试错误时返回 ``True``，否则 ``False``。
    """

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        message = str(current).lower()
        if any(fragment in message for fragment in _RETRYABLE_SQLITE_MESSAGES):
            return True
        if isinstance(current, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in _RETRYABLE_SQLITE_ERROR_CODES:
                return True
        for chained in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)
    return False


@dataclass(frozen=True)
class PersistenceRetryPolicy:
    """短写入重试策略。

    Attributes:
        max_attempts: 最大尝试次数（含首次）。
        initial_delay: 首次重试的初始延迟（秒）。
        max_delay: 单次重试的最大延迟（秒）。
        backoff_factor: 退避倍数。
    """

    max_attempts: int = 5
    initial_delay: float = 0.05
    max_delay: float = 1.0
    backoff_factor: float = 2.0


@dataclass
class RunRecord:
    """单个 Run 的可变运行期记录。

    Attributes:
        run_id: Run 唯一标识。
        thread_id: 所属线程 ID。
        assistant_id: 助手 ID（可空）。
        status: 当前 :class:`RunStatus`。
        on_disconnect: SSE 断开时的处理策略。
        multitask_strategy: 多任务策略。
        metadata: 元数据字典。
        kwargs: Run 调用参数。
        created_at: 创建时间 ISO 字符串。
        updated_at: 最后更新时间 ISO 字符串。
        task: 关联的后台 asyncio 任务。
        abort_event: 取消信号，触发后任务应尽快退出。
        abort_action: 取消行为，``"interrupt"`` 或 ``"rollback"``。
        error: 错误信息。
        model_name: 实际使用的模型名称。
        store_only: 标识该记录是从持久化存储水合出来的只读记录。
        total_input_tokens: 累计输入 token。
        total_output_tokens: 累计输出 token。
        total_tokens: 累计总 token。
        llm_call_count: LLM 调用次数。
        lead_agent_tokens: 主代理产生的 token。
        subagent_tokens: 子代理产生的 token。
        middleware_tokens: 中间件产生的 token。
        message_count: 消息数。
        last_ai_message: 最后一条 AI 消息文本（截断到 2000 字符）。
        first_human_message: 首条人类消息文本（截断到 2000 字符）。
    """

    run_id: str
    thread_id: str
    assistant_id: str | None
    status: RunStatus
    on_disconnect: DisconnectMode
    multitask_strategy: str = "reject"
    metadata: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    abort_action: str = "interrupt"
    error: str | None = None
    model_name: str | None = None
    store_only: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    message_count: int = 0
    last_ai_message: str | None = None
    first_human_message: str | None = None


class RunManager:
    """Run 内存注册表，可选挂载持久化 :class:`RunStore` 作为后端。

    所有写操作均在 ``asyncio.Lock`` 保护下进行。当提供 ``store`` 时，
    可序列化的元数据会同步持久化，使 Run 历史在进程重启后仍能恢复。
    """

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        persistence_retry_policy: PersistenceRetryPolicy | None = None,
    ) -> None:
        """构造 RunManager。

        Args:
            store: 可选的持久化后端。
            persistence_retry_policy: 可选的重试策略；不传使用默认配置。
        """
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()
        self._store = store
        self._persistence_retry_policy = persistence_retry_policy or PersistenceRetryPolicy()

    @staticmethod
    def _store_put_payload(record: RunRecord, *, error: str | None = None) -> dict[str, Any]:
        """构造写入 store 的可序列化快照。"""
        return {
            "thread_id": record.thread_id,
            "assistant_id": record.assistant_id,
            "status": record.status.value,
            "multitask_strategy": record.multitask_strategy,
            "metadata": record.metadata or {},
            "kwargs": record.kwargs or {},
            "error": error if error is not None else record.error,
            "created_at": record.created_at,
            "model_name": record.model_name,
        }

    async def _call_store_with_retry(
        self,
        operation_name: str,
        run_id: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """执行短 store 操作，遇到 SQLite 压力时按策略有限重试。"""
        policy = self._persistence_retry_policy
        attempt = 1
        delay = policy.initial_delay
        while True:
            try:
                return await operation()
            except Exception as exc:
                retryable = _is_retryable_persistence_error(exc)
                if attempt >= policy.max_attempts or not retryable:
                    raise
                logger.warning(
                    "Transient persistence failure during %s for run %s (attempt %d/%d); retrying",
                    operation_name,
                    run_id,
                    attempt,
                    policy.max_attempts,
                    exc_info=True,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                delay = min(policy.max_delay, delay * policy.backoff_factor if delay else policy.initial_delay)
                attempt += 1

    async def _persist_snapshot_to_store(self, run_id: str, payload: dict[str, Any]) -> bool:
        """尽力持久化一个已捕获的 Run 快照。"""
        if self._store is None:
            return True
        try:
            await self._call_store_with_retry(
                "put",
                run_id,
                lambda: self._store.put(run_id, **payload),
            )
            return True
        except Exception:
            logger.warning("Failed to persist run %s to store", run_id, exc_info=True)
            return False

    async def _persist_new_run_to_store(self, record: RunRecord) -> None:
        """持久化一个新建的 Run 记录。

        Run 创建属于可见性边界的一部分：调用方不应在 store 中不存在
        对应行时就在内存中观察到该 Run。与后续的状态/模型更新不同，
        这里的失败会向上抛出，调用方据此判定创建失败；如需回滚，
        由调用方在将记录插入 ``_runs`` 之后自行处理。
        """
        if self._store is None:
            return
        await self._call_store_with_retry(
            "put",
            record.run_id,
            lambda: self._store.put(record.run_id, **self._store_put_payload(record)),
        )

    async def _persist_to_store(self, record: RunRecord, *, error: str | None = None) -> bool:
        """尽力把 Run 记录持久化到后端。"""
        return await self._persist_snapshot_to_store(
            record.run_id,
            self._store_put_payload(record, error=error),
        )

    async def _persist_status(self, record: RunRecord, status: RunStatus, *, error: str | None = None) -> bool:
        """尽力把状态变更持久化到后端。"""
        if self._store is None:
            return True
        row_recovery_payload = self._store_put_payload(record, error=error)
        try:
            updated = await self._call_store_with_retry(
                "update_status",
                record.run_id,
                lambda: self._store.update_status(record.run_id, status.value, error=error),
            )
            if updated is False:
                return await self._persist_snapshot_to_store(record.run_id, row_recovery_payload)
            return True
        except Exception:
            logger.warning("Failed to persist status update for run %s", record.run_id, exc_info=True)
            return False

    @staticmethod
    def _record_from_store(row: dict[str, Any]) -> RunRecord:
        """从序列化 store 行构造一个只读运行时记录。

        状态/断连模式列若为 ``NULL``（例如在加列之前写入的历史行），
        默认填充为 ``pending`` 和 ``cancel``。
        """
        return RunRecord(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            assistant_id=row.get("assistant_id"),
            status=RunStatus(row.get("status") or RunStatus.pending.value),
            on_disconnect=DisconnectMode(row.get("on_disconnect") or DisconnectMode.cancel.value),
            multitask_strategy=row.get("multitask_strategy") or "reject",
            metadata=row.get("metadata") or {},
            kwargs=row.get("kwargs") or {},
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
            error=row.get("error"),
            model_name=row.get("model_name"),
            store_only=True,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            llm_call_count=row.get("llm_call_count") or 0,
            lead_agent_tokens=row.get("lead_agent_tokens") or 0,
            subagent_tokens=row.get("subagent_tokens") or 0,
            middleware_tokens=row.get("middleware_tokens") or 0,
            message_count=row.get("message_count") or 0,
            last_ai_message=row.get("last_ai_message"),
            first_human_message=row.get("first_human_message"),
        )

    async def update_run_completion(self, run_id: str, **kwargs) -> None:
        """把 token 用量与完成数据持久化到后端。

        Args:
            run_id: 目标 Run ID。
            **kwargs: 透传给 store 的字段（包含 ``status``、各类 token 字段等）。
        """
        row_recovery_payload: dict[str, Any] | None = None
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                for key, value in kwargs.items():
                    if key == "status":
                        continue
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
                row_recovery_payload = self._store_put_payload(record, error=kwargs.get("error"))
        if self._store is None:
            return
        try:
            updated = await self._call_store_with_retry(
                "update_run_completion",
                run_id,
                lambda: self._store.update_run_completion(run_id, **kwargs),
            )
            if updated is False:
                if row_recovery_payload is None:
                    logger.warning("Failed to recreate missing run %s for completion persistence", run_id)
                    return
                if not await self._persist_snapshot_to_store(run_id, row_recovery_payload):
                    return
                recovered = await self._call_store_with_retry(
                    "update_run_completion",
                    run_id,
                    lambda: self._store.update_run_completion(run_id, **kwargs),
                )
                if recovered is False:
                    logger.warning("Run completion update for %s affected no rows after row recreation", run_id)
        except Exception:
            logger.warning("Failed to persist run completion for %s", run_id, exc_info=True)

    async def update_run_progress(self, run_id: str, **kwargs) -> None:
        """写一份运行中 token/消息快照，不改变 Run 状态。

        Args:
            run_id: 目标 Run ID。
            **kwargs: 透传给 store 的字段。
        """
        should_persist = True
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                should_persist = record.status == RunStatus.running
            if record is not None and should_persist:
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
        if should_persist and self._store is not None:
            try:
                await self._store.update_run_progress(run_id, **kwargs)
            except Exception:
                logger.warning("Failed to persist run progress for %s", run_id, exc_info=True)

    async def create(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
    ) -> RunRecord:
        """创建并注册一个新的 pending Run。

        Args:
            thread_id: 所属线程 ID。
            assistant_id: 助手 ID。
            on_disconnect: SSE 断开时的处理策略。
            metadata: 元数据字典。
            kwargs: Run 调用参数。
            multitask_strategy: 多任务策略。

        Returns:
            新建的 :class:`RunRecord`。

        Raises:
            Exception: 当持久化后端失败时向上抛出；调用方负责回滚。
        """
        run_id = str(uuid.uuid4())
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._runs[run_id] = record
            persisted = False
            try:
                await self._persist_new_run_to_store(record)
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                raise
            finally:
                # Also covers cancellation, which bypasses ``except Exception``.
                if not persisted:
                    self._runs.pop(run_id, None)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def get(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """按 ID 查找 Run 记录，未命中时返回 ``None``。

        优先返回内存中的活动记录；缺失时回退到持久化后端并水合一个
        ``store_only`` 记录。

        Args:
            run_id: 要查找的 Run ID。
            user_id: 从 store 水合时的可选用户过滤。

        Returns:
            找到时返回 :class:`RunRecord`，否则 ``None``。
        """
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if self._store is None:
            return None
        try:
            row = await self._store.get(run_id, user_id=user_id)
        except Exception:
            logger.warning("Failed to hydrate run %s from store", run_id, exc_info=True)
            return None
        # Re-check after store await: a concurrent create() may have inserted the
        # in-memory record while the store call was in flight.
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if row is None:
            return None
        try:
            return self._record_from_store(row)
        except Exception:
            logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
            return None

    async def aget(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """按 ID 查找 Run 记录，必要时回退到持久化存储。``get`` 的别名。"""
        return await self.get(run_id, user_id=user_id)

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """返回线程下的 Run 记录（按 ``created_at`` 降序），最多 ``limit`` 条。

        内存与 store 的合并策略：当同一 ``run_id`` 同时存在于两侧时，
        内存版本优先；然后整体按 ``created_at`` 降序排序并截断到 ``limit``。

        Args:
            thread_id: 线程 ID。
            user_id: 从 store 水合时的可选用户过滤。
            limit: 返回的最大记录数。
        """
        async with self._lock:
            # Dict insertion order gives deterministic results when timestamps tie.
            memory_records = [r for r in self._runs.values() if r.thread_id == thread_id]
        if self._store is None:
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        records_by_id = {record.run_id: record for record in memory_records}
        store_limit = max(0, limit - len(memory_records))
        try:
            rows = await self._store.list_by_thread(thread_id, user_id=user_id, limit=store_limit)
        except Exception:
            logger.warning("Failed to hydrate runs for thread %s from store", thread_id, exc_info=True)
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        for row in rows:
            run_id = row.get("run_id")
            if run_id and run_id not in records_by_id:
                try:
                    records_by_id[run_id] = self._record_from_store(row)
                except Exception:
                    logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
        return sorted(records_by_id.values(), key=lambda record: record.created_at, reverse=True)[:limit]

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        """将一个 Run 转移到新状态。

        Args:
            run_id: 目标 Run ID。
            status: 目标 :class:`RunStatus`。
            error: 可选错误信息。
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            record.status = status
            record.updated_at = _now_iso()
            if error is not None:
                record.error = error
        await self._persist_status(record, status, error=error)
        logger.info("Run %s -> %s", run_id, status.value)

    async def _persist_model_name(self, run_id: str, model_name: str | None) -> None:
        """尽力把 ``model_name`` 写回后端。"""
        if self._store is None:
            return
        try:
            await self._call_store_with_retry(
                "update_model_name",
                run_id,
                lambda: self._store.update_model_name(run_id, model_name),
            )
        except Exception:
            logger.warning("Failed to persist model_name update for run %s", run_id, exc_info=True)

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        """更新一个 Run 的模型名称。

        Args:
            run_id: 目标 Run ID。
            model_name: 实际使用的模型名称。
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("update_model_name called for unknown run %s", run_id)
                return
            record.model_name = model_name
            record.updated_at = _now_iso()
        await self._persist_model_name(run_id, model_name)
        logger.info("Run %s model_name=%s", run_id, model_name)

    async def cancel(self, run_id: str, *, action: str = "interrupt") -> bool:
        """请求取消一个 Run。

        设置 abort 事件并取消关联 asyncio 任务。当 Run 已经被本 worker
        中断时再次调用是幂等的（返回 ``True``）。仅当 Run 在本 worker
        不可见、或已处于除 ``interrupted`` 之外的终态时返回 ``False``。

        Args:
            run_id: 目标 Run ID。
            action: ``"interrupt"`` 保留检查点，``"rollback"`` 回滚到
                运行前状态。
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            if record.status == RunStatus.interrupted:
                return True  # idempotent — already cancelled on this worker
            if record.status not in (RunStatus.pending, RunStatus.running):
                return False
            record.abort_action = action
            record.abort_event.set()
            if record.task is not None and not record.task.done():
                record.task.cancel()
            record.status = RunStatus.interrupted
            record.updated_at = _now_iso()
        await self._persist_status(record, RunStatus.interrupted)
        logger.info("Run %s cancelled (action=%s)", run_id, action)
        return True

    async def create_or_reject(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
        model_name: str | None = None,
    ) -> RunRecord:
        """原子地检查活跃 Run 并创建一个新 Run。

        对于 ``reject`` 策略，线程已存在 pending/running 的 Run 时抛出
        :class:`ConflictError`；对于 ``interrupt``/``rollback``，会先
        取消已有 Run 再创建新 Run。锁覆盖整个检查+插入窗口，避免
        ``has_inflight`` + ``create`` 之间的 TOCTOU 竞态。

        Args:
            thread_id: 所属线程 ID。
            assistant_id: 助手 ID。
            on_disconnect: SSE 断开时的处理策略。
            metadata: 元数据字典。
            kwargs: Run 调用参数。
            multitask_strategy: 多任务策略。
            model_name: 模型名称（可空）。

        Returns:
            新建的 :class:`RunRecord`。

        Raises:
            ConflictError: ``reject`` 策略下线程已有活跃 Run。
            UnsupportedStrategyError: 策略尚未实现。
        """
        run_id = str(uuid.uuid4())
        now = _now_iso()

        _supported_strategies = ("reject", "interrupt", "rollback")
        interrupted_records: list[RunRecord] = []

        async with self._lock:
            if multitask_strategy not in _supported_strategies:
                raise UnsupportedStrategyError(f"Multitask strategy '{multitask_strategy}' is not yet supported. Supported strategies: {', '.join(_supported_strategies)}")

            inflight = [r for r in self._runs.values() if r.thread_id == thread_id and r.status in (RunStatus.pending, RunStatus.running)]

            if multitask_strategy == "reject" and inflight:
                raise ConflictError(f"Thread {thread_id} already has an active run")

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                logger.info(
                    "Preparing to cancel %d inflight run(s) on thread %s (strategy=%s)",
                    len(inflight),
                    thread_id,
                    multitask_strategy,
                )

            record = RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                assistant_id=assistant_id,
                status=RunStatus.pending,
                on_disconnect=on_disconnect,
                multitask_strategy=multitask_strategy,
                metadata=metadata or {},
                kwargs=kwargs or {},
                created_at=now,
                updated_at=now,
                model_name=model_name,
            )
            self._runs[run_id] = record
            persisted = False
            try:
                await self._persist_new_run_to_store(record)
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                raise
            finally:
                # Also covers cancellation, which bypasses ``except Exception``.
                if not persisted:
                    self._runs.pop(run_id, None)

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                for r in inflight:
                    r.abort_action = multitask_strategy
                    r.abort_event.set()
                    if r.task is not None and not r.task.done():
                        r.task.cancel()
                    r.status = RunStatus.interrupted
                    r.updated_at = now
                    interrupted_records.append(r)

        for interrupted_record in interrupted_records:
            await self._persist_status(interrupted_record, RunStatus.interrupted)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def reconcile_orphaned_inflight_runs(
        self,
        *,
        error: str,
        before: str | None = None,
    ) -> list[RunRecord]:
        """在启动时把没有本地任务接管的持久化活跃 Run 标记为失败。

        Gateway 的 Run 是进程局部的：asyncio 任务和 abort 事件只活在
        内存里，但 Run 行是持久化的。基于 SQLite 的 gateway 重启后，
        任何启动前就存在的 ``pending``/``running`` 行都不可能还有
        本地 worker。本方法把这种不确定状态显式化为 error，避免 UI
        一直显示"运行中"。

        Args:
            error: 写入失败状态的错误描述。
            before: 可选时间过滤（ISO 字符串），只处理 ``created_at <= before`` 的行。
        """
        if self._store is None:
            return []
        try:
            rows = await self._call_store_with_retry(
                "list_inflight",
                "*",
                lambda: self._store.list_inflight(before=before),
            )
        except Exception:
            logger.warning("Failed to list orphaned inflight runs for reconciliation", exc_info=True)
            return []

        recovered: list[RunRecord] = []
        now = _now_iso()
        for row in rows:
            try:
                record = self._record_from_store(row)
            except Exception:
                logger.warning("Failed to map orphaned run row during reconciliation", exc_info=True)
                continue

            async with self._lock:
                live_record = self._runs.get(record.run_id)
                if live_record is not None and live_record.status in (RunStatus.pending, RunStatus.running):
                    continue

            record.status = RunStatus.error
            record.error = error
            record.updated_at = now
            persisted = await self._persist_status(record, RunStatus.error, error=error)
            if not persisted:
                logger.warning("Skipped orphaned run %s recovery because error status was not persisted", record.run_id)
                continue
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d orphaned inflight run(s) as error", len(recovered))
        return recovered

    async def has_inflight(self, thread_id: str) -> bool:
        """判断指定线程是否仍存在 pending/running 的 Run。"""
        async with self._lock:
            return any(r.thread_id == thread_id and r.status in (RunStatus.pending, RunStatus.running) for r in self._runs.values())

    async def cleanup(self, run_id: str, *, delay: float = 300) -> None:
        """在可选延迟后从内存中删除一个 Run 记录。

        Args:
            run_id: 目标 Run ID。
            delay: 删除前等待的秒数，``0`` 表示立即删除。
        """
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            self._runs.pop(run_id, None)
        logger.debug("Run record %s cleaned up", run_id)


class ConflictError(Exception):
    """``multitask_strategy=reject`` 且线程已有活跃 Run 时抛出。"""


class UnsupportedStrategyError(Exception):
    """传入尚未实现的 ``multitask_strategy`` 值时抛出。"""
