"""沙箱（sandbox）相关配置。"""

from pydantic import BaseModel, ConfigDict, Field


class VolumeMountConfig(BaseModel):
    """单个 volume mount 的配置。"""

    host_path: str = Field(..., description="宿主机上的路径")
    container_path: str = Field(..., description="容器内的路径")
    read_only: bool = Field(default=False, description="挂载是否为只读")


class SandboxConfig(BaseModel):
    """单个 sandbox 的配置段。

    通用选项：
        use: sandbox provider 的类路径（必填）
        allow_host_bash: 启用 ``LocalSandboxProvider`` 时是否允许在宿主机直接执行 bash。
            存在风险，仅建议在完全可信的本地工作流中开启。

    ``AioSandboxProvider`` 专属选项：
        image: 使用的 Docker 镜像（默认 ``enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest``）
        port: sandbox 容器的起始端口（默认 8080）
        replicas: sandbox 容器最大并发数（默认 3）。达到上限时淘汰最久未使用的 sandbox。
        container_prefix: 容器名前缀（默认 ``deer-flow-sandbox``）
        idle_timeout: 空闲超时（秒），超时后释放 sandbox（默认 600 = 10 分钟）。设为 0 禁用。
        mounts: 与容器共享的目录挂载列表
        environment: 注入到容器的环境变量（以 ``$`` 开头的值会从宿主机环境变量解析）
    """

    use: str = Field(
        ...,
        description="sandbox provider 的类路径（如 deerflow.sandbox.local:LocalSandboxProvider）",
    )
    allow_host_bash: bool = Field(
        default=False,
        description="使用 LocalSandboxProvider 时是否允许 bash 工具直接在宿主机执行。存在风险，仅建议在完全可信的本地环境中使用。",
    )
    image: str | None = Field(
        default=None,
        description="sandbox 容器使用的 Docker 镜像",
    )
    port: int | None = Field(
        default=None,
        description="sandbox 容器的起始端口",
    )
    replicas: int | None = Field(
        default=None,
        description="sandbox 容器最大并发数（默认 3）。达到上限时淘汰最久未使用的 sandbox。",
    )
    container_prefix: str | None = Field(
        default=None,
        description="容器名的前缀",
    )
    idle_timeout: int | None = Field(
        default=None,
        description="空闲超时（秒），超时后释放 sandbox（默认 600 = 10 分钟）。设为 0 禁用。",
    )
    mounts: list[VolumeMountConfig] = Field(
        default_factory=list,
        description="宿主机与容器之间的目录挂载列表",
    )
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="注入到 sandbox 容器的环境变量。以 $ 开头的值会从宿主机环境变量解析。",
    )

    bash_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="bash 工具输出保留的最大字符数。超出时进行中间截断（head + tail），保留前一半与后一半。设为 0 禁用截断。",
    )
    read_file_output_max_chars: int = Field(
        default=50000,
        ge=0,
        description="read_file 工具输出保留的最大字符数。超出时进行头部截断。设为 0 禁用截断。",
    )
    ls_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="ls 工具输出保留的最大字符数。超出时进行头部截断。设为 0 禁用截断。",
    )

    model_config = ConfigDict(extra="allow")
