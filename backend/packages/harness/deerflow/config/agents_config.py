"""自定义 Agent 的配置与加载器。

自定义 Agent 按用户存储在 ``{base_dir}/users/{user_id}/agents/{name}/``。
旧的共享目录 ``{base_dir}/agents/{name}/`` 仍可读取，以便尚未运行
``scripts/migrate_user_isolation.py`` 的旧版安装可以继续工作。
新写入始终落到 per-user 布局。
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def validate_agent_name(name: str | None) -> str | None:
    """在校验自定义 agent 名称后返回；用于文件系统路径。

    Args:
        name: 待校验的 agent 名称，可为 ``None``。

    Returns:
        str | None: 校验通过则原样返回；``None`` 输入时直接透传 ``None``。

    Raises:
        ValueError: 名称不是字符串或不匹配 ``AGENT_NAME_PATTERN`` 时。
    """
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("agent 名称非法，期望是字符串或 None。")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"agent 名称 '{name}' 非法，必须匹配模式：{AGENT_NAME_PATTERN.pattern}")
    return name


class AgentConfig(BaseModel):
    """自定义 agent 的配置。"""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills 控制哪些技能被加载到 agent 的 prompt 中：
    # - None（或省略）：加载所有启用的技能（默认回退行为）
    # - []（显式空列表）：禁用所有技能
    # - ["skill1", "skill2"]：只加载指定的技能
    skills: list[str] | None = None


def resolve_agent_dir(name: str, *, user_id: str | None = None) -> Path:
    """返回 agent 在磁盘上的目录，优先使用 per-user 布局。

    解析顺序：
    1. ``{base_dir}/users/{user_id}/agents/{name}/``（per-user，当前布局）
    2. ``{base_dir}/agents/{name}/``（旧共享布局，只读回退）

    若两者都不存在，则返回 per-user 路径，以便打算创建 agent 的调用方
    直接写入新布局。

    Args:
        name: 已校验的 agent 名称。
        user_id: agent 的所有者。默认为请求上下文中的有效用户
            （无 auth 模式下为 ``"default"``）。

    Returns:
        Path: 解析得到的 agent 目录路径。
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()
    user_path = paths.user_agent_dir(effective_user, name)
    if user_path.exists():
        return user_path

    legacy_path = paths.agent_dir(name)
    if legacy_path.exists():
        return legacy_path

    return user_path


def load_agent_config(name: str | None, *, user_id: str | None = None) -> AgentConfig | None:
    """从目录加载自定义或默认 agent 的配置。

    优先从 per-user 布局读取；对尚未迁移的安装回退到旧共享布局。

    Args:
        name: agent 名称。
        user_id: agent 的所有者。默认为当前请求上下文中的有效用户。

    Returns:
        AgentConfig | None: ``name`` 为 ``None`` 时返回 ``None``，否则返回加载到的配置。

    Raises:
        FileNotFoundError: agent 目录或 config.yaml 不存在。
        ValueError: config.yaml 解析失败。
    """

    if name is None:
        return None

    name = validate_agent_name(name)
    agent_dir = resolve_agent_dir(name, user_id=user_id)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"未找到 agent 目录：{agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"未找到 agent 配置：{config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"解析 agent 配置 {config_file} 失败：{e}") from e

    # 若文件未指定 name，则用目录名兜底
    if "name" not in data:
        data["name"] = name

    # 在传入 Pydantic 前剔除未知字段（如旧版的 prompt_file）
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def load_agent_soul(agent_name: str | None, *, user_id: str | None = None) -> str | None:
    """读取自定义 agent 的 SOUL.md（如存在）。

    SOUL.md 定义 agent 的人格、价值观与行为护栏，会作为附加上下文
    注入到 lead agent 的系统提示中。

    Args:
        agent_name: agent 名称；为 ``None`` 时取默认 agent。
        user_id: agent 的所有者。默认为当前请求上下文中的有效用户。

    Returns:
        str | None: SOUL.md 内容字符串；文件不存在时返回 ``None``。
    """
    if agent_name:
        agent_dir = resolve_agent_dir(agent_name, user_id=user_id)
    else:
        agent_dir = get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents(*, user_id: str | None = None) -> list[AgentConfig]:
    """扫描 agents 目录并返回所有合法的自定义 agent。

    返回 per-user 布局与旧共享布局的并集，确保迁移前的安装在被迁移
    之前仍可见；同名时 per-user 项会覆盖旧项。

    Args:
        user_id: 列出该用户拥有的 agents。默认为当前请求上下文中的有效用户。

    Returns:
        list[AgentConfig]: 找到的合法 agent 目录对应的 :class:`AgentConfig` 列表。
    """
    paths = get_paths()
    effective_user = user_id or get_effective_user_id()

    seen: set[str] = set()
    agents: list[AgentConfig] = []

    user_root = paths.user_agents_dir(effective_user)
    legacy_root = paths.agents_dir

    for root in (user_root, legacy_root):
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in seen:
                continue
            config_file = entry / "config.yaml"
            if not config_file.exists():
                logger.debug(f"跳过 {entry.name}：缺少 config.yaml")
                continue

            try:
                agent_cfg = load_agent_config(entry.name, user_id=effective_user)
                if agent_cfg is None:
                    continue
                agents.append(agent_cfg)
                seen.add(entry.name)
            except Exception as e:
                logger.warning(f"跳过 agent '{entry.name}'：{e}")

    agents.sort(key=lambda a: a.name)
    return agents
