"""Agent 网关的阈值和默认值，含义见 signal-format spec「信号的配置」、task-dispatch spec「派发的
配置」。
"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class GatewayConfig(BaseModel):
    """Agent 网关的阈值。配置项之间没有必须满足的关系。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 子 agent 和 PM 出信号时可以选的评估窗口，交易日数；至少一个，否则任何信号都不合法
    signal_horizons: tuple[PositiveInt, ...] = Field(default=(3, 5, 20, 60), min_length=1)
    # 每个案件 PM 派发出去的任务最多这么多个
    max_pm_tasks: PositiveInt = 6
    # 每个案件 PM 最多派这么多轮
    max_pm_rounds: PositiveInt = 2
    # 每个案件 Critic 派发出去的任务最多这么多个，不占 PM 的任务数和轮次
    max_critic_tasks: PositiveInt = 2
    # 截止时间是派发时刻加这么久
    task_timeout: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
