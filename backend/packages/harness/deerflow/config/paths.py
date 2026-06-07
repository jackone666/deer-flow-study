"""DeerFlow 应用数据的集中式路径配置。"""

import hashlib
import os
import re
import shutil
from pathlib import Path, PureWindowsPath

from deerflow.config.runtime_paths import runtime_home

# 沙箱内 agent 看到的虚拟路径前缀
VIRTUAL_PATH_PREFIX = "/mnt/user-data"

_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_UNSAFE_USER_ID_CHAR_RE = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_USER_ID_DIGEST_HEX_LEN = 16


def _default_local_base_dir() -> Path:
    """返回调用方项目可写的 DeerFlow 状态目录。"""
    return runtime_home()


def _validate_thread_id(thread_id: str) -> str:
    """在使用 thread_id 拼装文件系统路径前进行校验。

    Args:
        thread_id: 外部传入的 thread ID。

    Returns:
        str: 校验通过则原样返回。

    Raises:
        ValueError: thread_id 含非法字符时。
    """
    if not _SAFE_THREAD_ID_RE.match(thread_id):
        raise ValueError(f"非法 thread_id {thread_id!r}：只允许字母数字、连字符与下划线。")
    return thread_id


def _validate_user_id(user_id: str) -> str:
    """在使用 user_id 拼装文件系统路径前进行校验。

    Args:
        user_id: 外部传入的 user ID。

    Returns:
        str: 校验通过则原样返回。

    Raises:
        ValueError: user_id 含非法字符时。
    """
    if not _SAFE_USER_ID_RE.match(user_id):
        raise ValueError(f"非法 user_id {user_id!r}：只允许字母数字、连字符与下划线。")
    return user_id


def make_safe_user_id(raw: str) -> str:
    """将外部身份归一化为 user-id 字符集（``[A-Za-z0-9_-]``）。

    IM 渠道 ID（飞书/Slack/Telegram）可能包含 :func:`_validate_user_id`
    会拒绝的字符。已合法的 ID 原样返回；含不合法字符的会附加一个短的
    摘要后缀，保证两个不同输入不会落到同一存储桶。

    Args:
        raw: 原始用户标识字符串。

    Returns:
        str: 归一化后的安全 user_id。

    Raises:
        ValueError: ``raw`` 为空字符串时。
    """
    if not raw:
        raise ValueError("user_id 必须是非空字符串。")
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    if sanitized == raw:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def _join_host_path(base: str, *parts: str) -> str:
    """按本地路径风格拼接宿主机路径片段。

    Windows 上的 Docker Desktop 要求 bind mount 的源路径保持 Windows
    路径形式（如 ``C:\\repo\\backend\\.deer-flow``）。在 POSIX 主机上
    用 ``Path(base) / ...`` 拼接可能把分隔符混写，因此本函数保留原风格。

    Args:
        base: 基础路径字符串。
        parts: 后续路径片段。

    Returns:
        str: 拼接后的路径字符串。
    """
    if not parts:
        return base

    if re.match(r"^[A-Za-z]:[\\/]", base) or base.startswith("\\\\") or "\\" in base:
        result = PureWindowsPath(base)
        for part in parts:
            result /= part
        return str(result)

    result = Path(base)
    for part in parts:
        result /= part
    return str(result)


def join_host_path(base: str, *parts: str) -> str:
    """按本地路径风格拼接宿主机路径片段（:func:`_join_host_path` 的公开别名）。"""
    return _join_host_path(base, *parts)


class Paths:
    """
    DeerFlow 应用数据的集中式路径配置。

    目录布局（宿主机侧）：

        {base_dir}/
        ├── memory.json
        ├── USER.md          <-- 全局用户档案（注入到所有 agent）
        ├── agents/
        │   └── {agent_name}/
        │       ├── config.yaml
        │       ├── SOUL.md  <-- agent 人格/身份（与 lead prompt 一起注入）
        │       └── memory.json
        └── threads/
            └── {thread_id}/
                └── user-data/         <-- 在沙箱内挂载为 /mnt/user-data/
                    ├── workspace/     <-- /mnt/user-data/workspace/
                    ├── uploads/       <-- /mnt/user-data/uploads/
                    └── outputs/       <-- /mnt/user-data/outputs/

    BaseDir 解析顺序（按优先级）：
        1. 构造函数参数 ``base_dir``
        2. 环境变量 ``DEER_FLOW_HOME``
        3. 调用方项目回退：``{project_root}/.deer-flow``
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """构造 :class:`Paths` 实例。

        Args:
            base_dir: 可选的根目录；为 ``None`` 时按 BaseDir 解析顺序惰性解析。
        """
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    @property
    def host_base_dir(self) -> Path:
        """宿主机侧 base dir，用作 Docker volume mount 的源。

        在 Docker 中以挂载 Docker socket（DooD）方式运行时，Docker daemon
        实际跑在宿主机上，挂载路径会按宿主机文件系统解析。设置
        ``DEER_FLOW_HOST_BASE_DIR`` 为宿主机上对应本容器 ``base_dir`` 的
        路径，使 sandbox 容器的 volume mount 正常工作。环境变量未设置时
        回退到 ``base_dir``（原生/本地执行）。

        Returns:
            Path: 宿主机侧根目录。
        """
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return Path(env)
        return self.base_dir

    def _host_base_dir_str(self) -> str:
        """以字符串形式返回宿主机 base dir，供 bind mount 使用。"""
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return env
        return str(self.base_dir)

    @property
    def base_dir(self) -> Path:
        """所有应用数据的根目录。

        解析顺序：构造函数参数 -> ``DEER_FLOW_HOME`` -> 项目本地回退。

        Returns:
            Path: 解析后的根目录绝对路径。
        """
        if self._base_dir is not None:
            return self._base_dir

        if env_home := os.getenv("DEER_FLOW_HOME"):
            return Path(env_home).resolve()

        return _default_local_base_dir()

    @property
    def memory_file(self) -> Path:
        """:class:`memory.json` 持久化文件路径。"""
        return self.base_dir / "memory.json"

    @property
    def user_md_file(self) -> Path:
        """全局用户档案 ``USER.md`` 路径。"""
        return self.base_dir / "USER.md"

    @property
    def agents_dir(self) -> Path:
        """共享（用户隔离之前）自定义 agent 的旧根目录：``{base_dir}/agents/``。

        新代码应使用 :meth:`user_agents_dir`。该属性仅作为尚未运行
        ``migrate_user_isolation.py`` 脚本的旧版安装的读端回退。

        Returns:
            Path: 旧版共享 agents 根目录。
        """
        return self.base_dir / "agents"

    def agent_dir(self, name: str) -> Path:
        """旧版 per-agent 目录（无用户隔离）：``{base_dir}/agents/{name}/``。"""
        return self.agents_dir / name.lower()

    def agent_memory_file(self, name: str) -> Path:
        """旧版 per-agent memory 文件：``{base_dir}/agents/{name}/memory.json``。"""
        return self.agent_dir(name) / "memory.json"

    def user_dir(self, user_id: str) -> Path:
        """指定用户的目录：``{base_dir}/users/{user_id}/``。

        Args:
            user_id: 用户 ID（必须通过 :func:`_validate_user_id`）。

        Returns:
            Path: 用户目录。
        """
        return self.base_dir / "users" / _validate_user_id(user_id)

    def user_memory_file(self, user_id: str) -> Path:
        """per-user memory 文件：``{base_dir}/users/{user_id}/memory.json``。"""
        return self.user_dir(user_id) / "memory.json"

    def user_agents_dir(self, user_id: str) -> Path:
        """per-user 自定义 agent 根目录：``{base_dir}/users/{user_id}/agents/``。"""
        return self.user_dir(user_id) / "agents"

    def user_agent_dir(self, user_id: str, agent_name: str) -> Path:
        """per-user per-agent 目录：``{base_dir}/users/{user_id}/agents/{name}/``。"""
        return self.user_agents_dir(user_id) / agent_name.lower()

    def user_agent_memory_file(self, user_id: str, agent_name: str) -> Path:
        """按用户与 agent 隔离的记忆文件：``{base_dir}/users/{user_id}/agents/{name}/memory.json``。"""
        return self.user_agent_dir(user_id, agent_name) / "memory.json"

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """thread 数据的宿主机路径。

        传入 ``user_id`` 时：``{base_dir}/users/{user_id}/threads/{thread_id}/``；
        否则（旧布局）：``{base_dir}/threads/{thread_id}/``。

        该目录下包含挂载到沙箱 ``/mnt/user-data/`` 的 ``user-data/`` 子目录。

        Args:
            thread_id: thread ID。
            user_id: 可选的用户 ID；提供时走 per-user 布局。

        Returns:
            Path: 解析后的 thread 目录。

        Raises:
            ValueError: thread_id 或 user_id 含不合法字符（路径分隔符或 ``..``）
                导致目录穿越风险时。
        """
        if user_id is not None:
            return self.user_dir(user_id) / "threads" / _validate_thread_id(thread_id)
        return self.base_dir / "threads" / _validate_thread_id(thread_id)

    def sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """agent workspace 目录的宿主机路径。

        Host: ``{base_dir}/threads/{thread_id}/user-data/workspace/``
        Sandbox: ``/mnt/user-data/workspace/``
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "workspace"

    def sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """用户上传文件的宿主机路径。

        Host: ``{base_dir}/threads/{thread_id}/user-data/uploads/``
        Sandbox: ``/mnt/user-data/uploads/``
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "uploads"

    def sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """agent 产出物的宿主机路径。

        Host: ``{base_dir}/threads/{thread_id}/user-data/outputs/``
        Sandbox: ``/mnt/user-data/outputs/``
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "outputs"

    def acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """指定 thread 的 ACP workspace 宿主机路径。

        Host: ``{base_dir}/threads/{thread_id}/acp-workspace/``
        Sandbox: ``/mnt/acp-workspace/``

        每个 thread 拥有独立隔离的 ACP workspace，避免并发会话之间相互
        读取彼此的 ACP agent 输出。
        """
        return self.thread_dir(thread_id, user_id=user_id) / "acp-workspace"

    def sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """user-data 根的宿主机路径。

        Host: ``{base_dir}/threads/{thread_id}/user-data/``
        Sandbox: ``/mnt/user-data/``
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data"

    def host_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """thread 目录的宿主机路径，保留 Windows 路径语法。

        Args:
            thread_id: thread ID。
            user_id: 可选的用户 ID。

        Returns:
            str: 拼接得到的路径字符串。
        """
        if user_id is not None:
            return _join_host_path(self._host_base_dir_str(), "users", _validate_user_id(user_id), "threads", _validate_thread_id(thread_id))
        return _join_host_path(self._host_base_dir_str(), "threads", _validate_thread_id(thread_id))

    def host_sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """thread user-data 根的宿主机路径字符串。"""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "user-data")

    def host_sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """workspace mount 源的宿主机路径字符串。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "workspace")

    def host_sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """uploads mount 源的宿主机路径字符串。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "uploads")

    def host_sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """outputs mount 源的宿主机路径字符串。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "outputs")

    def host_acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """ACP workspace mount 源的宿主机路径字符串。"""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "acp-workspace")

    def ensure_thread_dirs(self, thread_id: str, *, user_id: str | None = None) -> None:
        """为一个 thread 创建所有标准沙箱目录。

        以 0o777 创建目录，保证沙箱容器（其 UID 可能与宿主机 backend
        进程不同）可以写入挂载路径而不会遇到 "Permission denied"。
        必须显式调用 ``chmod()`` 是因为 ``Path.mkdir(mode=...)`` 受进程
        umask 影响，可能得不到预期权限。

        同时也创建 ACP workspace 目录，使其在首次调用 ACP agent 之前
        即可被挂载到沙箱容器的 ``/mnt/acp-workspace``。

        Args:
            thread_id: thread ID。
            user_id: 可选的用户 ID。
        """
        for d in [
            self.sandbox_work_dir(thread_id, user_id=user_id),
            self.sandbox_uploads_dir(thread_id, user_id=user_id),
            self.sandbox_outputs_dir(thread_id, user_id=user_id),
            self.acp_workspace_dir(thread_id, user_id=user_id),
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

    def delete_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> None:
        """删除一个 thread 的全部持久化数据。

        操作幂等：thread 目录不存在时直接忽略。

        Args:
            thread_id: thread ID。
            user_id: 可选的用户 ID。
        """
        thread_dir = self.thread_dir(thread_id, user_id=user_id)
        if thread_dir.exists():
            shutil.rmtree(thread_dir)

    def resolve_virtual_path(self, thread_id: str, virtual_path: str, *, user_id: str | None = None) -> Path:
        """将沙箱虚拟路径解析为宿主机文件系统路径。

        Args:
            thread_id: thread ID。
            virtual_path: 沙箱内看到的虚拟路径，如
                ``/mnt/user-data/outputs/report.pdf``。匹配前会先去掉前导斜杠。
            user_id: 可选的用户 ID。

        Returns:
            Path: 解析后的宿主机绝对路径。

        Raises:
            ValueError: 路径不以预期虚拟前缀开头，或检测到路径穿越。
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # 要求按段边界精确匹配，避免前缀混淆
        # （如拒绝 "mnt/user-dataX/..." 这样的路径）。
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"路径必须以 /{prefix} 开头")

        relative = stripped[len(prefix) :].lstrip("/")
        base = self.sandbox_user_data_dir(thread_id, user_id=user_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("访问被拒绝：检测到路径穿越")

        return actual


# ── 单例 ────────────────────────────────────────────────────────────

_paths: Paths | None = None


def get_paths() -> Paths:
    """返回全局 :class:`Paths` 单例（惰性初始化）。

    Returns:
        Paths: 进程级单例的 :class:`Paths` 实例。
    """
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths


def resolve_path(path: str) -> Path:
    """将 ``path`` 解析为绝对 :class:`Path`。

    相对路径会基于应用 base dir 解析；绝对路径则按字面解析（规范化后返回）。

    Args:
        path: 待解析的路径字符串。

    Returns:
        Path: 解析后的绝对路径。
    """
    p = Path(path)
    if not p.is_absolute():
        p = get_paths().base_dir / path
    return p.resolve()
