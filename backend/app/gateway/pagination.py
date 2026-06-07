"""Gateway 路由器共用的分页辅助函数。"""

from __future__ import annotations


def trim_run_message_page(rows: list[dict], *, limit: int, after_seq: int | None) -> tuple[list[dict], bool]:
    """对 ``limit + 1`` 大小的运行消息分页进行裁剪，同时保留分页边界。

    Args:
        rows: 候选消息行列表，通常预先多取一条用于判断是否还有更多数据。
        limit: 单页最大消息数量。
        after_seq: 游标序列号，表示只取该序号之后的消息；为 ``None`` 时表示取末尾页。

    Returns:
        裁剪后的消息列表与是否还有更多数据的布尔标记。
    """
    has_more = len(rows) > limit
    if not has_more:
        return rows, False

    if after_seq is not None:
        return rows[:limit], True

    return rows[-limit:], True
