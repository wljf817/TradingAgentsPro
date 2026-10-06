"""买单和止损单错开的执行用例测试：买单挂着时撤掉止损单、买单结束后挂回、错开中的备用止损。

场景见 openspec 的 stop-protection spec「买单和止损单错开」，以及「本地备用止损的触发」
「保存止损保护」、order-lifecycle spec「先查询再提交」里错开的场景。除非另有说明，
都打开「买单和止损单错开」。测试用假券商和假止损单接口安排券商那边的情况，用测试时钟拨动时间；
"重启"是用同一套存储和假券商重新创建执行用例。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import (
    BASELINE_NOW,
    add_bbb,
    baseline_plan,
    decision,
    execution_service,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionConfig,
    ExecutionService,
    Order,
    OrderSide,
    OrderStatus,
    Protection,
    StopOrder,
    StopStatus,
    stop_target,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
STOP_1, STOP_2 = "BBB-STOP-1", "BBB-STOP-2"
STAGGER = ExecutionConfig(stagger_buys_and_stops=True)


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

    def submit(self, plan: TradePlan) -> Order:
        return self.service.submit(plan, decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime | None = None) -> Order:
        if at is not None:
            self.clock.set(at)
        return self.service.track(plan.plan_id)

    def report(
        self, plan: TradePlan, status: BrokerOrderStatus, filled: int, position: int
    ) -> None:
        """券商回报这笔订单变为……，券商持仓变为 position 股。"""
        self.broker.update(str(plan.plan_id), status, filled)
        self.broker.positions[plan.symbol] = position

    def protection(self) -> Protection:
        protection = self.protections.get("BBB")
        assert protection is not None
        return protection

    def stop(self) -> StopOrder:
        stop = self.protection().stop
        assert stop is not None
        return stop

    def submitted(self, plan: TradePlan) -> int:
        """券商收到了几次这个方案的提交。"""
        return self.broker.order_count(str(plan.plan_id))

    def hold_100(self) -> TradePlan:
        """持有 BBB 100 股，止损单已对齐：P1 全部成交，BBB-STOP-1 挂着 100 股、止损价 45。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.report(p1, FILLED, 100, position=100)
        self.track(p1)
        assert self.stops.stop(STOP_1).quantity == 100
        return p1

    def add(self, plan: TradePlan | None = None) -> TradePlan:
        """15:10 提交加仓方案（默认是 P2），晚于 P1 创建，止损价才会换成它的。"""
        plan = plan or add_bbb()
        self.clock.set(_at(15, 10))
        self.submit(plan)
        return plan

    def adding(self) -> TradePlan:
        """持有 BBB 100 股，提交 P2 以后 BBB-STOP-1 已经撤销，P2 的订单已提交。"""
        self.hold_100()
        p2 = self.add()
        self.stops.settle()
        assert self.track(p2).status is OrderStatus.SUBMITTED
        return p2


def _env(config: ExecutionConfig = STAGGER) -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(BASELINE_NOW)
    stops, protections = FakeStopBroker(), InMemoryProtectionStore()
    service = execution_service(store, broker, clock, config, stops, protections)
    return _Env(store, broker, stops, protections, clock, config, service)


# 买单和止损单错开


def test_an_add_waits_until_the_broker_ends_the_stop() -> None:
    """spec「加仓时先撤止损单，券商确认结束后再提交买单」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()

    order = env.submit(p2)

    assert env.stops.cancel_requests == [STOP_1]
    assert env.submitted(p2) == 0
    assert order.status is OrderStatus.PENDING
    assert env.protection().staggered
    assert env.protection().unprotected_since is None

    order = env.track(p2)

    assert env.stops.cancel_requests == [STOP_1, STOP_1]
    assert env.submitted(p2) == 0
    assert order.status is OrderStatus.PENDING

    env.stops.settle()
    order = env.track(p2)

    placed = env.broker.placed[-1]
    assert (placed.plan_id, placed.side, placed.quantity) == (p2.plan_id, OrderSide.BUY, 50)
    assert placed.limit_price == Decimal(52)
    assert order.status is OrderStatus.SUBMITTED
    assert [s.client_order_id for s in env.stops.placed] == [STOP_1]


def test_no_stop_while_an_add_fills_in_part() -> None:
    """spec「加仓部分成交时不挂止损单」。"""
    env = _env()
    p2 = env.adding()
    env.report(p2, WORKING, 20, position=120)

    env.track(p2)

    assert [s.client_order_id for s in env.stops.placed] == [STOP_1]
    target = stop_target(120, env.store.of_symbol("BBB"), stagger=True)
    assert (target.quantity, target.stop_price) == (0, Decimal(48))
    assert env.protection().staggered


def test_a_finished_add_places_a_stop_for_the_whole_holding() -> None:
    """spec「加仓结束后按合计持仓挂新止损单」。"""
    env = _env()
    p2 = env.adding()
    env.report(p2, WORKING, 20, position=120)
    env.track(p2)
    env.report(p2, FILLED, 50, position=150)

    env.track(p2)

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        150,
        Decimal(48),
    )
    assert not env.protection().staggered


def test_an_add_without_fills_puts_the_old_stop_back() -> None:
    """spec「加仓一股没成交就结束，按原来的止损价挂回」。"""
    env = _env()
    p2 = env.adding()
    env.report(p2, CANCELLED, 0, position=100)

    order = env.track(p2, at=AFTER_CLOSE)

    assert order.status is OrderStatus.EXPIRED
    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        100,
        Decimal(45),
    )
    assert not env.protection().staggered


def test_a_new_buy_gets_its_stop_once_it_ends() -> None:
    """spec「新买入部分成交时不挂止损单，买单结束后再挂」。"""
    env, p1 = _env(), baseline_plan()

    env.submit(p1)

    assert env.submitted(p1) == 1
    assert env.stops.calls == []
    assert env.protections.get("BBB") is None  # 没有记录：不是错开中

    env.report(p1, WORKING, 30, position=30)
    env.track(p1)

    assert env.stops.placed == []
    assert env.protection().staggered

    env.report(p1, FILLED, 100, position=100)
    env.track(p1)

    [placed] = env.stops.placed
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_1,
        100,
        Decimal(45),
    )
    assert not env.protection().staggered


def test_no_buy_while_the_stop_is_unknown() -> None:
    """spec「止损单状态未知时不提交买单」。"""
    env = _env()
    env.hold_100()
    env.protections.save(env.protection().with_stop(env.stop().mark_unknown(_at(15))))
    env.stops.calls.clear()
    env.clock.set(_at(15, 0, 10))
    p2 = add_bbb()

    order = env.submit(p2)

    assert env.submitted(p2) == 0
    assert order.status is OrderStatus.PENDING
    assert env.stops.calls == []


def test_staggered_is_not_unprotected() -> None:
    """spec「错开中不是无保护仓位，可以再提交买单」。"""
    env = _env()
    env.adding()
    p5 = add_bbb(30)

    order = env.submit(p5)

    assert order.status is OrderStatus.SUBMITTED


def test_cancelling_an_add_that_waits_for_the_stop_puts_the_stop_back() -> None:
    """spec「撤掉还在等止损单撤掉的买单后，挂回止损单」。"""
    env = _env()
    env.hold_100()
    p2 = add_bbb()
    env.submit(p2)

    env.service.cancel_orders("BBB")

    order = env.store.get(p2.plan_id)
    assert order is not None
    assert (order.status, order.filled) == (OrderStatus.CANCELLED, 0)
    assert env.submitted(p2) == 0
    assert not env.protection().staggered

    env.stops.settle()
    env.service.protect("BBB")

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity, placed.stop_price) == (
        STOP_2,
        100,
        Decimal(45),
    )


def test_adds_keep_the_stop_when_not_staggering() -> None:
    """spec「开关关闭时加仓不撤止损单」。"""
    env = _env(ExecutionConfig())
    env.hold_100()

    order = env.submit(add_bbb())

    assert order.status is OrderStatus.SUBMITTED
    assert env.stops.cancel_requests == []
    assert not env.protection().staggered


# 本地备用止损的触发


def test_backup_stop_fires_while_staggered() -> None:
    """spec「错开中、价格跌到止损价时触发」。"""
    env = _env()
    p2 = env.adding()

    assert env.service.backup_stop_due("BBB", Decimal(45))
    assert not env.service.backup_stop_due("BBB", Decimal("45.01"))

    env.report(p2, WORKING, 20, position=120)
    env.track(p2)

    assert env.service.backup_stop_due("BBB", Decimal("47.50"))


def test_no_backup_stop_once_the_stop_is_back() -> None:
    """spec「错开结束、挂上止损单后不触发」。"""
    env = _env()
    p2 = env.adding()
    env.report(p2, FILLED, 50, position=150)
    env.track(p2)
    assert env.stop().client_order_id == STOP_2
    assert env.stop().status is StopStatus.WORKING

    assert not env.service.backup_stop_due("BBB", Decimal(40))


# 保存止损保护


def test_still_staggered_after_a_restart() -> None:
    """spec「重启后仍是错开中」。"""
    env = _env()
    env.adding()

    env.restart()

    assert env.protection().staggered
    assert env.service.backup_stop_due("BBB", Decimal(45))


# order-lifecycle spec「先查询再提交」


def test_a_buy_is_not_submitted_while_the_stop_is_still_working() -> None:
    """order-lifecycle spec「买单和止损单错开时，止损单还没撤掉不提交买单」。"""
    env = _env()
    env.hold_100()
    env.broker.calls.clear()
    env.stops.calls.clear()
    p5 = add_bbb()

    order = env.submit(p5)

    assert env.stops.calls_for(STOP_1) == ["find", "cancel"]
    assert env.broker.calls_for(str(p5.plan_id)) == ["find"]
    assert order.status is OrderStatus.PENDING
