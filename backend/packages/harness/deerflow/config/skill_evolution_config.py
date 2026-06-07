"""技能（Skill）自动演化相关配置。"""

from pydantic import BaseModel, Field


class SkillEvolutionConfig(BaseModel):
    """Agent 管理的技能自演化配置。

    控制是否允许 agent 在运行时创建/修改 ``skills/custom`` 下的技能，
    以及在执行技能变更前是否调用安全审查模型进行审核。
    """

    enabled: bool = Field(
        default=False,
        description="是否允许 agent 在 skills/custom 目录下创建与修改技能。",
    )
    moderation_model_name: str | None = Field(
        default=None,
        description="用于技能安全审核的可选模型名；缺省时使用主 chat 模型。",
    )
