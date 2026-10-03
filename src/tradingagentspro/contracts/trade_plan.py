"""交易方案：结论里可以执行的部分，交给风控和执行模块。"""

from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        elif self.stop_price is not None or self.emergency_threshold is not None:
            raise ValueError("减仓和清仓不能写止损价和紧急阈值")
        return self
