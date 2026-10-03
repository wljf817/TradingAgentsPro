"""风控的用例：检查方案并预留额度，上报成交，释放预留。见 risk-reservations spec。"""

from uuid import UUID

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.modules.risk.domain.engine import evaluate_plan
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

    一次只能检查一个方案：M1 只有同步调用，M2 由调用方保证串行。上报成交和释放可以插在
    检查中间，只会让那次检查多算预留。
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
        reservations = self._store.active()
        buffer_ratio = self._config.cost_buffer_ratio
        request = RiskRequest(
            plan=plan,
            account=account,
            opening_reservations=tuple(
                opening_reservation(r, account, buffer_ratio)
                for r in reservations
                if r.plan.action.is_opening
            ),
            sell_reservations=tuple(
                sell_reservation(r) for r in reservations if not r.plan.action.is_opening
            ),
            daily_loss_breaker_tripped=daily_loss_breaker_tripped,
            now=self._clock.now(),
            calendar=self._calendar,
            config=self._config,
        )
        decision = evaluate_plan(request)
        if decision.quantity is not None:
            self._store.save(Reservation(plan=plan, decision=decision))
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
