"""风控的用例：检查方案并预留额度，下单前再校验，上报成交，释放预留。

见 risk-reservations spec 和 pre-order-recheck spec。
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.modules.risk.domain.engine import evaluate_plan
from tradingagentspro.modules.risk.domain.recheck import (
    OutsideSessionError,
    plan_ended,
    recheck_plan,
)
from tradingagentspro.modules.risk.domain.request import RiskRequest
from tradingagentspro.modules.risk.domain.reservation import (
    Reservation,
    opening_reservation,
    sell_reservation,
)
from tradingagentspro.modules.risk.ports import ReservationStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar


class RiskService:
    """风控引擎的入口。

    一次只能检查或再校验一个方案：M1 只有同步调用，M2 由调用方保证串行。上报成交和释放
    可以插在中间，只会让那次检查多算预留。
    """

    def __init__(
        self,
        store: ReservationStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: RiskConfig,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._config = config

    def check(
        self, plan: TradePlan, account: AccountSnapshot, daily_loss_breaker_tripped: bool
    ) -> RiskDecision:
        """按全部有效预留之外的剩余额度检查方案；通过或调整时，按最终股数预留。

        账户数据和熔断状态由调用方传入。已经有预留的方案直接返回第一次的结果：
        重试时如果重新判定，方案会被自己的预留挡住，而这份预留再也没有人释放。
        """
        existing = self._store.get(plan.plan_id)
        if existing is not None:
            return existing.decision
        request = self._request(plan, account, daily_loss_breaker_tripped, self._clock.now())
        decision = evaluate_plan(request)
        if decision.quantity is not None:
            self._store.save(Reservation(plan=plan, decision=decision))
        return decision

    def recheck(
        self,
        plan_id: UUID,
        account: AccountSnapshot,
        last_price: Decimal,
        daily_loss_breaker_tripped: bool,
    ) -> RiskDecision:
        """提交订单前用实时数据再校验方案；通过或调整时预留改为最终股数，拒绝时方案作废并释放。

        只能在常规交易时段内调用，否则抛 OutsideSessionError。账户数据、实时最新价和熔断状态
        由调用方从券商读取。没有有效预留的方案给出"方案已结束"，不改变任何预留。
        """
        now = self._clock.now()
        if self._calendar.session_at(now) is None:
            raise OutsideSessionError(f"{now} 不在常规交易时段内，不能再校验")
        reservation = self._store.get(plan_id)
        if reservation is None or not reservation.is_active:
            return plan_ended()
        request = self._request(reservation.plan, account, daily_loss_breaker_tripped, now)
        decision = recheck_plan(request, reservation, last_price)
        if decision.quantity is None:
            self._store.save(reservation.release())
        else:
            self._store.save(reservation.recheck(decision))
        return decision

    def record_fill(self, plan_id: UUID, filled_quantity: int) -> None:
        """上报方案的累计成交股数，预留缩小为剩余股数；没有预留时什么都不做。"""
        reservation = self._store.get(plan_id)
        if reservation is not None:
            self._store.save(reservation.fill(filled_quantity))

    def release(self, plan_id: UUID) -> None:
        """方案或订单结束时释放剩余的预留；没有预留时什么都不做。"""
        reservation = self._store.get(plan_id)
        if reservation is not None:
            self._store.save(reservation.release())

    def _request(
        self,
        plan: TradePlan,
        account: AccountSnapshot,
        daily_loss_breaker_tripped: bool,
        now: datetime,
    ) -> RiskRequest:
        """检查这个方案的输入：其他方案的预留是除它自己以外的全部有效预留。"""
        others = [r for r in self._store.active() if r.plan.plan_id != plan.plan_id]
        buffer_ratio = self._config.cost_buffer_ratio
        return RiskRequest(
            plan=plan,
            account=account,
            opening_reservations=tuple(
                opening_reservation(r, account, buffer_ratio)
                for r in others
                if r.plan.action.is_opening
            ),
            sell_reservations=tuple(
                sell_reservation(r) for r in others if not r.plan.action.is_opening
            ),
            daily_loss_breaker_tripped=daily_loss_breaker_tripped,
            now=now,
            calendar=self._calendar,
            config=self._config,
        )
