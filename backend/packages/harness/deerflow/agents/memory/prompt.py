"""记忆更新与注入相关的提示模板与格式化工具。"""

import math
import re
from typing import Any

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# 基于对话更新记忆的提示模板
MEMORY_UPDATE_PROMPT = """你是一个记忆管理系统。你的任务是分析对话并更新用户的记忆档案。

当前记忆状态：
<current_memory>
{current_memory}
</current_memory>

待处理的新对话：
<conversation>
{conversation}
</conversation>

操作指引：
1. 分析对话中与用户相关的重要信息
2. 提取相关的事实、偏好和上下文，包含具体细节（数字、名称、技术栈）
3. 按照下方的详细长度指南，按需更新记忆各节

在提取事实之前，先对对话进行结构化反思：
1. 错误/重试检测：Agent 是否遇到了错误、需要重试或产生了不正确的结果？
   如果是，将根因和正确做法记录为高置信度事实，类别设为 "correction"。
2. 用户纠偏检测：用户是否纠正了 Agent 的方向、理解或输出？
   如果是，将正确的理解或做法记录为高置信度事实，类别设为 "correction"。
   仅当类别为 "correction" 且错误在对话中明确出现时，才在 "sourceError" 中记录错在哪里。
3. 项目约束发现：对话中是否发现了项目特定的约束条件？
   如果是，以最合适的类别和置信度记录为事实。

{correction_hint}

记忆各节指南：

**用户上下文**（当前状态 — 简洁摘要）：
- workContext：职业角色、公司、关键项目、主要技术栈（2-3 句话）
  示例：核心贡献者、带指标的项目名称（16k+ stars）、技术栈
- personalContext：语言、沟通偏好、核心兴趣（1-2 句话）
  示例：双语能力、特定兴趣领域、专业领域
- topOfMind：多个进行中的关注领域和优先事项（3-5 句话，详细段落）
  示例：主要项目工作、并行的技术调研、持续的学习/跟踪
  包含：正在进行的实现工作、排查中的问题、市场/研究兴趣
  注意：本段涵盖**多个**并行的关注领域，而非仅一项任务

**历史记录**（时间上下文 — 丰富段落）：
- recentMonths：近期活动的详细摘要（4-6 句话或 1-2 段）
  时间范围：最近 1-3 个月的交互
  包含：探索的技术、参与的项目、解决的问题、展现的兴趣
- earlierContext：重要的历史模式（3-5 句话或 1 段）
  时间范围：3-12 个月前
  包含：过往项目、学习历程、已建立的模式
- longTermBackground：持续存在的背景和基础上下文（2-4 句话）
  时间范围：整体/基础信息
  包含：核心专长、长期兴趣、基本工作风格

**事实提取**：
- 提取具体的、可量化的细节（如 "16k+ GitHub stars"、"200+ datasets"）
- 包含专有名词（公司名、项目名、技术名）
- 保留技术术语和版本号
- 类别：
  * preference：用户偏好/不喜欢的工具、风格、方法
  * knowledge：具体的专长、已掌握的技术、领域知识
  * context：背景事实（职位、项目、地点、语言）
  * behavior：工作模式、沟通习惯、问题解决方法
  * goal：明确的目标、学习计划、项目志向
  * correction：Agent 的明确错误或用户的纠正，包含正确做法
- 置信度等级：
  * 0.9-1.0：明确陈述的事实（如"我在做 X"、"我的角色是 Y"）
  * 0.7-0.8：从行为/讨论中强推断
  * 0.5-0.6：推断出的模式（谨慎使用，仅用于明确模式）

**各项用途**：
- workContext：当前工作、活跃项目、主要技术栈
- personalContext：语言、个性、工作之外的兴趣
- topOfMind：用户近期关注的多个进行中的优先事项和焦点（更新最频繁）
  应涵盖 3-5 个并行主题：主要工作、副业探索、学习/跟踪兴趣
- recentMonths：近期技术探索和工作的详细记录
- earlierContext：较早期交互中仍相关的模式
- longTermBackground：关于用户不变的基础事实

**多语言内容**：
- 保留专有名词和公司名的原始语言
- 保留技术术语的原始形式（DeepSeek、LangGraph 等）
- 在 personalContext 中注明语言能力

输出格式（JSON）：
{{
  "user": {{
    "workContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "personalContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "topOfMind": {{ "summary": "...", "shouldUpdate": true/false }}
  }},
  "history": {{
    "recentMonths": {{ "summary": "...", "shouldUpdate": true/false }},
    "earlierContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "longTermBackground": {{ "summary": "...", "shouldUpdate": true/false }}
  }},
  "newFacts": [
    {{ "content": "...", "category": "preference|knowledge|context|behavior|goal|correction", "confidence": 0.0-1.0 }}
  ],
  "factsToRemove": ["fact_id_1", "fact_id_2"]
}}

重要规则：
- 仅在有实质性新信息时才设置 shouldUpdate=true
- 遵循长度指南：workContext/personalContext 保持简洁（1-3 句话），topOfMind 和历史各节应详细（段落级）
- 在事实中包含具体的指标、版本号和专有名词
- 仅添加明确陈述（0.9+）或强推断（0.7+）的事实
- 对明确的 Agent 错误或用户纠正使用 "correction" 类别；纠正明确时置信度 >= 0.95
- 仅在纠正明确且之前的错误或错误做法被清晰陈述时，才包含 "sourceError"；否则省略
- 删除与新信息相矛盾的事实
- 更新 topOfMind 时，整合新的关注领域，同时移除已完成/放弃的领域
  保留 3-5 个仍然活跃和相关的并行关注主题
- 对于历史各节，按时间顺序将新信息整合到合适的时间段
- 保持技术准确性 — 保留技术、公司、项目的准确名称
- 聚焦于对未来交互和个性化有用的信息
- 重要：**不要**将文件上传事件记录到记忆中。上传的文件是会话特定的临时文件
  ——在后续会话中无法访问。记录上传事件会导致后续对话出现混淆。

仅返回合法的 JSON，不要附带解释或 Markdown。"""


# 从单条消息中提取事实的提示模板
FACT_EXTRACTION_PROMPT = """从以下消息中提取与用户相关的事实信息。

消息：
{message}

按以下 JSON 格式提取事实：
{{
  "facts": [
    {{ "content": "...", "category": "preference|knowledge|context|behavior|goal|correction", 
    "confidence": 0.0-1.0 }}
  ]
}}

类别说明：
- preference：用户偏好（喜好/厌恶、风格、工具）
- knowledge：用户的专长或知识领域
- context：背景上下文（地点、工作、项目）
- behavior：行为模式
- goal：用户的目标或计划
- correction：明确的纠正或需要避免重复的错误

规则：
- 仅提取清晰、具体的事实
- 置信度应反映确定性（明确陈述 = 0.9+，推断 = 0.6-0.8）
- 跳过模糊或临时的信息

仅返回合法的 JSON。"""


def _count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """使用 tiktoken 统计文本中的 token 数量。

    Args:
        text: 待统计的文本。
        encoding_name: tiktoken 编码名（默认 ``cl100k_base``，适用于 GPT-4/3.5）。

    Returns:
        文本中的 token 数。
    """
    if not TIKTOKEN_AVAILABLE:
        # Fallback to character-based estimation if tiktoken is not available
        return len(text) // 4

    try:
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text))
    except Exception:
        # Fallback to character-based estimation on error
        return len(text) // 4


def _coerce_confidence(value: Any, default: float = 0.0) -> float:
    """将置信度类值强制转换为 ``[0, 1]`` 区间内的浮点数。

    非有限值（NaN、inf、-inf）被视为无效并在裁剪前回退到默认值，避免
    它们在排序中占据主导。``default`` 参数假定为有限值。
    """
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return max(0.0, min(1.0, default))
    if not math.isfinite(confidence):
        return max(0.0, min(1.0, default))
    return max(0.0, min(1.0, confidence))


def format_memory_for_injection(memory_data: dict[str, Any], max_tokens: int = 2000) -> str:
    """将记忆数据格式化为可注入系统提示的紧凑字符串，按 token 预算截断。

    输入示例（``memory_data`` 字典结构）：
    ```python
    {
        "user": {
            "workContext": {"summary": "DeerFlow 核心贡献者，专注 Agent 系统（16k+ stars）"},
            "personalContext": {"summary": "中英双语，偏好简洁回复"},
            "topOfMind": {"summary": "正在开发记忆子系统；调研 LangGraph 1.0 迁移方案"}
        },
        "history": {
            "recentMonths": {"summary": "近三个月完成沙箱重构、MCP 多服务器支持、Guardrails 中间件"},
            "earlierContext": {"summary": "从 LangChain 迁移到 LangGraph，搭建 Gateway 架构"},
            "longTermBackground": {"summary": "10+ 年后端经验，Python/Go 双修"}
        },
        "facts": [
            {"content": "偏好 uv 作为 Python 包管理工具", "category": "preference", "confidence": 0.95},
            {"content": "上次部署 sh 脚本不应直接用 rm -rf /tmp/sandbox", "category": "correction", "confidence": 0.98, "sourceError": "rm -rf /tmp/sandbox 误删了系统文件"},
            {"content": "项目目标 Q3 发布 v2.0", "category": "goal", "confidence": 0.9}
        ]
    }
    ```

    输出示例（max_tokens=2000 时返回的格式化字符串）：
    ```
    User Context:
    - Work: DeerFlow 核心贡献者，专注 Agent 系统（16k+ stars）
    - Personal: 中英双语，偏好简洁回复
    - Current Focus: 正在开发记忆子系统；调研 LangGraph 1.0 迁移方案

    History:
    - Recent: 近三个月完成沙箱重构、MCP 多服务器支持、Guardrails 中间件
    - Earlier: 从 LangChain 迁移到 LangGraph，搭建 Gateway 架构
    - Background: 10+ 年后端经验，Python/Go 双修

    Facts:
    - [preference | 0.95] 偏好 uv 作为 Python 包管理工具
    - [correction | 0.98] 上次部署 sh 脚本不应直接用 rm -rf /tmp/sandbox (avoid: rm -rf /tmp/sandbox 误删了系统文件)
    - [goal | 0.90] 项目目标 Q3 发布 v2.0
    ```

    处理逻辑：
    1. 依次提取 user → history → facts 三段，每段有值才输出
    2. facts 按置信度降序排列，逐行累计 token 数，超出 ``max_tokens`` 时截断
    3. correction 类事实会额外追加 ``(avoid: 错误原因)`` 后缀
    4. 最终整体再次校验 token 数，超出按比例截断并追加 ``...`` 标记

    Args:
        memory_data: 记忆数据字典（如上例结构）。
        max_tokens: 允许使用的最大 token 数（通过 tiktoken 精确统计，默认 2000）。

    Returns:
        可注入系统提示的格式化记忆字符串；输入为空或无有效内容时返回 ``""``。
    """
    if not memory_data:
        return ""

    # 最终输出由多个 section 组成，每段之间用双换行分隔
    sections = []

    # ── 第1步：格式化用户上下文 ──
    user_data = memory_data.get("user", {})
    if user_data:
        user_sections = []
        # 仅当 summary 非空时才输出对应行
        work_ctx = user_data.get("workContext", {})
        if work_ctx.get("summary"):
            user_sections.append(f"Work: {work_ctx['summary']}")

        personal_ctx = user_data.get("personalContext", {})
        if personal_ctx.get("summary"):
            user_sections.append(f"Personal: {personal_ctx['summary']}")

        top_of_mind = user_data.get("topOfMind", {})
        if top_of_mind.get("summary"):
            user_sections.append(f"Current Focus: {top_of_mind['summary']}")

        if user_sections:
            sections.append("User Context:\n" + "\n".join(f"- {s}" for s in user_sections))

    # ── 第2步：格式化历史记录 ──
    history_data = memory_data.get("history", {})
    if history_data:
        history_sections = []
        # 按时间从近到远排列：recent → earlier → background
        recent = history_data.get("recentMonths", {})
        if recent.get("summary"):
            history_sections.append(f"Recent: {recent['summary']}")

        earlier = history_data.get("earlierContext", {})
        if earlier.get("summary"):
            history_sections.append(f"Earlier: {earlier['summary']}")

        background = history_data.get("longTermBackground", {})
        if background.get("summary"):
            history_sections.append(f"Background: {background['summary']}")

        if history_sections:
            sections.append("History:\n" + "\n".join(f"- {s}" for s in history_sections))

    # ── 第3步：格式化事实列表（按置信度降序，受 token 预算约束）──
    facts_data = memory_data.get("facts", [])
    if isinstance(facts_data, list) and facts_data:
        # 过滤无效条目后按置信度从高到低排序，确保最重要的信息优先注入
        ranked_facts = sorted(
            (f for f in facts_data if isinstance(f, dict) and isinstance(f.get("content"), str) and f.get("content").strip()),
            key=lambda fact: _coerce_confidence(fact.get("confidence"), default=0.0),
            reverse=True,
        )

        # 先计算前两段的 token 数作为基准（避免每次循环重新计算整个字符串）
        base_text = "\n\n".join(sections)
        base_tokens = _count_tokens(base_text) if base_text else 0
        # 加上 Facts 段的标题和分隔符的 token 开销
        facts_header = "Facts:\n"
        separator_tokens = _count_tokens("\n\n" + facts_header) if base_text else _count_tokens(facts_header)
        running_tokens = base_tokens + separator_tokens

        # 逐条追加事实，token 数超出 max_tokens 时立即停止
        fact_lines: list[str] = []
        for fact in ranked_facts:
            content_value = fact.get("content")
            if not isinstance(content_value, str):
                continue
            content = content_value.strip()
            if not content:
                continue
            category = str(fact.get("category", "context")).strip() or "context"
            confidence = _coerce_confidence(fact.get("confidence"), default=0.0)
            source_error = fact.get("sourceError")
            # correction 类事实：额外追加错误原因，帮助模型避免重蹈覆辙
            # 输出格式：- [correction | 0.98] 正确做法 (avoid: 之前的错误)
            if category == "correction" and isinstance(source_error, str) and source_error.strip():
                line = f"- [{category} | {confidence:.2f}] {content} (avoid: {source_error.strip()})"
            else:
                # 普通事实：- [preference | 0.95] 事实内容
                line = f"- [{category} | {confidence:.2f}] {content}"

            # 第一条事实不加前导换行，后续每条前加 \n（避免首行空行）
            line_text = ("\n" + line) if fact_lines else line
            line_tokens = _count_tokens(line_text)

            # token 预算检查：当前行加入会超出则停止，该行及之后的事实被丢弃
            if running_tokens + line_tokens <= max_tokens:
                fact_lines.append(line)
                running_tokens += line_tokens
            else:
                break

        if fact_lines:
            sections.append("Facts:\n" + "\n".join(fact_lines))

    # 无任何有效内容时返回空串（调用方会跳过记忆注入）
    if not sections:
        return ""

    # 用双换行连接所有 section
    result = "\n\n".join(sections)

    # ── 第4步：安全兜底 —— 整体 token 校验 ──
    # 正常情况下第3步的逐条检查已保证不超出预算，此处作为二次防护
    token_count = _count_tokens(result)
    if token_count > max_tokens:
        # 按 token/字符 比例估算需截断的字符数（保留 95% 作为安全边界）
        char_per_token = len(result) / token_count
        target_chars = int(max_tokens * char_per_token * 0.95)
        result = result[:target_chars] + "\n..."

    return result


def format_conversation_for_update(messages: list[Any]) -> str:
    """将会话消息清洗并格式化为记忆更新提示可用的纯文本对话记录。

    输入示例（LangChain 消息对象列表）：
    ```python
    [
        HumanMessage(content="帮我查一下 DeepSeek-V3 的 token 计费方式"),
        AIMessage(content="DeepSeek-V3 的计费按 token 计算，输入 1元/百万token，输出 2元/百万token"),
        HumanMessage(content="<uploaded_files>\n- report.pdf\n</uploaded_files>\n把这个报告翻译成英文"),
        AIMessage(content="翻译完成，已保存到 outputs/"),
        ToolMessage(content="file written successfully", tool_call_id="tc_001"),
        AIMessage(content=["这是思考过程...", {"type": "text", "text": "最终答案如下..."}]),
    ]
    ```

    输出示例（``\\n\\n`` 分隔的纯文本对话）：
    ```
    User: 帮我查一下 DeepSeek-V3 的 token 计费方式

    Assistant: DeepSeek-V3 的计费按 token 计算，输入 1元/百万token，输出 2元/百万token

    User: 把这个报告翻译成英文

    Assistant: 翻译完成，已保存到 outputs/
    ```

    处理逻辑：
    1. 遍历消息列表，从 ``type`` 属性区分 human/ai/tool 角色
    2. 多模态 content（list 类型）展开为纯文本拼接
    3. **人类消息中剥离 ``<uploaded_files>`` 标签**（文件上传信息是临时的，不应写入长期记忆）
    4. 剥离后内容为空的纯上传消息直接跳过
    5. 每条消息超过 1000 字符时截断并追加 ``...``
    6. 最终以 ``\\n\\n`` 分隔各轮对话

    Args:
        messages: LangChain 消息对象列表（HumanMessage / AIMessage / ToolMessage 等），
                  每个对象需有 ``type`` 和 ``content`` 属性。

    Returns:
        格式化后的会话纯文本字符串，逐行形如 ``User: ...`` / ``Assistant: ...``；
        无有效内容时返回 ``""``。
    """
    lines = []
    for msg in messages:
        # 角色取自 LangChain 消息的 type 属性：human / ai / tool
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", str(msg))

        # ── 第1步：展开多模态 content ──
        # Anthropic 思考模式等场景下 content 是 list[dict]，需抽取 text 字段拼接
        if isinstance(content, list):
            text_parts = []
            for p in content:
                if isinstance(p, str):
                    text_parts.append(p)
                elif isinstance(p, dict):
                    text_val = p.get("text")
                    if isinstance(text_val, str):
                        text_parts.append(text_val)
            content = " ".join(text_parts) if text_parts else str(content)

        # ── 第2步：剥离人类消息中的 <uploaded_files> 标签 ──
        # 上传文件路径是会话临时的，写入长期记忆会导致后续会话引用不存在的文件
        if role == "human":
            content = re.sub(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", "", str(content)).strip()
            # 纯上传消息（剥离后为空）直接丢弃，不产生对话行
            if not content:
                continue

        # ── 第3步：超长消息截断 ──
        # 超过 1000 字符截断并标记，避免单条消息占用过多上下文
        if len(str(content)) > 1000:
            content = str(content)[:1000] + "..."

        # ── 第4步：按角色输出对话行 ──
        if role == "human":
            lines.append(f"User: {content}")
        elif role == "ai":
            lines.append(f"Assistant: {content}")
        # tool 类型的消息（ToolMessage）不产生输出，已在上方被过滤

    # 双换行分隔各轮对话，与 MEMORY_UPDATE_PROMPT 中的 {conversation} 占位符拼接
    return "\n\n".join(lines)
