"""执行模块的用例：提交订单，跟进订单到终态。见 order-lifecycle spec。"""

from datetime import datetime
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution.config import ExecutionConfig
from tradingagentspro.modules.execution.domain.order import Order, OrderPausedError, new_order
from tradingagentspro.modules.execution.ports import (
    Broker,
    BrokerUnavailableError,
    OrderRejectedError,
    OrderStore,
)
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import OutsideSessionError, TradingCalendar


class ExecutionService:
    """执行模块的入口：下单权限只在这里。

    submit 和 track 都可以重复调用：中途退出后再调用一次，不会重复下单，也不会重复撤单。
    同一笔订单一次只能有一个调用在处理，由调用方保证。
    """

    def __init__(
        self,
        broker: Broker,
        store: OrderStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: ExecutionConfig,
    ) -> None:
        self._broker = broker
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._config = config

    def submit(self, plan: TradePlan, decision: RiskDecision) -> Order:
        """按方案和它的再校验结果（通过或调整）提交订单，返回跟进一次之后的订单。

        这个方案已经有订单时不新建，只跟进一次。新建订单只能在常规交易时段内，
        否则抛 OutsideSessionError；这只股票有其他订单待提交或状态未知时，抛 OrderPausedError，
        调用方要等一会儿再从下单前再校验开始。
        """
        if self._store.get(plan.plan_id) is None:
            now = self._clock.now()
            session = self._calendar.session_at(now)
            if session is None:
                raise OutsideSessionError(f"{now} 不在常规交易时段内，不能提交新订单")
            if any(
                o.symbol == plan.symbol and o.blocks_new_orders for o in self._store.open_orders()
            ):
                raise OrderPausedError(f"{plan.symbol} 有待提交或状态未知的订单，暂停提交新订单")
            self._store.save(new_order(plan, decision, session, now))
        return self.track(plan.plan_id)

    def track(self, plan_id: UUID) -> Order:
        """跟进一次这个方案的订单：查询券商并更新状态，需要时提交或撤单，返回最新的订单。

        终态的订单，以及状态未知还没满等待时长的订单，直接返回，不调用券商。订单有变化时才保存。
        """
        order = self._store.get(plan_id)
        assert order is not None  # 只跟进提交过的方案
        now = self._clock.now()
        if order.status.is_terminal or not order.lookup_due(now, self._config.unknown_lookup_delay):
            return order
        updated = self._advance(order, now)
        if updated != order:
            self._store.save(updated)
        return updated

    def _advance(self, order: Order, now: datetime) -> Order:
        """先查询再处理，见 design D6。

        查到就按回报更新；受理过却查不到记为状态未知；没受理过又查不到，截止前提交、截止后记为
        已过期。之后到了截止时刻的请求撤单。任何一次券商调用超时或断线，就在已经得到的结果上
        记为状态未知。
        """
        try:
            report = self._broker.find(order)
            if report is not None:
                order = order.apply(report, now)
            elif order.broker_order_id is not None:
                order = order.mark_unknown(now)
            elif now >= order.deadline:
                order = order.expire_unsent()
            else:
                order = self._place(order, now)
            if order.cancel_due(now):
                self._broker.cancel(order)
                order = order.request_cancel()
        except BrokerUnavailableError:
            return order.mark_unknown(now)
        return order

    def _place(self, order: Order, now: datetime) -> Order:
        try:
            report = self._broker.place(order)
        except OrderRejectedError:
            return order.reject()
        return order.apply(report, now)
