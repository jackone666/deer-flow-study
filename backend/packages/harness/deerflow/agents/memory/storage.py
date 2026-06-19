"""记忆存储提供者。

提供两个核心能力：
1. **抽象存储接口**：``MemoryStorage`` 定义 load/reload/save 契约，支持替换为数据库等后端
2. **文件存储实现**：``FileMemoryStorage`` 基于 JSON 文件 + mtime 缓存，支持 per-user/per-agent 隔离

路径解析逻辑（``_get_memory_file_path``）：

```
user_id=None, agent_name=None → {base_dir}/memory.json          （全局记忆）
user_id="u1",  agent_name=None → {base_dir}/users/u1/memory.json （per-user 记忆）
user_id=None,  agent_name="a1"→ {base_dir}/agents/a1/memory.json（per-agent 记忆，旧布局）
user_id="u1",  agent_name="a1"→ {base_dir}/users/u1/agents/a1/memory.json（per-user-agent）
```

原子写入：先写 ``.tmp`` 文件再 ``replace``，避免写入中途崩溃破坏现有数据。"""

import abc
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.config.agents_config import AGENT_NAME_PATTERN
from deerflow.config.memory_config import get_memory_config
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)


def utc_now_iso_z() -> str:
    """返回带 ``Z`` 后缀的 UTC ISO-8601 时间戳（与历史朴素 UTC 输出保持一致）。"""
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"


def create_empty_memory() -> dict[str, Any]:
    """创建一份空白的记忆结构。

    输出示例：
    ```python
    {
        "version": "1.0",
        "lastUpdated": "2026-06-08T10:30:00Z",
        "user": {
            "workContext":     {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind":       {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths":       {"summary": "", "updatedAt": ""},
            "earlierContext":     {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }
    ```

    Returns:
        初始化的空记忆字典。
    """
    return {
        "version": "1.0",
        "lastUpdated": utc_now_iso_z(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


class MemoryStorage(abc.ABC):
    """记忆存储提供者的抽象基类。"""

    @abc.abstractmethod
    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """加载指定 Agent 的记忆数据。"""
        pass

    @abc.abstractmethod
    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """强制重新加载指定 Agent 的记忆数据。"""
        pass

    @abc.abstractmethod
    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """保存指定 Agent 的记忆数据。"""
        pass


class FileMemoryStorage(MemoryStorage):
    """基于文件的记忆存储提供者。"""

    def __init__(self):
        """初始化文件记忆存储。"""
        # 记忆缓存按 (user_id, agent_name) 隔离；None 表示全局维度。
        # 值为 (memory_data, file_mtime)，mtime 变化时自动重新加载。
        self._memory_cache: dict[tuple[str | None, str | None], tuple[dict[str, Any], float | None]] = {}
        # 多线程读写缓存时用同一把锁保护。
        self._cache_lock = threading.Lock()

    def _validate_agent_name(self, agent_name: str) -> None:
        """校验 Agent 名称在文件系统路径中的安全性。

        使用仓库统一的 ``AGENT_NAME_PATTERN`` 确保跨模块一致，并防止
        路径穿越或其他问题字符。
        """
        if not agent_name:
            raise ValueError("Agent name must be a non-empty string.")
        if not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name {agent_name!r}: names must match {AGENT_NAME_PATTERN.pattern}")

    def _get_memory_file_path(self, agent_name: str | None = None, *, user_id: str | None = None) -> Path:
        """获取记忆文件对应的路径。"""
        if user_id is not None:
            if agent_name is not None:
                self._validate_agent_name(agent_name)
                return get_paths().user_agent_memory_file(user_id, agent_name)
            config = get_memory_config()
            if config.storage_path and Path(config.storage_path).is_absolute():
                return Path(config.storage_path)
            return get_paths().user_memory_file(user_id)
        # 兼容旧布局：没有 user_id 时仍支持按 agent 或全局文件存储。
        if agent_name is not None:
            self._validate_agent_name(agent_name)
            return get_paths().agent_memory_file(agent_name)
        config = get_memory_config()
        if config.storage_path:
            p = Path(config.storage_path)
            return p if p.is_absolute() else get_paths().base_dir / p
        return get_paths().memory_file

    def _load_memory_from_file(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """从文件加载记忆数据。"""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)

        if not file_path.exists():
            return create_empty_memory()

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file: %s", e)
            return create_empty_memory()

    @staticmethod
    def _cache_key(agent_name: str | None = None, *, user_id: str | None = None) -> tuple[str | None, str | None]:
        """返回记忆缓存键，顺序与文件路径隔离维度保持一致。"""
        return (user_id, agent_name)

    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """加载记忆数据（带文件 mtime 检查的缓存）。"""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            current_mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            current_mtime = None

        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)
            if cached is not None and cached[1] == current_mtime:
                return cached[0]

        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, current_mtime)

        return memory_data

    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """强制从文件重新加载记忆数据并失效缓存。"""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            mtime = None

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, mtime)
        return memory_data

    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """将记忆数据以原子写入方式保存到文件并更新缓存。

        写入流程（防数据损坏）：
        1. 浅拷贝输入数据，注入当前时间戳 ``lastUpdated``
        2. 写入临时文件 ``memory.{uuid}.tmp``
        3. ``os.replace()`` 原子替换目标文件（POSIX 保证原子性）
        4. 读取新文件 mtime 并更新缓存

        调用示例：
        ```python
        storage = FileMemoryStorage()
        memory = storage.load(agent_name="my-agent", user_id="user_123")
        memory["facts"].append({"content": "新事实", ...})
        storage.save(memory, agent_name="my-agent", user_id="user_123")  # → True
        ```

        Args:
            memory_data: 要保存的记忆数据字典。
            agent_name: 若提供则按 Agent 隔离保存。
            user_id: 若提供则按用户隔离保存。

        Returns:
            保存成功返回 ``True``，IO 错误返回 ``False``。
        """
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # 写入前浅拷贝并更新时间戳，避免调用方对象在保存失败时被提前污染。
            memory_data = {**memory_data, "lastUpdated": utc_now_iso_z()}

            temp_path = file_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)

            temp_path.replace(file_path)

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = None

            with self._cache_lock:
                self._memory_cache[cache_key] = (memory_data, mtime)
            logger.info("Memory saved to %s", file_path)
            return True
        except OSError as e:
            logger.error("Failed to save memory file: %s", e)
            return False


_storage_instance: MemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> MemoryStorage:
    """获取已配置的记忆存储实例。"""
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    with _storage_lock:
        if _storage_instance is not None:
            return _storage_instance

        config = get_memory_config()
        storage_class_path = config.storage_class

        try:
            module_path, class_name = storage_class_path.rsplit(".", 1)
            import importlib

            module = importlib.import_module(module_path)
            storage_class = getattr(module, class_name)

            # 只接受 MemoryStorage 子类，避免配置错误对象破坏 load/save 契约。
            if not isinstance(storage_class, type):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a class: {storage_class!r}")
            if not issubclass(storage_class, MemoryStorage):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a subclass of MemoryStorage")

            _storage_instance = storage_class()
        except Exception as e:
            logger.error(
                "Failed to load memory storage %s, falling back to FileMemoryStorage: %s",
                storage_class_path,
                e,
            )
            _storage_instance = FileMemoryStorage()

    return _storage_instance
