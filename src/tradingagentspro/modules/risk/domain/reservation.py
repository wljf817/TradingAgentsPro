"""预留：已通过、尚未结束的方案占用的额度，见 risk-reservations spec。

记录只保存方案、风控结果、累计成交和是否已释放。占用多少额度在每次检查时按剩余股数和
当前持仓计算，所以加仓部分成交、整只股票的止损价改变之后，预留仍然和真实风险一致。
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Self

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.domain.exposure import (
    ZERO,
    holding_of,
    holding_risk,
    merged_risk,
)
from tradingagentspro.modules.risk.domain.request import OpeningReservation, SellReservation


@dataclass(frozen=True, slots=True)
class Reservation:
    """一个通过或调整的方案的预留。

    - decision：风控结果，最终股数就是预留股数
    - filled：这个方案的订单到目前为止累计成交的股数
    - released：方案或订单已经结束，剩余部分已经释放

    没有释放、剩余股数大于 0 时是有效预留；其他情况都已结束，不再变化。
    """

    plan: TradePlan
    decision: RiskDecision
    filled: int = 0
    released: bool = False

    @property
    def quantity(self) -> int:
        """预留股数：风控给出的最终股数。"""
        assert self.decision.quantity is not None  # 只有通过或调整的方案才有预留
        return self.decision.quantity

    @property
    def remaining(self) -> int:
        """剩余股数：预留股数减去累计成交股数。"""
        return self.quantity - self.filled

    @property
    def is_active(self) -> bool:
        return not self.released and self.remaining > 0

    def fill(self, filled: int) -> Self:
        """上报累计成交股数；已结束的预留原样返回。"""
        if not self.is_active:
            return self
        return replace(self, filled=filled)

    def release(self) -> Self:
        """方案或订单结束，释放剩余部分；已结束的预留原样返回。"""
        if not self.is_active:
            return self
        return replace(self, released=True)


def opening_reservation(
    reservation: Reservation, account: AccountSnapshot, buffer_ratio: Decimal
) -> OpeningReservation:
    """开仓预留占用的资金、市值和风险，按剩余股数和当前持仓计算。

    风险只计这只股票增加的部分，因为已有持仓的计划风险已经计入组合合计风险。增加的部分
    为负数时记 0：加仓成交之前，整只股票还是旧的止损价。
    """
    plan, quantity = reservation.plan, reservation.remaining
    assert plan.limit_price is not None  # 方案格式保证开仓方案有限价和止损价
    assert plan.stop_price is not None
    holding = holding_of(account, plan.symbol)
    merged = merged_risk(holding, quantity, plan.limit_price, plan.stop_price, buffer_ratio)
    held = holding_risk(holding, buffer_ratio) if holding else ZERO
    value = quantity * plan.limit_price
    return OpeningReservation(
        symbol=plan.symbol,
        sector=plan.sector,
        cash=value * (1 + buffer_ratio),
        market_value=value,
        risk=max(ZERO, merged - held),
    )


def sell_reservation(reservation: Reservation) -> SellReservation:
    """卖出预留占用的股数：剩余股数。"""
    return SellReservation(symbol=reservation.plan.symbol, quantity=reservation.remaining)
