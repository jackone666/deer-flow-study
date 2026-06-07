"""所有渠道实现共享的命令定义。

将权威命令集合集中在一处，可确保渠道解析器（例如飞书）与 ``ChannelManager``
调度器自动保持同步——添加或移除命令只需修改本文件。
"""

from __future__ import annotations

KNOWN_CHANNEL_COMMANDS: frozenset[str] = frozenset(
    {
        "/bootstrap",
        "/new",
        "/status",
        "/models",
        "/memory",
        "/help",
    }
)
