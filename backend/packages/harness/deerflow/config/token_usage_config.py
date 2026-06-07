"""Token 使用量追踪相关配置。"""

from pydantic import BaseModel, Field


class TokenUsageConfig(BaseModel):
    """Token 使用量追踪配置。

    控制 LangGraph 中间件是否对每次模型调用的 token 消耗进行累计与上报。
    """

    enabled: bool = Field(default=True, description="是否启用 token 使用量追踪中间件")
