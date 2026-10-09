"""Agent 网关的阈值和默认值，含义见 signal-format spec「信号的配置」。"""

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class GatewayConfig(BaseModel):
    """Agent 网关的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 子 agent 和 PM 出信号时可以选的评估窗口，交易日数；至少一个，否则任何信号都不合法
    signal_horizons: tuple[PositiveInt, ...] = Field(default=(3, 5, 20, 60), min_length=1)
