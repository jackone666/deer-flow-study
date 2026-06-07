"""SkillStorage 单例与基于反射的工厂函数。

模式与 :mod:`deerflow.sandbox.sandbox_provider` 一致。
"""

from __future__ import annotations

from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.skill_storage import SkillStorage

_default_skill_storage: SkillStorage | None = None
_default_skill_storage_config: object | None = None  # AppConfig identity the singleton was built from


def get_or_new_skill_storage(**kwargs) -> SkillStorage:
    """获取一个 :class:`SkillStorage` 实例——新实例或进程级单例。

    **新建实例**(不缓存)的场景:
    - 传入 ``skills_path``:作为 ``host_path`` 覆盖,具体类仍按配置解析。
    - 传入 ``app_config``:从 ``app_config.skills`` 构造,使每次请求使用自己的
      配置(如 Gateway 的 ``Depends(get_config)``),不会污染进程级单例。

    **返回单例**(首次调用创建,之后复用)的场景:
    - 既没有 ``skills_path`` 也没有 ``app_config``;此时使用 :func:`get_app_config`
      解析当前配置。

    Returns:
        :class:`SkillStorage` 实例。
    """
    global _default_skill_storage, _default_skill_storage_config

    from deerflow.config import get_app_config
    from deerflow.config.skills_config import SkillsConfig

    def _make_storage(skills_config: SkillsConfig, *, host_path: str | None = None, **kwargs) -> SkillStorage:
        """内部辅助方法。"""
        from deerflow.reflection import resolve_class

        cls = resolve_class(skills_config.use, SkillStorage)
        return cls(
            host_path=host_path if host_path is not None else str(skills_config.get_skills_path()),
            container_path=skills_config.container_path,
            **kwargs,
        )

    skills_path = kwargs.pop("skills_path", None)
    app_config = kwargs.pop("app_config", None)

    if skills_path is not None:
        if app_config is not None:
            return _make_storage(app_config.skills, host_path=str(skills_path), **kwargs)
        # No app_config: use a default SkillsConfig so we never need to read config.yaml
        # when the caller has already supplied an explicit host path.
        from deerflow.config.skills_config import SkillsConfig

        return _make_storage(SkillsConfig(), host_path=str(skills_path), **kwargs)

    if app_config is not None:
        return _make_storage(app_config.skills, **kwargs)

    # If the singleton was manually injected (e.g. in tests) without a config
    # identity (_default_skill_storage_config is None), skip get_app_config()
    # entirely to avoid requiring a config.yaml on disk.
    if _default_skill_storage is not None and _default_skill_storage_config is None:
        return _default_skill_storage

    app_config_now = get_app_config()
    if _default_skill_storage is None or _default_skill_storage_config is not app_config_now:
        _default_skill_storage = _make_storage(app_config_now.skills, **kwargs)
        _default_skill_storage_config = app_config_now
    return _default_skill_storage


def reset_skill_storage() -> None:
    """清空缓存的单例(供测试或热重载场景使用)。"""
    global _default_skill_storage, _default_skill_storage_config
    _default_skill_storage = None
    _default_skill_storage_config = None


__all__ = [
    "LocalSkillStorage",
    "SkillStorage",
    "get_or_new_skill_storage",
    "reset_skill_storage",
]
