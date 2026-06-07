"""用于跨进程发现与状态持久化的沙箱元数据。

本模块定义了 :class:`SandboxInfo` 数据类,用于在跨进程/跨重启场景下记录
沙箱的最小可重连信息。该数据结构同时被本地容器后端与远程 K8s 后端
使用,因此字段对两种后端都保持向后兼容(本地字段可选)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SandboxInfo:
    """持久化的沙箱元数据,用于跨进程发现与重连。

    Attributes:
        sandbox_id: 沙箱唯一标识。
        sandbox_url: 沙箱访问 URL,例如 ``http://localhost:8080`` 或 ``http://k3s:30001``。
        container_name: 容器名,仅本地容器后端使用。
        container_id: 容器 ID,仅本地容器后端使用。
        created_at: 创建时间(epoch 秒)。
    """

    sandbox_id: str
    sandbox_url: str  # e.g. http://localhost:8080 or http://k3s:30001
    container_name: str | None = None  # Only for local container backend
    container_id: str | None = None  # Only for local container backend
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """把 :class:`SandboxInfo` 序列化为可 JSON 化的字典。

        Returns:
            dict: 包含全部字段的字典,可以直接写入 JSON。
        """
        return {
            "sandbox_id": self.sandbox_id,
            "sandbox_url": self.sandbox_url,
            "container_name": self.container_name,
            "container_id": self.container_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SandboxInfo:
        """从字典构造 :class:`SandboxInfo` 实例。

        为了兼容历史数据,允许 ``sandbox_url`` 字段缺失时回退到旧字段
        ``base_url``;其余可选字段在缺失时使用 dataclass 的默认值。

        Args:
            data: 包含沙箱元数据的字典,通常来自 :meth:`to_dict`。

        Returns:
            SandboxInfo: 还原出的沙箱元数据实例。
        """
        return cls(
            sandbox_id=data["sandbox_id"],
            sandbox_url=data.get("sandbox_url", data.get("base_url", "")),
            container_name=data.get("container_name"),
            container_id=data.get("container_id"),
            created_at=data.get("created_at", time.time()),
        )
