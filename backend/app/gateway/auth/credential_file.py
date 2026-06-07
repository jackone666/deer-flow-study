"""把初始管理员凭据写入权限受限的文件，而不是日志。

将密钥打印到 stdout/stderr 是一个广为人知的 CodeQL 告警
（py/clear-text-logging-sensitive-data）——生产环境下这些日志会被收集到
ELK/Splunk 等系统，进而成为凭据扩散的源头。本辅助函数把凭据写入只有进程
用户能读的 0600 文件，并返回路径，调用方可以记录**路径**（而不是密码），
供运维人员取用。
"""

from __future__ import annotations

import os
from pathlib import Path

from deerflow.config.paths import get_paths

_CREDENTIAL_FILENAME = "admin_initial_credentials.txt"


def write_initial_credentials(email: str, password: str, *, label: str = "initial") -> Path:
    """把管理员邮箱和密码写入 ``{base_dir}/admin_initial_credentials.txt``。

    文件通过 ``os.open`` **原子地**以 0600 权限创建并覆盖，因此即使在
    ``write_text`` 和 ``chmod`` 之间的极短窗口内密码也不会被其他用户可读。

    ``label`` 用于在文件头部区分 “initial”（首次创建）和 “reset”
    （重置密码），方便运维在重启后取文件时能识别出是哪个事件产生的。

    Args:
        email: 管理员邮箱。
        password: 管理员密码。
        label: 文件头部标识，默认 ``"initial"``。

    Returns:
        Path: 生成文件的绝对路径。
    """
    target = get_paths().base_dir / _CREDENTIAL_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)

    content = (
        f"# DeerFlow admin {label} credentials\n# This file is generated on first boot or password reset.\n# Change the password after login via Settings -> Account,\n# then delete this file.\n#\nemail: {email}\npassword: {password}\n"
    )

    # Atomic 0600 create-or-truncate. O_TRUNC (not O_EXCL) so the
    # reset-password path can rewrite an existing file without a
    # separate unlink-then-create dance.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)

    return target.resolve()
