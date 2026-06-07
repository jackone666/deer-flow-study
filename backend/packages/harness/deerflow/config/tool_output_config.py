"""工具输出预算保护相关配置。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolOutputConfig(BaseModel):
    """工具结果输出预算的强制配置。

    当工具返回的字符数超过 ``externalize_min_chars`` 时，完整输出会被持久化到磁盘，
    并替换为精简的预览片段 + 文件引用。若磁盘持久化不可用，则回退到 head+tail 截断。

    Attributes:
        enabled: 是否启用工具输出预算中间件。
        externalize_min_chars: 触发磁盘外置化的字符阈值。
        preview_head_chars: 预览中保留的输出头部字符数。
        preview_tail_chars: 预览中保留的输出尾部字符数。
        fallback_max_chars: 磁盘不可用时的最大字符数；0 禁用回退截断。
        fallback_head_chars: 回退截断时保留的头部字符数。
        fallback_tail_chars: 回退截断时保留的尾部字符数。
        storage_subdir: thread outputs 路径下用于持久化工具结果的子目录。
        exempt_tools: 豁免预算强制的工具名列表（防止 persist→read→persist 循环）。
        tool_overrides: 按工具覆盖 ``externalize_min_chars``，键为工具名，值为字符阈值。
    """

    enabled: bool = Field(
        default=True,
        description="是否启用工具输出预算中间件。",
    )
    externalize_min_chars: int = Field(
        default=12_000,
        ge=0,
        description="触发磁盘外置化的字符阈值。低于该值的输出原样通过；设为 0 可禁用外置化（但超过 fallback_max_chars 时仍会触发回退截断）。",
    )
    preview_head_chars: int = Field(
        default=2_000,
        ge=0,
        description="预览中保留的输出头部字符数。",
    )
    preview_tail_chars: int = Field(
        default=1_000,
        ge=0,
        description="预览中保留的输出尾部字符数。",
    )
    fallback_max_chars: int = Field(
        default=30_000,
        ge=0,
        description="磁盘持久化不可用时的最大字符数；设为 0 禁用回退截断。",
    )
    fallback_head_chars: int = Field(
        default=8_000,
        ge=0,
        description="回退截断时保留的头部字符数。",
    )
    fallback_tail_chars: int = Field(
        default=3_000,
        ge=0,
        description="回退截断时保留的尾部字符数。",
    )
    storage_subdir: str = Field(
        default=".tool-results",
        description="thread outputs 路径下用于存放持久化工具结果的子目录。",
    )
    exempt_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "read_file_tool"],
        description="豁免预算强制的工具名列表（防止 persist→read→persist 循环）。",
    )
    tool_overrides: dict[str, int] = Field(
        default_factory=dict,
        description="按工具覆盖 externalize_min_chars，键为工具名，值为字符阈值；设为 0 可对指定工具禁用外置化。",
    )
