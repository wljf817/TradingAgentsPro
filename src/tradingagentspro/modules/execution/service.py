"""执行模块的用例：提交订单，跟进订单到终态，对齐止损单。

见 order-lifecycle spec 和 stop-protection spec。
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution.config import ExecutionConfig
from tradingagentspro.modules.execution.domain.order import (
    Order,
    OrderPausedError,
    OrderSide,
    new_order,
)
from tradingagentspro.modules.execution.domain.protection import (
    Protection,
    StopAction,
    StopStatus,
    StopTarget,
    UnprotectedPositionError,
    is_aligned,
    next_action,
    stop_price_of,
    stop_target,
    stop_yields,
)
from tradingagentspro.modules.execution.domain.protection import (
    backup_stop_due as _backup_stop_due,
)
from tradingagentspro.modules.execution.ports import (
    Broker,
    BrokerUnavailableError,
    OrderRejectedError,
    OrderStore,
    ProtectionStore,
    StopBroker,
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
        stops: StopBroker,
        store: OrderStore,
        protections: ProtectionStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: ExecutionConfig,
    ) -> None:
        self._broker = broker
        self._stops = stops
        self._store = store
        self._protections = protections
        self._clock = clock
        self._calendar = calendar
        self._config = config

    def submit(self, plan: TradePlan, decision: RiskDecision) -> Order:
        """按方案和它的再校验结果（通过或调整）提交订单，返回跟进一次之后的订单。

        这个方案已经有订单时不新建，只跟进一次。新建订单只能在常规交易时段内，
        否则抛 OutsideSessionError；这只股票有其他订单待提交或状态未知时，抛 OrderPausedError，
        调用方要等一会儿再从下单前再校验开始；买单的股票是无保护仓位时，抛
        UnprotectedPositionError。
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
            protection = self._protections.get(plan.symbol)
            if plan.action.is_opening and protection and protection.unprotected_since:
                raise UnprotectedPositionError(f"{plan.symbol} 是无保护仓位，补挂成功之前不能买入")
            self._store.save(new_order(plan, decision, session, now))
        return self.track(plan.plan_id)

    def track(self, plan_id: UUID) -> Order:
        """跟进一次这个方案的订单：查询券商并更新状态，需要时提交或撤单，返回最新的订单。

        终态的订单，以及状态未知还没满等待时长的订单，直接返回，不调用券商。订单有变化时才保存。
        还没提交出去的卖单，先对齐止损单，止损单让出股数之后才提交，见 design D5。
        跟进之后对齐这只股票的止损单；已经对齐过、订单又没变化时不再对齐。
        """
        order = self._store.get(plan_id)
        assert order is not None  # 只跟进提交过的方案
        now = self._clock.now()
        if order.status.is_terminal or not order.lookup_due(now, self._config.unknown_lookup_delay):
            return order
        aligned, may_place = False, True
        if order.side is OrderSide.SELL and order.unsent:
            protection, target = self._align(order.symbol, now)
            aligned = True
            may_place = target is not None and stop_yields(protection, target)
        updated = self._advance(order, now, may_place)
        if updated != order:
            self._store.save(updated)
        if not aligned or updated != order:
            self._align(updated.symbol, now)
        return updated

    def protect(self, symbol: str) -> Protection | None:
        """单独对齐这只股票的止损单，返回最新的止损保护；没有记录、也不需要止损单时为 None。

        给定时任务调用；跟进订单之后也会对齐。可以重复调用，不会挂出两张止损单。
        """
        protection, _ = self._align(symbol, self._clock.now())
        return protection

    def backup_stop_due(self, symbol: str, last_price: Decimal) -> bool:
        """这只股票的本地备用止损现在该不该触发：无保护仓位、盘中、最新价不高于止损价。

        触发之后怎样卖出由调用方决定。见 stop-protection spec「本地备用止损的触发」。
        """
        return _backup_stop_due(
            self._protections.get(symbol),
            stop_price_of(self._store.of_symbol(symbol)),
            last_price,
            in_session=self._calendar.session_at(self._clock.now()) is not None,
        )

    def _advance(self, order: Order, now: datetime, may_place: bool) -> Order:
        """先查询再处理，见 add-order-state-machine 的 design D6。

        查到就按回报更新；受理过却查不到记为状态未知；没受理过又查不到，截止前提交（may_place
        为假时这次不提交）、截止后记为已过期。之后到了截止时刻的请求撤单。任何一次券商调用超时
        或断线，就在已经得到的结果上记为状态未知。
        """
        try:
            report = self._broker.find(order)
            if report is not None:
                order = order.apply(report, now)
            elif order.broker_order_id is not None:
                order = order.mark_unknown(now)
            elif now >= order.deadline:
                order = order.expire_unsent()
            elif may_place:
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

    def _align(self, symbol: str, now: datetime) -> tuple[Protection | None, StopTarget | None]:
        """对齐这只股票的止损单，见 design D4。

        先查询当前这张止损单并按回报更新，再查询持仓、计算止损目标，按 next_action 处理。
        返回最新的止损保护和这次的止损目标。止损单状态未知、还没满等待时长时不调用券商；
        查询后仍是状态未知，或者查询持仓失败时，不再做别的处理。这些情况目标为 None。
        止损单的券商调用超时或断线，止损单记为状态未知，见 design D9。
        """
        original = self._protections.get(symbol)
        protection = original or Protection(symbol)
        stop = protection.stop
        if stop is not None and not stop.lookup_due(now, self._config.unknown_lookup_delay):
            return original, None
        if stop is not None and stop.status is not StopStatus.ENDED:
            try:
                report = self._stops.find(stop)
            except BrokerUnavailableError:
                return self._save(original, protection.with_stop(stop.mark_unknown(now))), None
            stop = stop.apply(report, now) if report else stop.not_found(now)
            protection = protection.with_stop(stop)
            if stop.status is StopStatus.UNKNOWN:
                return self._save(original, protection), None
        try:
            position = self._broker.position(symbol)
        except BrokerUnavailableError:
            return self._save(original, protection), None
        target = stop_target(position, self._store.of_symbol(symbol))
        protection = self._act(protection, target, now)
        if is_aligned(protection.stop, target) or target.quantity == 0:
            protection = protection.mark_protected()
        return self._save(original, protection), target

    def _act(self, protection: Protection, target: StopTarget, now: datetime) -> Protection:
        """按对齐时的处理调用券商。改单、撤单只是请求，不改变记录里的止损单。

        挂单、改单被拒时立即重试，都被拒就标记无保护仓位，见 design D6。
        """
        action = next_action(protection, target)
        if action is StopAction.PLACE:
            return self._place_stop(protection, target, now)
        stop = protection.stop
        match action:
            case StopAction.MODIFY:
                return self._modify_stop(protection, target, now)
            case StopAction.CANCEL:
                assert stop is not None
                try:
                    self._stops.cancel(stop)
                except BrokerUnavailableError:
                    return protection.with_stop(stop.mark_unknown(now))
            case StopAction.DROP:
                assert stop is not None
                return protection.with_stop(stop.end())
        return protection

    def _place_stop(self, protection: Protection, target: StopTarget, now: datetime) -> Protection:
        """按目标挂单。新建的止损单先记为还没挂出去并保存，再挂单。"""
        stop = protection.stop
        if stop is None or stop.status is StopStatus.ENDED:
            protection = protection.new_stop(target)
            self._protections.save(protection)
        else:
            protection = protection.with_stop(stop.aim(target))
        stop = protection.stop
        assert stop is not None
        for _ in range(self._attempts):
            try:
                report = self._stops.place(stop)
            except OrderRejectedError:
                continue
            except BrokerUnavailableError:
                return protection.with_stop(stop.mark_unknown(now))
            return protection.with_stop(stop.apply(report, now))
        return protection.with_stop(stop.end()).mark_unprotected(now)

    def _modify_stop(self, protection: Protection, target: StopTarget, now: datetime) -> Protection:
        """请求改单为目标，记下请求的股数；被拒的改单不改变券商那边的止损单。

        超时或断线时请求可能已经生效，所以也记下请求的股数，再记为状态未知。
        """
        stop = protection.stop
        assert stop is not None and target.stop_price is not None
        for _ in range(self._attempts):
            try:
                self._stops.modify(stop, target.quantity, target.stop_price)
            except OrderRejectedError:
                continue
            except BrokerUnavailableError:
                return protection.with_stop(stop.request(target.quantity).mark_unknown(now))
            return protection.with_stop(stop.request(target.quantity))
        return protection.mark_unprotected(now)

    @property
    def _attempts(self) -> int:
        """挂单或改单最多发几次：第一次加上立即重试的次数。"""
        return self._config.stop_immediate_retries + 1

    def _save(self, original: Protection | None, protection: Protection) -> Protection | None:
        """有变化时保存。没有记录、也不需要止损单的股票不新建记录。"""
        if original is None and protection == Protection(protection.symbol):
            return None
        if protection != original:
            self._protections.save(protection)
        return protection
