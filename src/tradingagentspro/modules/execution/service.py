"""执行模块的用例：提交订单，跟进订单到终态，提前撤单，对齐止损单，和券商对账，记交易记录。

见 order-lifecycle spec、stop-protection spec、reconciliation spec 和 trade-records spec。
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution.config import ExecutionConfig
from tradingagentspro.modules.execution.domain.account import Account, account_of
from tradingagentspro.modules.execution.domain.order import (
    Order,
    OrderPausedError,
    OrderSide,
    new_order,
    opening_order,
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
    stop_withdrawn,
    stop_yields,
)
from tradingagentspro.modules.execution.domain.protection import (
    backup_stop_due as _backup_stop_due,
)
from tradingagentspro.modules.execution.domain.reconciliation import (
    Listing,
    NotReconciledError,
    ReconcileMismatchError,
    Reconciliation,
    extra_stops,
    position_explained,
    stray_orders,
)
from tradingagentspro.modules.execution.domain.trade import trade_of_order, trade_of_stop
from tradingagentspro.modules.execution.ports import (
    Broker,
    BrokerUnavailableError,
    OrderRejectedError,
    OrderStore,
    ProtectionStore,
    StopBroker,
    TradeStore,
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
        trades: TradeStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: ExecutionConfig,
    ) -> None:
        self._broker = broker
        self._stops = stops
        self._store = store
        self._protections = protections
        self._trades = trades
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._last: Reconciliation | None = None  # 最近一次完成的对账；新建以后还没有

    def submit(self, plan: TradePlan, decision: RiskDecision) -> Order:
        """按方案和它的再校验结果（通过或调整）提交订单，返回跟进一次之后的订单。

        这个方案已经有订单时不新建，只跟进一次。新建订单之前依次检查：
        - 还没完成过对账时，抛 NotReconciledError，调用方等对账完成再从下单前再校验开始
        - 最近一次对账有不一致时，买单抛 ReconcileMismatchError
        - 只能在常规交易时段内，否则抛 OutsideSessionError
        - 这只股票有其他订单待提交或状态未知时，抛 OrderPausedError，调用方要等一会儿再从
          下单前再校验开始
        - 买单的股票是无保护仓位时，抛 UnprotectedPositionError
        """
        if self._store.get(plan.plan_id) is None:
            if self._last is None:
                raise NotReconciledError("执行模块启动以后还没完成过对账，不能提交新订单")
            if plan.action.is_opening and not self._last.clean:
                raise ReconcileMismatchError("最近一次对账有不一致，暂停开仓")
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
            self._save_order(new_order(plan, decision, session, now))
        return self.track(plan.plan_id)

    def track(self, plan_id: UUID) -> Order:
        """跟进一次这个方案的订单：查询券商并更新状态，需要时提交或撤单，返回最新的订单。

        终态的订单，以及状态未知还没满等待时长的订单，直接返回，不调用券商。订单有变化时才保存。
        还没提交出去的卖单，先对齐止损单，止损单让出股数之后才提交，见 design D5；买单和止损单
        错开时，还没提交出去的买单也先对齐，止损单撤掉之后才提交，见 update-stagger-buys-and-stops
        的 design D3。有撤单要求的不提交，也就不用先对齐，见 update-order-emergency-cancel 的
        design D2。
        跟进之后对齐这只股票的止损单；已经对齐过、订单又没变化时不再对齐。
        """
        return self._track(plan_id, None)

    def _track(self, plan_id: UUID, listing: Listing | None) -> Order:
        """跟进一次订单。对账时 listing 是读到的券商挂单，平时为 None。

        见 add-reconciliation 的 design D4。
        """
        order = self._store.get(plan_id)
        assert order is not None  # 只跟进提交过的方案
        now = self._clock.now()
        if order.status.is_terminal or not order.lookup_due(now, self._config.unknown_lookup_delay):
            return order
        aligned, may_place = False, True
        if order.unsent and not order.cancel_wanted and self._waits_for_stop(order.side):
            protection, target = self._align(order.symbol, now, listing)
            aligned = True
            if order.side is OrderSide.SELL:
                may_place = target is not None and stop_yields(protection, target)
            else:
                may_place = target is not None and stop_withdrawn(protection)
        updated = self._advance(order, now, may_place, listing)
        if updated != order:
            self._save_order(updated)
        if not aligned or updated != order:
            self._align(updated.symbol, now, listing)
        return updated

    def _waits_for_stop(self, side: OrderSide) -> bool:
        """提交之前要先对齐止损单：卖单总是；买单在买单和止损单错开时。"""
        return side is OrderSide.SELL or self._config.stagger_buys_and_stops

    def cancel_orders(self, symbol: str) -> tuple[Order, ...]:
        """撤掉这只股票全部没结束的订单（买单和卖单），返回跟进一次之后的这些订单。

        见 order-lifecycle spec「提前撤单」。任何时刻都可以调用，也可以重复调用。
        """
        return self._cancel(o for o in self._store.open_orders() if o.symbol == symbol)

    def cancel_buy_orders(self) -> tuple[Order, ...]:
        """撤掉全部没结束的买单，返回跟进一次之后的这些订单。

        见 order-lifecycle spec「提前撤单」。任何时刻都可以调用，也可以重复调用。
        """
        return self._cancel(o for o in self._store.open_orders() if o.side is OrderSide.BUY)

    def _cancel(self, orders: Iterable[Order]) -> tuple[Order, ...]:
        """先给每笔订单记下撤单要求并保存，再各跟进一次：中途退出时，撤单要求都已经记下。"""
        targets = tuple(orders)
        for order in targets:
            if not order.cancel_wanted:
                self._save_order(order.want_cancel())
        return tuple(self.track(o.plan_id) for o in targets)

    def protect(self, symbol: str) -> Protection | None:
        """单独对齐这只股票的止损单，返回最新的止损保护；没有记录、也不需要止损单时为 None。

        给定时任务调用；跟进订单之后也会对齐。可以重复调用，不会挂出两张止损单。
        """
        protection, _ = self._align(symbol, self._clock.now())
        return protection

    def reconcile(self) -> Reconciliation:
        """和券商全量对账，返回不一致的地方，见 reconciliation spec「对账」和 design D1。

        先读券商的全部挂单、止损单和持仓；读取超时或断线时抛 BrokerUnavailableError，
        不改变任何东西。读到以后先比对，再跟进每笔未结束的订单、对齐需要对齐的股票，
        最后找来历不明的持仓。完成以后记为最近一次对账。可以重复调用。
        """
        working = self._broker.working_orders()
        working_stops = self._stops.working_stops()
        positions = self._broker.all_positions()
        stray = stray_orders(working, self._store.open_orders())
        in_use = {
            symbol
            for symbol in {s.symbol for s in working_stops}
            if (p := self._protections.get(symbol))
            and p.stop is not None
            and p.stop.status is not StopStatus.ENDED
        }
        extra = extra_stops(working_stops, in_use)
        listing = Listing(
            order_ids=frozenset(w.broker_order_id for w in working),
            stop_symbols=frozenset(s.symbol for s in working_stops),
        )
        for order in self._store.open_orders():
            self._track(order.plan_id, listing)
        for symbol in sorted(set(positions) | {p.symbol for p in self._protections.active()}):
            self._align(symbol, self._clock.now(), listing)
        unexplained = tuple(
            (symbol, shares)
            for symbol, shares in sorted(positions.items())
            if not position_explained(self._store.of_symbol(symbol))
        )
        self._last = Reconciliation(stray, extra, unexplained)
        return self._last

    def open_orders(self) -> tuple[Order, ...]:
        """全部没有进入终态的订单，按创建的先后；只读状态库，不调用券商。

        给盘中巡检用，见 add-session-patrol 的 design D6。
        """
        return tuple(sorted(self._store.open_orders(), key=lambda o: o.created_at))

    def order(self, plan_id: UUID) -> Order | None:
        """这个方案的订单，包括已经结束的；没有订单时为 None。只读状态库，不调用券商。"""
        return self._store.get(plan_id)

    def protection(self, symbol: str) -> Protection | None:
        """这只股票的止损保护；没有记录时为 None。只读状态库，不调用券商。"""
        return self._protections.get(symbol)

    def symbols_to_protect(self) -> tuple[str, ...]:
        """需要对齐止损单的股票，按代码排序：券商那边有持仓的，加上止损保护需要对齐的。

        和 reconcile 第 4 步的范围相同，见 add-session-patrol 的 design D5。只读券商的全部持仓，
        超时或断线时抛 BrokerUnavailableError。
        """
        positions = self._broker.all_positions()
        return tuple(sorted(set(positions) | {p.symbol for p in self._protections.active()}))

    def account(self) -> Account:
        """账户数据：券商账户加上本系统的记录，见 trade-records spec「账户数据」和 design D7。

        先读券商账户，超时或断线时抛 BrokerUnavailableError；再读这些持仓的订单和全部有成交的
        交易记录。可以在任何时刻调用，不要求已经完成对账。
        """
        broker = self._broker.account()
        orders = {p.symbol: self._store.of_symbol(p.symbol) for p in broker.positions}
        return account_of(broker, orders, self._trades.traded(), self._clock.now(), self._calendar)

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

    def _advance(
        self, order: Order, now: datetime, may_place: bool, listing: Listing | None
    ) -> Order:
        """先查询再处理，见 add-order-state-machine 的 design D6。

        查到就按回报更新；受理过却查不到记为状态未知，对账时 listing 里也没有它就结束；没受理过
        又查不到，截止后记为已过期，截止前有撤单要求的记为已撤销，否则提交（may_place 为假时
        这次不提交）。之后到了截止时刻或者有撤单要求的请求撤单。任何一次券商调用超时或断线，
        就在已经得到的结果上记为状态未知。
        """
        try:
            report = self._broker.find(order)
            if report is not None:
                order = order.apply(report, now)
            elif order.broker_order_id is not None:
                gone = listing is not None and order.broker_order_id not in listing.order_ids
                order = order.end_missing(now) if gone else order.mark_unknown(now)
            elif now >= order.deadline:
                order = order.expire_unsent()
            elif order.cancel_wanted:
                order = order.cancel_unsent()
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

    def _align(
        self, symbol: str, now: datetime, listing: Listing | None = None
    ) -> tuple[Protection | None, StopTarget | None]:
        """对齐这只股票的止损单，见 add-stop-protection 的 design D4。

        先查询当前这张止损单并按回报更新，再查询持仓、计算止损目标，按 next_action 处理，
        并按目标记下是否错开中（update-stagger-buys-and-stops 的 design D4）。
        返回最新的止损保护和这次的止损目标。止损单状态未知、还没满等待时长时不调用券商；
        查询后仍是状态未知，或者查询持仓失败时，不再做别的处理。这些情况目标为 None。
        止损单的券商调用超时或断线，止损单记为状态未知，见 add-stop-protection 的 design D9。
        对账时 listing 里这只股票一张止损单都没有，受理过却查不到的止损单就结束，
        见 add-reconciliation 的 design D4。
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
            if report is not None:
                stop = stop.apply(report, now)
            elif listing is not None and stop.broker_order_id is not None:
                gone = symbol not in listing.stop_symbols
                stop = stop.end() if gone else stop.not_found(now)
            else:
                stop = stop.not_found(now)
            protection = protection.with_stop(stop)
            if stop.status is StopStatus.UNKNOWN:
                return self._save(original, protection), None
        try:
            position = self._broker.position(symbol)
        except BrokerUnavailableError:
            return self._save(original, protection), None
        target = stop_target(
            position, self._store.of_symbol(symbol), stagger=self._config.stagger_buys_and_stops
        )
        protection = self._act(protection, target, now).with_staggered(target.staggered)
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
            protection = protection.new_stop(target, now)
            self._save_protection(protection)
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
            self._save_protection(protection)
        return protection

    def _save_order(self, order: Order) -> None:
        """先写交易记录，再保存订单，见 add-trade-records 的 design D2。"""
        self._trades.save(trade_of_order(order, self._calendar))
        self._store.save(order)

    def _save_protection(self, protection: Protection) -> None:
        """有止损单时先写它的交易记录，再保存止损保护，见 add-trade-records 的 design D2。

        来源方案是这只股票止损价来自的开仓订单的方案。
        """
        if protection.stop is not None:
            source = opening_order(self._store.of_symbol(protection.symbol))
            plan_id = None if source is None else source.plan_id
            self._trades.save(trade_of_stop(protection.stop, plan_id, self._calendar))
        self._protections.save(protection)
