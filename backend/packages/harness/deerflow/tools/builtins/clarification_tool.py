"""Clarification 工具:在需要更多信息时向用户提问。"""

from typing import Literal

from langchain.tools import tool


@tool("ask_clarification", parse_docstring=True, return_direct=True)
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """在需要更多信息才能继续时向用户澄清问题。

    在以下无法绕过用户输入继续推进的情况下使用本工具:

    - **信息缺失(Missing information)**:所需细节(文件路径、URL、具体要求)未给出
    - **需求歧义(Ambiguous requirements)**:存在多种合理解读
    - **方案选择(Approach choices)**:存在多种可行方案,需要用户偏好
    - **风险操作(Risky operations)**:破坏性操作需要用户明确确认(例如删除文件、修改生产环境)
    - **建议(Suggestions)**:你已有推荐方案,但希望继续前获得用户批准

    执行会被打断,问题会被展示给用户,等待用户答复后再继续。

    使用时机:
    - 你需要用户提供的信息在请求中并未出现
    - 需求可以被解读为多种含义
    - 存在多种合法的实现路径
    - 你即将执行潜在危险的操作
    - 你有推荐方案但需要用户确认

    最佳实践:
    - 一次只问一个问题,确保清晰
    - 问题要具体明确
    - 在需要澄清时不要自行假设
    - 对于风险操作,务必先取得用户确认
    - 调用本工具后,执行会自动中断

    Args:
        question: 要询问用户的澄清问题,要具体清晰。
        clarification_type: 澄清类型(missing_info、ambiguous_requirement、approach_choice、risk_confirmation、suggestion)。
        context: 可选的上下文说明,帮助用户理解为何需要澄清。
        options: 可选选项列表(用于 approach_choice 或 suggestion 类型),给出清晰的备选。
    """
    # This is a placeholder implementation
    # The actual logic is handled by ClarificationMiddleware which intercepts this tool call
    # and interrupts execution to present the question to the user
    return "Clarification request processed by middleware"
