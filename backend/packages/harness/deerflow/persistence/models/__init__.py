"""ORM 模型注册入口。

导入本模块会确保所有 ORM 模型都被注册到 ``Base.metadata``，使 Alembic
autogenerate 能发现每张表。

实际的 ORM 类已迁移到各实体专属子包：
- ``deerflow.persistence.thread_meta``
- ``deerflow.persistence.run``
- ``deerflow.persistence.feedback``
- ``deerflow.persistence.user``

``RunEventRow`` 仍保留在 ``deerflow.persistence.models.run_event``，
因为它的存储实现位于 ``deerflow.runtime.events.store.db``，并且没有
对应的实体目录。
"""

from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

__all__ = ["FeedbackRow", "RunEventRow", "RunRow", "ThreadMetaRow", "UserRow"]
