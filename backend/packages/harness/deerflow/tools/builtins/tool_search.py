"""Tool search:在运行时进行延迟工具发现。

包含:
- :class:`DeferredToolCatalog`:不可变的、可搜索的延迟工具目录。
- :func:`build_tool_search_tool`:以目录作为闭包构造 ``tool_search`` 工具,
  通过 :class:`Command` 把提升操作记录到图状态。
- :func:`build_deferred_tool_setup`:在经过策略过滤的工具列表上装配目录与工具
  (需要在 tool policy 过滤之后调用)。

Agent 在系统提示的 ``<available-deferred-tools>`` 中能看到延迟工具的名字,
但在通过 :func:`tool_search` 拉取完整 schema 之前无法调用。延迟集合随构建时
闭包传递,提升操作写入每线程图状态,没有使用 :class:`ContextVar`。来源无关:
只要工具带有 ``deerflow_mcp`` 元数据标签就视为"延迟"。
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated

from langchain.tools import BaseTool
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.types import Command

from deerflow.tools.mcp_metadata import is_mcp_tool

logger = logging.getLogger(__name__)

MAX_RESULTS = 5  # Max tools returned per search


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    """把 ``pattern`` 编译为大小写不敏感的正则,失败时退化为字面子串匹配。

    搜索查询来自模型,无效正则(如括号不配对)必须退化为字面匹配,而不是抛错。
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


# ── Catalog ──


# NOTE: frozen=True without slots=True keeps __dict__, which is what lets the
# @cached_property fields below cache (they write to instance.__dict__, bypassing
# the frozen __setattr__). Do NOT add slots=True or hash/names break at runtime.
@dataclass(frozen=True)
class DeferredToolCatalog:
    """不可变的延迟工具目录,只做搜索,不做修改。"""

    tools: tuple[BaseTool, ...]

    @cached_property
    def names(self) -> frozenset[str]:
        """目录中所有工具名的不可变集合。"""
        return frozenset(t.name for t in self.tools)

    @cached_property
    def hash(self) -> str:
        """目录内容的稳定短哈希(供图状态做作用域键)。"""
        canon = [{"name": t.name, "schema": convert_to_openai_function(t)} for t in sorted(self.tools, key=lambda t: t.name)]
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def search(self, query: str) -> list[BaseTool]:
        """按多种查询形式搜索目录。

        Args:
            query: 查询字符串,支持 ``select:``、``+required`` 与普通正则三种形式。

        Returns:
            命中工具列表(最多 :data:`MAX_RESULTS` 个)。
        """
        query = query.strip()
        if not query:
            return []

        if query.startswith("select:"):
            wanted = {n.strip() for n in query[7:].split(",")}
            return [t for t in self.tools if t.name in wanted][:MAX_RESULTS]

        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            if not parts:
                return []  # bare "+" with no required token — nothing to require
            required = parts[0].lower()
            candidates = [t for t in self.tools if required in t.name.lower()]
            if len(parts) > 1:
                candidates.sort(key=lambda t: _catalog_regex_score(parts[1], t), reverse=True)
            return candidates[:MAX_RESULTS]

        regex = _compile_catalog_regex(query)
        scored: list[tuple[int, BaseTool]] = []
        for t in self.tools:
            searchable = f"{t.name} {t.description or ''}"
            if regex.search(searchable):
                scored.append((2 if regex.search(t.name) else 1, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored][:MAX_RESULTS]


def _catalog_regex_score(pattern: str, t: BaseTool) -> int:
    """计算 ``+required pattern`` 形式中剩余 pattern 部分对工具的相关性得分。"""
    regex = _compile_catalog_regex(pattern)
    return len(regex.findall(f"{t.name} {t.description or ''}"))


# ── Setup / tool ──


@dataclass(frozen=True)
class DeferredToolSetup:
    """为单个 Agent 构建一次性装配的延迟工具支持。

    三个字段以整体传递,调用方根据 ``tool_search_tool`` 判别:

    - **空** ``(None, frozenset(), None)``:延迟被禁用,或没有 MCP 工具通过策略过滤。
      此时不做延迟,把工具按原样绑定即可。
    - **非空**:`tool_search_tool`` 会追加到 Agent 的工具列表,
      ``deferred_names`` 在被提升前对模型不可见,
      ``catalog_hash`` 在图状态中对提升操作做作用域限定。

    不变量:``tool_search_tool is None`` ⟺ ``deferred_names`` 为空 ⟺ ``catalog_hash is None``。
    """

    tool_search_tool: BaseTool | None
    deferred_names: frozenset[str]
    catalog_hash: str | None


def build_tool_search_tool(catalog: DeferredToolCatalog) -> BaseTool:
    """基于目录闭包构造 ``tool_search`` 工具,允许 Agent 拉取延迟工具 schema。"""

    catalog_hash = catalog.hash

    @tool
    def tool_search(query: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """拉取延迟工具的完整 schema 定义,使其可被调用。

        延迟工具的名字会出现在系统提示的 ``<available-deferred-tools>`` 中,
        在被拉取之前,模型只知道名字。本工具按查询匹配延迟工具,返回命中的完整
        schema,被返回的工具随即变为可调用。

        查询形式:
          - ``select:Read,Edit`` —— 按名字精确拉取
          - ``notebook jupyter`` —— 关键词搜索,最多返回 :data:`MAX_RESULTS` 个最佳匹配
          - ``+slack send`` —— 名字中必须包含 ``slack``,按其余词排序
        """
        matched = catalog.search(query)[:MAX_RESULTS]
        if not matched:
            content, names = f"No tools found matching: {query}", []
        else:
            content = json.dumps([convert_to_openai_function(t) for t in matched], indent=2, ensure_ascii=False)
            names = [t.name for t in matched]
        return Command(
            update={
                "promoted": {"catalog_hash": catalog_hash, "names": names},
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id, name="tool_search")],
            }
        )

    return tool_search


def build_deferred_tool_setup(filtered_tools: list[BaseTool], *, enabled: bool) -> DeferredToolSetup:
    """从经过策略过滤的工具列表构造延迟工具装配。

    必须在 skill/agent 工具策略过滤之后调用,以保证目录绝不会暴露当前 Agent
    不允许使用的工具。

    Args:
        filtered_tools: 已经过策略过滤的可用工具列表。
        enabled: 是否启用延迟功能。

    Returns:
        :class:`DeferredToolSetup` 实例;两种情况下会返回空 setup:
        延迟被禁用,或启用但没有 MCP 工具通过过滤。
    """
    if not enabled:
        # Deferral disabled: defer nothing; the model binds every tool as before.
        return DeferredToolSetup(None, frozenset(), None)
    deferred = [t for t in filtered_tools if is_mcp_tool(t)]
    if not deferred:
        # Enabled, but no MCP tool to defer: same empty result, different reason.
        return DeferredToolSetup(None, frozenset(), None)
    catalog = DeferredToolCatalog(tuple(deferred))
    return DeferredToolSetup(build_tool_search_tool(catalog), catalog.names, catalog.hash)
