"""update_agent 工具:让自定义 Agent 把对自身 SOUL.md / config.yaml 的更新持久化。

仅在 ``runtime.context['agent_name']`` 存在(即已进入某个自定义 Agent 的对话
上下文)时才会绑定到 lead agent。默认 Agent 看不到该工具,而启动引导流程仍然
使用 :func:`setup_agent` 完成初次创建握手。

该工具写入 ``{base_dir}/users/{user_id}/agents/{agent_name}/{config.yaml,SOUL.md}``,
保证一个用户创建的 Agent 不会被其他用户看到或修改。文件先写入临时文件,
两个临时文件都成功落盘后再原子地 rename 到正式位置——避免部分失败导致
config.yaml 已被替换而 SOUL.md 还是旧内容。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated, Any

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BeforeValidator

from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

_NULLISH_STRINGS = frozenset({"null", "none", "undefined"})


def _stage_temp(path: Path, text: str) -> Path:
    """把 ``text`` 写入同目录的临时文件并返回其路径。

    调用方负责在所有暂存文件就绪后通过 :meth:`Path.replace` 替换到目标,
    或在失败时自行 unlink。

    Args:
        path: 目标文件路径(临时文件位于其同目录)。
        text: 待写入的文本内容。

    Returns:
        创建的临时文件路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        fd.write(text)
        fd.flush()
        fd.close()
        return Path(fd.name)
    except BaseException:
        fd.close()
        Path(fd.name).unlink(missing_ok=True)
        raise


def _cleanup_temps(temps: list[Path]) -> None:
    """尽力清理已暂存的临时文件。"""
    for tmp in temps:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to clean up temp file %s", tmp, exc_info=True)


def _is_nullish_string(value: object) -> bool:
    """判断值是否为表示"无"的字面字符串(``null``/``none``/``undefined``)。"""
    return isinstance(value, str) and value.strip().lower() in _NULLISH_STRINGS


def _normalize_nullish_string(value: object) -> object:
    """把"无"的字面字符串规范化为 ``None``(供 Pydantic 验证器使用)。"""
    return None if _is_nullish_string(value) else value


OptionalText = Annotated[str | None, BeforeValidator(_normalize_nullish_string)]
OptionalStringList = Annotated[list[str] | None, BeforeValidator(_normalize_nullish_string)]


@tool(parse_docstring=True)
def update_agent(
    runtime: Runtime,
    soul: OptionalText = None,
    description: OptionalText = None,
    skills: OptionalStringList = None,
    tool_groups: OptionalStringList = None,
    model: OptionalText = None,
) -> Command:
    """把对当前自定义 Agent 的 SOUL.md / config.yaml 修改持久化。

    当用户要求调整 Agent 的身份、描述、技能白名单、工具组白名单或默认模型时
    使用本工具。仅显式传入的字段会被更新;省略的字段保持原值。

    ``soul`` 是完整替换内容——没有 patch 语义,因此请始终基于当前 SOUL 进行编辑。

    传入 ``skills=[]`` 关闭该 Agent 的所有技能;省略 ``skills`` 则保持当前白名单
    不变。对于未变更字段不要传 ``"null"``/``"none"``/``"undefined"`` 之类的字面量,
    直接省略即可。

    Args:
        soul: 可选的完整 SOUL.md 替换内容。
        description: 可选的一行新描述。
        skills: 可选技能白名单。``[]`` = 无技能,省略 = 不变。
        tool_groups: 可选工具组白名单。``[]`` = 空,省略 = 不变。
        model: 可选模型覆盖(必须匹配已配置的模型名)。

    Returns:
        带 :class:`ToolMessage` 的 :class:`Command` 描述结果。变更将在下一次
        用户轮转时生效(那时 lead agent 会用新的 SOUL.md 与 config.yaml 重建)。
    """
    tool_call_id = runtime.tool_call_id
    agent_name_raw: str | None = runtime.context.get("agent_name") if runtime.context else None

    def _err(message: str) -> Command:
        """返回值。"""
        return Command(update={"messages": [ToolMessage(content=f"Error: {message}", tool_call_id=tool_call_id, status="error")]})

    if soul is None and description is None and skills is None and tool_groups is None and model is None:
        return _err('No fields provided. Pass at least one of: soul, description, skills, tool_groups, model. Omit unchanged fields instead of passing null-like strings such as "null", "none", or "undefined".')

    try:
        agent_name = validate_agent_name(agent_name_raw)
    except ValueError as e:
        return _err(str(e))

    if not agent_name:
        return _err("update_agent is only available inside a custom agent's chat. There is no agent_name in the current runtime context, so there is nothing to update. If you are inside the bootstrap flow, use setup_agent instead.")

    # Resolve the active user so that updates only affect this user's agent.
    # ``resolve_runtime_user_id`` prefers ``runtime.context["user_id"]`` (set by
    # the gateway from the auth-validated request) and falls back to the
    # contextvar, then DEFAULT_USER_ID. This matches setup_agent so a user
    # creating an agent and later refining it always touches the same files,
    # even if the contextvar gets lost across an async/thread boundary
    # (issue #2782 / #2862 class of bugs).
    user_id = resolve_runtime_user_id(runtime)

    # Reject an unknown ``model`` *before* touching the filesystem. Otherwise
    # ``_resolve_model_name`` silently falls back to the default at runtime
    # and the user sees confusing repeated warnings on every later turn.
    if model is not None and get_app_config().get_model_config(model) is None:
        return _err(f"Unknown model '{model}'. Pass a model name that exists in config.yaml's models section.")

    paths = get_paths()
    agent_dir = paths.user_agent_dir(user_id, agent_name)
    if not agent_dir.exists() and paths.agent_dir(agent_name).exists():
        return _err(f"Agent '{agent_name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating.")

    try:
        existing_cfg = load_agent_config(agent_name, user_id=user_id)
    except FileNotFoundError:
        return _err(f"Agent '{agent_name}' does not exist for the current user. Use setup_agent to create a new agent first.")
    except ValueError as e:
        return _err(f"Agent '{agent_name}' has an unreadable config: {e}")

    if existing_cfg is None:
        return _err(f"Agent '{agent_name}' could not be loaded.")

    updated_fields: list[str] = []

    # Force the on-disk ``name`` to match the directory we are writing into,
    # even if ``existing_cfg.name`` had drifted (e.g. from manual yaml edits).
    config_data: dict[str, Any] = {"name": agent_name}
    new_description = description if description is not None else existing_cfg.description
    config_data["description"] = new_description
    if description is not None and description != existing_cfg.description:
        updated_fields.append("description")

    new_model = model if model is not None else existing_cfg.model
    if new_model is not None:
        config_data["model"] = new_model
    if model is not None and model != existing_cfg.model:
        updated_fields.append("model")

    new_tool_groups = tool_groups if tool_groups is not None else existing_cfg.tool_groups
    if new_tool_groups is not None:
        config_data["tool_groups"] = new_tool_groups
    if tool_groups is not None and tool_groups != existing_cfg.tool_groups:
        updated_fields.append("tool_groups")

    new_skills = skills if skills is not None else existing_cfg.skills
    if new_skills is not None:
        config_data["skills"] = new_skills
    if skills is not None and skills != existing_cfg.skills:
        updated_fields.append("skills")

    config_changed = bool({"description", "model", "tool_groups", "skills"} & set(updated_fields))

    # Stage every file we intend to rewrite into a temp sibling. Only after
    # *all* temp files exist do we rename them into place — so a failure on
    # SOUL.md cannot leave config.yaml already replaced.
    pending: list[tuple[Path, Path]] = []
    staged_temps: list[Path] = []

    try:
        agent_dir.mkdir(parents=True, exist_ok=True)

        if config_changed:
            yaml_text = yaml.dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            config_target = agent_dir / "config.yaml"
            config_tmp = _stage_temp(config_target, yaml_text)
            staged_temps.append(config_tmp)
            pending.append((config_tmp, config_target))

        if soul is not None:
            soul_target = agent_dir / "SOUL.md"
            soul_tmp = _stage_temp(soul_target, soul)
            staged_temps.append(soul_tmp)
            pending.append((soul_tmp, soul_target))
            updated_fields.append("soul")

        # Commit phase. ``Path.replace`` is atomic per file on POSIX/NTFS and
        # the staging step above means any earlier failure has already been
        # reported. The remaining failure mode is a crash *between* two
        # ``replace`` calls, which is reported via the partial-write error
        # branch below so the caller knows which files are now on disk.
        committed: list[Path] = []
        try:
            for tmp, target in pending:
                tmp.replace(target)
                committed.append(target)
        except Exception as e:
            _cleanup_temps([t for t, _ in pending if t not in committed])
            if committed:
                logger.error(
                    "[update_agent] Partial write for agent '%s' (user=%s): committed=%s, failed during rename: %s",
                    agent_name,
                    user_id,
                    [p.name for p in committed],
                    e,
                    exc_info=True,
                )
                return _err(f"Partial update for agent '{agent_name}': {[p.name for p in committed]} were updated, but the rest failed ({e}). Re-run update_agent to retry the remaining fields.")
            raise

    except Exception as e:
        _cleanup_temps(staged_temps)
        logger.error("[update_agent] Failed to update agent '%s' (user=%s): %s", agent_name, user_id, e, exc_info=True)
        return _err(f"Failed to update agent '{agent_name}': {e}")

    if not updated_fields:
        return Command(update={"messages": [ToolMessage(content=f"No changes applied to agent '{agent_name}'. The provided values matched the existing config.", tool_call_id=tool_call_id)]})

    logger.info("[update_agent] Updated agent '%s' (user=%s) fields: %s", agent_name, user_id, updated_fields)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(f"Agent '{agent_name}' updated successfully. Changed: {', '.join(updated_fields)}. The new configuration takes effect on the next user turn."),
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )
