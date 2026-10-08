"""交易方案：结论里可以执行的部分，交给风控和执行模块。"""

from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

# 美股报价的最小变动单位（Reg NMS Rule 612）：1 美元及以上到 0.01，低于 1 美元到 0.0001。
# 是市场规则，不是可以配置的阈值；券商会拒绝不符合的订单和止损单。
_ONE_DOLLAR = Decimal(1)
_CENT = Decimal("0.01")
_SUB_DOLLAR_TICK = Decimal("0.0001")
_PRICE_NAMES = {"limit_price": "限价", "stop_price": "止损价"}


class TradeAction(StrEnum):
    """方案的动作。买入、加仓属于开仓类；减仓、清仓属于减仓类。"""

    BUY = "buy"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"

    @property
    def is_opening(self) -> bool:
        return self in (TradeAction.BUY, TradeAction.ADD)


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TradePlan(BaseModel):
    """一个交易方案。

    方案最终来自模型输出，所以在这里校验格式；按动作区分的要求见 risk-rules spec
    「交易方案的格式」。清仓不写股数，表示卖出全部可卖数量。

    plan_id 由创建方案的一方生成；预留、成交、释放、人工确认和下单都用它指向这个方案。

    expires_at 和 max_price_deviation 在下单前再校验时使用，见 pre-order-recheck spec；
    没填时由风控给出默认值。

    限价和止损价要符合最小变动单位：1 美元及以上最多 2 位小数，低于 1 美元最多 4 位小数。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: UUID
    symbol: str = Field(min_length=1)
    sector: str = Field(min_length=1)
    action: TradeAction
    quantity: int | None = Field(default=None, gt=0)
    order_type: OrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    emergency_threshold: Decimal | None = Field(default=None, gt=0, lt=1)
    expires_at: AwareDatetime | None = None
    max_price_deviation: Decimal | None = Field(default=None, gt=0, lt=1)

    @field_validator("limit_price", "stop_price")
    @classmethod
    def _check_tick(cls, price: Decimal | None, info: ValidationInfo) -> Decimal | None:
        """按数值判断，末尾多写的 0 不算：180.500 符合，180.505 不符合。"""
        if price is None:
            return price
        tick = _CENT if price >= _ONE_DOLLAR else _SUB_DOLLAR_TICK
        if price % tick != 0:
            name = _PRICE_NAMES[info.field_name or ""]
            raise ValueError(
                f"{name} {price} 不符合最小变动单位：1 美元及以上最多 2 位小数，"
                "1 美元以下最多 4 位小数"
            )
        return price

    @model_validator(mode="after")
    def _check_fields_for_action(self) -> Self:
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("限价单必须写限价，市价单不能写限价")
        if self.action is TradeAction.CLOSE:
            if self.quantity is not None:
                raise ValueError("清仓不写股数，卖出全部可卖数量")
        elif self.quantity is None:
            raise ValueError("买入、加仓和减仓必须写股数")
        if self.action.is_opening:
            if self.order_type is not OrderType.LIMIT:
                raise ValueError("买入和加仓只能用限价单")
            if self.stop_price is None:
                raise ValueError("买入和加仓必须写止损价")
        elif (
            self.stop_price is not None
            or self.emergency_threshold is not None
            or self.max_price_deviation is not None
        ):
            raise ValueError("减仓和清仓不能写止损价、紧急阈值和允许价格偏离")
        return self
