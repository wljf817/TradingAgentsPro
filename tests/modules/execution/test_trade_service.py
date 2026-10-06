"""执行用例写交易记录的测试：订单和止损单每次变化都更新交易记录，先写交易记录再保存。

场景见 openspec 的 trade-records spec「交易记录的内容」「交易记录的写入」「止损单交易记录的状态」
「成交日和结算日」。测试用假券商安排券商那边的情况，用测试时钟拨动时间。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.modules.execution.builders import (
    BASELINE_NOW,
    add_bbb,
    baseline_plan,
    close_bbb,
    decision,
    execution_service,
    reduce_aaa,
)
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionService,
    Order,
    OrderStatus,
    Protection,
    Trade,
    TradeKind,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
STOP_1 = "BBB-STOP-1"
WEDNESDAY, THURSDAY = date(2026, 10, 14), date(2026, 10, 15)


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """基准交易日 2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


class _VersionedTrades(InMemoryTradeStore):
    """记下每条交易记录的各个版本；内容和最新版本相同时不记，和数据库实现一样。"""

    def __init__(self) -> None:
        super().__init__()
        self.versions: dict[str, list[Trade]] = {}

    def save(self, trade: Trade) -> None:
        history = self.versions.setdefault(trade.client_order_id, [])
        if not history or history[-1] != trade:
            history.append(trade)
        super().save(trade)


class _StoreError(RuntimeError):
    """存储出错，比如数据库连不上。"""


class _FailingOrders(InMemoryOrderStore):
    """fail_next 之后的下一次保存抛 _StoreError，不保存。"""

    def __init__(self) -> None:
        super().__init__()
        self._fail = False

    def fail_next(self) -> None:
        self._fail = True

    def save(self, order: Order) -> None:
        if self._fail:
            self._fail = False
            raise _StoreError("保存订单出错")
        super().save(order)


class _FailingProtections(InMemoryProtectionStore):
    """fail_next 之后的下一次保存抛 _StoreError，不保存。"""

    def __init__(self) -> None:
        super().__init__()
        self._fail = False

    def fail_next(self) -> None:
        self._fail = True

    def save(self, protection: Protection) -> None:
        if self._fail:
            self._fail = False
            raise _StoreError("保存止损保护出错")
        super().save(protection)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、假止损单接口、测试时钟。"""

    store: _FailingOrders
    broker: FakeBroker
    stops: FakeStopBroker
    protections: _FailingProtections
    trades: _VersionedTrades
    clock: FakeClock
    service: ExecutionService

    def submit(self, plan: TradePlan, result: RiskDecision | None = None) -> Order:
        return self.service.submit(plan, result or decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime | None = None) -> Order:
        if at is not None:
            self.clock.set(at)
        return self.service.track(plan.plan_id)

    def protect(self, at: datetime | None = None) -> None:
        if at is not None:
            self.clock.set(at)
        self.service.protect("BBB")

    def report(
        self,
        plan: TradePlan,
        status: BrokerOrderStatus,
        filled: int,
        average_price: str | None = None,
        fees: str = "0",
        position: int | None = None,
    ) -> None:
        """券商回报这笔订单变为……；给了 position 时，券商持仓变为 position 股。"""
        self.broker.update(str(plan.plan_id), status, filled, average_price, fees)
        if position is not None:
            self.broker.positions[plan.symbol] = position

    def trade(self, client_order_id: str) -> Trade:
        trade = self.trades.get(client_order_id)
        assert trade is not None
        return trade

    def statuses(self, client_order_id: str) -> list[OrderStatus]:
        return [t.status for t in self.trades.versions[client_order_id]]

    def hold_100(self) -> TradePlan:
        """持有 BBB 100 股，止损单已对齐：P1 全部成交，BBB-STOP-1 挂着 100 股、止损价 45。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.report(p1, FILLED, 100, "50", position=100)
        self.track(p1)
        assert self.stops.stop(STOP_1).quantity == 100
        return p1


def _env(now: datetime = BASELINE_NOW) -> _Env:
    store, broker, clock = _FailingOrders(), FakeBroker(), FakeClock(now)
    stops, protections, trades = FakeStopBroker(), _FailingProtections(), _VersionedTrades()
    service = execution_service(store, broker, clock, None, stops, protections, trades)
    broker.positions["AAA"] = 100  # 基准数据：持有 AAA 100 股，没有 AAA 的止损单
    return _Env(store, broker, stops, protections, trades, clock, service)


# 交易记录的内容


def test_trade_of_a_buy_order() -> None:
    """spec「买单的交易记录」。"""
    env, p1 = _env(), baseline_plan()

    env.submit(p1)

    assert env.trade(str(p1.plan_id)) == Trade(
        client_order_id=str(p1.plan_id),
        kind=TradeKind.BUY,
        symbol="BBB",
        plan_id=p1.plan_id,
        quantity=100,
        filled=0,
        average_price=None,
        fees=Decimal(0),
        status=OrderStatus.SUBMITTED,
        created_at=BASELINE_NOW,
        traded_on=None,
        settles_on=None,
    )


def test_trade_of_a_sell_order() -> None:
    """spec「卖单的交易记录」。"""
    env, p5 = _env(), reduce_aaa(60)
    env.submit(p5)
    env.report(p5, FILLED, 60, "101", fees="2")

    env.track(p5)

    trade = env.trade(str(p5.plan_id))
    assert (trade.kind, trade.symbol, trade.plan_id) == (TradeKind.SELL, "AAA", p5.plan_id)
    assert (trade.quantity, trade.filled, trade.average_price) == (60, 60, Decimal(101))
    assert (trade.fees, trade.status) == (Decimal(2), OrderStatus.FILLED)
    assert (trade.traded_on, trade.settles_on) == (WEDNESDAY, THURSDAY)


def test_trade_of_a_stop_order() -> None:
    """spec「止损单的交易记录」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30, "49.90", position=30)

    env.track(p1, at=_at(15))

    trade = env.trade(STOP_1)
    assert (trade.kind, trade.symbol, trade.plan_id) == (TradeKind.STOP, "BBB", p1.plan_id)
    assert (trade.quantity, trade.filled, trade.status) == (30, 0, OrderStatus.SUBMITTED)
    assert (trade.created_at, trade.traded_on, trade.settles_on) == (_at(15), None, None)


def test_an_add_fill_switches_the_stop_source_to_the_add() -> None:
    """spec「加仓成交后，止损单的来源方案换成加仓方案」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()
    env.clock.set(_at(15, 10))
    env.submit(p2)
    env.report(p2, WORKING, 20, position=120)

    env.track(p2)

    assert env.stops.modify_requests == [(STOP_1, 120, Decimal(48))]
    assert (env.trade(STOP_1).plan_id, env.trade(STOP_1).quantity) == (p2.plan_id, 100)

    env.stops.update(STOP_1, WORKING, quantity=120, stop_price="48")
    env.protect()

    assert (env.trade(STOP_1).plan_id, env.trade(STOP_1).quantity) == (p2.plan_id, 120)


# 交易记录的写入


def test_every_change_updates_the_trade() -> None:
    """spec「每次变化都更新交易记录」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30, "49.90")
    env.track(p1)
    env.report(p1, FILLED, 100, "49.95", fees="2")
    env.track(p1)

    versions = env.trades.versions[str(p1.plan_id)]
    assert [(t.status, t.filled, t.average_price, t.fees) for t in versions] == [
        (OrderStatus.PENDING, 0, None, Decimal(0)),
        (OrderStatus.SUBMITTED, 0, None, Decimal(0)),
        (OrderStatus.PARTIALLY_FILLED, 30, Decimal("49.90"), Decimal(0)),
        (OrderStatus.FILLED, 100, Decimal("49.95"), Decimal(2)),
    ]


def test_a_cancel_request_alone_adds_no_version() -> None:
    """spec「只有撤单要求时不写新版本」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    assert len(env.trades.versions[str(p1.plan_id)]) == 2

    env.clock.set(_at(15, 10))
    [order] = env.service.cancel_orders("BBB")

    assert order.cancel_wanted
    assert env.statuses(str(p1.plan_id)) == [OrderStatus.PENDING, OrderStatus.SUBMITTED]


def test_the_trade_is_written_before_a_failing_order_save() -> None:
    """spec「保存订单出错时交易记录已经更新」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.store.fail_next()
    env.report(p1, FILLED, 100, "50")

    with pytest.raises(_StoreError):
        env.track(p1)

    assert env.trade(str(p1.plan_id)).status is OrderStatus.FILLED
    stored = env.store.get(p1.plan_id)
    assert stored is not None
    assert stored.status is OrderStatus.SUBMITTED

    assert env.track(p1).status is OrderStatus.FILLED
    assert env.statuses(str(p1.plan_id)) == [
        OrderStatus.PENDING,
        OrderStatus.SUBMITTED,
        OrderStatus.FILLED,
    ]


def test_the_trade_is_written_before_a_failing_protection_save() -> None:
    """spec「保存止损保护出错时交易记录已经更新」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.protections.fail_next()
    env.report(p1, WORKING, 30, position=30)

    with pytest.raises(_StoreError):
        env.track(p1)

    assert env.trade(STOP_1).status is OrderStatus.PENDING
    assert env.stops.placed == []

    env.track(p1)

    [placed] = env.stops.placed
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_1,
        30,
        Decimal(45),
    )
    assert env.trade(STOP_1).status is OrderStatus.SUBMITTED


# 止损单交易记录的状态


def test_a_timed_out_stop_placement_is_pending_then_unknown() -> None:
    """spec「挂单超时时先后是待提交和状态未知」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.fail_next("place")
    env.report(p1, WORKING, 30, position=30)

    env.track(p1, at=_at(15))

    assert env.statuses(STOP_1) == [OrderStatus.PENDING, OrderStatus.UNKNOWN]


def test_a_working_stop_without_fills_is_submitted() -> None:
    """spec「挂着、没有成交时是已提交」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, WORKING, 30, position=30)

    env.track(p1)

    trade = env.trade(STOP_1)
    assert (trade.status, trade.quantity, trade.filled) == (OrderStatus.SUBMITTED, 30, 0)


def test_a_partly_filled_stop() -> None:
    """spec「止损单部分成交」。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, WORKING, quantity=60, filled=40, average_price="44.90", fees="2")
    env.broker.positions["BBB"] = 60

    env.protect()

    trade = env.trade(STOP_1)
    assert (trade.status, trade.quantity, trade.filled) == (OrderStatus.PARTIALLY_FILLED, 100, 40)
    assert (trade.average_price, trade.fees) == (Decimal("44.90"), Decimal(2))


def test_a_filled_stop() -> None:
    """spec「止损单全部成交」。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, FILLED, quantity=0, filled=100, average_price="44.80", fees="2")
    env.broker.positions["BBB"] = 0

    env.protect()

    trade = env.trade(STOP_1)
    assert (trade.status, trade.quantity, trade.filled) == (OrderStatus.FILLED, 100, 100)
    assert (trade.average_price, trade.fees) == (Decimal("44.80"), Decimal(2))


def test_a_stop_cancelled_for_a_close() -> None:
    """spec「清仓时撤销的止损单」。"""
    env = _env()
    env.hold_100()
    p4 = close_bbb()
    env.submit(p4)
    env.stops.settle()  # 撤单生效：BBB-STOP-1 撤销，未成交 100 股

    env.track(p4)

    trade = env.trade(STOP_1)
    assert (trade.status, trade.quantity, trade.filled) == (OrderStatus.CANCELLED, 100, 0)


def test_a_stop_whose_placements_were_all_rejected() -> None:
    """spec「挂单都被拒的止损单」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.stops.reject_next("place", 2)
    env.report(p1, WORKING, 30, position=30)

    env.track(p1)

    trade = env.trade(STOP_1)
    assert (trade.status, trade.quantity, trade.filled) == (OrderStatus.REJECTED, 30, 0)


# 成交日和结算日


def test_an_order_seen_filled_after_the_close() -> None:
    """spec「收盘后才查到成交的订单」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, FILLED, 100, "50")

    env.track(p1, at=_at(20, 0, 5))

    trade = env.trade(str(p1.plan_id))
    assert (trade.traded_on, trade.settles_on) == (WEDNESDAY, THURSDAY)


@pytest.mark.parametrize(
    "seen_at",
    [_at(15, 30), datetime(2026, 10, 15, 13, tzinfo=UTC)],
    ids=["in-session", "before-the-next-open"],
)
def test_a_stop_trades_on_the_session_it_filled_in(seen_at: datetime) -> None:
    """spec「盘中发现成交的止损单」「第二天开盘前才发现成交的止损单」。"""
    env = _env()
    env.hold_100()
    env.stops.update(STOP_1, FILLED, quantity=0, filled=100, average_price="44.80")
    env.broker.positions["BBB"] = 0

    env.protect(at=seen_at)

    trade = env.trade(STOP_1)
    assert (trade.traded_on, trade.settles_on) == (WEDNESDAY, THURSDAY)


@pytest.mark.parametrize(
    ("now", "expires_at", "traded_on", "settles_on"),
    [
        (
            datetime(2026, 10, 16, 15, tzinfo=UTC),
            datetime(2026, 10, 16, 20, tzinfo=UTC),
            date(2026, 10, 16),
            date(2026, 10, 19),
        ),
        (
            datetime(2026, 11, 25, 15, tzinfo=UTC),
            datetime(2026, 11, 25, 21, tzinfo=UTC),
            date(2026, 11, 25),
            date(2026, 11, 27),
        ),
    ],
    ids=["friday", "before-thanksgiving"],
)
def test_settlement_is_the_next_trading_day(
    now: datetime, expires_at: datetime, traded_on: date, settles_on: date
) -> None:
    """spec「周五成交，周一结算」「结算日跳过休市日」。"""
    env, p1 = _env(now), baseline_plan()
    env.submit(p1, decision(expires_at=expires_at))
    env.report(p1, FILLED, 100, "50")

    env.track(p1)

    trade = env.trade(str(p1.plan_id))
    assert (trade.traded_on, trade.settles_on) == (traded_on, settles_on)


def test_a_trade_without_fills_has_no_trade_day() -> None:
    """spec「没有成交的交易记录没有成交日」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.report(p1, CANCELLED, 0)

    env.track(p1)

    trade = env.trade(str(p1.plan_id))
    assert trade.status is OrderStatus.CANCELLED
    assert (trade.traded_on, trade.settles_on) == (None, None)
