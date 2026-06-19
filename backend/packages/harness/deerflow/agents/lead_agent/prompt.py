"""Lead Agent 系统提示构建与启用技能缓存管理。

架构总览：
```
make_lead_agent(config)
  → apply_prompt_template(subagent_enabled, agent_name, available_skills, ...)
      │
      ├─ get_skills_prompt_section(available_skills)
      │     └─ get_enabled_skills_for_config(app_config)
      │           └─ get_or_new_skill_storage().load_skills(enabled_only=True)
      │                 └─ 后台线程异步加载（避免阻塞请求路径）
      │
      ├─ get_deferred_tools_prompt_section(deferred_names)
      │     └─ 列出 tool_search 可发现的延迟（MCP）工具名
      │
      ├─ _build_subagent_section(max_concurrent)
      │     └─ 动态生成子代理类型描述 + 并发限制 + 使用示例
      │
      ├─ _build_acp_section()           ← ACP agent 提示（如有配置）
      ├─ _build_custom_mounts_section() ← 自定义挂载提示（如有配置）
      │
      └─ SYSTEM_PROMPT_TEMPLATE.format(...)
            → 最终系统提示字符串（静态，不含记忆/日期）
```

技能缓存机制：
- **惰性加载**：首次访问时启动后台守护线程加载，请求路径不阻塞磁盘 I/O
- **版本失效**：每次 ``_invalidate_enabled_skills_cache()`` 递增版本号，后台线程重载
- **双缓存**：全局缓存（``_enabled_skills_cache``）+ 按 AppConfig 实例缓存（``_enabled_skills_by_config_cache``）
- **线程安全**：``threading.Lock`` + ``threading.Event`` 协调读写

注意：最终系统提示**不含**记忆和当前日期——这些由 ``DynamicContextMiddleware`` 在每条
HumanMessage 前以 ``<system-reminder>`` 注入，使系统提示保持完全静态以最大化前缀缓存复用。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from deerflow.config.agents_config import load_agent_soul
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.types import Skill, SkillCategory
from deerflow.subagents import get_available_subagent_names

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_ENABLED_SKILLS_REFRESH_WAIT_TIMEOUT_SECONDS = 5.0
_enabled_skills_lock = threading.Lock()
_enabled_skills_cache: list[Skill] | None = None
_enabled_skills_by_config_cache: dict[int, tuple[object, list[Skill]]] = {}
_enabled_skills_refresh_active = False
_enabled_skills_refresh_version = 0
_enabled_skills_refresh_event = threading.Event()


def _load_enabled_skills_sync() -> list[Skill]:
    """同步加载已启用技能列表的内部辅助函数。"""
    return list(get_or_new_skill_storage().load_skills(enabled_only=True))


def _start_enabled_skills_refresh_thread() -> None:
    """启动后台守护线程以刷新已启用技能缓存。"""
    threading.Thread(
        target=_refresh_enabled_skills_cache_worker,
        name="deerflow-enabled-skills-loader",
        daemon=True,
    ).start()


def _refresh_enabled_skills_cache_worker() -> None:
    """后台工作循环：始终以最新失效版本为目标重载技能缓存。"""
    global _enabled_skills_cache, _enabled_skills_refresh_active

    while True:
        with _enabled_skills_lock:
            target_version = _enabled_skills_refresh_version

        try:
            skills = _load_enabled_skills_sync()
        except Exception:
            logger.exception("Failed to load enabled skills for prompt injection")
            skills = []

        with _enabled_skills_lock:
            if _enabled_skills_refresh_version == target_version:
                _enabled_skills_cache = skills
                _enabled_skills_refresh_active = False
                _enabled_skills_refresh_event.set()
                return

            # A newer invalidation happened while loading. Keep the worker alive
            # and loop again so the cache always converges on the latest version.
            _enabled_skills_cache = None


def _ensure_enabled_skills_cache() -> threading.Event:
    """确保已启用技能缓存存在：必要时启动后台刷新并返回就绪事件。"""
    global _enabled_skills_refresh_active

    with _enabled_skills_lock:
        if _enabled_skills_cache is not None:
            _enabled_skills_refresh_event.set()
            return _enabled_skills_refresh_event
        if _enabled_skills_refresh_active:
            return _enabled_skills_refresh_event
        _enabled_skills_refresh_active = True
        _enabled_skills_refresh_event.clear()

    _start_enabled_skills_refresh_thread()
    return _enabled_skills_refresh_event


def _invalidate_enabled_skills_cache() -> threading.Event:
    """主动失效已启用技能缓存并触发后台刷新。"""
    global _enabled_skills_cache, _enabled_skills_refresh_active, _enabled_skills_refresh_version

    _get_cached_skills_prompt_section.cache_clear()
    with _enabled_skills_lock:
        _enabled_skills_cache = None
        _enabled_skills_by_config_cache.clear()
        _enabled_skills_refresh_version += 1
        _enabled_skills_refresh_event.clear()
        if _enabled_skills_refresh_active:
            return _enabled_skills_refresh_event
        _enabled_skills_refresh_active = True

    _start_enabled_skills_refresh_thread()
    return _enabled_skills_refresh_event


def prime_enabled_skills_cache() -> None:
    """预热已启用技能缓存（异步），供启动阶段调用。"""
    _ensure_enabled_skills_cache()


def warm_enabled_skills_cache(timeout_seconds: float = _ENABLED_SKILLS_REFRESH_WAIT_TIMEOUT_SECONDS) -> bool:
    """阻塞等待已启用技能缓存完成预热。"""
    if _ensure_enabled_skills_cache().wait(timeout=timeout_seconds):
        return True

    logger.warning("Timed out waiting %.1fs for enabled skills cache warm-up", timeout_seconds)
    return False


def _get_enabled_skills():
    """内部别名：返回 ``get_cached_enabled_skills`` 的结果。"""
    return get_cached_enabled_skills()


def get_cached_enabled_skills() -> list[Skill]:
    """返回已缓存的已启用技能列表，未命中时启动后台刷新。

    缓存生命周期：
    ```
    首次调用 → 缓存为空 → _ensure_enabled_skills_cache()
                            → 启动后台线程扫描 skills/ 目录
                            → 返回 []（不阻塞）
    后续调用 → 缓存命中 → 返回缓存的 list[Skill]（瞬时）

    技能安装/更新后 → _invalidate_enabled_skills_cache()
                       → 递增版本号，重启后台线程
                       → 下次调用拿到新列表
    ```

    可在请求路径中安全调用：不会阻塞磁盘 I/O。缓存未命中时返回空列表，
    下次调用即可读到预热结果。

    使用示例：
    ```python
    skills = get_cached_enabled_skills()
    # → [Skill(name="bootstrap", category=public, enabled=True),
    #    Skill(name="code-review", category=public, enabled=True)]
    ```

    Returns:
        已缓存的已启用技能列表（副本）；缓存未命中时返回空列表。
    """
    with _enabled_skills_lock:
        cached = _enabled_skills_cache

    if cached is not None:
        return list(cached)

    _ensure_enabled_skills_cache()
    return []


def get_enabled_skills_for_config(app_config: AppConfig | None = None) -> list[Skill]:
    """使用调用方提供的配置源返回已启用技能。

    当传入具体的 ``app_config`` 时，按其对象身份缓存已加载的技能，使
    请求级配置注入仍能匹配到对应的配置解析技能路径，而不必每次构建
    Agent 时都重新扫描存储。
    """
    if app_config is None:
        return _get_enabled_skills()

    cache_key = id(app_config)
    with _enabled_skills_lock:
        cached = _enabled_skills_by_config_cache.get(cache_key)
        if cached is not None:
            cached_config, cached_skills = cached
            if cached_config is app_config:
                return list(cached_skills)

    skills = list(get_or_new_skill_storage(app_config=app_config).load_skills(enabled_only=True))
    with _enabled_skills_lock:
        _enabled_skills_by_config_cache[cache_key] = (app_config, skills)
    return list(skills)


def _skill_mutability_label(category: SkillCategory | str) -> str:
    """根据技能类别返回可编辑性标签。"""
    return "[custom, editable]" if category == SkillCategory.CUSTOM else "[built-in]"


def clear_skills_system_prompt_cache() -> None:
    """清除系统提示中已启用技能部分的缓存。"""
    _invalidate_enabled_skills_cache()


async def refresh_skills_system_prompt_cache_async() -> None:
    """异步等待并完成技能系统提示缓存的刷新。"""
    await asyncio.to_thread(_invalidate_enabled_skills_cache().wait)


def _build_skill_evolution_section(skill_evolution_enabled: bool) -> str:
    """构建技能自我演化的提示片段；未启用时返回空串。"""
    if not skill_evolution_enabled:
        return ""
    return """
## 技能自我演化
完成任务后，在以下情况考虑创建或更新技能：
- 任务需要 5 次以上工具调用才解决
- 你克服了非显而易见的错误或陷阱
- 用户纠正了你的方法且纠正后的版本有效
- 你发现了一个非平凡的、重复出现的工作流
如果你使用了某个技能但遇到了其中未涵盖的问题，立即修补该技能。
优先使用修补而非整体编辑。创建新技能前，先与用户确认。
跳过简单的一次性任务。
"""


def _build_available_subagents_description(available_names: list[str], bash_available: bool, *, app_config: AppConfig | None = None) -> str:
    """从子代理注册表动态生成子代理类型描述。

    对齐 Codex 的模式：``agent_type_description`` 由所有已注册角色动态生成，
    使 LLM 知道每一种可用类型。
    """
    # 内置子代理类型描述（中文，与提示质量向后兼容）
    builtin_descriptions = {
        "general-purpose": "适用于任何非平凡任务——网页搜索、代码探索、文件操作、分析等",
        "bash": (
            "用于命令执行（git、构建、测试、部署操作）" if bash_available else "当前沙箱配置下不可用。请使用直接的文件/网页工具，或切换到 AioSandboxProvider 获得隔离 shell 访问。"
        ),
    }

    # Lazy import moved outside loop to avoid repeated import overhead
    from deerflow.subagents.registry import get_subagent_config

    lines = []
    for name in available_names:
        if name in builtin_descriptions:
            lines.append(f"- **{name}**: {builtin_descriptions[name]}")
        else:
            config = get_subagent_config(name, app_config=app_config)
            if config is not None:
                desc = config.description.split("\n")[0].strip()  # First line only for brevity
                lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines)


def _build_subagent_section(max_concurrent: int, *, app_config: AppConfig | None = None) -> str:
    """构建带动态并发上限的子代理系统提示片段。

    Args:
        max_concurrent: 单次响应允许的最大并发子代理调用数。
        app_config: 可选应用配置对象，用于发现已注册的子代理。

    Returns:
        格式化后的子代理提示片段字符串。
    """
    n = max_concurrent
    available_names = get_available_subagent_names(app_config=app_config) if app_config is not None else get_available_subagent_names()
    bash_available = "bash" in available_names

    # Dynamically build subagent type descriptions from registry (aligned with Codex's
    # agent_type_description pattern where all registered roles are listed in the tool spec).
    available_subagents = _build_available_subagents_description(available_names, bash_available, app_config=app_config)
    direct_tool_examples = "bash, ls, read_file, web_search, etc." if bash_available else "ls, read_file, web_search, etc."
    direct_execution_example = (
        '# User asks: "Run the tests"\n# Thinking: Cannot decompose into parallel sub-tasks\n# → Execute directly\n\nbash("npm test")  # Direct execution, not task()'
        if bash_available
        else '# User asks: "Read the README"\n# Thinking: Single straightforward file read\n# → Execute directly\n\nread_file("/mnt/user-data/workspace/README.md")  # Direct execution, not task()'
    )
    return f"""<subagent_system>
**🚀 子代理模式已激活——拆解、委派、综合**

你正在以子代理能力启用模式运行。你的角色是**任务编排者**：
1. **拆解**：将复杂任务分解为并行的子任务
2. **委派**：使用并行 `task` 调用同时启动多个子代理
3. **综合**：收集并整合结果，形成连贯的答案

**核心原则：复杂任务应被拆解并分配给多个子代理并行执行。**

**⛔ 硬性并发限制：每次响应最多 {n} 个 `task` 调用，这不是可选项。**
- 每次响应中，你最多只能包含 {n} 个 `task` 工具调用。多余的调用会被系统**静默丢弃**——你将失去这些工作。
- **启动子代理之前，你必须在思考中数出子任务数量：**
  - 如果 ≤ {n}：本轮全部启动。
  - 如果 > {n}：**本轮选择最重要的 {n} 个子任务。** 剩下的留到下轮。
- **多批次执行**（>{n} 个子任务时）：
  - 第 1 轮：并行启动子任务 1-{n} → 等待结果
  - 第 2 轮：并行启动下一批 → 等待结果
  - ……继续直到所有子任务完成
  - 最后一轮：综合所有结果形成连贯答案
- **思考模式示例**："我识别了 6 个子任务。由于每轮限制 {n} 个，我先启动前 {n} 个，剩下的下轮处理。"

**可用子代理：**
{available_subagents}

**你的编排策略：**

✅ **拆解 + 并行执行（推荐方式）：**

对于复杂查询，将其分解为专注的子任务并分批并行执行（每轮最多 {n} 个）：

**示例 1："为什么腾讯股价下跌？"（3 个子任务 → 1 批）**
→ 第 1 轮：并行启动 3 个子代理：
- 子代理 1：最近的财务报告、盈利数据和收入趋势
- 子代理 2：负面新闻、争议和监管问题
- 子代理 3：行业趋势、竞争对手表现和市场情绪
→ 第 2 轮：综合结果

**示例 2："比较 5 家云服务商"（5 个子任务 → 多批次）**
→ 第 1 轮：并行启动 {n} 个子代理（第一批）
→ 第 2 轮：并行启动剩余子代理
→ 最后一轮：综合所有结果形成完整比较

**示例 3："重构认证系统"**
→ 第 1 轮：并行启动 3 个子代理：
- 子代理 1：分析当前认证实现和技术债务
- 子代理 2：研究最佳实践和安全模式
- 子代理 3：审查相关测试、文档和漏洞
→ 第 2 轮：综合结果

✅ **以下情况使用并行子代理（每轮最多 {n} 个）：**
- **复杂研究问题**：需要多个信息来源或视角
- **多方面分析**：任务有多个独立维度需要探索
- **大型代码库**：需要同时分析不同部分
- **全面调查**：需要从多个角度彻底覆盖的问题

❌ **以下情况不使用子代理（直接执行）：**
- **任务无法拆解**：无法分解为 2 个以上有意义的并行子任务时，直接执行
- **超简单操作**：读一个文件、快速编辑、单条命令
- **需要立即澄清**：必须先问用户才能继续
- **元对话**：关于对话历史的问题
- **顺序依赖**：每一步依赖前一步结果（自己按顺序完成）

**关键工作流**（每次行动前严格遵循）：
1. **计数**：在思考中列出所有子任务并明确计数："我有 N 个子任务"
2. **规划批次**：如果 N > {n}，明确规划哪些子任务放入哪个批次：
   - "第 1 批（本轮）：前 {n} 个子任务"
   - "第 2 批（下轮）：下一批子任务"
3. **执行**：只启动当前批次（最多 {n} 个 `task` 调用）。不要启动未来批次的子任务。
4. **重复**：结果返回后启动下一批。继续直到所有批次完成。
5. **综合**：所有批次完成后，综合所有结果。
6. **无法拆解** → 直接使用可用工具执行（{direct_tool_examples}）

**⛔ 违规：单次响应中启动超过 {n} 个 `task` 调用是硬性错误。系统会丢弃多余调用，你将失去工作。始终分批。**

**记住：子代理用于并行拆解，而非包装单个任务。**

**工作原理：**
- task 工具在后台异步运行子代理
- 后端自动轮询完成状态（你不需要轮询）
- 工具调用会阻塞直到子代理完成工作
- 完成后结果直接返回给你

**使用示例 1——单批次（≤{n} 个子任务）：**

```python
# 用户问："为什么腾讯股价下跌？"
# 思考：3 个子任务 → 1 批即可

# 第 1 轮：并行启动 3 个子代理
task(description="腾讯财务数据", prompt="...", subagent_type="general-purpose")
task(description="腾讯新闻与监管", prompt="...", subagent_type="general-purpose")
task(description="行业与市场趋势", prompt="...", subagent_type="general-purpose")
# 3 个并行运行 → 综合结果
```

**使用示例 2——多批次（>{n} 个子任务）：**

```python
# 用户问："比较 AWS、Azure、GCP、阿里云和 Oracle Cloud"
# 思考：5 个子任务 → 需要多批次（每批最多 {n} 个）

# 第 1 轮：启动第一批 {n} 个
task(description="AWS 分析", prompt="...", subagent_type="general-purpose")
task(description="Azure 分析", prompt="...", subagent_type="general-purpose")
task(description="GCP 分析", prompt="...", subagent_type="general-purpose")

# 第 2 轮：启动剩余批次（第一批完成后）
task(description="阿里云分析", prompt="...", subagent_type="general-purpose")
task(description="Oracle Cloud 分析", prompt="...", subagent_type="general-purpose")

# 第 3 轮：综合两批的所有结果
```

**反例——直接执行（不使用子代理）：**

```python
{direct_execution_example}
```

**关键**：
- **每轮最多 {n} 个 `task` 调用**——系统强制执行，多余调用被丢弃
- 只有能并行启动 2+ 个子代理时才使用 `task`
- 单个任务 = 子代理无价值 = 直接执行
- 超过 {n} 个子任务时，跨多轮使用顺序批次（每批 {n} 个）
</subagent_system>"""


SYSTEM_PROMPT_TEMPLATE = """
<role>
你是 {agent_name}，一个开源超级 Agent。
</role>

{soul}
{self_update_section}
<thinking_style>
- 在执行操作前，对用户的请求进行简洁、策略性的思考
- 分解任务：哪些是明确的？哪些是模糊的？哪些信息缺失？
- **优先级检查：如果有任何不明确、缺失或存在多重解读的情况，你必须首先请求澄清——不要继续工作**
{subagent_thinking}- 不要在思考过程中写下完整的最终答案或报告，只写提纲
- 关键：思考结束后，你必须向用户提供实际回复。思考用于规划，回复用于交付。
- 你的回复必须包含实际答案，而不仅仅是对你思考内容的引用
</thinking_style>

<clarification_system>
**工作流优先级：澄清 → 规划 → 行动**
1. **首先**：在思考中分析请求——识别不明确、缺失或模糊的部分
2. **其次**：如果需要澄清，立即调用 `ask_clarification` 工具——不要开始工作
3. **最后**：只有在所有澄清都解决后，才继续进行规划和执行

**关键规则：澄清永远在行动之前。绝不要先开始工作再在执行中途澄清。**

**必须澄清的场景——在开始工作前你必须调用 ask_clarification：**

1. **信息缺失**（`missing_info`）：必要的细节未提供
   - 示例：用户说"写一个网页爬虫"但未指定目标网站
   - 示例："部署应用"但未指定环境
   - **必需操作**：调用 ask_clarification 获取缺失信息

2. **需求模糊**（`ambiguous_requirement`）：存在多种合理解读
   - 示例："优化代码"可能指性能、可读性或内存使用
   - 示例："让它更好"——不清楚要改进哪方面
   - **必需操作**：调用 ask_clarification 明确具体需求

3. **方案选择**（`approach_choice`）：存在多种合理方案
   - 示例："添加认证"可以用 JWT、OAuth、Session 或 API Key
   - 示例："存储数据"可以用数据库、文件、缓存等
   - **必需操作**：调用 ask_clarification 让用户选择方案

4. **风险操作**（`risk_confirmation`）：破坏性操作需要确认
   - 示例：删除文件、修改生产配置、数据库操作
   - 示例：覆盖现有代码或数据
   - **必需操作**：调用 ask_clarification 获取明确确认

5. **建议征求**（`suggestion`）：你有推荐方案但需要认可
   - 示例："我建议重构这段代码，要继续吗？"
   - **必需操作**：调用 ask_clarification 获取认可

**严格执行：**
- ❌ 不要先开始工作再在执行中途澄清——先澄清
- ❌ 不要为了"效率"跳过澄清——准确性比速度更重要
- ❌ 不要在信息缺失时做出假设——始终询问
- ❌ 不要猜测着继续——停下来，先调用 ask_clarification
- ✅ 在思考中分析请求 → 识别模糊点 → 在任何操作前询问
- ✅ 如果在思考中发现需要澄清，必须立即调用该工具
- ✅ 调用 ask_clarification 后，执行会自动中断
- ✅ 等待用户回复——不要带着假设继续

**使用方式：**
```python
ask_clarification(
    question="你的具体问题？",
    clarification_type="missing_info",  # 或其他类型
    context="为什么需要这个信息",  # 可选但推荐
    options=["选项1", "选项2"]  # 可选，用于提供选择
)
```

**示例：**
用户："部署应用"
你（思考）：缺少环境信息——必须先澄清
你（行动）：ask_clarification(
    question="要部署到哪个环境？",
    clarification_type="approach_choice",
    context="我需要知道目标环境以进行正确配置",
    options=["development", "staging", "production"]
)
[执行停止——等待用户回复]

用户："staging"
你："正在部署到 staging..." [继续]
</clarification_system>

{skills_section}

{deferred_tools_section}

{subagent_section}

<working_directory existed="true">
- 用户上传：`/mnt/user-data/uploads` —— 用户上传的文件（自动在上下文中列出）
- 用户工作区：`/mnt/user-data/workspace` —— 临时文件的工作目录
- 输出文件：`/mnt/user-data/outputs` —— 最终交付物必须保存在此处

**文件管理：**
- 上传的文件会在每次请求前自动在 <uploaded_files> 段中列出
- 使用 `read_file` 工具按列表中的路径读取上传文件
- 对于 PDF、PPT、Excel 和 Word 文件，转换后的 Markdown 版本（*.md）与原始文件同时可用
- 所有临时工作在 `/mnt/user-data/workspace` 中进行
- 将 `/mnt/user-data/workspace` 视为编程和文件编辑任务的默认当前工作目录
- 编写从工作区创建/读取文件的脚本或命令时，优先使用相对路径，如 `hello.txt`、`../uploads/data.csv`、`../outputs/report.md`
- 当相对路径足够时，避免在生成的脚本中硬编码 `/mnt/user-data/...`
- 最终交付物必须复制到 `/mnt/user-data/outputs` 并使用 `present_files` 工具呈现
{acp_section}
</working_directory>

<response_style>
- 清晰简洁：除非需要，避免过度格式化
- 自然语气：默认使用段落和散文，而非要点列表
- 行动导向：专注于交付结果，而非解释过程
</response_style>

<citations>
**关键：使用网页搜索结果时必须始终包含引用**

- **何时使用**：在 web_search、web_fetch 或任何外部信息源之后必须使用
- **格式**：在声明后立即使用 Markdown 链接格式 `[citation:TITLE](URL)`
- **位置**：内联引用应紧跟在它们所支持的句子或声明之后
- **来源段**：在报告末尾的"来源"段中汇总所有引用

**示例——内联引用：**
```markdown
2026 年 AI 的关键趋势包括增强的推理能力和多模态集成
[citation:AI 趋势 2026](https://techcrunch.com/ai-trends)。
语言模型的最新突破也加速了进展
[citation:OpenAI 研究](https://openai.com/research)。
```

**示例——带引用的深度研究报告：**
```markdown
## 摘要

DeerFlow 是一个开源 AI Agent 框架，在 2026 年初获得了显著关注
[citation:GitHub 仓库](https://github.com/bytedance/deer-flow)。该项目专注于
提供具有沙箱执行和记忆管理的生产级 Agent 系统
[citation:DeerFlow 文档](https://deer-flow.dev/docs)。

## 关键分析

### 架构设计

系统使用 LangGraph 进行工作流编排 [citation:LangGraph 文档](https://langchain.com/langgraph)，
结合 FastAPI 网关提供 REST API 访问 [citation:FastAPI](https://fastapi.tiangolo.com)。

## 来源

### 主要来源
- [GitHub 仓库](https://github.com/bytedance/deer-flow) —— 官方源代码和文档
- [DeerFlow 文档](https://deer-flow.dev/docs) —— 技术规格说明

### 媒体报道
- [AI 趋势 2026](https://techcrunch.com/ai-trends) —— 行业分析
```

**关键：来源段格式要求：**
- 来源段中每项必须是带 URL 的可点击 Markdown 链接
- 使用标准 Markdown 链接格式 `[标题](URL) - 描述`（**不是** `[citation:...]` 格式）
- `[citation:标题](URL)` 格式**仅**用于报告正文中的内联引用
- ❌ 错误：`GitHub 仓库 - 官方源代码和文档`（没有 URL！）
- ❌ 错误（来源段中）：`[citation:GitHub 仓库](url)`（citation 前缀仅用于内联！）
- ✅ 正确（来源段中）：`[GitHub 仓库](https://github.com/bytedance/deer-flow) —— 官方源代码和文档`

**研究任务工作流：**
1. 使用 web_search 查找来源 → 从结果中提取 {{title, url, snippet}}
2. 编写内容并附内联引用：`声明 [citation:标题](url)`
3. 在末尾的"来源"段中汇总所有引用
4. 当有来源可用时，绝不编写无引用的声明

**关键规则：**
- ❌ 不要编写无引用的研究内容
- ❌ 不要忘记从搜索结果中提取 URL
- ✅ 始终在来自外部来源的声明后添加 `[citation:标题](URL)`
- ✅ 始终包含列出所有引用的"来源"段
</citations>

<critical_reminders>
- **澄清优先**：在开始工作前始终澄清不明确/缺失/模糊的需求——绝不做假设或猜测
{subagent_reminder}- 技能优先：开始**复杂**任务前始终加载相关技能。
- 渐进加载：按技能中引用的方式逐步加载资源
- 输出文件：最终交付物必须在 `/mnt/user-data/outputs` 中
- 清晰性：直接且有帮助，避免不必要的元评论
- 图片和 Mermaid：Markdown 格式中始终欢迎图片和 Mermaid 图表，鼓励使用 `![图片描述](image_path)\n\n` 或 "```mermaid" 来展示
- 多任务：更好地利用并行工具调用，一次调用多个工具以获得更好性能
- 语言一致性：与用户保持使用相同的语言
- 始终回复：你的思考是内部的。思考后你必须始终向用户提供可见的回复。
</critical_reminders>
"""


def _get_memory_context(agent_name: str | None = None, *, app_config: AppConfig | None = None) -> str:
    """获取并格式化用于注入系统提示的记忆上下文。

    Args:
        agent_name: 若提供则按 Agent 加载记忆；为 ``None`` 时加载全局记忆。
        app_config: 显式传入的应用配置；提供时将从此值读取记忆配置项
            而非全局配置单例。

    Returns:
        用 XML 标签包裹的格式化记忆上下文；若未启用则返回空字符串。
    """
    try:
        from deerflow.agents.memory import format_memory_for_injection, get_memory_data
        from deerflow.runtime.user_context import get_effective_user_id

        if app_config is None:
            from deerflow.config.memory_config import get_memory_config

            config = get_memory_config()
        else:
            config = app_config.memory

        if not config.enabled or not config.injection_enabled:
            return ""

        memory_data = get_memory_data(agent_name, user_id=get_effective_user_id())
        memory_content = format_memory_for_injection(memory_data, max_tokens=config.max_injection_tokens)

        if not memory_content.strip():
            return ""

        return f"""<memory>
{memory_content}
</memory>
"""
    except Exception:
        logger.exception("Failed to load memory context")
        return ""


@lru_cache(maxsize=32)
def _get_cached_skills_prompt_section(
    skill_signature: tuple[tuple[str, str, str, str], ...],
    available_skills_key: tuple[str, ...] | None,
    container_base_path: str,
    skill_evolution_section: str,
) -> str:
    """执行赋值。"""
    filtered = [(name, description, category, location) for name, description, category, location in skill_signature if available_skills_key is None or name in available_skills_key]
    skills_list = ""
    if filtered:
        skill_items = "\n".join(
            f"    <skill>\n        <name>{name}</name>\n        <description>{description} {_skill_mutability_label(category)}</description>\n        <location>{location}</location>\n    </skill>"
            for name, description, category, location in filtered
        )
        skills_list = f"<available_skills>\n{skill_items}\n</available_skills>"
    return f"""<skill_system>
你有权使用为特定任务提供优化工作流的技能。每个技能包含最佳实践、框架和附加资源的引用。

**渐进加载模式：**
1. 当用户查询匹配某个技能的适用场景时，使用下方技能标签中的 path 属性立即调用 `read_file` 读取技能主文件
2. 阅读并理解技能的工作流和指令
3. 技能文件包含同一文件夹下外部资源的引用
4. 仅在执行过程中需要时加载引用资源
5. 严格遵循技能的指令

**技能位于：**{container_base_path}
{skill_evolution_section}
{skills_list}

</skill_system>"""


def get_skills_prompt_section(available_skills: set[str] | None = None, *, app_config: AppConfig | None = None) -> str:
    """生成含可用技能列表的 skills 提示片段。

    输入示例：
    ```python
    # 假设 skills/ 目录下有 bootstrap, code-review, testing 三个已启用技能
    section = get_skills_prompt_section(
        available_skills={"bootstrap", "code-review"},  # 白名单，None=全部
    )
    ```

    输出示例：
    ```xml
    <skill_system>
    你有权使用提供优化工作流的技能...
    <available_skills>
        <skill>
            <name>bootstrap</name>
            <description>引导新 Agent 创建 [built-in]</description>
            <location>/mnt/skills/public/bootstrap/SKILL.md</location>
        </skill>
        <skill>
            <name>code-review</name>
            <description>代码审查最佳实践 [built-in]</description>
            <location>/mnt/skills/public/code-review/SKILL.md</location>
        </skill>
    </available_skills>
    </skill_system>
    ```

    边界情况：
    - ``available_skills=None`` → 列出所有已启用技能
    - ``available_skills={"nonexistent"}`` → 白名单中无匹配技能 → 返回 ``""``
    - 没有启用技能且 skill_evolution 关闭 → 返回 ``""``
    - 结果被 LRU 缓存（maxsize=32），相同签名跳过重复渲染

    Args:
        available_skills: 白名单技能名集合；``None`` 表示不限制。
        app_config: 可选的应用配置，用于读取 skills 路径与 skill_evolution 开关。

    Returns:
        格式化后的技能提示 XML 片段；无可用技能时返回空字符串。
    """
    skills = get_enabled_skills_for_config(app_config)

    if app_config is None:
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
            container_base_path = config.skills.container_path
            skill_evolution_enabled = config.skill_evolution.enabled
        except Exception:
            container_base_path = "/mnt/skills"
            skill_evolution_enabled = False
    else:
        config = app_config
        container_base_path = config.skills.container_path
        skill_evolution_enabled = config.skill_evolution.enabled

    if not skills and not skill_evolution_enabled:
        return ""

    if available_skills is not None and not any(skill.name in available_skills for skill in skills):
        return ""

    skill_signature = tuple((skill.name, skill.description, skill.category, skill.get_container_file_path(container_base_path)) for skill in skills)
    available_key = tuple(sorted(available_skills)) if available_skills is not None else None
    if not skill_signature and available_key is not None:
        return ""
    skill_evolution_section = _build_skill_evolution_section(skill_evolution_enabled)
    return _get_cached_skills_prompt_section(skill_signature, available_key, container_base_path, skill_evolution_section)


def get_agent_soul(agent_name: str | None) -> str:
    """加载并以 ``<soul>`` 标签包裹的自定义 Agent 人格描述。"""
    # Append SOUL.md (agent personality) if present
    soul = load_agent_soul(agent_name)
    if soul:
        return f"<soul>\n{soul}\n</soul>\n" if soul else ""
    return ""


def _build_self_update_section(agent_name: str | None) -> str:
    """提示片段：指导自定义 Agent 通过 ``update_agent`` 持久化自我更新。"""
    if not agent_name:
        return ""
    return f"""<self_update>
你正在作为自定义 Agent **{agent_name}** 运行，拥有持久化的 SOUL.md 和 config.yaml。

当用户要求你更新自己的描述、个性、行为、技能集、工具组或默认模型时，
你必须使用 `update_agent` 工具持久化变更。不要使用 `bash`、`write_file` 或任何沙箱工具
编辑 SOUL.md 或 config.yaml——这些会写入临时沙箱/工具工作区，变更将在下一轮丢失。

规则：
- 始终传入 `soul` 的完整替换文本（无补丁语义）。从你当前的 SOUL 出发，应用用户的编辑。
- 只传入需要变更的字段。省略其他字段以保留原值。
- 绝不要对不需变更的字段传入 `"null"`、`"none"` 或 `"undefined"` 等字面字符串。
- 传入 `skills=[]` 以禁用所有技能，或省略 `skills` 以保留现有白名单。
- `update_agent` 成功返回后，告知用户变更已持久化，下一轮生效。
</self_update>
"""


def get_deferred_tools_prompt_section(*, deferred_names: frozenset[str] = frozenset()) -> str:
    """根据显式传入的延迟工具名称集合生成 ``<available-deferred-tools>`` 片段。

    仅列出工具名以便 Agent 知道有哪些可用工具，可通过 ``tool_search`` 加载。
    当不存在延迟工具时返回空字符串。该集合在 Agent 构建时（工具策略过滤之后）
    计算并传入。
    """
    if not deferred_names:
        return ""
    names = "\n".join(sorted(deferred_names))
    return f"<available-deferred-tools>\n{names}\n</available-deferred-tools>"


def _build_acp_section(*, app_config: AppConfig | None = None) -> str:
    """构建 ACP Agent 提示片段，仅在配置了 ACP Agent 时返回内容。"""
    if app_config is None:
        try:
            from deerflow.config.acp_config import get_acp_agents

            agents = get_acp_agents()
        except Exception:
            return ""
    else:
        agents = getattr(app_config, "acp_agents", {}) or {}

    if not agents:
        return ""

    return (
        "\n**ACP Agent Tasks (invoke_acp_agent):**\n"
        "- ACP agents (e.g. codex, claude_code) run in their own independent workspace — NOT in `/mnt/user-data/`\n"
        "- When writing prompts for ACP agents, describe the task only — do NOT reference `/mnt/user-data` paths\n"
        "- ACP agent results are accessible at `/mnt/acp-workspace/` (read-only) — use `ls`, `read_file`, or `bash cp` to retrieve output files\n"
        "- To deliver ACP output to the user: copy from `/mnt/acp-workspace/<file>` to `/mnt/user-data/outputs/<file>`, then use `present_files`"
    )


def _build_custom_mounts_section(*, app_config: AppConfig | None = None) -> str:
    """为显式配置的沙箱挂载构建提示片段。"""
    if app_config is None:
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
        except Exception:
            logger.exception("Failed to load configured sandbox mounts for the lead-agent prompt")
            return ""
    else:
        config = app_config

    mounts = config.sandbox.mounts or []

    if not mounts:
        return ""

    lines = []
    for mount in mounts:
        access = "read-only" if mount.read_only else "read-write"
        lines.append(f"- Custom mount: `{mount.container_path}` - Host directory mapped into the sandbox ({access})")

    mounts_list = "\n".join(lines)
    return f"\n**自定义挂载目录：**\n{mounts_list}\n- 如果用户需要 `/mnt/user-data` 之外的文件，当这些绝对容器路径与请求的目录匹配时直接使用"


def apply_prompt_template(
    subagent_enabled: bool = False,
    max_concurrent_subagents: int = 3,
    *,
    agent_name: str | None = None,
    available_skills: set[str] | None = None,
    app_config: AppConfig | None = None,
    deferred_names: frozenset[str] = frozenset(),
) -> str:
    """渲染 Lead Agent 的完整系统提示模板。

    根据参数拼装 skills、子代理、ACP、挂载、延迟工具等可选片段，
    生成最终静态系统提示（记忆与当前日期由 ``DynamicContextMiddleware`` 单独按 turn 注入）。

    输入示例：
    ```python
    prompt = apply_prompt_template(
        subagent_enabled=True,
        max_concurrent_subagents=3,
        agent_name="my-assistant",
        available_skills={"bootstrap", "code-review"},
        deferred_names=frozenset({"mcp_tool_search", "mcp_github_search"}),
    )
    ```

    输出结构（简化示意）：
    ```
    <role>
    你是 my-assistant，一个开源超级 Agent。
    </role>

    <soul>
    我是一个专注于代码审查的助手...
    </soul>

    <self_update>
    你正在作为自定义 Agent **my-assistant** 运行...
    </self_update>

    <thinking_style>
    - 在执行操作前对请求进行简洁的策略性思考
    - **并行检查：能否拆解为 2+ 并行子任务？计数。超过 3 则必须先批次规划**
    ...
    </thinking_style>

    <clarification_system>
    **工作流优先级：澄清 → 规划 → 行动**
    ...
    </clarification_system>

    <skill_system>
    你有权使用提供优化工作流的技能...
    <available_skills>
        <skill>
            <name>bootstrap</name>
            <description>引导新 Agent 创建流程</description>
            <location>/mnt/skills/public/bootstrap/SKILL.md</location>
        </skill>
        <skill>
            <name>code-review</name>
            <description>代码审查最佳实践</description>
            <location>/mnt/skills/public/code-review/SKILL.md</location>
        </skill>
    </available_skills>
    </skill_system>

    <available-deferred-tools>
    mcp_github_search
    mcp_tool_search
    </available-deferred-tools>

    <subagent_system>
    **🚀 子代理模式激活 — 拆解、委派、综合**
    硬限制：每轮最多 3 个 task 调用
    ...
    </subagent_system>

    <working_directory existed="true">
    - 用户上传：/mnt/user-data/uploads
    - 工作区：/mnt/user-data/workspace
    - 输出文件：/mnt/user-data/outputs
    </working_directory>
    ```

    动态判断逻辑：
    | 参数 | 效果 |
    |------|------|
    | ``subagent_enabled=True`` | 注入完整的子代理使用指南 + 并发限制 + 示例代码 |
    | ``agent_name="xxx"`` | 加载 SOUL.md + 注入 self_update 提示 |
    | ``available_skills={"a","b"}`` | 仅列出白名单内的技能，其余隐藏 |
    | ``deferred_names=frozenset({...})`` | 列出可用 tool_search 发现的工具名 |
    | 有 ACP agent 配置 | 追加 ACP workspace 使用说明 |
    | 有自定义挂载 | 追加挂载路径清单 |

    Args:
        subagent_enabled: 是否启用子代理提示片段。
        max_concurrent_subagents: 单次响应允许的最大并发子代理调用数。
        agent_name: 自定义 Agent 名称，用于加载 SOUL 与自更新提示。
        available_skills: 白名单内的技能名集合；``None`` 表示不限制。
        app_config: 可选应用配置，便于测试或非默认配置注入。
        deferred_names: 在 Agent 构建时计算出的延迟（MCP）工具名集合。

    Returns:
        渲染完成的系统提示字符串（不含记忆/日期，这些由 DynamicContextMiddleware 动态注入）。
    """
    # Include subagent section only if enabled (from runtime parameter)
    n = max_concurrent_subagents
    subagent_section = _build_subagent_section(n, app_config=app_config) if subagent_enabled else ""

    # 子代理启用时，向 critical_reminders 追加编排器模式提示
    subagent_reminder = (
        "- **编排器模式**：你是任务编排者——将复杂任务分解为并行子任务。 "
        f"**硬性限制：每次响应最多 {n} 个 `task` 调用。** "
        f"超过 {n} 个子任务时，拆分为 ≤{n} 的顺序批次。所有批次完成后综合结果。\n"
        if subagent_enabled
        else ""
    )

    # 子代理启用时，向 thinking_style 追加拆解检查引导
    subagent_thinking = (
        "- **拆解检查：这个任务能否拆解为 2+ 个并行子任务？如果可以，数出来。 "
        f"如果超过 {n} 个，你必须规划 ≤{n} 的批次，且仅启动第一批。 "
        f"绝不在单次响应中启动超过 {n} 个 `task` 调用。**\n"
        if subagent_enabled
        else ""
    )

    # Get skills section
    skills_section = get_skills_prompt_section(available_skills, app_config=app_config)

    # Get deferred tools section (tool_search)
    deferred_tools_section = get_deferred_tools_prompt_section(deferred_names=deferred_names)

    # Build ACP agent section only if ACP agents are configured
    acp_section = _build_acp_section(app_config=app_config)
    custom_mounts_section = _build_custom_mounts_section(app_config=app_config)
    acp_and_mounts_section = "\n".join(section for section in (acp_section, custom_mounts_section) if section)

    # Build and return the fully static system prompt.
    # Memory and current date are injected per-turn via DynamicContextMiddleware
    # as a <system-reminder> in the first HumanMessage, keeping this prompt
    # identical across users and sessions for maximum prefix-cache reuse.
    return SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=agent_name or "DeerFlow 2.0",
        soul=get_agent_soul(agent_name),
        self_update_section=_build_self_update_section(agent_name),
        skills_section=skills_section,
        deferred_tools_section=deferred_tools_section,
        subagent_section=subagent_section,
        subagent_reminder=subagent_reminder,
        subagent_thinking=subagent_thinking,
        acp_section=acp_and_mounts_section,
    )
