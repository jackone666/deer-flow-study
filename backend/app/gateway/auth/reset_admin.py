"""重置管理员密码的 CLI 工具。

用法：
    python -m app.gateway.auth.reset_admin
    python -m app.gateway.auth.reset_admin --email admin@example.com

新密码会写入 ``.deer-flow/admin_initial_credentials.txt``（权限 0600），
而不是直接打印，避免在 CI / 日志聚合中出现明文密钥。
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from app.gateway.auth.credential_file import write_initial_credentials
from app.gateway.auth.password import hash_password
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from deerflow.persistence.user.model import UserRow


async def _run(email: str | None) -> int:
    """CLI 主流程：找到目标管理员并重置其密码。

    Args:
        email: 可选的管理员邮箱；未提供时使用第一条 ``admin`` 记录。

    Returns:
        int: 进程退出码（0 表示成功，1 表示错误）。
    """
    from deerflow.config import get_app_config
    from deerflow.persistence.engine import (
        close_engine,
        get_session_factory,
        init_engine_from_config,
    )

    config = get_app_config()
    await init_engine_from_config(config.database)
    try:
        sf = get_session_factory()
        if sf is None:
            print("Error: persistence engine not available (check config.database).", file=sys.stderr)
            return 1

        repo = SQLiteUserRepository(sf)

        if email:
            user = await repo.get_user_by_email(email)
        else:
            # 通过直接 SELECT 找到第一个管理员 —— 仓储并未暴露
            # “首个管理员” 辅助方法，且仅为此 CLI 增加它显得不必要。
            async with sf() as session:
                stmt = select(UserRow).where(UserRow.system_role == "admin").limit(1)
                row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                user = None
            else:
                user = await repo.get_user_by_id(row.id)

        if user is None:
            if email:
                print(f"Error: user '{email}' not found.", file=sys.stderr)
            else:
                print("Error: no admin user found.", file=sys.stderr)
            return 1

        new_password = secrets.token_urlsafe(16)
        user.password_hash = hash_password(new_password)
        user.token_version += 1
        user.needs_setup = True
        await repo.update_user(user)

        cred_path = write_initial_credentials(user.email, new_password, label="reset")
        print(f"Password reset for: {user.email}")
        print(f"Credentials written to: {cred_path} (mode 0600)")
        print("Next login will require setup (new email + password).")
        return 0
    finally:
        await close_engine()


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="重置管理员密码")
    parser.add_argument("--email", help="管理员邮箱（默认：找到的第一个管理员）")
    args = parser.parse_args()

    exit_code = asyncio.run(_run(args.email))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
