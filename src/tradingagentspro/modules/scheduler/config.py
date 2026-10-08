"""调度器的阈值和默认值，含义见 case-types spec「研究要求的配置」。"""

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.agent import SubAgent


class SchedulerConfig(BaseModel):
    """调度器的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 开仓方案、加仓和升级引用的状态是完成的 Result，至少来自这么多个不同子 agent；
    # 上限是子 agent 的个数
    min_cited_agents: int = Field(default=2, ge=1, le=len(SubAgent))
