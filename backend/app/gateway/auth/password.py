"""带版本号的密码哈希工具。

哈希格式：``$dfv<N>$<bcrypt_hash>``，其中 ``<N>`` 为版本号。

- **v1**（旧版）：``bcrypt(password)``——纯 bcrypt，存在 72 字节静默截断问题。
- **v2**（当前）：``bcrypt(b64(sha256(password)))``——SHA-256 预哈希绕开
  72 字节限制，让完整密码都参与哈希。

校验时自动识别版本；对于无前缀的旧哈希按 v1 处理，因此已有部署可以在
下次登录时透明地升级。
"""

import asyncio
import base64
import hashlib

import bcrypt

_CURRENT_VERSION = 2
_PREFIX_V2 = "$dfv2$"
_PREFIX_V1 = "$dfv1$"


def _pre_hash_v2(password: str) -> bytes:
    """SHA-256 预哈希，绕过 bcrypt 的 72 字节上限。"""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    """哈希一个密码（当前版本：v2 —— SHA-256 + bcrypt）。"""
    raw = bcrypt.hashpw(_pre_hash_v2(password), bcrypt.gensalt()).decode("utf-8")
    return f"{_PREFIX_V2}{raw}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码，自动识别哈希版本。

    支持 v2（``$dfv2$…``）、v1（``$dfv1$…``）以及裸的 bcrypt 哈希
    （无前缀按 v1 处理，兼容无版本号的旧数据）。
    """
    try:
        if hashed_password.startswith(_PREFIX_V2):
            bcrypt_hash = hashed_password[len(_PREFIX_V2) :]
            return bcrypt.checkpw(_pre_hash_v2(plain_password), bcrypt_hash.encode("utf-8"))

        if hashed_password.startswith(_PREFIX_V1):
            bcrypt_hash = hashed_password[len(_PREFIX_V1) :]
        else:
            bcrypt_hash = hashed_password

        return bcrypt.checkpw(plain_password.encode("utf-8"), bcrypt_hash.encode("utf-8"))
    except ValueError:
        # bcrypt 在哈希格式错误（例如盐值非法）时会抛出 ValueError。
        # 失败时返回 False 而不是让请求崩溃。
        return False


def needs_rehash(hashed_password: str) -> bool:
    """如果哈希版本较旧需要重新哈希，则返回 ``True``。"""
    return not hashed_password.startswith(_PREFIX_V2)


async def hash_password_async(password: str) -> str:
    """以非阻塞方式哈希密码。

    将阻塞的 bcrypt 操作放在线程池中执行，避免在哈希过程中阻塞事件循环。
    """
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """以非阻塞方式校验密码。

    将阻塞的 bcrypt 操作放在线程池中执行，避免在校验过程中阻塞事件循环。
    """
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)
