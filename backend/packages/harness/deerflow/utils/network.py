"""线程安全的网络工具。

主要提供 :class:`PortAllocator` 与一组基于它的模块级便利函数（``get_free_port``、
``release_port``），用于在并发环境下安全地申请与释放端口。
"""

import socket
import threading
from contextlib import contextmanager


class PortAllocator:
    """线程安全的端口分配器，避免并发环境下的端口冲突。

    内部维护一份「已保留端口」集合，配合锁保证分配动作的原子性。已分配
    的端口会一直保留，直到调用方显式调用 :meth:`release`。

    用法::

        allocator = PortAllocator()

        # 方式一：手动申请与释放
        port = allocator.allocate(start_port=8080)
        try:
            # 使用端口...
        finally:
            allocator.release(port)

        # 方式二：上下文管理器（推荐）
        with allocator.allocate_context(start_port=8080) as port:
            # 使用端口...
            # 退出上下文时自动释放
    """

    def __init__(self):
        """初始化分配器内部状态（锁与保留集合）。"""
        self._lock = threading.Lock()
        self._reserved_ports: set[int] = set()

    def _is_port_available(self, port: int) -> bool:
        """检查端口是否可用于绑定。

        不仅会查询「已保留集合」，还会实际尝试 ``bind`` 到 ``0.0.0.0``。
        选择通配地址是为了与 Docker 的行为保持一致——Docker 默认绑定
        到 ``0.0.0.0:PORT``，仅探测 ``127.0.0.1`` 会在 Docker 已占用
        通配地址时误判为可用。

        Args:
            port: 待检测的端口号。

        Returns:
            端口可用时返回 ``True``，否则返回 ``False``。
        """
        if port in self._reserved_ports:
            return False

        # Bind to 0.0.0.0 (wildcard) rather than localhost so that the check
        # mirrors exactly what Docker does.  Docker binds to 0.0.0.0:PORT;
        # checking only 127.0.0.1 can falsely report a port as available even
        # when Docker already occupies it on the wildcard address.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

    def allocate(self, start_port: int = 8080, max_range: int = 100) -> int:
        """以线程安全的方式申请一个可用端口。

        该方法线程安全：先找到一个可用端口，再将其加入保留集合并返回。
        端口会一直保留到 :meth:`release` 被调用。

        Args:
            start_port: 搜索起始端口。
            max_range: 最多搜索的端口数。

        Returns:
            分配到的可用端口号。

        Raises:
            RuntimeError: 在指定区间内未找到可用端口。
        """
        with self._lock:
            for port in range(start_port, start_port + max_range):
                if self._is_port_available(port):
                    self._reserved_ports.add(port)
                    return port

            raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_range}")

    def release(self, port: int) -> None:
        """释放先前申请的端口。

        Args:
            port: 待释放的端口号。
        """
        with self._lock:
            self._reserved_ports.discard(port)

    @contextmanager
    def allocate_context(self, start_port: int = 8080, max_range: int = 100):
        """带自动释放的端口分配上下文管理器。

        Args:
            start_port: 搜索起始端口。
            max_range: 最多搜索的端口数。

        Yields:
            分配到的可用端口号。
        """
        port = self.allocate(start_port, max_range)
        try:
            yield port
        finally:
            self.release(port)


# Global port allocator instance for shared use across the application
_global_port_allocator = PortAllocator()


def get_free_port(start_port: int = 8080, max_range: int = 100) -> int:
    """以线程安全的方式获取一个空闲端口。

    内部使用全局端口分配器，确保并发调用不会返回同一端口。端口会被标记
    为保留，直到调用 :func:`release_port` 才释放。

    Args:
        start_port: 搜索起始端口。
        max_range: 最多搜索的端口数。

    Returns:
        分配到的可用端口号。

    Raises:
        RuntimeError: 在指定区间内未找到可用端口。
    """
    return _global_port_allocator.allocate(start_port, max_range)


def release_port(port: int) -> None:
    """释放先前通过 :func:`get_free_port` 申请的端口。

    Args:
        port: 待释放的端口号。
    """
    _global_port_allocator.release(port)
