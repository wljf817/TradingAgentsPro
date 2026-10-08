"""调度器的阈值和默认值，含义见 case-types spec「研究要求的配置」、case-queue spec「队列配置」
和 case-stages spec「案件阶段的配置」。
"""

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.agent import SubAgent


class SchedulerConfig(BaseModel):
    """调度器的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 开仓方案、加仓和升级引用的状态是完成的 Result，至少来自这么多个不同子 agent；
    # 上限是子 agent 的个数
    min_cited_agents: int = Field(default=2, ge=1, le=len(SubAgent))
    # 同一时间还在调用模型的案件最多这么多个
    max_concurrent_cases: int = Field(default=3, ge=1)
    # 结论校验不通过时，PM 在同一个案件里最多重交这么多次；0 表示不重交
    max_resubmissions: int = Field(default=2, ge=0)
    # 开仓类方案的质询与回应最多这么多轮；至少 1，否则质询永远交不到 PM 手里
    max_challenge_rounds: int = Field(default=2, ge=1)
