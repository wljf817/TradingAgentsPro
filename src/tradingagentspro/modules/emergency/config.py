"""紧急退出的阈值和默认值，含义见 emergency-exit spec「紧急退出配置」。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class EmergencyConfig(BaseModel):
    """紧急退出的阈值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 单日亏损达到前一交易日收盘时的权益乘以这个比例时熔断
    daily_loss_breaker_ratio: Decimal = Field(default=Decimal("0.07"), gt=0, lt=1)
