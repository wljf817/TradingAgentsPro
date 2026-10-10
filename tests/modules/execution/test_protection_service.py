"""止损保护的执行用例测试：对齐止损单、卖单先让出股数、被拒和无保护、备用止损、调用失败。

场景见 openspec 的 stop-protection spec。测试用假券商和假止损单接口安排券商那边的情况，
用测试时钟拨动时间；"重启"是用同一套存储和假券商重新创建执行用例。
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import (
    BASELINE_NOW,
    BASELINE_SESSION,
    add_aaa,
    add_bbb,
    baseline_plan,
    close_bbb,
    decision,
    execution_service,
    reduce_aaa,
    reduce_bbb,
)
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    ExecutionConfig,
    ExecutionService,
    NotReconciledError,
    Order,
    OrderPausedError,
    OrderSide,
    OrderStatus,
    Protection,
    StopOrder,
    StopStatus,
    UnprotectedPositionError,
    new_order,
    stop_target,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
STOP_1, STOP_2 = "BBB-STOP-1", "BBB-STOP-2"


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """基准交易日 2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


AFTER_CLOSE = _at(20, 0, 5)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、假止损单接口、测试时钟。"""

    store: InMemoryOrderStore
    broker: FakeBroker
    stops: FakeStopBroker
    protections: InMemoryProtectionStore
    clock: FakeClock
    config: ExecutionConfig
    service: ExecutionService

    def restart(self) -> None:
        """进程重启：用同一套存储和假券商重新创建执行用例，还没有对过账。"""
        self.service = execution_service(
            self.store,
            self.broker,
            self.clock,
            self.config,
            self.stops,
            self.protections,
            reconciled=False,
        )

    def submit(self, plan: TradePlan, result: RiskDecision | None = None) -> Order:
        return self.service.submit(plan, result or decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime | None = None) -> Order:
        if at is not None:
            self.clock.set(at)
        return self.service.track(plan.plan_id)

    def protect(self, symbol: str = "BBB", at: datetime | None = None) -> Protection | None:
        if at is not None:
            self.clock.set(at)
        return self.service.protect(symbol)

    def report(
        self, plan: TradePlan, status: BrokerOrderStatus, filled: int, position: int
    ) -> None:
        """券商回报这笔订单变为……，券商持仓变为 position 股。"""
        self.broker.update(str(plan.plan_id), status, filled)
        self.broker.positions[plan.symbol] = position

    def protection(self, symbol: str = "BBB") -> Protection:
        protection = self.protections.get(symbol)
        assert protection is not None
        return protection

    def stop(self, symbol: str = "BBB") -> StopOrder:
        stop = self.protection(symbol).stop
        assert stop is not None
        return stop

    def bought_30(self) -> TradePlan:
        """P1 部分成交、累计成交 30，券商持仓 30 股，BBB-STOP-1 挂着 30 股。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.report(p1, WORKING, 30, position=30)
        self.track(p1)
        assert self.stop().status is StopStatus.WORKING
        return p1

    def hold_100(self) -> TradePlan:
        """持有 BBB 100 股，止损单已对齐：P1 全部成交，BBB-STOP-1 挂着 100 股、止损价 45。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.report(p1, FILLED, 100, position=100)
        self.track(p1)
        assert self.stops.stop(STOP_1).quantity == 100
        return p1

    def requested_100(self) -> TradePlan:
        """P1 部分成交 30、BBB-STOP-1 挂着 30 股；P1 全部成交、券商持仓 100 股，15:00Z 跟进 P1 时
        请求了改单：100 股，止损价 45，券商还没生效。
        """
        p1 = self.bought_30()
        self.report(p1, FILLED, 100, position=100)
        self.track(p1, at=_at(15))
        assert self.stops.modify_requests == [(STOP_1, 100, Decimal(45))]
        return p1


def _env(now: datetime = BASELINE_NOW, config: ExecutionConfig | None = None) -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(now)
    stops, protections = FakeStopBroker(), InMemoryProtectionStore()
    config = config or ExecutionConfig()
    service = execution_service(store, broker, clock, config, stops, protections)
    return _Env(store, broker, stops, protections, clock, config, service)


# 止损单


def test_first_stop_after_a_partial_buy_fill() -> None:
    """spec「买入部分成交后挂第一张止损单」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30, position=30)

    env.track(p1)

    [placed] = env.stops.placed
    assert (placed.symbol, placed.client_order_id) == ("BBB", STOP_1)
    assert (placed.quantity, placed.stop_price) == (30, Decimal(45))
    assert env.stop().status is StopStatus.WORKING


def test_a_new_stop_with_the_next_sequence_after_the_stop_ends() -> None:
    """spec「止损单结束后挂序号加 1 的新止损单」。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, CANCELLED)

    env.protect()

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        100,
        Decimal(45),
    )


# 止损价


def test_an_add_filled_in_part_switches_to_its_stop_price() -> None:
    """spec「加仓部分成交后换成加仓方案的止损价」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()
    env.clock.set(_at(15, 10))
    env.submit(p2)
    env.report(p2, WORKING, 20, position=120)

    env.track(p2)

    assert env.stops.modify_requests == [(STOP_1, 120, Decimal(48))]


def test_an_add_without_fills_keeps_the_stop_price() -> None:
    """spec「加仓一股没成交就结束，止损价不变」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()
    env.clock.set(_at(15, 10))
    env.submit(p2)
    env.report(p2, CANCELLED, 0, position=100)

    assert env.track(p2).status is OrderStatus.CANCELLED
    assert env.stops.modify_requests == []
    assert (env.stop().quantity, env.stop().stop_price) == (100, Decimal(45))


def test_a_holding_without_opening_orders_gets_no_stop() -> None:
    """spec「没有开仓订单的持仓不挂止损单」。"""
    env = _env()
    env.broker.positions["CCC"] = 100

    assert env.protect("CCC") is None
    assert env.stops.calls == []


# 对齐止损单


def test_the_stop_is_modified_when_the_buy_fills_more() -> None:
    """spec「买入继续成交后改单」。"""
    env = _env()
    p1 = env.bought_30()
    env.report(p1, FILLED, 100, position=100)

    env.track(p1)

    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))]
    assert len(env.stops.placed) == 1


def test_an_aligned_stop_is_left_alone() -> None:
    """spec「已经对齐时不动」。"""
    env = _env()
    env.hold_100()
    env.stops.calls.clear()

    env.protect()

    assert env.stops.calls == [("find", STOP_1)]


def test_the_modify_result_comes_from_the_next_lookup() -> None:
    """spec「改单的结果以查询为准」：请求还没生效时不再改单，生效以后也不再改。"""
    env = _env()
    env.requested_100()
    env.stops.calls.clear()

    env.protect(at=_at(15, 0, 1))  # 券商回报仍是 30 股

    assert env.stops.calls == [("find", STOP_1)]
    assert env.protection().unprotected_since is None

    env.stops.update(STOP_1, WORKING, quantity=100)
    env.protect(at=_at(15, 0, 15))

    assert len(env.stops.modify_requests) == 1
    assert (env.stop().status, env.stop().quantity) == (StopStatus.WORKING, 100)
    assert env.stop().requested is None


def test_a_modify_not_effective_after_the_wait_is_requested_again() -> None:
    """spec「改单请求满等待时长还没生效，再改一次」。"""
    env = _env()
    env.requested_100()

    env.protect(at=_at(15, 0, 29))
    assert len(env.stops.modify_requests) == 1

    env.protect(at=_at(15, 0, 30))
    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))] * 2

    env.protect(at=_at(15, 0, 45))  # 从 15:00:30Z 重新等待
    assert len(env.stops.modify_requests) == 2


def test_a_stuck_modify_rejected_again_marks_unprotected() -> None:
    """spec「券商一直没完成改单，再改单被拒时标记无保护」。"""
    env = _env()
    env.requested_100()
    env.stops.reject_next("modify", 2)

    env.protect(at=_at(15, 0, 30))

    assert env.stops.calls_for(STOP_1)[-2:] == ["modify", "modify"]
    assert len(env.stops.modify_requests) == 1
    assert env.protection().unprotected_since == _at(15, 0, 30)


def test_no_new_stop_after_the_stop_fills() -> None:
    """spec「止损单成交后不再挂新的」：记下成交和发现成交的时刻。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, FILLED, quantity=0, filled=100, average_price="44.80", fees="2")
    env.broker.positions["BBB"] = 0

    env.protect(at=_at(15, 30))

    assert len(env.stops.placed) == 1
    stop = env.stop()
    assert (stop.status, stop.quantity, stop.filled) == (StopStatus.ENDED, 0, 100)
    assert (stop.average_price, stop.fees) == (Decimal("44.80"), Decimal(2))
    assert stop.filled_at == _at(15, 30)
    assert env.stops.working("BBB") == {}


def test_a_partial_stop_fill_is_recorded_without_a_modify() -> None:
    """spec「止损单部分成交后记下成交，不改单」。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, WORKING, quantity=60, filled=40, average_price="44.90", fees="2")
    env.broker.positions["BBB"] = 60

    env.protect(at=_at(15, 30))

    stop = env.stop()
    assert (stop.status, stop.quantity, stop.filled) == (StopStatus.WORKING, 60, 40)
    assert (stop.average_price, stop.fees) == (Decimal("44.90"), Decimal(2))
    assert stop.filled_at == _at(15, 30)
    assert env.stops.modify_requests == []

    env.protect(at=_at(15, 40))

    assert env.stop().filled_at == _at(15, 30)
    assert env.stops.modify_requests == []


def test_the_stop_returns_to_the_holding_after_a_partial_sell_expires() -> None:
    """spec「卖单部分成交后过期，止损单恢复为剩下的持仓」。"""
    env = _env()
    env.hold_100()
    p3 = reduce_bbb()
    env.submit(p3)
    env.stops.settle()  # BBB-STOP-1 改为 40 股
    env.track(p3)
    assert env.broker.order_count(str(p3.plan_id)) == 1
    env.report(p3, CANCELLED, 20, position=80)  # 收盘失效

    order = env.track(p3, at=AFTER_CLOSE)

    assert order.status is OrderStatus.EXPIRED
    assert env.stops.modify_requests[-1] == (STOP_1, 80, Decimal(45))


def test_a_new_stop_when_a_close_does_not_finish() -> None:
    """spec「清仓没卖完时重新挂止损单」。"""
    env = _env()
    env.hold_100()
    p4 = close_bbb()
    env.submit(p4)
    env.stops.settle()  # BBB-STOP-1 撤销
    env.track(p4)
    assert env.broker.order_count(str(p4.plan_id)) == 1
    env.report(p4, CANCELLED, 70, position=30)

    order = env.track(p4, at=AFTER_CLOSE)

    assert order.status is OrderStatus.EXPIRED
    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        30,
        Decimal(45),
    )
    assert (env.stop().client_order_id, env.stop().created_at) == (STOP_2, AFTER_CLOSE)


def test_a_stop_that_never_went_out_is_dropped_when_the_target_is_zero() -> None:
    """spec「还没挂出去的止损单在目标为 0 时放弃」。"""
    env = _env()
    pending = StopOrder(
        symbol="BBB",
        client_order_id=STOP_1,
        quantity=30,
        stop_price=Decimal(45),
        created_at=BASELINE_NOW,
    )
    env.protections.save(Protection("BBB", used=1, stop=pending))

    env.protect()

    assert env.stops.calls_for(STOP_1) == ["find"]
    assert env.stop().status is StopStatus.ENDED


# 卖单先让出股数


def test_a_reduce_waits_until_the_broker_shows_the_smaller_stop() -> None:
    """spec「减仓时先改小止损单，券商确认后再提交卖单」。"""
    env = _env()
    env.hold_100()
    p3 = reduce_bbb()

    order = env.submit(p3)

    assert env.stops.modify_requests == [(STOP_1, 40, Decimal(45))]
    assert env.broker.order_count(str(p3.plan_id)) == 0
    assert order.status is OrderStatus.PENDING

    env.track(p3, at=_at(15, 0, 15))  # 券商回报仍是 100 股
    assert env.stops.modify_requests == [(STOP_1, 40, Decimal(45))]
    assert env.broker.order_count(str(p3.plan_id)) == 0

    env.stops.update(STOP_1, WORKING, quantity=40)
    env.track(p3, at=_at(15, 0, 20))
    placed = env.broker.placed[-1]
    assert (placed.plan_id, placed.side, placed.quantity) == (p3.plan_id, OrderSide.SELL, 60)
    assert placed.order_type is OrderType.MARKET


def test_a_close_waits_until_the_stop_is_cancelled() -> None:
    """spec「清仓时先撤销止损单」。"""
    env = _env()
    env.hold_100()
    p4 = close_bbb()

    env.submit(p4)

    assert env.stops.cancel_requests == [STOP_1]
    assert env.broker.order_count(str(p4.plan_id)) == 0

    env.stops.update(STOP_1, CANCELLED)
    env.track(p4)
    placed = env.broker.placed[-1]
    assert (placed.plan_id, placed.side, placed.quantity) == (p4.plan_id, OrderSide.SELL, 100)
    assert len(env.stops.placed) == 1


def test_no_sell_when_the_stop_fires_while_waiting() -> None:
    """spec「等待让出时止损单触发，不再提交卖单」。"""
    env = _env()
    env.hold_100()
    p3 = reduce_bbb()
    env.submit(p3)
    env.stops.update(STOP_1, FILLED, quantity=0)
    env.broker.positions["BBB"] = 0

    order = env.track(p3)

    assert env.broker.order_count(str(p3.plan_id)) == 0
    assert order.status is OrderStatus.PENDING


def test_no_sell_while_a_larger_stop_request_is_pending() -> None:
    """spec「改大的请求还没生效时不提交卖单」：等改大的请求生效，再改小，生效后才提交。"""
    env = _env()
    env.requested_100()
    reduce = reduce_bbb(70)
    env.clock.set(_at(15, 0, 5))

    env.submit(reduce, decision(70))

    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))]
    assert env.stops.cancel_requests == []
    assert env.broker.order_count(str(reduce.plan_id)) == 0

    env.stops.settle()  # 券商回报变为 100 股
    env.track(reduce, at=_at(15, 0, 20))
    assert env.stops.modify_requests == [(STOP_1, 30, Decimal(45))]
    assert env.broker.order_count(str(reduce.plan_id)) == 0

    env.stops.settle()  # 券商回报变为 30 股
    env.track(reduce, at=_at(15, 0, 35))
    assert env.broker.order_count(str(reduce.plan_id)) == 1


def test_a_close_waits_for_a_pending_modify_before_cancelling_the_stop() -> None:
    """spec「清仓时改单请求还没生效，先不撤止损单」。"""
    env = _env()
    env.requested_100()
    p4 = close_bbb()
    env.clock.set(_at(15, 0, 5))

    order = env.submit(p4)

    assert env.stops.cancel_requests == []
    assert len(env.stops.modify_requests) == 1
    assert env.broker.order_count(str(p4.plan_id)) == 0
    assert order.status is OrderStatus.PENDING

    env.stops.settle()  # 券商回报变为 100 股
    env.track(p4, at=_at(15, 0, 20))
    assert env.stops.cancel_requests == [STOP_1]
    assert env.broker.order_count(str(p4.plan_id)) == 0

    env.stops.update(STOP_1, CANCELLED)
    env.track(p4, at=_at(15, 0, 35))
    placed = env.broker.placed[-1]
    assert (placed.plan_id, placed.side, placed.quantity) == (p4.plan_id, OrderSide.SELL, 100)


def test_no_sell_while_the_stop_is_unknown() -> None:
    """spec「止损单状态未知时不提交卖单」。"""
    env = _env()
    env.hold_100()
    env.protections.save(env.protection().with_stop(env.stop().mark_unknown(_at(15))))
    env.stops.calls.clear()
    env.clock.set(_at(15, 0, 10))
    p3 = reduce_bbb()

    order = env.submit(p3)

    assert env.broker.order_count(str(p3.plan_id)) == 0
    assert order.status is OrderStatus.PENDING
    assert env.stops.calls == []


def test_a_sell_goes_out_directly_without_a_stop() -> None:
    """spec「没有止损单的股票直接提交卖单」。"""
    env = _env()
    env.broker.positions["AAA"] = 100
    reduce = reduce_aaa(60)

    env.submit(reduce, decision(60))

    assert env.broker.order_count(str(reduce.plan_id)) == 1


# 挂单或改单被拒


def _unprotected(env: _Env, at: datetime = BASELINE_NOW) -> None:
    """持有 BBB 100 股，止损单被券商撤销后补挂两次都被拒：无保护仓位，没有挂着的止损单。"""
    env.hold_100()
    env.stops.update(STOP_1, CANCELLED)
    env.stops.reject_next("place", 2)
    assert env.protect(at=at) is not None
    assert env.protection().unprotected_since == at


def test_a_rejected_place_is_retried_once() -> None:
    """spec「挂单被拒一次，重试成功」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.reject_next("place")
    env.report(p1, WORKING, 30, position=30)

    env.track(p1)

    assert env.stops.calls_for(STOP_1) == ["place", "place"]
    assert env.stop().status is StopStatus.WORKING
    assert env.protection().unprotected_since is None


def test_rejected_again_marks_unprotected_until_a_stop_is_placed() -> None:
    """spec「重试也被拒时标记无保护，之后补挂成功就解除」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.reject_next("place", 2)
    env.report(p1, WORKING, 30, position=30)

    env.track(p1, at=_at(15))

    assert env.protection().unprotected_since == _at(15)
    assert env.stop().status is StopStatus.ENDED

    env.protect(at=_at(15, 5))

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        30,
        Decimal(45),
    )
    assert env.protection().unprotected_since is None


def test_unprotected_is_cleared_once_the_holding_is_sold() -> None:
    """spec「无保护的持仓卖完后解除」。"""
    env = _env()
    _unprotected(env)
    p4 = close_bbb()
    env.submit(p4)
    env.report(p4, FILLED, 100, position=0)

    env.track(p4)

    assert env.protection().unprotected_since is None
    assert len(env.stops.placed) == 1


def test_an_unprotected_holding_keeps_its_first_time() -> None:
    """spec「已经无保护时保留原来的开始时刻」。"""
    env = _env()
    _unprotected(env, at=_at(15))
    env.stops.reject_next("place", 2)

    env.protect(at=_at(15, 5))

    assert env.protection().unprotected_since == _at(15)


def test_a_rejected_modify_after_an_add_marks_unprotected() -> None:
    """spec「加仓后改单被拒」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()
    env.clock.set(_at(15, 10))
    env.submit(p2)
    env.stops.reject_next("modify", 2)
    env.report(p2, WORKING, 20, position=120)

    env.track(p2)

    assert env.stops.calls_for(STOP_1)[-2:] == ["modify", "modify"]
    assert env.stops.modify_requests == []
    assert env.protection().unprotected_since == _at(15, 10)
    assert (env.stops.stop(STOP_1).quantity, env.stops.stop(STOP_1).stop_price) == (
        100,
        Decimal(45),
    )


def test_no_immediate_retry_when_configured_to_zero() -> None:
    """order-lifecycle spec「立即重试次数为 0」。"""
    env, p1 = _env(config=ExecutionConfig(stop_immediate_retries=0)), baseline_plan()
    env.submit(p1)
    env.stops.reject_next("place")
    env.report(p1, WORKING, 30, position=30)

    env.track(p1)

    assert env.stops.calls_for(STOP_1) == ["place"]
    assert env.protection().unprotected_since is not None


# 无保护时禁止加仓


def test_an_add_is_refused_while_unprotected() -> None:
    """spec「无保护时加仓被拒」。"""
    env = _env()
    _unprotected(env)
    p2 = add_bbb()

    with pytest.raises(UnprotectedPositionError):
        env.submit(p2)

    assert env.store.get(p2.plan_id) is None
    assert env.broker.calls_for(str(p2.plan_id)) == []


def test_a_sell_is_allowed_while_unprotected() -> None:
    """spec「无保护时卖出不受影响」。"""
    env = _env()
    _unprotected(env)
    p3 = reduce_bbb()

    env.submit(p3)

    assert env.broker.order_count(str(p3.plan_id)) == 1


def test_other_stocks_are_not_affected_by_an_unprotected_holding() -> None:
    """spec「其他股票不受影响」。"""
    env = _env()
    _unprotected(env)
    add = add_aaa()

    assert env.submit(add, decision(50)).status is OrderStatus.SUBMITTED


def test_adds_resume_once_the_holding_is_protected_again() -> None:
    """spec「解除后恢复加仓」。"""
    env = _env()
    _unprotected(env)

    env.protect()

    assert env.protection().unprotected_since is None
    assert env.submit(add_bbb()).status is OrderStatus.SUBMITTED


# 本地备用止损的触发


@pytest.mark.parametrize("last_price", ["45", "44"])
def test_backup_stop_fires_when_unprotected_in_session_at_the_stop(last_price: str) -> None:
    """spec「无保护、盘中、价格跌到止损价时触发」。"""
    env = _env()
    _unprotected(env)

    assert env.service.backup_stop_due("BBB", Decimal(last_price))


def test_no_backup_stop_above_the_stop_price() -> None:
    """spec「价格高于止损价时不触发」。"""
    env = _env()
    _unprotected(env)

    assert not env.service.backup_stop_due("BBB", Decimal("45.01"))


def test_no_backup_stop_outside_the_session() -> None:
    """spec「常规交易时段之外不触发」。"""
    env = _env()
    _unprotected(env)
    env.clock.set(_at(21))

    assert not env.service.backup_stop_due("BBB", Decimal(40))


def test_no_backup_stop_while_protected() -> None:
    """spec「有保护时不触发」。"""
    env = _env()
    env.hold_100()

    assert not env.service.backup_stop_due("BBB", Decimal(40))


# 止损单的券商调用失败


def test_place_times_out_but_the_broker_got_it() -> None:
    """spec「挂单超时，券商其实收到了」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.fail_next("place", after_effect=True)
    env.report(p1, WORKING, 30, position=30)

    env.track(p1, at=_at(15))

    assert (env.stop().status, env.stop().unknown_since) == (StopStatus.UNKNOWN, _at(15))
    assert env.protection().unprotected_since is None

    calls = len(env.stops.calls)
    env.protect(at=_at(15, 0, 29))
    assert len(env.stops.calls) == calls

    env.protect(at=_at(15, 0, 30))
    assert (env.stop().status, env.stop().quantity) == (StopStatus.WORKING, 30)
    assert env.stops.stop_count(STOP_1) == 1
    assert env.stops.calls_for(STOP_1)[-1] == "find"


def test_place_times_out_and_the_broker_never_got_it() -> None:
    """spec「挂单超时，券商没有收到」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.fail_next("place")
    env.report(p1, WORKING, 30, position=30)

    env.track(p1, at=_at(15))
    assert env.stop().status is StopStatus.UNKNOWN

    env.protect(at=_at(15, 0, 30))

    assert env.stops.calls_for(STOP_1)[-2:] == ["find", "place"]
    assert env.stops.stop_count(STOP_1) == 1
    assert env.stop().status is StopStatus.WORKING


def test_modify_times_out() -> None:
    """spec「改单超时」。"""
    env = _env()
    p1 = env.bought_30()
    env.stops.fail_next("modify", after_effect=True)
    env.report(p1, FILLED, 100, position=100)

    env.track(p1, at=_at(15))

    assert (env.stop().status, env.stop().quantity) == (StopStatus.UNKNOWN, 30)

    env.stops.update(STOP_1, WORKING, quantity=100)
    env.protect(at=_at(15, 0, 30))

    assert (env.stop().status, env.stop().quantity) == (StopStatus.WORKING, 100)
    assert len(env.stops.modify_requests) == 1


def test_modify_times_out_and_the_broker_never_got_it() -> None:
    """spec「改单超时，券商没有收到」：满等待时长查清以后，请求也不再等，这次对齐就再改。"""
    env = _env()
    p1 = env.bought_30()
    env.stops.fail_next("modify")
    env.report(p1, FILLED, 100, position=100)

    env.track(p1, at=_at(15))

    assert (env.stop().status, env.stop().quantity) == (StopStatus.UNKNOWN, 30)
    assert env.stops.modify_requests == []

    env.protect(at=_at(15, 0, 30))  # 券商回报仍是 30 股

    assert (env.stop().status, env.stop().quantity) == (StopStatus.WORKING, 30)
    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))]


def test_an_accepted_stop_that_cannot_be_found() -> None:
    """spec「受理过的止损单查不到」。"""
    env = _env()
    env.hold_100()
    env.stops.lose(STOP_1)

    env.protect(at=_at(15, 10))
    assert (env.stop().status, env.stop().unknown_since) == (StopStatus.UNKNOWN, _at(15, 10))

    env.protect(at=_at(15, 10, 30))
    assert (env.stop().status, env.stop().unknown_since) == (
        StopStatus.UNKNOWN,
        _at(15, 10, 30),
    )
    assert len(env.stops.placed) == 1


def test_nothing_else_happens_when_the_position_lookup_fails() -> None:
    """spec「查询持仓失败时不做处理」。"""
    env = _env()
    p1 = env.bought_30()
    env.report(p1, FILLED, 100, position=100)
    env.broker.fail_next("position")

    env.track(p1)

    assert env.stops.modify_requests == []
    assert (env.stop().status, env.stop().quantity) == (StopStatus.WORKING, 30)


def test_a_failed_lookup_restarts_the_wait() -> None:
    env = _env()
    env.hold_100()
    env.stops.fail_next("find")

    env.protect(at=_at(15, 10))
    assert env.stop().unknown_since == _at(15, 10)

    env.stops.fail_next("find")
    env.protect(at=_at(15, 10, 30))
    assert env.stop().unknown_since == _at(15, 10, 30)

    env.protect(at=_at(15, 11))
    assert env.stop().status is StopStatus.WORKING


# 保存止损保护


def test_aligning_continues_after_a_restart() -> None:
    """spec「重启后继续对齐」。"""
    env = _env()
    p1 = env.bought_30()
    env.restart()
    env.report(p1, FILLED, 100, position=100)

    env.track(p1)

    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))]
    assert len(env.stops.placed) == 1


def test_a_modify_still_awaits_after_a_restart() -> None:
    """spec「重启后改单请求仍在等待生效」。"""
    env = _env()
    env.requested_100()
    env.restart()

    env.protect(at=_at(15, 0, 10))  # 券商回报仍是 30 股
    assert len(env.stops.modify_requests) == 1

    env.protect(at=_at(15, 0, 30))
    assert env.stops.modify_requests == [(STOP_1, 100, Decimal(45))] * 2


def test_process_exited_after_placing_before_saving() -> None:
    """spec「挂单之后、保存结果之前进程退出」。"""
    env = _env()
    p1 = baseline_plan()
    bought = new_order(p1, decision(), BASELINE_SESSION, BASELINE_NOW)
    env.store.save(replace(bought, status=OrderStatus.PARTIALLY_FILLED, filled=30))
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

    env.protect()

    assert env.stops.placed == []
    assert env.stops.stop_count(STOP_1) == 1
    assert env.stop().status is StopStatus.WORKING


# 状态机性质测试（design D12）

_BUYING = (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)


class StopProtectionMachine(RuleBasedStateMachine):
    """随机交错买入成交、卖单提交和成交、提前撤单、止损单的改单撤单生效、止损单触发、
    券商调用失败或被拒、重启、对账和拨动时间，检查止损单和卖单不会合计超过持仓，
    最终止损单对齐到目标。

    只有执行模块在操作券商，所以每次完成的对账都没有不一致（add-reconciliation 的 design D7）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.env = _env()
        self.plans: list[TradePlan] = []

    @property
    def position(self) -> int:
        return self.env.broker.positions.get("BBB", 0)

    def _move(self, shares: int) -> None:
        self.env.broker.positions["BBB"] = self.position + shares

    def _working_at_broker(self, plan: TradePlan) -> bool:
        cid = str(plan.plan_id)
        broker = self.env.broker
        return broker.order_count(cid) > 0 and broker.order(cid).status is WORKING

    def _fill(self, plan: TradePlan, shares: int) -> int:
        """券商那边这笔订单再成交 shares 股，最多到委托股数；返回实际成交的股数。"""
        order = self.env.store.get(plan.plan_id)
        assert order is not None
        current = self.env.broker.order(str(plan.plan_id))
        filled = min(order.quantity, current.filled + shares)
        status = FILLED if filled == order.quantity else WORKING
        self.env.broker.update(str(plan.plan_id), status, filled, "50")
        return filled - current.filled

    def _sells(self) -> list[TradePlan]:
        return [p for p in self.plans if not p.action.is_opening]

    @initialize(filled=st.integers(0, 100))
    def buy(self, filled: int) -> None:
        """提交 P1，先成交 filled 股并跟进一次，让大多数样例一开始就有持仓和止损单。"""
        p1 = baseline_plan()
        self.env.submit(p1)
        self.plans.append(p1)
        if filled:
            self._move(self._fill(p1, filled))
            self.env.track(p1)

    @rule(shares=st.integers(1, 60))
    def buy_fills(self, shares: int) -> None:
        p1 = self.plans[0]
        if self._working_at_broker(p1):
            self._move(self._fill(p1, shares))

    @rule(quantity=st.integers(1, 120), close=st.booleans())
    def submit_sell(self, quantity: int, close: bool) -> None:
        """持仓大于 0、没有未结束的卖单时，提交减仓或清仓。"""
        open_sell = any(
            (o := self.env.store.get(p.plan_id)) and not o.status.is_terminal for p in self._sells()
        )
        if self.position == 0 or open_sell:
            return
        plan = close_bbb() if close else reduce_bbb(min(quantity, self.position))
        final = self.position if close else min(quantity, self.position)
        try:
            self.env.submit(plan, decision(final))
        except (OrderPausedError, NotReconciledError):
            return
        self.plans.append(plan)

    @rule(shares=st.integers(1, 60))
    def sell_fills(self, shares: int) -> None:
        for plan in self._sells():
            if self._working_at_broker(plan):
                self._move(-self._fill(plan, shares))
                event("卖单成交")

    @rule()
    def sell_cancelled_at_the_broker(self) -> None:
        for plan in self._sells():
            if self._working_at_broker(plan):
                cid = str(plan.plan_id)
                self.env.broker.update(cid, CANCELLED, self.env.broker.order(cid).filled, "50")

    @rule()
    def cancel_bbb_orders(self) -> None:
        """撤掉 BBB 的订单，买单和卖单都撤。"""
        unsent = [p for p in self._sells() if (o := self.env.store.get(p.plan_id)) and o.unsent]
        self.env.service.cancel_orders("BBB")
        for plan in unsent:
            order = self.env.store.get(plan.plan_id)
            if order is not None and order.status is OrderStatus.CANCELLED:
                event("等让出的卖单被撤")

    @rule()
    def stop_requests_take_effect(self) -> None:
        self.env.stops.settle()

    @rule(seconds=st.integers(0, 60))
    def stop_requests_take_effect_then_track(self, seconds: int) -> None:
        """改单和撤单生效后马上跟进，让卖单常常在中途提交出去。"""
        self.env.stops.settle()
        self.track_everything(seconds)

    @rule()
    def stop_triggers(self) -> None:
        for cid, report in self.env.stops.working("BBB").items():
            self.env.stops.update(cid, FILLED, quantity=0)
            self._move(-report.quantity)
            event("止损单触发")

    @rule(
        operation=st.sampled_from(["find", "place", "modify", "cancel", "working_stops"]),
        after_effect=st.booleans(),
    )
    def stop_call_fails(self, operation: str, after_effect: bool) -> None:
        self.env.stops.fail_next(operation, after_effect=after_effect)

    @rule(operation=st.sampled_from(["place", "modify"]))
    def stop_request_rejected(self, operation: str) -> None:
        self.env.stops.reject_next(operation)

    @rule(
        operation=st.sampled_from(
            ["find", "place", "cancel", "position", "working_orders", "all_positions"]
        ),
        after_effect=st.booleans(),
    )
    def broker_call_fails(self, operation: str, after_effect: bool) -> None:
        self.env.broker.fail_next(operation, after_effect=after_effect)

    @rule()
    def restart(self) -> None:
        """重启之后先对账；对账失败时，执行用例在下一次对账完成之前不接受新订单。"""
        self.env.restart()
        self.reconcile()

    @rule()
    def reconcile(self) -> None:
        try:
            result = self.env.service.reconcile()
        except BrokerUnavailableError:
            event("对账失败")
            return
        event("对账完成")
        assert result.clean, result

    @rule(seconds=st.integers(0, 60))
    def track_everything(self, seconds: int) -> None:
        """拨动时间，跟进每笔未结束的订单，再单独对齐一次。"""
        self.env.clock.advance(timedelta(seconds=seconds))
        for plan in self.plans:
            order = self.env.store.get(plan.plan_id)
            if order is not None and not order.status.is_terminal:
                self.env.service.track(plan.plan_id)
                if not plan.action.is_opening and order.unsent and self._working_at_broker(plan):
                    event("卖单提交给券商")
        self.env.protect()

    @rule()
    def align_twice_right_away(self) -> None:
        """不拨时间、不让请求生效，接着对齐两次：第一次有被券商接受的改单请求时，第二次不再改单、
        也不撤单（update-stop-pending-modify 的 design D7）。两次之间目标不变。
        """
        requested = len(self.env.stops.modify_requests)
        self.env.protect()
        if len(self.env.stops.modify_requests) == requested:
            return
        event("第一次对齐有改单请求")
        seen = len(self.env.stops.calls)
        self.env.protect()
        again = [op for op, _ in self.env.stops.calls[seen:] if op in ("modify", "cancel")]
        assert again == []

    @invariant()
    def at_most_one_working_stop(self) -> None:
        assert len(self.env.stops.working("BBB")) <= 1

    @invariant()
    def stop_and_sells_never_exceed_the_position(self) -> None:
        """券商那边挂着的止损单和卖单，未成交股数合计不超过持仓。"""
        stop = sum(r.quantity for r in self.env.stops.working("BBB").values())
        sells = 0
        for plan in self._sells():
            if self._working_at_broker(plan):
                order = self.env.store.get(plan.plan_id)
                assert order is not None
                sells += order.quantity - self.env.broker.order(str(plan.plan_id)).filled
        assert stop + sells <= self.position

    def teardown(self) -> None:
        """券商不再失败、请求都生效之后，多跟进几次，止损单对齐到目标，不是无保护仓位。"""
        self.env.broker.clear_failures()
        self.env.stops.clear_failures()
        delay = self.env.config.unknown_lookup_delay
        for _ in range(5):
            self.env.stops.settle()
            self.track_everything(int(delay.total_seconds()) + 1)
            self.stop_and_sells_never_exceed_the_position()
        self.env.stops.settle()
        self.env.protect()
        assert self.env.service.reconcile().clean
        target = stop_target(
            self.position,
            self.env.store.of_symbol("BBB"),
            stagger=self.env.config.stagger_buys_and_stops,
        )
        working = list(self.env.stops.working("BBB").values())
        if target.quantity == 0:
            assert working == []
        else:
            [stop] = working
            assert (stop.quantity, stop.stop_price) == (target.quantity, target.stop_price)
        protection = self.env.protections.get("BBB")
        assert protection is None or protection.unprotected_since is None


TestStopProtectionMachine = StopProtectionMachine.TestCase


class StaggeredStopMachine(StopProtectionMachine):
    """打开买单和止损单错开的止损保护状态机（update-stagger-buys-and-stops 的 design D7）。

    在原来的步骤上增加持有时提交加仓、加仓订单成交，检查券商那边 BBB 不会同时挂着买单和止损单；
    最终止损单按错开的止损目标对齐，错开中和"有买单没结束、持仓大于 0"一致。
    """

    def __init__(self) -> None:
        super().__init__()
        self.env = _env(config=ExecutionConfig(stagger_buys_and_stops=True))

    def _buys(self) -> list[TradePlan]:
        return [p for p in self.plans if p.action.is_opening]

    def _open(self, plans: list[TradePlan]) -> list[TradePlan]:
        return [
            p for p in plans if (o := self.env.store.get(p.plan_id)) and not o.status.is_terminal
        ]

    @rule(quantity=st.integers(1, 60))
    def submit_add(self, quantity: int) -> None:
        """持仓大于 0、没有未结束的买单时，提交加仓。"""
        if self.position == 0 or self._open(self._buys()):
            return
        plan = add_bbb(quantity)
        try:
            self.env.submit(plan, decision(quantity))
        except (OrderPausedError, NotReconciledError, UnprotectedPositionError):
            return
        self.plans.append(plan)

    @rule(shares=st.integers(1, 60))
    def add_fills(self, shares: int) -> None:
        for plan in self._buys()[1:]:
            if self._working_at_broker(plan):
                self._move(self._fill(plan, shares))

    @rule(seconds=st.integers(0, 60))
    def track_everything(self, seconds: int) -> None:
        """跟进和对齐一轮，统计加仓提交出去、错开以后挂回止损单。"""
        adds = self._buys()[1:]
        unsent = [p for p in adds if (o := self.env.store.get(p.plan_id)) and o.unsent]
        protection = self.env.protections.get("BBB")
        staggered = protection is not None and protection.staggered
        super().track_everything(seconds)
        if any(self._working_at_broker(p) for p in unsent):
            event("加仓提交给券商")
        protection = self.env.protections.get("BBB")
        if staggered and protection and not protection.staggered and self.env.stops.working("BBB"):
            event("错开以后挂回止损单")

    @invariant()
    def never_a_working_buy_and_a_working_stop(self) -> None:
        buying = any(self._working_at_broker(p) for p in self._buys())
        assert not (buying and self.env.stops.working("BBB"))

    def teardown(self) -> None:
        super().teardown()
        protection = self.env.protections.get("BBB")
        target = stop_target(self.position, self.env.store.of_symbol("BBB"), stagger=True)
        assert (protection is not None and protection.staggered) == target.staggered


TestStaggeredStopMachine = StaggeredStopMachine.TestCase
