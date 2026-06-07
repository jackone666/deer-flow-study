"""一次性迁移脚本：将旧版线程目录和记忆迁移到按用户隔离的布局。

    用法：``PYTHONPATH=. python scripts/migrate_user_isolation.py [--dry-run] [--user-id USER_ID]``。
    该脚本是幂等的——成功迁移后再运行不会有任何副作用。
"""


import argparse
import logging
import shutil

from deerflow.config.paths import Paths, get_paths

logger = logging.getLogger(__name__)


def migrate_thread_dirs(
    paths: Paths,
    thread_owner_map: dict[str, str],
    *,
    dry_run: bool = False,
) -> list[dict]:
    """将旧版线程目录迁移到按用户隔离的布局。
    
            Args:
                paths: ``Paths`` 实例。
                thread_owner_map: 来自 ``threads_meta`` 表的 ``thread_id -> user_id`` 映射。
                dry_run: 若为 True，仅记录将要执行的动作。
    """

    report: list[dict] = []
    legacy_threads = paths.base_dir / "threads"
    if not legacy_threads.exists():
        logger.info("No legacy threads directory found — nothing to migrate.")
        return report

    for thread_dir in sorted(legacy_threads.iterdir()):
        if not thread_dir.is_dir():
            continue
        thread_id = thread_dir.name
        user_id = thread_owner_map.get(thread_id, "default")
        dest = paths.base_dir / "users" / user_id / "threads" / thread_id

        entry = {"thread_id": thread_id, "user_id": user_id, "action": ""}

        if dest.exists():
            conflicts_dir = paths.base_dir / "migration-conflicts" / thread_id
            entry["action"] = f"conflict -> {conflicts_dir}"
            if not dry_run:
                conflicts_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(thread_dir), str(conflicts_dir))
            logger.warning("Conflict for thread %s: moved to %s", thread_id, conflicts_dir)
        else:
            entry["action"] = f"moved -> {dest}"
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(thread_dir), str(dest))
            logger.info("Migrated thread %s -> user %s", thread_id, user_id)

        report.append(entry)

    # Clean up empty legacy threads dir
    if not dry_run and legacy_threads.exists() and not any(legacy_threads.iterdir()):
        legacy_threads.rmdir()

    return report


def migrate_agents(
    paths: Paths,
    user_id: str = "default",
    *,
    dry_run: bool = False,
) -> list[dict]:
    """将旧版自定义 agent 目录迁移到按用户隔离的布局。
    
        旧版布局：``{base_dir}/agents/{name}/``
        按用户布局：``{base_dir}/users/{user_id}/agents/{name}/``
        按用户已存在的 agent 优先——绝不会被旧版全局版本覆盖。
    """

    report: list[dict] = []
    legacy_agents = paths.agents_dir
    if not legacy_agents.exists():
        logger.info("No legacy agents directory found — nothing to migrate.")
        return report

    for agent_dir in sorted(legacy_agents.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_name = agent_dir.name
        dest = paths.user_agent_dir(user_id, agent_name)

        entry = {"agent": agent_name, "user_id": user_id, "action": ""}

        if dest.exists():
            conflicts_dir = paths.base_dir / "migration-conflicts" / "agents" / agent_name
            entry["action"] = f"conflict -> {conflicts_dir}"
            if not dry_run:
                conflicts_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(agent_dir), str(conflicts_dir))
            logger.warning("Conflict for agent %s: moved legacy copy to %s", agent_name, conflicts_dir)
        else:
            entry["action"] = f"moved -> {dest}"
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(agent_dir), str(dest))
            logger.info("Migrated agent %s -> user %s", agent_name, user_id)

        report.append(entry)

    # Clean up empty legacy agents dir
    if not dry_run and legacy_agents.exists() and not any(legacy_agents.iterdir()):
        legacy_agents.rmdir()

    return report


def migrate_memory(
    paths: Paths,
    user_id: str = "default",
    *,
    dry_run: bool = False,
) -> None:
    """将旧版全局 ``memory.json`` 迁移到按用户隔离的布局。
    
            Args:
                paths: ``Paths`` 实例。
                user_id: 接收旧版记忆的目标用户。
                dry_run: 若为 True，仅记录而不实际改动。
    """

    legacy_mem = paths.base_dir / "memory.json"
    if not legacy_mem.exists():
        logger.info("No legacy memory.json found — nothing to migrate.")
        return

    dest = paths.user_memory_file(user_id)
    if dest.exists():
        legacy_backup = paths.base_dir / "memory.legacy.json"
        logger.warning("Destination %s exists; renaming legacy to %s", dest, legacy_backup)
        if not dry_run:
            legacy_mem.rename(legacy_backup)
        return

    logger.info("Migrating memory.json -> %s", dest)
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_mem), str(dest))


def _build_owner_map_from_db(paths: Paths) -> dict[str, str]:
    """查询 ``threads_meta`` 表以获得 ``thread_id -> user_id`` 映射。
    
        使用原生 ``sqlite3`` 以避免异步依赖。
    """

    import sqlite3

    db_path = paths.base_dir / "deer-flow.db"
    if not db_path.exists():
        logger.info("No database found at %s — using empty owner map.", db_path)
        return {}

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT thread_id, user_id FROM threads_meta WHERE user_id IS NOT NULL")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError as e:
        logger.warning("Failed to query threads_meta: %s", e)
        return {}
    finally:
        conn.close()


def main() -> None:
    """执行赋值。
    
            Returns:
                None。
    """
    parser = argparse.ArgumentParser(description="Migrate DeerFlow data to per-user layout")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without making changes")
    parser.add_argument(
        "--user-id",
        default="default",
        metavar="USER_ID",
        help=("User ID to claim un-owned legacy data (global memory.json and legacy custom agents). Defaults to 'default'. In multi-user installs, set this to the operator account that should inherit those legacy artifacts."),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    paths = get_paths()
    logger.info("Base directory: %s", paths.base_dir)
    logger.info("Dry run: %s", args.dry_run)
    logger.info("Claiming un-owned legacy data for user_id=%s", args.user_id)

    owner_map = _build_owner_map_from_db(paths)
    logger.info("Found %d thread ownership records in DB", len(owner_map))

    report = migrate_thread_dirs(paths, owner_map, dry_run=args.dry_run)
    migrate_memory(paths, user_id=args.user_id, dry_run=args.dry_run)
    agent_report = migrate_agents(paths, user_id=args.user_id, dry_run=args.dry_run)

    if report:
        logger.info("Thread migration report:")
        for entry in report:
            logger.info("  thread=%s user=%s action=%s", entry["thread_id"], entry["user_id"], entry["action"])
    else:
        logger.info("No threads to migrate.")

    if agent_report:
        logger.info("Agent migration report:")
        for entry in agent_report:
            logger.info("  agent=%s user=%s action=%s", entry["agent"], entry["user_id"], entry["action"])
    else:
        logger.info("No agents to migrate.")

    unowned = [e for e in report if e["user_id"] == "default"]
    if unowned:
        logger.warning("%d thread(s) had no owner and were assigned to 'default':", len(unowned))
        for e in unowned:
            logger.warning("  %s", e["thread_id"])

    if agent_report:
        logger.warning(
            "%d legacy agent(s) were assigned to '%s'. If those agents belonged to other users, move them manually under {base_dir}/users/<user_id>/agents/.",
            len(agent_report),
            args.user_id,
        )


if __name__ == "__main__":
    main()
