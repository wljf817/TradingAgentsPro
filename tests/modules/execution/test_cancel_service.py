"""提前撤单的测试：撤掉一只股票的订单、撤掉全部买单，以及有撤单要求的订单怎样跟进。

场景见 openspec 的 order-lifecycle spec「提前撤单」和「保存订单」的「重启后继续提前撤单」，
基准数据和 order-lifecycle spec 相同。测试用假券商安排券商那边的情况，用测试时钟拨动时间；
"重启"是用同一套存储和假券商重新创建执行用例，还没有对过账。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import (
    BASELINE_NOW,
    add_aaa,
    baseline_plan,
    decision,
    execution_service,
    reduce_aaa,
    reduce_bbb,
)
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionConfig,
    ExecutionService,
    Order,
    OrderStatus,
    stop_target,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED


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
        """进程重启：用同一套存储和假券商重新创建执行用例，还没有对过账。"""
        self.service = execution_service(
            self.store,
            self.broker,
            self.clock,
            ExecutionConfig(),
            self.stops,
            self.protections,
            reconciled=False,
        )

    def submit(self, plan: TradePlan, result: RiskDecision | None = None) -> Order:
        return self.service.submit(plan, result or decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime) -> Order:
        self.clock.set(at)
        return self.service.track(plan.plan_id)

    def cancel(self, symbol: str = "BBB", at: datetime | None = None) -> tuple[Order, ...]:
        """撤掉这只股票的订单。"""
        if at is not None:
            self.clock.set(at)
        return self.service.cancel_orders(symbol)

    def order(self, plan: TradePlan) -> Order:
        order = self.store.get(plan.plan_id)
        assert order is not None
        return order

    def calls(self, plan: TradePlan) -> list[str]:
        return self.broker.calls_for(str(plan.plan_id))

    def update(self, plan: TradePlan, status: BrokerOrderStatus, filled: int) -> None:
        self.broker.update(str(plan.plan_id), status, filled, "50" if filled else None)


def _env() -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(BASELINE_NOW)
    stops, protections = FakeStopBroker(), InMemoryProtectionStore()
    service = execution_service(store, broker, clock, ExecutionConfig(), stops, protections)
    broker.positions["AAA"] = 100  # 基准数据：持有 AAA 100 股，没有 AAA 的止损单
    return _Env(store, broker, stops, protections, clock, service)


def _submitted(env: _Env) -> TradePlan:
    """提交基准方案 P1，订单已提交。"""
    p1 = baseline_plan()
    assert env.submit(p1).status is OrderStatus.SUBMITTED
    return p1


def _place_timed_out(env: _Env, *, received: bool, at: datetime = BASELINE_NOW) -> TradePlan:
    """在 at 提交基准方案，提交超时；received 为真时券商其实收到了。订单状态未知。"""
    p1 = baseline_plan()
    env.clock.set(at)
    env.broker.fail_next("place", after_effect=received)
    assert env.submit(p1).status is OrderStatus.UNKNOWN
    return p1


def test_cancel_a_working_order() -> None:
    """spec「撤掉挂着的订单」。"""
    env = _env()
    p1 = _submitted(env)

    (order,) = env.cancel(at=_at(15, 10))

    assert env.broker.cancel_requests == [str(p1.plan_id)]
    assert order.plan_id == p1.plan_id
    assert order.status is OrderStatus.SUBMITTED
    assert order.cancel_wanted
    env.update(p1, CANCELLED, 0)
    ended = env.track(p1, _at(15, 10, 5))
    assert (ended.status, ended.filled) == (OrderStatus.CANCELLED, 0)


def test_cancel_after_a_partial_fill_and_fills_before_it_takes_effect() -> None:
    """spec「部分成交后撤单，撤单生效之前又有成交」。"""
    env = _env()
    p1 = _submitted(env)
    env.update(p1, WORKING, 30)

    env.cancel(at=_at(15, 10))
    assert env.broker.cancel_requests == [str(p1.plan_id)]

    env.update(p1, WORKING, 50)
    more = env.track(p1, _at(15, 10, 5))
    assert (more.status, more.filled) == (OrderStatus.PARTIALLY_FILLED, 50)
    assert env.broker.cancel_requests == [str(p1.plan_id)]

    env.update(p1, CANCELLED, 50)
    ended = env.track(p1, _at(15, 10, 10))
    assert (ended.status, ended.filled) == (OrderStatus.CANCELLED, 50)


def test_cancel_a_sell_still_waiting_for_the_stop_to_yield() -> None:
    """spec「撤掉还在等止损单让出股数的卖单」。"""
    env = _env()
    p1 = _submitted(env)
    env.update(p1, FILLED, 100)
    env.broker.positions["BBB"] = 100
    env.track(p1, BASELINE_NOW)  # 挂上 BBB-STOP-1，100 股
    p3 = reduce_bbb(60)
    assert env.submit(p3).status is OrderStatus.PENDING  # 止损单还没让出股数

    env.cancel()

    assert env.calls(p3) == ["find", "find"]
    assert env.order(p3).status is OrderStatus.CANCELLED
    assert env.order(p3).filled == 0
    assert stop_target(100, env.store.of_symbol("BBB")).quantity == 100


def test_order_whose_placement_timed_out_and_the_broker_did_not_receive_it() -> None:
    """spec「提交超时、券商没有收到的订单」。"""
    env = _env()
    p1 = _place_timed_out(env, received=False)
    calls = len(env.broker.calls)

    (order,) = env.cancel(at=_at(15, 0, 10))

    assert len(env.broker.calls) == calls
    assert order.status is OrderStatus.UNKNOWN
    assert order.cancel_wanted
    ended = env.track(p1, _at(15, 0, 30))
    assert env.calls(p1) == ["find", "place", "find"]
    assert env.broker.placed == []
    assert (ended.status, ended.filled) == (OrderStatus.CANCELLED, 0)


def test_order_whose_placement_timed_out_but_the_broker_received_it() -> None:
    """spec「提交超时、券商其实收到了的订单」。"""
    env = _env()
    p1 = _place_timed_out(env, received=True)

    env.cancel(at=_at(15, 0, 10))
    order = env.track(p1, _at(15, 0, 30))

    assert env.broker.cancel_requests == [str(p1.plan_id)]
    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.order_count(str(p1.plan_id)) == 1


def test_accepted_order_in_unknown_status() -> None:
    """spec「受理过的订单状态未知」。"""
    env = _env()
    p1 = _submitted(env)
    env.broker.fail_next("find")
    assert env.track(p1, _at(15, 10)).status is OrderStatus.UNKNOWN
    calls = len(env.broker.calls)

    env.cancel(at=_at(15, 10, 10))
    assert len(env.broker.calls) == calls

    order = env.track(p1, _at(15, 10, 30))
    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.cancel_requests == [str(p1.plan_id)]


def test_with_a_cancel_request_the_broker_is_found_not_to_have_it_after_the_deadline() -> None:
    """spec「有撤单要求，截止时刻之后才确认券商没有收到」。"""
    env = _env()
    p1 = baseline_plan()
    env.clock.set(_at(15, 59, 50))
    env.broker.fail_next("place")
    assert env.submit(p1, decision(expires_at=_at(16))).status is OrderStatus.UNKNOWN

    env.cancel(at=_at(15, 59, 55))
    ended = env.track(p1, _at(16, 0, 20))

    assert env.broker.placed == []
    assert (ended.status, ended.filled) == (OrderStatus.EXPIRED, 0)


def test_early_cancel_request_fails() -> None:
    """spec「提前撤单的请求失败」。"""
    env = _env()
    p1 = _submitted(env)
    env.broker.fail_next("cancel")

    (order,) = env.cancel(at=_at(15, 10))

    assert order.status is OrderStatus.UNKNOWN
    assert order.unknown_since == _at(15, 10)
    assert order.cancel_wanted
    retried = env.track(p1, _at(15, 10, 30))
    assert retried.status is OrderStatus.SUBMITTED
    assert env.calls(p1).count("cancel") == 2
    assert env.broker.cancel_requests == [str(p1.plan_id)]


def test_order_filled_before_the_cancel() -> None:
    """spec「撤单之前已经全部成交」。"""
    env = _env()
    p1 = _submitted(env)
    env.update(p1, FILLED, 100)

    (order,) = env.cancel()

    assert order.plan_id == p1.plan_id
    assert (order.status, order.filled) == (OrderStatus.FILLED, 100)
    assert "cancel" not in env.calls(p1)


def test_only_the_orders_of_this_stock_are_cancelled() -> None:
    """spec「只撤这只股票的订单」。"""
    env = _env()
    p1 = _submitted(env)
    p2 = add_aaa(50)
    env.submit(p2)

    orders = env.cancel("BBB")

    assert [o.plan_id for o in orders] == [p1.plan_id]
    assert env.broker.cancel_requests == [str(p1.plan_id)]
    assert not env.order(p2).cancel_wanted


def test_cancel_all_buy_orders() -> None:
    """spec「撤掉全部买单」。"""
    env = _env()
    p1 = _submitted(env)
    p2, p3 = add_aaa(50), reduce_aaa(60)
    env.submit(p2)
    assert env.submit(p3).status is OrderStatus.SUBMITTED

    orders = env.service.cancel_buy_orders()

    assert {o.plan_id for o in orders} == {p1.plan_id, p2.plan_id}
    assert sorted(env.broker.cancel_requests) == sorted([str(p1.plan_id), str(p2.plan_id)])
    assert not env.order(p3).cancel_wanted


def test_repeated_cancels_and_ended_orders() -> None:
    """spec「重复撤单和已经结束的订单」。"""
    env = _env()
    p1 = _submitted(env)
    env.cancel(at=_at(15, 10))

    env.cancel(at=_at(15, 10, 5))
    assert env.broker.cancel_requests == [str(p1.plan_id)]

    env.update(p1, CANCELLED, 0)
    assert env.track(p1, _at(15, 10, 10)).status is OrderStatus.CANCELLED
    calls = len(env.calls(p1))
    assert env.cancel() == ()
    assert len(env.calls(p1)) == calls


def test_resubmitting_a_plan_with_a_cancel_request() -> None:
    """spec「有撤单要求的方案再次提交」。"""
    env = _env()
    p1 = _place_timed_out(env, received=False)
    env.cancel(at=_at(15, 0, 10))

    env.clock.set(_at(15, 0, 30))
    order = env.submit(p1)

    assert env.broker.placed == []
    assert env.broker.order_count(str(p1.plan_id)) == 0
    assert order.status is OrderStatus.CANCELLED


def test_cancel_before_reconciling() -> None:
    """spec「还没对账时也可以撤单」。"""
    env = _env()
    p1 = _submitted(env)
    env.restart()

    env.cancel()

    assert env.broker.cancel_requests == [str(p1.plan_id)]


def test_early_cancel_continues_after_a_restart() -> None:
    """spec「重启后继续提前撤单」。"""
    env = _env()
    p1 = _submitted(env)
    env.broker.fail_next("cancel")
    assert env.cancel(at=_at(15, 10))[0].status is OrderStatus.UNKNOWN
    env.restart()

    order = env.track(p1, _at(15, 10, 30))

    assert env.broker.cancel_requests == [str(p1.plan_id)]
    assert order.status is OrderStatus.SUBMITTED
    assert order.cancel_wanted
