"""循环检测中间件的配置。"""

from pydantic import BaseModel, Field, model_validator


class ToolFreqOverride(BaseModel):
    """按工具覆盖的频率阈值。

    可以高于或低于全局默认。常用于在批量工作流（如 RNA-seq 流水线）下为
    高频工具（如 bash）提高阈值，同时不削弱对其他工具的保护。
    """

    warn: int = Field(ge=1)
    hard_limit: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate(self) -> "ToolFreqOverride":
        """校验 ``hard_limit`` 不低于 ``warn``。"""
        if self.hard_limit < self.warn:
            raise ValueError("hard_limit 必须 >= warn")
        return self


class LoopDetectionConfig(BaseModel):
    """重复工具调用循环检测配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用重复工具调用循环检测",
    )
    warn_threshold: int = Field(
        default=3,
        ge=1,
        description="注入警告前允许的相同工具调用集合数",
    )
    hard_limit: int = Field(
        default=5,
        ge=1,
        description="强制停止前允许的相同工具调用集合数",
    )
    window_size: int = Field(
        default=20,
        ge=1,
        description="每个 thread 跟踪的最近工具调用集合数",
    )
    max_tracked_threads: int = Field(
        default=100,
        ge=1,
        description="内存中保存的 thread 历史记录最大数量",
    )
    tool_freq_warn: int = Field(
        default=30,
        ge=1,
        description="对同一类工具的调用次数达到此值时注入频率警告",
    )
    tool_freq_hard_limit: int = Field(
        default=50,
        ge=1,
        description="对同一类工具的调用次数达到此值时强制停止",
    )
    tool_freq_overrides: dict[str, ToolFreqOverride] = Field(
        default_factory=dict,
        description=("按工具覆盖 tool_freq_warn / tool_freq_hard_limit，键为工具名，值可高于或低于全局默认。常用于为高频工具（如 bash）提高阈值。"),
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "LoopDetectionConfig":
        """确保硬停止阈值不会早于警告阈值。

        Returns:
            LoopDetectionConfig: 校验通过后的自身实例。

        Raises:
            ValueError: 任何一对硬阈值低于其对应警告阈值时。
        """
        if self.hard_limit < self.warn_threshold:
            raise ValueError("hard_limit 必须大于等于 warn_threshold")
        if self.tool_freq_hard_limit < self.tool_freq_warn:
            raise ValueError("tool_freq_hard_limit 必须大于等于 tool_freq_warn")
        return self
