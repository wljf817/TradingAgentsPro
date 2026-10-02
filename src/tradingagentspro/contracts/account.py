"""账户数据：风控检查方案时用的权益、资金和持仓。

首次检查时来自状态库，下单前再校验时来自券商。数据来自本系统和券商，不加约束。
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    """一只持仓。止损价是这只股票当前唯一的止损价。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    sector: str
    quantity: int
    average_cost: Decimal
    last_price: Decimal
    stop_price: Decimal


class AccountSnapshot(BaseModel):
    """检查方案时的账户数据。

    settled_cash 是已结算、可以用于开仓的现金，不含未结算的卖出款。
    last_trade_days 是每只股票最近一次成交（买入、卖出、止损）所在的交易日。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    equity: Decimal
    settled_cash: Decimal
    positions: tuple[Position, ...] = ()
    last_trade_days: Mapping[str, date] = Field(default_factory=dict)
