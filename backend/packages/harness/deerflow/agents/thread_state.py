"""LangGraph ``AgentState`` 的 DeerFlow 线程状态扩展及配套 Reducer。

定义 Lead Agent 运行所需的线程级状态字段（如沙箱、标题、产物、待办、上传/已查看图片、
延迟工具提升集合），并提供对应 Reducer 控制这些字段的合并/清空语义。
"""

from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    """线程关联的沙箱状态。"""

    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    """线程数据目录路径集合。"""

    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """已查看图片的内联数据。"""

    base64: str
    mime_type: str


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """``artifacts`` 列表的 Reducer：合并并去重。"""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # dict.fromkeys 可以在保持顺序的同时去重。
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """``viewed_images`` 字典的 Reducer：合并图片字典。

    特殊情况：若 ``new`` 为空字典 ``{}``，则清空已有图片。
    借助该行为，中间件在处理完图片后可以清除 ``viewed_images`` 状态。
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # 空字典是显式清空信号，用于图片上下文被消费后的状态重置。
    if len(new) == 0:
        return {}
    # 同一路径图片以新值覆盖旧值。
    return {**existing, **new}


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """``todos`` 列表的 Reducer：保留最后一个非 None 值。

    语义：
    - 当 ``new`` 为 ``None``（节点未修改 todos）时，保留 ``existing``。
    - 当 ``new`` 非空（包括空列表）时，表示一次显式更新，将覆盖 ``existing``。
    """
    if new is None:
        return existing
    return new


class PromotedTools(TypedDict):
    """延迟工具提升集合，附带目录哈希以区分不同工具目录。"""

    catalog_hash: str
    names: list[str]


def merge_promoted(existing: PromotedTools | None, new: PromotedTools | None) -> PromotedTools | None:
    """延迟工具提升集合的 Reducer：按目录哈希作用域合并。

    - ``new`` 为 None/空 → 保留 ``existing``（节点未修改提升集合）。
    - ``catalog_hash`` 发生变化 → 整体替换，丢弃旧名字（避免在目录漂移后，
      持久化的旧名字误指向同名但功能不同的工具）。
    - ``catalog_hash`` 相同 → 对名字取并集并去重，保持顺序。
    """
    if not new:
        return existing
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }


class ThreadState(AgentState):
    """DeerFlow 的 LangGraph 线程状态，扩展自 ``AgentState``。

    关键字段：
    - ``sandbox``/``thread_data``：当前线程关联的沙箱与目录路径。
    - ``title``：线程标题（由 ``TitleMiddleware`` 自动生成）。
    - ``artifacts``：用户可见的产物路径列表（去重合并）。
    - ``todos``：当前计划模式下的待办列表。
    - ``uploaded_files``：本轮上传文件元数据列表。
    - ``viewed_images``：模型已内联查看的图片字典，键为 ``image_path``。
    - ``promoted``：当前目录哈希下已被提升的延迟（MCP）工具名字集合。
    """

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path 映射到 {base64, mime_type}
    promoted: Annotated[PromotedTools | None, merge_promoted]
