"""针对 MindIE 引擎的 Chat model 适配器。"""

import ast
import html
import json
import re
import uuid
from collections.abc import Iterator

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


def _fix_messages(messages: list) -> list:
    """为 MindIE 兼容性清洗入站消息。

    MindIE 的 chat template 可能无法解析 LangChain 原生的 ``tool_calls``
    或 ``ToolMessage`` 角色，导致生成 0 token 的错误。本函数将多模态
    列表形式的 content 展平为字符串，并把与工具相关的消息转换为底层
    模型期望的 XML 标签格式。

    Args:
        messages: 原始 LangChain 消息列表。

    Returns:
        list: 经过清洗、可安全发送给 MindIE 的消息列表。
    """
    fixed = []
    for msg in messages:
        # 将 list 形式的 content 展平
        if isinstance(msg.content, list):
            parts = []
            for block in msg.content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            text = "".join(parts)
        else:
            text = msg.content or ""

        # 把带 tool_calls 的 AIMessage 转换为 XML 文本
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", []):
            xml_parts = []
            for tool in msg.tool_calls:
                args_xml = " ".join(f"<parameter={html.escape(str(k), quote=False)}>{html.escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False), quote=False)}</parameter>" for k, v in tool.get("args", {}).items())
                xml_parts.append(f"<tool_call> <function={html.escape(str(tool['name']), quote=False)}> {args_xml} </function> </tool_call>")
            full_text = f"{text}\n" + "\n".join(xml_parts) if text else "\n".join(xml_parts)
            fixed.append(AIMessage(content=full_text.strip() or " "))
            continue

        # 用 XML 包裹工具结果，并改写为 HumanMessage
        if isinstance(msg, ToolMessage):
            tool_result_text = f"<tool_response>\n{text}\n</tool_response>"
            fixed.append(HumanMessage(content=tool_result_text))
            continue

        # 防止出现完全为空的 message content
        if not text.strip():
            text = " "

        fixed.append(msg.model_copy(update={"content": text}))

    return fixed


def _parse_xml_tool_call_to_dict(content: str) -> tuple[str, list[dict]]:
    """把模型输出中的 XML 风格工具调用解析为 LangChain dict。

    Args:
        content: 模型原始文本输出。

    Returns:
        tuple[str, list[dict]]: 清洗后的文本（已移除 XML 块）与 LangChain 格式的工具调用列表。
    """
    if not isinstance(content, str) or "<tool_call>" not in content:
        return content, []

    tool_calls = []
    clean_parts: list[str] = []
    cursor = 0
    for start, end, inner_content in _iter_tool_call_blocks(content):
        clean_parts.append(content[cursor:start])
        cursor = end

        func_match = re.search(r"<function=([^>]+)>", inner_content)
        if not func_match:
            continue
        function_name = html.unescape(func_match.group(1).strip())

        # 解析本调用的参数时，忽略嵌套的 tool_call 块。
        # 嵌套的 `<tool_call>` 表示另一次独立调用，其 `<parameter>` 标签
        # 不应泄漏到当前调用的 args 中。
        param_source_parts: list[str] = []
        nested_cursor = 0
        for nested_start, nested_end, _ in _iter_tool_call_blocks(inner_content):
            param_source_parts.append(inner_content[nested_cursor:nested_start])
            nested_cursor = nested_end
        param_source_parts.append(inner_content[nested_cursor:])
        param_source = "".join(param_source_parts)

        args = {}
        param_pattern = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.DOTALL)
        for param_match in param_pattern.finditer(param_source):
            key = html.unescape(param_match.group(1).strip())
            raw_value = html.unescape(param_match.group(2).strip())

            # 尝试把字符串值反序列化为原生 Python 类型，
            # 以满足下游 Pydantic 校验。
            parsed_value = raw_value
            if raw_value.startswith(("[", "{")) or raw_value in ("true", "false", "null") or raw_value.isdigit():
                try:
                    parsed_value = json.loads(raw_value)
                except json.JSONDecodeError:
                    try:
                        parsed_value = ast.literal_eval(raw_value)
                    except (ValueError, SyntaxError):
                        pass

            args[key] = parsed_value

        tool_calls.append({"name": function_name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}"})
    clean_parts.append(content[cursor:])

    return "".join(clean_parts).strip(), tool_calls


def _iter_tool_call_blocks(content: str) -> Iterator[tuple[int, int, str]]:
    """迭代 ``<tool_call>...</tool_call>`` 块并容忍嵌套。"""
    token_pattern = re.compile(r"</?tool_call>")
    depth = 0
    block_start = -1

    for match in token_pattern.finditer(content):
        token = match.group(0)
        if token == "<tool_call>":
            if depth == 0:
                block_start = match.start()
            depth += 1
            continue

        if depth == 0:
            continue

        depth -= 1
        if depth == 0 and block_start != -1:
            block_end = match.end()
            inner_start = block_start + len("<tool_call>")
            inner_end = match.start()
            yield block_start, block_end, content[inner_start:inner_end]
            block_start = -1


def _decode_escaped_newlines_outside_fences(content: str) -> str:
    """在围栏代码块之外解码字面 ``\\n`` 序列。"""
    if "\\n" not in content:
        return content

    parts = re.split(r"(```[\s\S]*?```)", content)
    for idx, part in enumerate(parts):
        if part.startswith("```"):
            continue
        parts[idx] = part.replace("\\n", "\n")
    return "".join(parts)


class MindIEChatModel(ChatOpenAI):
    """针对 MindIE 引擎的 Chat model 适配器。

    解决的兼容性问题包括：
    - 将多模态列表形式的 content 展平为字符串。
    - 拦截并解析硬编码的 XML 风格 tool call，转换为 LangChain 标准格式。
    - 处理 ``stream=True`` 在含 tools 时丢失 choices 的问题——回退到非流式
      生成，再以分块形式 yield 出来模拟流式。
    - 修正网关响应中过度转义的换行符。
    """

    def __init__(self, **kwargs):
        """归一化 timeout kwargs，且不创建长生命周期的 client。"""
        connect_timeout = kwargs.pop("connect_timeout", 30.0)
        read_timeout = kwargs.pop("read_timeout", 900.0)
        write_timeout = kwargs.pop("write_timeout", 60.0)
        pool_timeout = kwargs.pop("pool_timeout", 30.0)

        kwargs.setdefault(
            "timeout",
            httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=write_timeout,
                pool=pool_timeout,
            ),
        )
        super().__init__(**kwargs)

    def _patch_result_with_tools(self, result: ChatResult) -> ChatResult:
        """对模型结果应用生成后修复。"""
        for gen in result.generations:
            msg = gen.message

            if isinstance(msg.content, str):
                # 围栏代码块内的转义换行保持原样。
                msg.content = _decode_escaped_newlines_outside_fences(msg.content)

                if "<tool_call>" in msg.content:
                    clean_content, extracted_tools = _parse_xml_tool_call_to_dict(msg.content)

                    if extracted_tools:
                        msg.content = clean_content
                        if getattr(msg, "tool_calls", None) is None:
                            msg.tool_calls = []
                        msg.tool_calls.extend(extracted_tools)
        return result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """同步生成：先清洗消息，再委托父类，最后应用工具相关后处理。"""
        result = super()._generate(_fix_messages(messages), stop=stop, run_manager=run_manager, **kwargs)
        return self._patch_result_with_tools(result)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        """异步生成：先清洗消息，再委托父类，最后应用工具相关后处理。"""
        result = await super()._agenerate(_fix_messages(messages), stop=stop, run_manager=run_manager, **kwargs)
        return self._patch_result_with_tools(result)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """流式生成：无 tools 时走原生流；含 tools 时回退到非流式 + 模拟分块。"""
        # 普通查询走原生流式以获得更低的 TTFB
        if not kwargs.get("tools"):
            async for chunk in super()._astream(_fix_messages(messages), stop=stop, run_manager=run_manager, **kwargs):
                if isinstance(chunk.message.content, str):
                    chunk.message.content = _decode_escaped_newlines_outside_fences(chunk.message.content)
                yield chunk
            return

        # 含 tools 的回退：
        # MindIE 当前在 stream=True 且存在 tools 时会丢掉 choices。
        # 这里先等待完整生成，再以分块形式 yield 以模拟流式。
        result = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

        for gen in result.generations:
            msg = gen.message
            content = msg.content
            standard_tool_calls = getattr(msg, "tool_calls", [])

            # 将文本按块 yield，方便下游 UI/Markdown 解析器平滑渲染
            if isinstance(content, str) and content:
                chunk_size = 15
                for i in range(0, len(content), chunk_size):
                    chunk_text = content[i : i + chunk_size]
                    chunk_msg = AIMessageChunk(content=chunk_text, id=msg.id, response_metadata=msg.response_metadata if i == 0 else {})
                    yield ChatGenerationChunk(message=chunk_msg, generation_info=gen.generation_info if i == 0 else None)

                if standard_tool_calls:
                    yield ChatGenerationChunk(message=AIMessageChunk(content="", id=msg.id, tool_calls=standard_tool_calls, invalid_tool_calls=getattr(msg, "invalid_tool_calls", [])))
            else:
                chunk_msg = AIMessageChunk(content=content, id=msg.id, tool_calls=standard_tool_calls, invalid_tool_calls=getattr(msg, "invalid_tool_calls", []))
                yield ChatGenerationChunk(message=chunk_msg, generation_info=gen.generation_info)
