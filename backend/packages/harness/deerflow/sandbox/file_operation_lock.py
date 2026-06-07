"""为同一 sandbox+路径 组合提供全局文件操作锁。

通过 :class:`weakref.WeakValueDictionary` 避免长生命周期进程中的内存泄漏:
当某个锁不再被任何线程引用时,会自动从表中移除。
"""

import threading
import weakref

from deerflow.sandbox.sandbox import Sandbox

# Use WeakValueDictionary to prevent memory leak in long-running processes.
# Locks are automatically removed when no longer referenced by any thread.
_LockKey = tuple[str, str]
_FILE_OPERATION_LOCKS: weakref.WeakValueDictionary[_LockKey, threading.Lock] = weakref.WeakValueDictionary()
_FILE_OPERATION_LOCKS_GUARD = threading.Lock()


def get_file_operation_lock_key(sandbox: Sandbox, path: str) -> tuple[str, str]:
    """根据 sandbox 实例和路径生成锁的键。

    优先使用 ``sandbox.id`` 作为第一段键;当 sandbox 没有 ``id`` 属性时,使用
    内存地址的字符串 ``"instance:<id>"`` 作为兜底,以保证键稳定。

    Args:
        sandbox: 目标 sandbox 实例。
        path: 文件路径。

    Returns:
        由 sandbox 标识与路径组成的二元组,可作为锁表的键。
    """
    sandbox_id = getattr(sandbox, "id", None)
    if not sandbox_id:
        sandbox_id = f"instance:{id(sandbox)}"
    return sandbox_id, path


def get_file_operation_lock(sandbox: Sandbox, path: str) -> threading.Lock:
    """获取(惰性创建)指定 sandbox+路径 对应的文件操作锁。

    同一对 (sandbox, path) 总是返回同一把 :class:`threading.Lock` 实例,从而
    保证对该路径的并发文件操作是串行化的。

    Args:
        sandbox: 目标 sandbox 实例。
        path: 文件路径。

    Returns:
        与该 (sandbox, path) 绑定的线程锁实例。
    """
    lock_key = get_file_operation_lock_key(sandbox, path)
    with _FILE_OPERATION_LOCKS_GUARD:
        lock = _FILE_OPERATION_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _FILE_OPERATION_LOCKS[lock_key] = lock
        return lock
