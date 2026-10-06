"""执行用例的测试：提交订单、跟进订单、到期撤单、状态未知和暂停。

场景见 openspec 的 order-lifecycle spec。测试用假券商安排券商那边的情况，用测试时钟拨动时间；
"重启"是用同一个存储和假券商重新创建执行用例。基准数据里执行模块已经完成一次没有不一致的对账，
新建的执行用例都先对账一次；重启之后的执行用例还没有对过账。
"""

from collections.abc import Callable
from dataclasses import dataclass
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
    BASELINE_CLOSE,
    BASELINE_NOW,
    BASELINE_SESSION,
    add_aaa,
    baseline_plan,
    close_aaa,
    decision,
    execution_service,
    reduce_aaa,
)
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionConfig,
    ExecutionService,
    Order,
    OrderPausedError,
    OrderSide,
    OrderStatus,
    new_order,
)
from tradingagentspro.shared.trading_calendar import OutsideSessionError

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """基准交易日 2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


EXPIRES_16 = _at(16)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、测试时钟。"""

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
        return self.service.submit(plan, result or decision())

    def track(self, plan: TradePlan, at: datetime) -> Order:
        self.clock.set(at)
        return self.service.track(plan.plan_id)

    def calls(self, plan: TradePlan) -> list[str]:
        return self.broker.calls_for(str(plan.plan_id))

    def update(
        self,
        plan: TradePlan,
        status: BrokerOrderStatus,
        filled: int,
        average_price: str | None = None,
        fees: str = "0",
    ) -> None:
        self.broker.update(str(plan.plan_id), status, filled, average_price, fees)


def _env(now: datetime = BASELINE_NOW, config: ExecutionConfig | None = None) -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(now)
    stops, protections = FakeStopBroker(), InMemoryProtectionStore()
    config = config or ExecutionConfig()
    service = execution_service(store, broker, clock, config, stops, protections)
    broker.positions["AAA"] = 100  # 基准数据：持有 AAA 100 股，没有 AAA 的止损单
    return _Env(store, broker, stops, protections, clock, config, service)


def _pending(env: _Env, plan: TradePlan, result: RiskDecision | None = None) -> Order:
    """存储里放一笔基准时刻建立的待提交订单，模拟进程在提交前后退出。"""
    order = new_order(plan, result or decision(), BASELINE_SESSION, BASELINE_NOW)
    env.store.save(order)
    return order


# 提交订单


def test_buy_plan_submits_a_limit_buy_order() -> None:
    env, plan = _env(), baseline_plan()

    order = env.submit(plan)

    assert env.broker.order_count(str(plan.plan_id)) == 1
    [placed] = env.broker.placed
    assert placed.client_order_id == str(plan.plan_id)
    assert (placed.side, placed.symbol, placed.quantity) == (OrderSide.BUY, "BBB", 100)
    assert (placed.order_type, placed.limit_price) == (OrderType.LIMIT, Decimal(50))
    assert order.status is OrderStatus.SUBMITTED
    assert order.deadline == BASELINE_CLOSE
    assert order.created_at == BASELINE_NOW
    assert order.filled == 0
    assert order.broker_order_id == env.broker.order(str(plan.plan_id)).broker_order_id
    assert order.stop_price == Decimal(45)
    assert (order.sector, order.emergency_threshold) == ("医疗", Decimal("0.08"))
    assert order.fees == 0


def test_reduce_plan_submits_a_market_sell_order() -> None:
    env, plan = _env(), reduce_aaa(60)

    order = env.submit(plan, decision(60))

    [placed] = env.broker.placed
    assert placed.client_order_id == str(plan.plan_id)
    assert (placed.side, placed.symbol, placed.quantity) == (OrderSide.SELL, "AAA", 60)
    assert (placed.order_type, placed.limit_price) == (OrderType.MARKET, None)
    assert order.sector == "科技"
    assert (order.stop_price, order.emergency_threshold) == (None, None)


def test_orders_use_the_final_quantity_of_the_recheck() -> None:
    env = _env()

    adjusted = env.submit(baseline_plan(), decision(60))
    env.submit(close_aaa(), decision(100))

    assert adjusted.quantity == 60
    assert [(p.symbol, p.side, p.quantity) for p in env.broker.placed] == [
        ("BBB", OrderSide.BUY, 60),
        ("AAA", OrderSide.SELL, 100),
    ]


def test_deadline_when_the_expiry_is_before_the_close() -> None:
    order = _env().submit(baseline_plan(), decision(expires_at=EXPIRES_16))

    assert order.deadline == EXPIRES_16


def test_deadline_when_the_expiry_is_after_the_close() -> None:
    expires_at = datetime(2026, 10, 16, 20, tzinfo=UTC)

    order = _env().submit(baseline_plan(), decision(expires_at=expires_at))

    assert order.deadline == BASELINE_CLOSE


def test_deadline_on_an_early_close_day() -> None:
    # 感恩节次日 13:00（纽约）提前收盘，即 18:00Z
    env = _env(now=datetime(2026, 11, 27, 15, tzinfo=UTC))

    order = env.submit(baseline_plan(), decision(expires_at=datetime(2026, 11, 30, 21, tzinfo=UTC)))

    assert order.deadline == datetime(2026, 11, 27, 18, tzinfo=UTC)


def test_submitting_outside_the_session_is_an_error() -> None:
    env, plan = _env(now=_at(21)), baseline_plan()

    with pytest.raises(OutsideSessionError):
        env.submit(plan)

    assert env.store.get(plan.plan_id) is None
    assert env.broker.calls == []


def test_submitting_the_same_plan_again() -> None:
    env, plan = _env(), baseline_plan()
    first = env.submit(plan)

    again = env.submit(plan)

    assert env.broker.order_count(str(plan.plan_id)) == 1
    assert again == first
    assert again.status is OrderStatus.SUBMITTED


# 订单状态和券商回报


def test_partial_fill_then_full_fill() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)

    env.update(plan, WORKING, 30, "49.90")
    partly = env.track(plan, _at(15, 1))
    env.update(plan, FILLED, 100, "49.95", fees="2.15")
    filled = env.track(plan, _at(15, 2))

    assert (partly.status, partly.filled, str(partly.average_price), str(partly.fees)) == (
        OrderStatus.PARTIALLY_FILLED,
        30,
        "49.90",
        "0",
    )
    assert (filled.status, filled.filled, str(filled.average_price), str(filled.fees)) == (
        OrderStatus.FILLED,
        100,
        "49.95",
        "2.15",
    )


@pytest.mark.parametrize(
    ("seen_at", "expected"),
    [(_at(19, 59, 59), OrderStatus.CANCELLED), (_at(20), OrderStatus.EXPIRED)],
    ids=["before-deadline", "at-deadline"],
)
def test_cancelled_or_expired_depends_on_the_deadline(
    seen_at: datetime, expected: OrderStatus
) -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)
    env.update(plan, WORKING, 30)
    env.track(plan, _at(15, 1))

    env.update(plan, CANCELLED, 30)  # 比如在券商 App 里手动撤单
    order = env.track(plan, seen_at)

    assert (order.status, order.filled) == (expected, 30)


def test_day_order_lapses_at_the_close() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)

    env.update(plan, CANCELLED, 0)
    order = env.track(plan, _at(20, 0, 5))

    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 0)
    assert env.broker.cancel_requests == []


def test_rejected_after_acceptance() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)

    env.update(plan, BrokerOrderStatus.REJECTED, 0)
    order = env.track(plan, _at(15, 1))

    assert (order.status, order.filled) == (OrderStatus.REJECTED, 0)


def test_broker_reports_the_result_is_unknown() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)

    env.update(plan, BrokerOrderStatus.UNKNOWN, 0)
    order = env.track(plan, _at(15, 10))

    assert order.status is OrderStatus.UNKNOWN
    assert order.unknown_since == _at(15, 10)


def test_report_with_fewer_fills_is_ignored() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)
    env.update(plan, WORKING, 50)
    env.track(plan, _at(15, 1))

    env.update(plan, WORKING, 30)  # 过时的回报
    order = env.track(plan, _at(15, 2))

    assert (order.status, order.filled) == (OrderStatus.PARTIALLY_FILLED, 50)


def test_terminal_orders_are_not_tracked() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)
    env.update(plan, FILLED, 100)
    env.track(plan, _at(15, 1))
    calls = len(env.broker.calls)

    order = env.track(plan, _at(15, 2))

    assert len(env.broker.calls) == calls
    assert order.status is OrderStatus.FILLED


# 先查询再提交


def test_first_submission_looks_up_before_placing() -> None:
    env, plan = _env(), baseline_plan()

    env.submit(plan)

    assert env.calls(plan) == ["find", "place"]


def test_broker_refuses_the_order() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.reject_next_place()

    order = env.submit(plan)

    assert (order.status, order.filled) == (OrderStatus.REJECTED, 0)
    assert order.broker_order_id is None


def test_no_sell_before_the_stop_yields_its_shares() -> None:
    """spec「止损单还没让出股数时不提交卖单」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.update(p1, FILLED, 100)
    env.broker.positions["BBB"] = 100
    env.track(p1, BASELINE_NOW)  # 挂上 BBB-STOP-1，100 股
    p3 = baseline_plan(
        action=TradeAction.REDUCE,
        quantity=60,
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_price=None,
    )

    order = env.submit(p3, decision(60))

    assert env.calls(p3) == ["find"]
    assert order.status is OrderStatus.PENDING


def test_process_exited_after_placing_before_saving() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.accept(_pending(env, plan))
    env.restart()

    order = env.track(plan, _at(15, 5))

    assert env.calls(plan) == ["find"]
    assert env.broker.order_count(str(plan.plan_id)) == 1
    assert order.status is OrderStatus.SUBMITTED
    assert order.broker_order_id == env.broker.order(str(plan.plan_id)).broker_order_id


def test_process_exited_before_placing() -> None:
    env, plan = _env(), baseline_plan()
    _pending(env, plan)
    env.restart()

    order = env.track(plan, _at(15, 5))

    assert env.calls(plan) == ["find", "place"]
    assert order.status is OrderStatus.SUBMITTED


def test_downtime_past_the_deadline() -> None:
    env, plan = _env(), baseline_plan()
    _pending(env, plan)
    env.restart()

    order = env.track(plan, datetime(2026, 10, 15, 13, tzinfo=UTC))

    assert env.calls(plan) == ["find"]
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 0)


# 到期撤单


def test_cancel_at_the_deadline_and_not_before() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))
    cid = str(plan.plan_id)

    env.track(plan, _at(15, 59, 59))
    assert env.broker.cancel_requests == []

    at_deadline = env.track(plan, _at(16))
    assert env.broker.cancel_requests == [cid]
    assert at_deadline.status is OrderStatus.SUBMITTED

    env.update(plan, CANCELLED, 0)
    order = env.track(plan, _at(16, 0, 10))
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 0)


def test_partly_filled_order_expires_at_the_deadline() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))
    env.update(plan, WORKING, 30)

    env.track(plan, _at(16))
    assert env.broker.cancel_requests == [str(plan.plan_id)]

    env.update(plan, CANCELLED, 30)
    order = env.track(plan, _at(16, 0, 10))
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 30)


def test_fills_after_the_cancel_request() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))
    env.update(plan, WORKING, 30)
    env.track(plan, _at(16))

    env.update(plan, WORKING, 50)
    partly = env.track(plan, _at(16, 0, 5))
    assert (partly.status, partly.filled) == (OrderStatus.PARTIALLY_FILLED, 50)
    assert env.broker.cancel_requests == [str(plan.plan_id)]

    env.update(plan, CANCELLED, 50)
    order = env.track(plan, _at(16, 0, 10))
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 50)


def test_order_filled_by_the_deadline_is_not_cancelled() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))

    env.update(plan, FILLED, 100)
    order = env.track(plan, _at(16))

    assert order.status is OrderStatus.FILLED
    assert env.broker.cancel_requests == []


# 保存订单


def test_tracking_continues_after_a_restart() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)
    env.restart()

    env.update(plan, FILLED, 100, "50")
    order = env.track(plan, _at(15, 5))

    assert (order.status, order.filled, str(order.average_price)) == (
        OrderStatus.FILLED,
        100,
        "50",
    )


def test_cancel_is_not_repeated_after_a_restart() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))
    env.track(plan, _at(16))
    env.restart()

    order = env.track(plan, _at(16, 0, 5))

    assert env.broker.cancel_requests == [str(plan.plan_id)]
    assert order.status is OrderStatus.SUBMITTED


# 券商调用失败时状态未知


def test_place_timed_out_but_the_broker_received_it() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.fail_next("place", after_effect=True)

    unknown = env.submit(plan)
    assert unknown.status is OrderStatus.UNKNOWN
    assert unknown.unknown_since == BASELINE_NOW

    waiting = env.track(plan, _at(15, 0, 29))
    assert env.calls(plan) == ["find", "place"]
    assert waiting.status is OrderStatus.UNKNOWN

    order = env.track(plan, _at(15, 0, 30))
    assert env.calls(plan) == ["find", "place", "find"]
    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.order_count(str(plan.plan_id)) == 1


def test_place_timed_out_and_the_broker_did_not_receive_it() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.fail_next("place")

    assert env.submit(plan).status is OrderStatus.UNKNOWN
    assert env.broker.order_count(str(plan.plan_id)) == 0

    order = env.track(plan, _at(15, 0, 30))

    assert env.calls(plan) == ["find", "place", "find", "place"]
    assert [p.client_order_id for p in env.broker.placed] == [str(plan.plan_id)]
    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.order_count(str(plan.plan_id)) == 1


def test_failed_lookup_restarts_the_timer() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.fail_next("place", after_effect=True)
    env.submit(plan)

    env.broker.fail_next("find")
    again = env.track(plan, _at(15, 0, 30))
    assert again.status is OrderStatus.UNKNOWN
    assert again.unknown_since == _at(15, 0, 30)

    calls = len(env.broker.calls)
    env.track(plan, _at(15, 0, 59))
    assert len(env.broker.calls) == calls

    order = env.track(plan, _at(15, 1))
    assert order.status is OrderStatus.SUBMITTED


def test_broker_did_not_receive_it_until_after_the_deadline() -> None:
    env, plan = _env(now=_at(15, 59, 50)), baseline_plan()
    env.broker.fail_next("place")
    env.submit(plan, decision(expires_at=EXPIRES_16))

    order = env.track(plan, _at(16, 0, 20))

    assert env.calls(plan) == ["find", "place", "find"]
    assert env.broker.order_count(str(plan.plan_id)) == 0
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 0)


def test_disconnect_while_tracking() -> None:
    env, plan = _env(), baseline_plan()
    submitted = env.submit(plan)
    env.update(plan, WORKING, 30)
    env.track(plan, _at(15, 1))

    env.broker.fail_next("find")
    unknown = env.track(plan, _at(15, 10))
    assert (unknown.status, unknown.filled) == (OrderStatus.UNKNOWN, 30)
    assert unknown.broker_order_id == submitted.broker_order_id

    env.update(plan, WORKING, 60)
    order = env.track(plan, _at(15, 10, 30))
    assert (order.status, order.filled) == (OrderStatus.PARTIALLY_FILLED, 60)


def test_cancel_request_fails() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan, decision(expires_at=EXPIRES_16))

    env.broker.fail_next("cancel")
    unknown = env.track(plan, _at(16))
    assert unknown.status is OrderStatus.UNKNOWN
    assert unknown.unknown_since == _at(16)
    assert env.broker.cancel_requests == []

    order = env.track(plan, _at(16, 0, 30))
    assert order.status is OrderStatus.SUBMITTED
    assert env.broker.cancel_requests == [str(plan.plan_id)]


def test_accepted_order_that_the_broker_cannot_find() -> None:
    env, plan = _env(), baseline_plan()
    env.submit(plan)
    env.broker.lose(str(plan.plan_id))

    unknown = env.track(plan, _at(15, 10))
    assert unknown.status is OrderStatus.UNKNOWN
    assert unknown.unknown_since == _at(15, 10)

    still = env.track(plan, _at(15, 10, 30))
    assert still.status is OrderStatus.UNKNOWN
    assert env.calls(plan) == ["find", "place", "find", "find"]


# 状态未知时暂停这只股票的新订单


def _unknown_add_aaa(env: _Env) -> TradePlan:
    """加仓 AAA 的方案提交超时，券商其实收到了，订单状态未知。"""
    add = add_aaa()
    env.broker.fail_next("place", after_effect=True)
    assert env.submit(add).status is OrderStatus.UNKNOWN
    return add


def test_unknown_order_pauses_new_orders_for_the_same_stock() -> None:
    env = _env()
    _unknown_add_aaa(env)
    reduce = reduce_aaa()

    with pytest.raises(OrderPausedError):
        env.submit(reduce, decision(60))

    assert env.store.get(reduce.plan_id) is None
    assert env.calls(reduce) == []


def test_other_stocks_are_not_paused() -> None:
    env = _env()
    _unknown_add_aaa(env)

    assert env.submit(baseline_plan()).status is OrderStatus.SUBMITTED


def test_new_orders_resume_after_the_unknown_order_is_resolved() -> None:
    env = _env()
    add = _unknown_add_aaa(env)

    assert env.track(add, _at(15, 0, 30)).status is OrderStatus.SUBMITTED
    assert env.submit(reduce_aaa(), decision(60)).status is OrderStatus.SUBMITTED


def test_pending_order_also_pauses_new_orders() -> None:
    env = _env()
    _pending(env, add_aaa())
    reduce = reduce_aaa()

    with pytest.raises(OrderPausedError):
        env.submit(reduce, decision(60))

    assert env.store.get(reduce.plan_id) is None


def test_resubmitting_the_same_plan_is_not_paused() -> None:
    env, plan = _env(), baseline_plan()
    env.broker.fail_next("place")
    env.submit(plan)
    calls = len(env.broker.calls)
    env.clock.set(_at(15, 0, 10))

    order = env.submit(plan)

    assert order.status is OrderStatus.UNKNOWN
    assert len(env.broker.calls) == calls


# 执行配置


def test_lookup_delay_can_be_configured() -> None:
    env, plan = (
        _env(config=ExecutionConfig(unknown_lookup_delay=timedelta(seconds=60))),
        baseline_plan(),
    )
    env.broker.fail_next("place", after_effect=True)
    env.submit(plan)
    calls = len(env.broker.calls)

    env.track(plan, _at(15, 0, 59))
    assert len(env.broker.calls) == calls

    assert env.track(plan, _at(15, 1)).status is OrderStatus.SUBMITTED


# 状态机性质测试（design D11）

# 券商那边还没结束的订单：已受理或结果未知
_OPEN_AT_BROKER = (WORKING, BrokerOrderStatus.UNKNOWN)

# 券商那边的变化，成交出现得最多，让订单常常能挂到截止时刻
_BROKER_CHANGES = ("fill", "fill", "fill", "unknown", "reject", "cancel", "external_cancel")

# 刚查询过券商、而且订单不是状态未知时，券商回报和订单状态的对应
_MATCHING_STATUSES = {
    WORKING: (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED),
    FILLED: (OrderStatus.FILLED,),
    CANCELLED: (OrderStatus.CANCELLED, OrderStatus.EXPIRED),
    BrokerOrderStatus.REJECTED: (OrderStatus.REJECTED,),
}


class OrderLifecycleMachine(RuleBasedStateMachine):
    """随机交错跟进、提前撤单、券商那边的变化、券商调用失败和重启，检查订单只往前走、不会重复下单，
    有撤单要求以后不再提交。

    重启之后先对账。只有执行模块在操作券商，所以对账都没有不一致，
    见 add-reconciliation 的 design D7。
    """

    def __init__(self) -> None:
        super().__init__()
        self.env = _env()
        # 状态机不用基准数据里的 AAA 持仓：没有对应的买单，对账会报成来历不明
        del self.env.broker.positions["AAA"]
        self.plan = baseline_plan()
        self.cid = str(self.plan.plan_id)
        self.previous: Order | None = None
        # 最近一次调用查询了券商、而且没有失败；之后券商那边又变化时清掉
        self.synced = False
        self.last_failure: tuple[str, bool] | None = None
        # 第一次提前撤单之前，券商收到 P1 提交的次数；还没提前撤单时为 None
        self.places_before_cancel: int | None = None

    def _order(self) -> Order:
        order = self.env.store.get(self.plan.plan_id)
        assert order is not None
        return order

    def _broker_open(self) -> bool:
        broker = self.env.broker
        return broker.order_count(self.cid) > 0 and broker.order(self.cid).status in _OPEN_AT_BROKER

    def _broker_update(self, status: BrokerOrderStatus, filled: int | None = None) -> None:
        current = self.env.broker.order(self.cid)
        average_price = None if current.average_price is None else str(current.average_price)
        self.env.broker.update(
            self.cid,
            status,
            current.filled if filled is None else filled,
            average_price or ("50" if filled else None),
        )
        self.synced = False

    def _call(self, call: Callable[[], Order]) -> None:
        """调用执行用例，记下这次有没有查询券商、有没有失败，并检查截止时刻之后不再提交。"""
        broker = self.env.broker
        calls, failures = len(broker.calls), len(broker.failures)
        places = broker.calls_for(self.cid).count("place")
        before = self.env.store.get(self.plan.plan_id)

        order = call()

        new_failures = broker.failures[failures:]
        if new_failures:
            self.last_failure = new_failures[-1]
        self.synced = len(broker.calls) > calls and not new_failures
        if broker.calls_for(self.cid).count("place") > places:
            assert self.env.clock.now() < order.deadline, "截止时刻之后不能再提交"
        if before is None or before.status is order.status:
            return
        if before.status is OrderStatus.UNKNOWN and self.last_failure is not None:
            operation, after_effect = self.last_failure
            event(f"{operation} 失败（{'生效后' if after_effect else '生效前'}）后恢复")
        if order.status is OrderStatus.EXPIRED and order.filled > 0:
            event("部分成交后到期")
        if order.status is OrderStatus.CANCELLED and order.cancel_wanted:
            event("提前撤单后已撤销")

    @initialize(
        minutes=st.integers(1, 5),
        place_failure=st.sampled_from([None, False, True]),
        cancel_failure=st.sampled_from([None, False, True]),
    )
    def submit(self, minutes: int, place_failure: bool | None, cancel_failure: bool | None) -> None:
        """提交基准方案，截止时刻在 minutes 分钟后；可能安排第一次提交、第一次撤单失败。"""
        if place_failure is not None:
            self.env.broker.fail_next("place", after_effect=place_failure)
        if cancel_failure is not None:
            self.env.broker.fail_next("cancel", after_effect=cancel_failure)
        result = decision(expires_at=BASELINE_NOW + timedelta(minutes=minutes))
        self._call(lambda: self.env.submit(self.plan, result))

    @rule(seconds=st.integers(0, 120))
    def track(self, seconds: int) -> None:
        self.env.clock.advance(timedelta(seconds=seconds))
        self._call(lambda: self.env.service.track(self.plan.plan_id))

    @rule(seconds=st.integers(0, 120))
    def cancel_early(self, seconds: int) -> None:
        """拨动时间后撤掉 BBB 的订单。"""
        self.env.clock.advance(timedelta(seconds=seconds))
        if self.places_before_cancel is None:
            self.places_before_cancel = self.env.broker.calls_for(self.cid).count("place")
        self._call(self._cancel)

    def _cancel(self) -> Order:
        """撤掉 BBB 的订单，返回之后的订单。"""
        self.env.service.cancel_orders("BBB")
        return self._order()

    @rule(change=st.sampled_from(_BROKER_CHANGES), shares=st.integers(1, 40))
    def broker_changes(self, change: str, shares: int) -> None:
        """券商那边的变化，订单在券商那边已经结束时什么都不做：

        - fill：成交 shares 股，成交够委托股数就是全部成交
        - unknown：券商报结果未知
        - reject：拒绝，只发生在没有成交的订单上
        - cancel：请求的撤单生效，或者过了截止时刻当日单失效；两者都不是时什么都不做
        - external_cancel：截止前在券商那边被撤销，比如手动撤单
        """
        if not self._broker_open():
            return
        current, order = self.env.broker.order(self.cid), self._order()
        if change == "fill":
            filled = min(order.quantity, current.filled + shares)
            self._broker_update(FILLED if filled == order.quantity else WORKING, filled)
        elif change == "unknown":
            self._broker_update(BrokerOrderStatus.UNKNOWN)
        elif change == "reject" and current.filled == 0:
            self._broker_update(BrokerOrderStatus.REJECTED)
        elif change == "external_cancel" or (
            change == "cancel"
            and (self.env.broker.cancel_requests or self.env.clock.now() >= order.deadline)
        ):
            self._broker_update(CANCELLED)

    @rule(operation=st.sampled_from(["find", "place", "cancel"]), after_effect=st.booleans())
    def next_call_fails(self, operation: str, after_effect: bool) -> None:
        self.env.broker.fail_next(operation, after_effect=after_effect)

    @rule()
    def restart(self) -> None:
        self.env.restart()
        event("重启")
        self._call(self._reconcile)

    def _reconcile(self) -> Order:
        """对账，检查没有不一致，返回对账之后的订单。"""
        assert self.env.service.reconcile().clean
        return self._order()

    @invariant()
    def broker_has_at_most_one_order(self) -> None:
        assert self.env.broker.order_count(self.cid) <= 1

    @invariant()
    def order_only_moves_forward(self) -> None:
        order = self.env.store.get(self.plan.plan_id)
        if order is None:
            return
        assert 0 <= order.filled <= order.quantity
        if self.previous is not None:
            assert order.filled >= self.previous.filled
            if self.previous.status.is_terminal:
                assert order == self.previous
        self.previous = order

    @invariant()
    def no_place_after_a_cancel_request(self) -> None:
        if self.places_before_cancel is not None:
            places = self.env.broker.calls_for(self.cid).count("place")
            assert places == self.places_before_cancel, "有撤单要求以后不能再提交"

    @invariant()
    def cancel_succeeds_at_most_once(self) -> None:
        broker = self.env.broker
        failed = sum(1 for operation, _ in broker.failures if operation == "cancel")
        assert broker.calls_for(self.cid).count("cancel") - failed <= 1

    @invariant()
    def order_matches_the_broker_after_a_lookup(self) -> None:
        if not self.synced:
            return
        order, broker = self._order(), self.env.broker
        if order.status is OrderStatus.UNKNOWN:
            return
        if broker.order_count(self.cid) == 0:
            # 有撤单要求、截止时刻之前确认券商没收到的，结束为已撤销
            ended = (OrderStatus.EXPIRED, OrderStatus.REJECTED)
            assert order.status in ended or (
                order.cancel_wanted and order.status is OrderStatus.CANCELLED
            )
            return
        reported = broker.order(self.cid)
        assert order.filled == reported.filled
        assert order.status in _MATCHING_STATUSES[reported.status]

    def teardown(self) -> None:
        """过了截止时刻、券商不再失败时，订单最终一定进入终态。"""
        order = self.env.store.get(self.plan.plan_id)
        if order is None:
            return
        self.env.broker.clear_failures()
        start = max(self.env.clock.now(), order.deadline)
        delay = self.env.config.unknown_lookup_delay
        self.env.clock.set(start + delay)
        self.env.service.track(self.plan.plan_id)
        if self._broker_open():
            self._broker_update(CANCELLED)
        self.env.clock.set(start + 2 * delay)
        assert self.env.service.track(self.plan.plan_id).status.is_terminal


TestOrderLifecycleMachine = OrderLifecycleMachine.TestCase
