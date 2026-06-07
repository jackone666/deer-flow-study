"""工具（Tool）与工具组（Tool Group）相关配置。"""

from pydantic import BaseModel, ConfigDict, Field


class ToolGroupConfig(BaseModel):
    """工具组的配置段。

    工具组用于将多个工具聚合到同一逻辑分组中，方便整体启用/禁用与权限管理。
    """

    name: str = Field(..., description="工具组的唯一名称")
    model_config = ConfigDict(extra="allow")


class ToolConfig(BaseModel):
    """工具的配置段。

    描述单个工具的标识、所属工具组以及具体要加载的 provider 路径。
    """

    name: str = Field(..., description="工具的唯一名称")
    group: str = Field(..., description="所属工具组名称")
    use: str = Field(
        ...,
        description="工具 provider 变量路径（如 ``deerflow.sandbox.tools:bash_tool``）。",
    )
    model_config = ConfigDict(extra="allow")
