"""对账的执行用例测试：补上停机期间的变化、找出不一致、结束查不到的订单和止损单。

场景见 openspec 的 reconciliation spec。测试用假券商和假止损单接口安排券商那边的情况，
用测试时钟拨动时间；"重启"是用同一套存储和假券商重新创建执行用例，新建的执行用例还没有对过账。
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import (
    BASELINE_NOW,
    add_aaa,
    add_bbb,
    baseline_plan,
    decision,
    execution_service,
    next_plan_id,
    reduce_bbb,
)
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    ExecutionConfig,
    ExecutionService,
    NotReconciledError,
    Order,
    OrderSide,
    OrderStatus,
    Protection,
    ReconcileMismatchError,
    Reconciliation,
    StopOrder,
    StopStatus,
    WorkingOrder,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
STOP_1, STOP_2 = "BBB-STOP-1", "BBB-STOP-2"


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """基准交易日 2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、假止损单接口、测试时钟。"""

    store: InMemoryOrderStore
    broker: FakeBroker
    stops: FakeStopBroker
    protections: InMemoryProtectionStore
    clock: FakeClock
    service: ExecutionService

    def restart(self) -> None:
        """进程重启：用同一套存储和假券商重新创建执行用例，清掉之前的调用记录。"""
        self.service = execution_service(
            self.store,
            self.broker,
            self.clock,
            ExecutionConfig(),
            self.stops,
            self.protections,
            reconciled=False,
        )
        self.broker.calls.clear()
        self.stops.calls.clear()

    def reconcile(self, at: datetime | None = None) -> Reconciliation:
        if at is not None:
            self.clock.set(at)
        return self.service.reconcile()

    def submit(self, plan: TradePlan, result: RiskDecision | None = None) -> Order:
        return self.service.submit(plan, result or decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime) -> Order:
        self.clock.set(at)
        return self.service.track(plan.plan_id)

    def order(self, plan: TradePlan) -> Order:
        order = self.store.get(plan.plan_id)
        assert order is not None
        return order

    def report(
        self,
        plan: TradePlan,
        status: BrokerOrderStatus,
        filled: int,
        average_price: str | None = None,
    ) -> None:
        """券商回报这笔订单变为……"""
        self.broker.update(str(plan.plan_id), status, filled, average_price)

    def stop(self, symbol: str = "BBB") -> StopOrder:
        protection = self.protections.get(symbol)
        assert protection is not None and protection.stop is not None
        return protection.stop

    def stop_calls(self) -> list[str]:
        """止损单接口收到的查询、挂单、改单和撤单，不含读取全部止损单。"""
        return [op for op, _ in self.stops.calls if op != "working_stops"]

    def hold_100(self) -> TradePlan:
        """持有 BBB 100 股，止损单已对齐：P1 全部成交，BBB-STOP-1 挂着 100 股、止损价 45。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.report(p1, FILLED, 100, "50")
        self.broker.positions["BBB"] = 100
        self.track(p1, self.clock.now())
        assert self.stops.stop(STOP_1).quantity == 100
        return p1


def _env(now: datetime = BASELINE_NOW) -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(now)
    stops, protections = FakeStopBroker(), InMemoryProtectionStore()
    service = execution_service(store, broker, clock, ExecutionConfig(), stops, protections)
    return _Env(store, broker, stops, protections, clock, service)


# 对账


def test_buy_filled_while_down() -> None:
    """spec「停机期间买入成交」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.restart()
    env.report(p1, FILLED, 100, "49.95")
    env.broker.positions["BBB"] = 100

    result = env.reconcile()

    order = env.order(p1)
    assert (order.status, order.filled, order.average_price) == (
        OrderStatus.FILLED,
        100,
        Decimal("49.95"),
    )
    [placed] = env.stops.placed
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_1,
        100,
        Decimal(45),
    )
    assert result.clean


def test_stop_cancelled_while_down_is_placed_again() -> None:
    """spec「停机期间止损单被撤销，对账时补挂」。"""
    env = _env()
    env.hold_100()
    env.restart()
    env.stops.update(STOP_1, CANCELLED)

    result = env.reconcile()

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        100,
        Decimal(45),
    )
    assert result.clean


def test_stop_triggered_while_down() -> None:
    """spec「停机期间止损单触发」。"""
    env = _env()
    env.hold_100()
    env.restart()
    env.stops.update(STOP_1, FILLED, quantity=0)
    env.broker.positions["BBB"] = 0

    result = env.reconcile()

    assert env.stop().status is StopStatus.ENDED
    assert len(env.stops.placed) == 1
    assert result.clean


def test_reading_from_the_broker_fails() -> None:
    """spec「读取券商数据时断线」：读取全部持仓排在最后，前两项读取已经发生。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.restart()
    env.broker.fail_next("all_positions")

    with pytest.raises(BrokerUnavailableError):
        env.reconcile()

    assert env.broker.calls_for(str(p1.plan_id)) == []
    assert env.stop_calls() == []
    with pytest.raises(NotReconciledError):
        env.submit(add_aaa(), decision(50))


def test_reconciliation_completes_when_tracking_fails() -> None:
    """spec「跟进订单时断线，对账照样完成」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.restart()
    env.broker.fail_next("find")

    result = env.reconcile(at=_at(15, 10))

    assert result.clean
    order = env.order(p1)
    assert (order.status, order.unknown_since) == (OrderStatus.UNKNOWN, _at(15, 10))
    assert env.submit(add_aaa(), decision(50)).status is OrderStatus.SUBMITTED


def test_reconciling_twice() -> None:
    """spec「重复对账」。"""
    env = _env()
    env.hold_100()
    env.stops.calls.clear()

    first, second = env.reconcile(), env.reconcile()

    assert first.clean and second.clean
    assert set(env.stop_calls()) == {"find"}
    assert env.stops.stop_count(STOP_1) == 1
    assert len(env.stops.placed) == 1


# 不认得的挂单


def test_our_order_is_working() -> None:
    """spec「执行模块的订单挂着」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)

    assert env.reconcile().clean


def test_order_without_a_broker_order_id_yet() -> None:
    """spec「订单还没有记下券商订单号」。"""
    env, p1 = _env(), baseline_plan()
    env.broker.fail_next("place", after_effect=True)
    env.submit(p1)
    env.store.save(replace(env.order(p1), status=OrderStatus.PENDING, unknown_since=None))
    env.restart()

    result = env.reconcile()

    assert result.clean
    order = env.order(p1)
    assert order.status is OrderStatus.SUBMITTED
    assert order.broker_order_id is not None
    assert env.broker.order_count(str(p1.plan_id)) == 1


def test_second_order_with_the_same_client_order_id() -> None:
    """spec「同一个客户端订单 ID 的第二笔订单」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    assert env.order(p1).broker_order_id == "B1"
    second = env.broker.add_working("BBB", str(p1.plan_id), "B2")

    result = env.reconcile()

    assert result.stray_orders == (second,)
    assert env.broker.cancel_requests == []


def test_ended_order_still_working_at_the_broker() -> None:
    """spec「已经结束的订单在券商仍挂着」。"""
    env, p1 = _env(_at(15, 59, 50)), baseline_plan()
    env.broker.fail_next("place")
    env.submit(p1, decision(expires_at=_at(16)))
    assert env.track(p1, _at(16, 0, 20)).status is OrderStatus.EXPIRED
    late = env.broker.add_working("BBB", str(p1.plan_id))

    result = env.reconcile(at=_at(16, 10))

    assert result.stray_orders == (late,)
    assert env.order(p1).status is OrderStatus.EXPIRED


def test_order_never_seen() -> None:
    """spec「执行模块从没见过的订单」。"""
    env = _env()
    unknown = env.broker.add_working("CCC", "X1")

    result = env.reconcile()

    assert result.stray_orders == (unknown,)
    assert (unknown.symbol, unknown.client_order_id) == ("CCC", "X1")


# 多出来的止损单


def test_our_stop_accepted_before_its_result_was_saved() -> None:
    """spec「执行模块的止损单还没记下受理结果」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30)
    env.store.save(replace(env.order(p1), status=OrderStatus.PARTIALLY_FILLED, filled=30))
    env.broker.positions["BBB"] = 30
    pending = StopOrder(
        symbol="BBB",
        client_order_id=STOP_1,
        quantity=30,
        stop_price=Decimal(45),
        created_at=BASELINE_NOW,
    )
    env.protections.save(Protection("BBB", used=1, stop=pending))
    env.stops.accept(pending)
    env.restart()

    result = env.reconcile()

    assert result.clean
    assert env.stops.placed == []
    assert env.stops.stop_count(STOP_1) == 1
    assert env.stop().status is StopStatus.WORKING


def test_working_stop_without_a_stop_in_use() -> None:
    """spec「执行模块没有在用的止损单，券商却挂着一张」。"""
    env = _env()
    orphan = env.stops.add_working("BBB", "X2")

    result = env.reconcile()

    assert result.extra_stops == (orphan,)
    assert env.stops.cancel_requests == []


def test_two_working_stops_for_one_symbol() -> None:
    """spec「一只股票挂着两张止损单」。"""
    env = _env()
    env.hold_100()
    extra = env.stops.add_working("BBB", "X2")

    result = env.reconcile()

    ours = WorkingOrder("BBB", STOP_1, env.stops.stop(STOP_1).broker_order_id)
    assert result.extra_stops == (ours, extra)
    assert env.stops.cancel_requests == []


# 来历不明的持仓


def test_position_never_bought() -> None:
    """spec「执行模块没有买过的持仓」。"""
    env = _env()
    env.broker.positions["CCC"] = 100

    result = env.reconcile()

    assert result.unexplained == (("CCC", 100),)
    assert env.stops.placed == []


def test_an_unresolved_buy_explains_the_position() -> None:
    """spec「还没查清的买单能解释持仓」。"""
    env, p1 = _env(), baseline_plan()
    env.broker.fail_next("place", after_effect=True)
    env.submit(p1)
    env.report(p1, FILLED, 100, "50")
    env.broker.positions["BBB"] = 100

    result = env.reconcile(at=_at(15, 0, 10))

    assert env.broker.calls_for(str(p1.plan_id)) == ["find", "place"]
    assert env.order(p1).status is OrderStatus.UNKNOWN
    assert result.clean


def test_a_buy_that_ended_without_recorded_fills_does_not_explain_the_position() -> None:
    """spec「结束时没有记下成交的买单解释不了持仓」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id))
    env.report(p1, FILLED, 100, "50")
    env.broker.positions["BBB"] = 100

    result = env.reconcile(at=_at(15, 10))

    order = env.order(p1)
    assert (order.status, order.filled) == (OrderStatus.CANCELLED, 0)
    assert result.unexplained == (("BBB", 100),)


# 结束查不到的订单和止损单


def test_order_missing_from_the_broker_and_its_working_orders() -> None:
    """spec「订单查不到，券商挂单里也没有」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id))
    assert env.track(p1, _at(15, 5)).status is OrderStatus.UNKNOWN

    env.reconcile(at=_at(15, 10))

    order = env.order(p1)
    assert (order.status, order.filled) == (OrderStatus.CANCELLED, 0)
    assert env.broker.calls_for(str(p1.plan_id)).count("place") == 1
    p2 = add_bbb()
    assert env.submit(p2, decision(50)).status is OrderStatus.SUBMITTED


def test_missing_order_ends_as_expired_after_the_deadline_and_keeps_its_fills() -> None:
    """spec「截止时刻之后结束为已过期，保留成交」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30, "49.90")
    env.broker.positions["BBB"] = 30
    env.track(p1, BASELINE_NOW)
    assert env.stops.stop(STOP_1).quantity == 30
    env.broker.lose(str(p1.plan_id))

    result = env.reconcile(at=_at(20, 10))

    order = env.order(p1)
    assert (order.status, order.filled, order.average_price) == (
        OrderStatus.EXPIRED,
        30,
        Decimal("49.90"),
    )
    assert result.clean


def test_missing_order_still_in_the_working_orders() -> None:
    """spec「订单查不到，但券商挂单里有它」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id), listed=True)

    result = env.reconcile(at=_at(15, 10))

    order = env.order(p1)
    assert (order.status, order.unknown_since) == (OrderStatus.UNKNOWN, _at(15, 10))
    assert result.clean


def test_missing_stop_without_working_stops_for_the_symbol() -> None:
    """spec「止损单查不到，这只股票也没有挂着的止损单」。"""
    env = _env()
    env.hold_100()
    env.stops.lose(STOP_1)

    result = env.reconcile()

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        100,
        Decimal(45),
    )
    assert (env.stop().client_order_id, env.stop().status) == (STOP_2, StopStatus.WORKING)
    assert result.clean


def test_missing_stop_while_the_symbol_still_has_a_working_stop() -> None:
    """spec「止损单查不到，但这只股票还挂着一张止损单」。"""
    env = _env()
    env.hold_100()
    env.stops.lose(STOP_1, listed=True)

    result = env.reconcile(at=_at(15, 10))

    assert (env.stop().status, env.stop().unknown_since) == (StopStatus.UNKNOWN, _at(15, 10))
    assert len(env.stops.placed) == 1
    assert result.clean


def test_missing_order_is_not_ended_before_the_wait_is_over() -> None:
    """spec「还没满等待时长时不结束」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id))
    assert env.track(p1, _at(15)).status is OrderStatus.UNKNOWN
    finds = env.broker.calls_for(str(p1.plan_id)).count("find")

    env.reconcile(at=_at(15, 0, 10))

    assert env.broker.calls_for(str(p1.plan_id)).count("find") == finds
    order = env.order(p1)
    assert (order.status, order.unknown_since) == (OrderStatus.UNKNOWN, _at(15))


# 对账之前不接受新订单


def _close_ccc() -> TradePlan:
    """市价清仓 CCC（科技），不写股数。"""
    return TradePlan(
        plan_id=next_plan_id(),
        symbol="CCC",
        sector="科技",
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )


def test_new_orders_wait_for_the_first_reconciliation_after_a_restart() -> None:
    """spec「重启后先对账，才接受新订单」。"""
    env, p1 = _env(), baseline_plan()
    env.restart()

    with pytest.raises(NotReconciledError):
        env.submit(p1)
    assert env.store.get(p1.plan_id) is None
    assert env.broker.calls == []

    assert env.reconcile().clean
    assert env.submit(p1).status is OrderStatus.SUBMITTED


def test_sells_also_wait_for_the_reconciliation() -> None:
    """spec「卖单也要等对账」。"""
    env = _env()
    env.hold_100()
    env.restart()
    p3 = reduce_bbb(60)

    with pytest.raises(NotReconciledError):
        env.submit(p3, decision(60))

    assert env.store.get(p3.plan_id) is None
    assert env.broker.calls_for(str(p3.plan_id)) == []


def test_a_failed_reconciliation_does_not_count() -> None:
    """spec「对账失败时仍不接受新订单」。"""
    env, p1 = _env(), baseline_plan()
    env.restart()
    env.broker.fail_next("working_orders")
    with pytest.raises(BrokerUnavailableError):
        env.reconcile()

    with pytest.raises(NotReconciledError):
        env.submit(p1)
    assert env.store.get(p1.plan_id) is None


def test_existing_orders_and_tracking_need_no_reconciliation() -> None:
    """spec「已经有订单的方案和跟进不受影响」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.restart()
    env.report(p1, FILLED, 100, "50")
    env.broker.positions["BBB"] = 100

    order = env.submit(p1)

    assert order.status is OrderStatus.FILLED
    [placed] = env.stops.placed
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_1,
        100,
        Decimal(45),
    )


# 有不一致时暂停开仓


def test_buys_are_paused_and_sells_go_on_after_a_mismatch() -> None:
    """spec「有不一致时买入被拒，卖出照常」。"""
    env, p1 = _env(), baseline_plan()
    env.broker.positions["CCC"] = 100
    assert env.reconcile().unexplained == (("CCC", 100),)

    with pytest.raises(ReconcileMismatchError):
        env.submit(p1)
    assert env.store.get(p1.plan_id) is None
    assert env.broker.calls_for(str(p1.plan_id)) == []

    env.submit(_close_ccc(), decision(100))
    [placed] = env.broker.placed
    assert (placed.symbol, placed.side, placed.quantity) == ("CCC", OrderSide.SELL, 100)


def test_adds_are_paused_too() -> None:
    """spec「加仓也被拒」。"""
    env = _env()
    env.hold_100()
    env.broker.add_working("CCC", "X1")
    env.reconcile()
    p2 = add_bbb()

    with pytest.raises(ReconcileMismatchError):
        env.submit(p2, decision(50))
    assert env.store.get(p2.plan_id) is None


def test_buys_resume_after_a_clean_reconciliation() -> None:
    """spec「之后的对账没有不一致时恢复」。"""
    env, p1 = _env(), baseline_plan()
    env.broker.positions["CCC"] = 100
    assert not env.reconcile().clean
    env.broker.positions["CCC"] = 0

    assert env.reconcile().clean
    assert env.submit(p1).status is OrderStatus.SUBMITTED


def test_buys_stay_paused_when_a_reconciliation_fails() -> None:
    """spec「对账失败时仍然暂停开仓」。"""
    env, p1 = _env(), baseline_plan()
    env.broker.positions["CCC"] = 100
    env.reconcile()
    env.broker.fail_next("all_positions")
    with pytest.raises(BrokerUnavailableError):
        env.reconcile()

    with pytest.raises(ReconcileMismatchError):
        env.submit(p1)
    assert env.store.get(p1.plan_id) is None


def test_plans_with_an_order_are_not_paused() -> None:
    """spec「已经有订单的方案不受暂停影响」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.positions["CCC"] = 100
    assert not env.reconcile().clean

    order = env.submit(p1)

    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.order_count(str(p1.plan_id)) == 1
