"""在 LLM 调用前将图片详情注入到会话中的中间件。

工作流程：
```
用户调用 view_image("/path/to/img.png") 工具
       ↓
ToolNode 执行 → 图片 base64 写入 state.viewed_images
       ↓
ViewImageMiddleware.before_model() 被触发
       ├─ 检查最后一条 AIMessage 是否含 view_image tool_call
       ├─ 检查所有 tool_call 是否已有对应 ToolMessage（全部完成？）
       ├─ 检查是否已注入过（防重复）
       └─ 是 → 构造 HumanMessage 包含 base64 图片数据 → 注入
       ↓
LLM 看到图片内容，可以分析/描述
```

注入的消息格式：
```python
HumanMessage(content=[
    {"type": "text", "text": "Here are the images you've viewed:"},
    {"type": "text", "text": "\\n- **/mnt/user-data/outputs/chart.png** (image/png)"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KG..."}},
])
```"""

import logging
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)


class ViewImageMiddlewareState(ThreadState):
    """复用线程状态，使带 Reducer 的键保留其注解。"""


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """当 ``view_image`` 工具调用完成后，在 LLM 调用前以人类消息形式注入图片详情。

    该中间件：
    1. 在每次 LLM 调用前运行；
    2. 检查最后一条助手消息是否包含 ``view_image`` 工具调用；
    3. 确认该消息中的所有工具调用都已完成（存在对应 ``ToolMessage``）；
    4. 若条件满足，构造一条包含全部已查看图片详情（含 base64 数据）的
       人类消息；
    5. 将消息加入 state，让 LLM 看到并分析这些图片。

    这让 LLM 能自动接收并分析通过 ``view_image`` 工具加载的图片，无需用户
    显式提示。
    """

    state_schema = ViewImageMiddlewareState

    def _get_last_assistant_message(self, messages: list) -> AIMessage | None:
        """从消息列表中获取最后一条助手消息。

        Args:
            messages: 消息列表。

        Returns:
            最后一条 ``AIMessage``，找不到时返回 ``None``。
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _has_view_image_tool(self, message: AIMessage) -> bool:
        """检查助手消息是否包含 ``view_image`` 工具调用。

        Args:
            message: 待检查的助手消息。

        Returns:
            当消息包含 ``view_image`` 工具调用时返回 ``True``。
        """
        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return False

        return any(tool_call.get("name") == "view_image" for tool_call in message.tool_calls)

    def _all_tools_completed(self, messages: list, assistant_msg: AIMessage) -> bool:
        """检查助手消息中的所有工具调用是否都已完成。

        Args:
            messages: 全部消息列表。
            assistant_msg: 含工具调用的助手消息。

        Returns:
            所有工具调用都有对应 ``ToolMessage`` 时返回 ``True``。
        """
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # Get all tool call IDs from the assistant message
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # Find the index of the assistant message
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        # Get all ToolMessages after the assistant message
        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # Check if all tool calls have been completed
        return tool_call_ids.issubset(completed_tool_ids)

    def _create_image_details_message(self, state: ViewImageMiddlewareState) -> list[str | dict]:
        """构造包含全部已查看图片详情的格式化消息。

        Args:
            state: 当前 state，其中应包含 ``viewed_images``。

        Returns:
            适用于 ``HumanMessage`` 的内容块列表（文本与图片）。
        """
        viewed_images = state.get("viewed_images", {})
        if not viewed_images:
            # Return a properly formatted text block, not a plain string array
            return [{"type": "text", "text": "No images have been viewed."}]

        # Build the message with image information
        content_blocks: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]

        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            base64_data = image_data.get("base64", "")

            # Add text description
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            # Add the actual image data so LLM can "see" it
            if base64_data:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                )

        return content_blocks

    def _should_inject_image_message(self, state: ViewImageMiddlewareState) -> bool:
        """判断是否应当注入图片详情消息。

        Args:
            state: 当前 state。

        Returns:
            当需要注入时返回 ``True``。
        """
        messages = state.get("messages", [])
        if not messages:
            return False

        # Get the last assistant message
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        # Check if it has view_image tool calls
        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # Check if all tools have been completed
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # Check if we've already added an image details message
        # Look for a human message after the last assistant message that contains image details
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                content_str = str(msg.content)
                if "Here are the images you've viewed" in content_str or "Here are the details of the images you've viewed" in content_str:
                    # Already added, don't add again
                    return False

        return True

    def _inject_image_message(self, state: ViewImageMiddlewareState) -> dict | None:
        """内部辅助：注入图片详情消息。

        Args:
            state: 当前 state。

        Returns:
            含新增人类消息的 state 更新；无需更新时返回 ``None``。
        """
        if not self._should_inject_image_message(state):
            return None

        # Create the image details message with text and image content
        image_content = self._create_image_details_message(state)

        # Create a new human message with mixed content (text + images)
        human_msg = HumanMessage(content=image_content)

        logger.debug("Injecting image details message with images before LLM call")

        # Return state update with the new message
        return {"messages": [human_msg]}

    @override
    def before_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """在 ``view_image`` 工具调用完成后，于 LLM 调用前注入图片详情（同步版本）。

        该钩子会在每次 LLM 调用前运行，检查上一轮中的 ``view_image``
        工具调用是否已全部完成；若完成，则注入一条携带图片详情的人类消息，
        让 LLM 能看到并分析图片。

        Args:
            state: 当前 state。
            runtime: 运行期 context（接口要求但此处未使用）。

        Returns:
            含新增人类消息的 state 更新；无需更新时返回 ``None``。
        """
        return self._inject_image_message(state)

    @override
    async def abefore_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """在 ``view_image`` 工具调用完成后，于 LLM 调用前注入图片详情（异步版本）。

        该钩子会在每次 LLM 调用前运行，检查上一轮中的 ``view_image``
        工具调用是否已全部完成；若完成，则注入一条携带图片详情的人类消息，
        让 LLM 能看到并分析图片。

        Args:
            state: 当前 state。
            runtime: 运行期 context（接口要求但此处未使用）。

        Returns:
            含新增人类消息的 state 更新；无需更新时返回 ``None``。
        """
        return self._inject_image_message(state)
