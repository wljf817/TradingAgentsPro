"""预留：已通过、尚未结束的方案占用的额度，见 risk-reservations spec。

记录只保存方案、风控结果、累计成交和是否已释放。占用多少额度在每次检查时按剩余股数和
当前持仓计算，所以加仓部分成交、整只股票的止损价改变之后，预留仍然和真实风险一致。
"""

from dataclasses import dataclass, replace
from typing import Self

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.config import RiskConfig
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

    - decision：首次检查的风控结果
    - rechecked：最近一次下单前再校验的结果（通过或调整）；没有再校验过时为 None
    - filled：这个方案的订单到目前为止累计成交的股数
    - released：方案或订单已经结束，剩余部分已经释放

    没有释放、剩余股数大于 0 时是有效预留；其他情况都已结束，不再变化。
    """

    plan: TradePlan
    decision: RiskDecision
    rechecked: RiskDecision | None = None
    filled: int = 0
    released: bool = False

    @property
    def quantity(self) -> int:
        """预留股数：最近一次再校验的最终股数；没有再校验过时是首次检查的最终股数。"""
        latest = self.decision if self.rechecked is None else self.rechecked
        assert latest.quantity is not None  # 只有通过或调整的结果才会存进预留
        return latest.quantity

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

    def recheck(self, decision: RiskDecision) -> Self:
        """再校验通过或调整，预留股数改为它的最终股数；已结束的预留原样返回。"""
        if not self.is_active:
            return self
        return replace(self, rechecked=decision)

    def release(self) -> Self:
        """方案或订单结束，释放剩余部分；已结束的预留原样返回。"""
        if not self.is_active:
            return self
        return replace(self, released=True)


def opening_reservation(
    reservation: Reservation, account: AccountSnapshot, config: RiskConfig
) -> OpeningReservation:
    """开仓预留占用的资金、市值和风险，按剩余股数和当前持仓计算。

    资金加一笔买入佣金。风险只计这只股票增加的部分，因为已有持仓的计划风险已经计入组合
    合计风险，增加的部分自然包含方案买入的那一笔佣金。增加的部分为负数时记 0：加仓成交
    之前，整只股票还是旧的止损价。
    """
    plan, quantity = reservation.plan, reservation.remaining
    assert plan.limit_price is not None  # 方案格式保证开仓方案有限价和止损价
    assert plan.stop_price is not None
    holding = holding_of(account, plan.symbol)
    merged = merged_risk(holding, quantity, plan.limit_price, plan.stop_price, config)
    held = holding_risk(holding, config) if holding else ZERO
    value = quantity * plan.limit_price
    return OpeningReservation(
        symbol=plan.symbol,
        sector=plan.sector,
        cash=value * (1 + config.cost_buffer_ratio) + config.commission_per_order,
        market_value=value,
        risk=max(ZERO, merged - held),
    )


def sell_reservation(reservation: Reservation) -> SellReservation:
    """卖出预留占用的股数：剩余股数。"""
    return SellReservation(symbol=reservation.plan.symbol, quantity=reservation.remaining)
