"""``ChannelStore`` —— 持久化 IM 会话到 DeerFlow 主题的映射。"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChannelStore:
    """基于 JSON 文件的存储，将 IM 会话映射到 DeerFlow 主题。

    磁盘数据布局::

        {
            "<channel_name>:<chat_id>": {
                "thread_id": "<uuid>",
                "user_id": "<platform_user>",
                "created_at": 1700000000.0,
                "updated_at": 1700000000.0
            },
            ...
        }

    该存储有意做得简单——单个 JSON 文件，每次变更以原子方式整体重写。
    对于高并发的生产负载，可替换为合适的数据库后端。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        """初始化存储实例。

        Args:
            path: 可选的 JSON 文件路径。默认使用 ``base_dir/channels/store.json``。
        """
        if path is None:
            from deerflow.config.paths import get_paths

            path = Path(get_paths().base_dir) / "channels" / "store.json"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()
        self._lock = threading.Lock()

    # -- persistence -------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        """从 JSON 文件加载数据；文件不存在或损坏时返回空字典。"""
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt channel store at %s, starting fresh", self._path)
        return {}

    def _save(self) -> None:
        """将当前数据原子地写回磁盘（先写临时文件再 rename）。"""
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self._path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(self._data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(self._path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    # -- key helpers -------------------------------------------------------

    @staticmethod
    def _key(channel_name: str, chat_id: str, topic_id: str | None = None) -> str:
        """根据渠道、会话和（可选的）主题 ID 拼装内部存储键。"""
        if topic_id:
            return f"{channel_name}:{chat_id}:{topic_id}"
        return f"{channel_name}:{chat_id}"

    # -- public API --------------------------------------------------------

    def get_thread_id(self, channel_name: str, chat_id: str, topic_id: str | None = None) -> str | None:
        """查找给定 IM 会话/主题对应的 DeerFlow ``thread_id``。

        Args:
            channel_name: 渠道名称。
            chat_id: 平台聊天/会话标识。
            topic_id: 可选的主题标识。

        Returns:
            str | None: 对应的 DeerFlow 主题 ID，若不存在则为 ``None``。
        """
        entry = self._data.get(self._key(channel_name, chat_id, topic_id))
        return entry["thread_id"] if entry else None

    def set_thread_id(
        self,
        channel_name: str,
        chat_id: str,
        thread_id: str,
        *,
        topic_id: str | None = None,
        user_id: str = "",
    ) -> None:
        """为指定 IM 会话/主题创建或更新映射。

        Args:
            channel_name: 渠道名称。
            chat_id: 平台聊天/会话标识。
            thread_id: DeerFlow 主题 ID。
            topic_id: 可选的主题标识。
            user_id: 可选的平台用户 ID，便于审计。
        """
        with self._lock:
            key = self._key(channel_name, chat_id, topic_id)
            now = time.time()
            existing = self._data.get(key)
            self._data[key] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
            self._save()

    def remove(self, channel_name: str, chat_id: str, topic_id: str | None = None) -> bool:
        """移除一条映射。

        若指定 ``topic_id``，只移除该会话/主题对应的映射；
        若省略 ``topic_id``，则删除所有以 ``"<channel_name>:<chat_id>"``
        开头的键（包括基于主题的衍生键）。

        Returns:
            bool: 至少有一条映射被删除则返回 ``True``。
        """
        with self._lock:
            # 移除一个特定的会话/主题映射。
            if topic_id is not None:
                key = self._key(channel_name, chat_id, topic_id)
                if key in self._data:
                    del self._data[key]
                    self._save()
                    return True
                return False

            # 移除该 channel/chat_id 的全部映射（基础键以及所有主题键）。
            prefix = self._key(channel_name, chat_id)
            keys_to_delete = [k for k in self._data if k == prefix or k.startswith(prefix + ":")]
            if not keys_to_delete:
                return False

            for k in keys_to_delete:
                del self._data[k]
            self._save()
            return True

    def list_entries(self, channel_name: str | None = None) -> list[dict[str, Any]]:
        """列出全部已存储的映射，可按渠道名过滤。

        Args:
            channel_name: 若给定，则只返回该渠道的条目。

        Returns:
            list[dict[str, Any]]: 每条结果均展开 ``channel_name``、``chat_id``，并在
            可用时附上 ``topic_id``，再加上存储的 ``thread_id``、``user_id``、
            ``created_at`` 和 ``updated_at`` 字段。
        """
        results = []
        for key, entry in self._data.items():
            parts = key.split(":", 2)
            ch = parts[0]
            chat = parts[1] if len(parts) > 1 else ""
            topic = parts[2] if len(parts) > 2 else None
            if channel_name and ch != channel_name:
                continue
            item: dict[str, Any] = {"channel_name": ch, "chat_id": chat, **entry}
            if topic is not None:
                item["topic_id"] = topic
            results.append(item)
        return results
