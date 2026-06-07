"""用户存储子包。

包含 ``users`` 表的 ORM 模型。具体 repository 实现
（``SQLiteUserRepository``）位于 app 层（``app.gateway.auth.repositories.sqlite``），
因为它需要在 ORM 行与 auth 模块的 pydantic ``User`` 类之间进行转换。
这让 harness 包对 app 代码零依赖。
"""

from deerflow.persistence.user.model import UserRow

__all__ = ["UserRow"]
