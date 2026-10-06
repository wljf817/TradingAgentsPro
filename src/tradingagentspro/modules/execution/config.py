"""执行模块的阈值和默认值，含义见 order-lifecycle spec「执行配置」。"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class ExecutionConfig(BaseModel):
    """执行模块的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 订单或止损单变为状态未知之后，等这么久才再查询券商
    unknown_lookup_delay: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    # 止损单挂单或改单被券商拒绝后，立即用同样的内容重试几次
    stop_immediate_retries: int = Field(default=1, ge=0)
    # 同一只股票的买单和券商端止损单不同时挂着，见 stop-protection spec「买单和止损单错开」；
    # 给不允许两者同时挂着的券商打开，比如 Alpaca
    stagger_buys_and_stops: bool = False
