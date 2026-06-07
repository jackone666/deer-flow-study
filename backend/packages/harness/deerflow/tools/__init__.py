"""工具子包:面向 Agent 暴露可调用的 LangChain 工具集合。

包含沙箱工具的懒加载、技能管理、ACP Agent 调用、文件呈现等内置工具。
"""

from .tools import get_available_tools

__all__ = ["get_available_tools", "skill_manage_tool"]


def __getattr__(name: str):
    """延迟导入 ``skill_manage_tool`` 以避免不必要的依赖加载。"""
    if name == "skill_manage_tool":
        from .skill_manage_tool import skill_manage_tool

        return skill_manage_tool
    raise AttributeError(name)
