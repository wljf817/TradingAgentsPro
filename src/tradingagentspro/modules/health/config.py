"""健康监控的阈值和默认值，含义见 degraded-mode spec「健康配置」。"""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class HealthConfig(BaseModel):
    """健康监控的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 常规交易时段内，行情超过这么久没有更新算异常
    max_quote_delay: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # 调度器超过这么久没有心跳算异常
    scheduler_heartbeat_timeout: timedelta = Field(default=timedelta(seconds=300), gt=timedelta(0))
