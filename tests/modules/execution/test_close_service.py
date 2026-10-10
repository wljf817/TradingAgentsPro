"""收盘对账的执行用例测试：什么时候到期、一次收盘对账的步骤、差异、连续天数、记录和失败。

场景见 openspec 的 close-reconciliation spec。测试用假券商和假止损单接口安排券商那边的情况，
用测试时钟拨动时间；止损保护存储带着测试时钟，记下历史版本。"重启"是用同一套存储和假券商重新
创建执行用例；重启以后先做启动对账，除非另有说明。这里直接调用执行用例，不经过巡检、不拿锁。
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.closes import InMemoryCloseReconciliationStore
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import baseline_plan, decision, execution_service
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    CloseReconciliation,
    ExecutionConfig,
    ExecutionService,
    LeftoverOrder,
    NewStop,
    Order,
    OrderSide,
    OrderStatus,
    ReconcileMismatchError,
    Reconciliation,
    UnprotectedSpell,
    WorkingOrder,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
DAY = date(2026, 10, 14)


def _at(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 10) -> datetime:
    """2026 年某一天的某个 UTC 时刻，默认 10 月。"""
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


def _record(day: date, streak: int, **changes: Any) -> CloseReconciliation:
    """一条之前保存的收盘对账记录：没有差异、1 只持仓。"""
    record = CloseReconciliation(
        day=day,
        finished_at=datetime(day.year, day.month, day.day, 20, 10, tzinfo=UTC),
        result=Reconciliation(),
        leftover_orders=(),
        new_stops=(),
        unprotected=(),
        holdings=1,
        resumed=False,
        streak=streak,
    )
    return replace(record, **changes)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、假止损单接口、测试时钟。"""

    clock: FakeClock
    config: ExecutionConfig = field(default_factory=ExecutionConfig)
    store: InMemoryOrderStore = field(default_factory=InMemoryOrderStore)
    broker: FakeBroker = field(default_factory=FakeBroker)
    stops: FakeStopBroker = field(default_factory=FakeStopBroker)
    closes: InMemoryCloseReconciliationStore = field(
        default_factory=InMemoryCloseReconciliationStore
    )

    def __post_init__(self) -> None:
        self.protections = InMemoryProtectionStore(self.clock)
        self.service = self._service(reconciled=True)

    def _service(self, *, reconciled: bool) -> ExecutionService:
        return execution_service(
            self.store,
            self.broker,
            self.clock,
            self.config,
            self.stops,
            self.protections,
            closes=self.closes,
            reconciled=reconciled,
        )

    def restart(self, at: datetime, *, reconcile: bool = True) -> None:
        """进程在 at 重启：重新创建执行用例，清掉调用记录；reconcile 为真时先做启动对账。"""
        self.clock.set(at)
        self.service = self._service(reconciled=False)
        if reconcile:
            self.service.reconcile()
        self.forget_calls()

    def forget_calls(self) -> None:
        self.broker.calls.clear()
        self.stops.calls.clear()

    def due(self, at: datetime) -> date | None:
        self.clock.set(at)
        return self.service.close_due()

    def close(self, day: date = DAY, at: datetime | None = None) -> CloseReconciliation:
        """在 at（默认这一天的 20:10Z）做 day 的收盘对账。"""
        self.clock.set(at or datetime(day.year, day.month, day.day, 20, 10, tzinfo=UTC))
        return self.service.close_reconcile(day)

    def submit(self, plan: TradePlan) -> Order:
        return self.service.submit(plan, decision(plan.quantity or 100))

    def track(self, plan: TradePlan, at: datetime) -> Order:
        self.clock.set(at)
        return self.service.track(plan.plan_id)

    def buy_next_day(self) -> Order:
        """2026-10-15 14:00Z（盘中）提交一个买入 DDD 的方案，有效期到当天收盘。"""
        self.clock.set(_at(15, 14))
        return self.service.submit(baseline_plan(symbol="DDD"), decision(expires_at=_at(15, 20)))

    def order(self, plan: TradePlan) -> Order:
        order = self.store.get(plan.plan_id)
        assert order is not None
        return order

    def stop_id(self, symbol: str = "BBB") -> str:
        protection = self.protections.get(symbol)
        assert protection is not None and protection.stop is not None
        return protection.stop.client_order_id

    def writes_to_stops(self) -> list[str]:
        """止损单接口收到的挂单、改单和撤单。"""
        return [op for op, _ in self.stops.calls if op in ("place", "modify", "cancel")]

    def hold_100(self) -> TradePlan:
        """持有 BBB 100 股，止损单已对齐：P1 全部成交，BBB-STOP-1 挂着 100 股、止损价 45。"""
        p1 = baseline_plan()
        self.submit(p1)
        self.broker.update(str(p1.plan_id), FILLED, 100, "50")
        self.broker.positions["BBB"] = 100
        self.track(p1, self.clock.now())
        assert self.stops.stop("BBB-STOP-1").quantity == 100
        return p1

    def unprotect(self, start: datetime, end: datetime) -> None:
        """BBB 从 start 起是无保护仓位（止损单被撤、补挂两次被拒），end 补挂成功。"""
        self.stops.update(self.stop_id(), CANCELLED)
        self.stops.reject_next("place", 2)
        self.clock.set(start)
        protection = self.service.protect("BBB")
        assert protection is not None and protection.unprotected_since == start
        self.clock.set(end)
        protection = self.service.protect("BBB")
        assert protection is not None and protection.unprotected_since is None


def _env(now: datetime | None = None, config: ExecutionConfig | None = None) -> _Env:
    """一套执行用例，默认从 2026-10-14 14:00Z（盘中）开始，已经完成一次没有不一致的对账。"""
    return _Env(FakeClock(now or _at(14, 14)), config or ExecutionConfig())


# 收盘对账的时机


def test_due_after_the_close_plus_the_delay() -> None:
    """spec「收盘加延迟以后到期」。"""
    env = _env()
    env.hold_100()

    assert env.due(_at(14, 20, 9, 59)) is None
    assert env.due(_at(14, 20, 10)) == DAY


def test_not_due_in_session() -> None:
    """spec「盘中不做」。"""
    env = _env()

    assert env.due(_at(14, 15)) is None
    assert env.due(_at(14, 19, 59, 59)) is None


def test_only_once_a_trading_day() -> None:
    """spec「一个交易日只做一次」。"""
    env = _env()
    env.hold_100()
    env.close()

    assert env.due(_at(14, 20, 10, 15)) is None
    assert env.due(_at(15, 3)) is None


def test_made_up_after_a_restart_before_the_open() -> None:
    """spec「开盘以前重启时补做」。"""
    env = _env()
    env.hold_100()
    env.restart(_at(15, 13))

    assert env.due(_at(15, 13, 0, 10)) == DAY


def test_not_made_up_after_the_open() -> None:
    """spec「开盘以后不再补做」。"""
    env = _env()
    env.hold_100()
    env.restart(_at(15, 13, 30))

    assert env.due(_at(15, 13, 30, 10)) is None
    assert env.due(_at(15, 20, 10)) == date(2026, 10, 15)
    assert env.closes.get(DAY) is None


def test_half_day() -> None:
    """spec「半日市按实际收盘」。"""
    env = _env()

    assert env.due(_at(27, 18, 9, 59, month=11)) is None
    assert env.due(_at(27, 18, 10, month=11)) == date(2026, 11, 27)


def test_friday_made_up_on_saturday() -> None:
    """spec「周末补做周五的」。"""
    env = _env()
    env.restart(_at(17, 15))

    assert env.due(_at(17, 15, 0, 10)) == date(2026, 10, 16)


def test_nothing_more_on_a_holiday() -> None:
    """spec「节假日不另做」。"""
    env = _env()
    env.closes.save(_record(date(2026, 11, 25), 1))

    assert env.due(_at(26, 18, month=11)) is None


def test_a_longer_delay() -> None:
    """spec「修改延迟」。"""
    env = _env(config=ExecutionConfig(close_reconcile_delay=timedelta(minutes=30)))

    assert env.due(_at(14, 20, 29, 59)) is None
    assert env.due(_at(14, 20, 30)) == DAY


# 收盘对账的步骤


def test_no_differences() -> None:
    """spec「没有差异」，以及「连续天数」的「第一个交易日」。"""
    env = _env()
    env.hold_100()
    env.forget_calls()

    record = env.close()

    assert record.clean
    assert (record.day, record.finished_at, record.holdings, record.streak) == (
        DAY,
        _at(14, 20, 10),
        1,
        1,
    )
    assert env.writes_to_stops() == []
    assert env.closes.get(DAY) == record


def test_no_second_reconciliation_for_a_day() -> None:
    """spec「已经有记录时不再对账」。"""
    env = _env()
    env.hold_100()
    first = env.close()
    env.forget_calls()

    second = env.close(at=_at(14, 20, 11))

    assert second == first
    assert env.broker.calls == []
    assert env.stops.calls == []
    assert env.closes.saved == [first]


# 差异


def test_unexplained_position() -> None:
    """spec「来历不明的持仓」：算不一致，之后的买入方案报对账不一致的错误。"""
    env = _env()
    env.hold_100()
    env.broker.positions["CCC"] = 100

    record = env.close()

    assert record.result.unexplained == (("CCC", 100),)
    assert not record.clean
    with pytest.raises(ReconcileMismatchError):
        env.submit(baseline_plan(symbol="DDD"))


def test_stray_order() -> None:
    """spec「不认得的挂单」：不撤销它。"""
    env = _env()
    env.hold_100()
    env.broker.add_working("CCC", "X1", "B9")

    record = env.close()

    assert record.result.stray_orders == (WorkingOrder("CCC", "X1", "B9"),)
    assert env.broker.cancel_requests == []


def test_extra_stop() -> None:
    """spec「多出来的止损单」。"""
    env = _env()
    env.hold_100()
    extra = env.stops.add_working("BBB", "X2")

    record = env.close()

    assert {s.client_order_id for s in record.result.extra_stops} == {"BBB-STOP-1", "X2"}
    assert extra in record.result.extra_stops


def test_order_still_unknown_at_the_close() -> None:
    """spec「收盘时还状态未知的订单」：还没满等待时长，不查询，记为差异。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id))
    env.track(p1, _at(14, 20, 9, 50))
    env.forget_calls()

    record = env.close()

    assert env.broker.calls_for(str(p1.plan_id)) == []
    assert env.order(p1).status is OrderStatus.UNKNOWN
    assert record.leftover_orders == (
        LeftoverOrder(p1.plan_id, "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),
    )
    assert record.result.clean


def test_order_ended_because_the_broker_cannot_find_it() -> None:
    """spec「券商查不到而结束的订单」：对账结束它，过了截止时刻记为已过期。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.lose(str(p1.plan_id))
    env.track(p1, _at(14, 20, 9))

    record = env.close()

    order = env.order(p1)
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 0)
    assert record.leftover_orders == (
        LeftoverOrder(p1.plan_id, "BBB", OrderSide.BUY, OrderStatus.EXPIRED),
    )


def _partially_filled(env: _Env) -> TradePlan:
    """P1 部分成交，累计成交 30；券商持仓 BBB 30 股，BBB-STOP-1 挂着 30 股。"""
    p1 = baseline_plan()
    env.submit(p1)
    env.broker.update(str(p1.plan_id), WORKING, 30, "50")
    env.broker.positions["BBB"] = 30
    env.track(p1, env.clock.now())
    assert env.stops.stop("BBB-STOP-1").quantity == 30
    return p1


def test_orders_ended_by_the_patrol_before_the_close_do_not_count() -> None:
    """spec「巡检在收盘对账以前结束的订单不算」。"""
    env = _env()
    p1 = _partially_filled(env)
    env.track(p1, _at(14, 20, 0, 10))
    env.broker.update(str(p1.plan_id), CANCELLED, 30, "50")
    assert env.track(p1, _at(14, 20, 0, 25)).status is OrderStatus.EXPIRED

    assert env.close().clean


def test_stop_placed_again_by_the_reconciliation() -> None:
    """spec「对账重挂的止损单」：不是不一致，之后的买入照常受理。"""
    env = _env()
    env.hold_100()
    env.stops.update("BBB-STOP-1", CANCELLED)

    record = env.close()

    placed = env.stops.placed[-1]
    assert (placed.client_order_id, placed.quantity) == ("BBB-STOP-2", 100)
    assert record.new_stops == (NewStop("BBB", "BBB-STOP-2"),)
    assert record.result.clean
    assert env.buy_next_day().status is OrderStatus.SUBMITTED


def test_stop_placed_for_a_holding_without_one() -> None:
    """spec「对账给还没有止损单的持仓补挂」。"""
    env, p1 = _env(), baseline_plan()
    env.submit(p1)
    env.broker.update(str(p1.plan_id), FILLED, 100, "50")
    env.broker.positions["BBB"] = 100
    env.broker.fail_next("position")
    env.track(p1, _at(14, 14))
    assert env.protections.get("BBB") is None

    record = env.close()

    assert env.stops.placed[-1].client_order_id == "BBB-STOP-1"
    assert record.new_stops == (NewStop("BBB", "BBB-STOP-1"),)


def test_stop_placed_again_and_rejected() -> None:
    """spec「对账补挂又被拒」。"""
    env = _env()
    env.hold_100()
    env.stops.update("BBB-STOP-1", CANCELLED)
    env.stops.reject_next("place", 2)

    record = env.close()

    protection = env.protections.get("BBB")
    assert protection is not None and protection.unprotected_since == _at(14, 20, 10)
    assert record.new_stops == (NewStop("BBB", "BBB-STOP-2"),)
    assert record.unprotected == (UnprotectedSpell("BBB", _at(14, 20, 10)),)


def test_stops_placed_by_the_startup_reconciliation_do_not_count() -> None:
    """spec「启动对账补挂的止损单不算」。"""
    env = _env()
    env.hold_100()
    env.stops.update("BBB-STOP-1", CANCELLED)
    env.restart(_at(14, 17))
    assert env.stop_id() == "BBB-STOP-2"

    assert env.close().clean


def test_unprotected_during_the_day() -> None:
    """spec「当天出现过的无保护仓位」：不是不一致。"""
    env = _env()
    env.hold_100()
    env.unprotect(_at(14, 15), _at(14, 15, 5))

    record = env.close()

    assert record.unprotected == (UnprotectedSpell("BBB", _at(14, 15)),)
    assert record.new_stops == ()
    assert record.result.clean


def test_two_spells() -> None:
    """spec「两段无保护各一条」。"""
    env = _env()
    env.hold_100()
    env.unprotect(_at(14, 15), _at(14, 15, 5))
    env.unprotect(_at(14, 16), _at(14, 16, 30))

    record = env.close()

    assert record.unprotected == (
        UnprotectedSpell("BBB", _at(14, 15)),
        UnprotectedSpell("BBB", _at(14, 16)),
    )


def test_a_spell_ended_before_the_previous_close_does_not_count() -> None:
    """spec「上一个交易日收盘对账以前结束的无保护不算」。"""
    env = _env(_at(13, 18))
    env.hold_100()
    env.unprotect(_at(13, 19), _at(13, 19, 30))

    assert env.close().clean


def test_a_spell_across_the_previous_close() -> None:
    """spec「跨过上一个交易日收盘对账的无保护」。"""
    env = _env(_at(13, 18))
    env.hold_100()
    env.unprotect(_at(13, 20, 5), _at(14, 14))

    record = env.close()

    assert record.unprotected == (UnprotectedSpell("BBB", _at(13, 20, 5)),)


# 连续天数


def test_following_the_previous_trading_day() -> None:
    """spec「接着上一个交易日」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 13), 4))

    assert env.close().streak == 5


def test_across_a_weekend() -> None:
    """spec「跨过周末」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 16), 3))

    assert env.close(date(2026, 10, 19)).streak == 4


def test_across_a_holiday_and_a_half_day() -> None:
    """spec「跨过节假日和半日市」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 11, 25), 6))

    record = env.close(date(2026, 11, 27), at=_at(27, 18, 10, month=11))

    assert record.streak == 7


def test_differences_reset_the_streak() -> None:
    """spec「有差异时清零」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 13), 4))
    env.broker.positions["CCC"] = 100

    assert env.close().streak == 0

    env.broker.positions["CCC"] = 0
    record = env.close(date(2026, 10, 15))
    assert (record.clean, record.streak) == (True, 1)


def test_a_missing_day_resets_the_streak() -> None:
    """spec「漏做一天时清零」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 13), 4))
    env.restart(_at(15, 14))

    assert env.close(date(2026, 10, 15)).streak == 1


def test_no_holdings_at_the_close() -> None:
    """spec「收盘时没有持仓」。"""
    env = _env()
    env.closes.save(_record(date(2026, 10, 13), 4))

    record = env.close()

    assert (record.clean, record.holdings, record.streak) == (True, 0, 0)


def test_a_restart_does_not_reset_the_streak() -> None:
    """spec「重启不清零」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 13), 4))
    env.restart(_at(14, 17))

    assert env.close().streak == 5


# 开仓的暂停和恢复（spec「结果消息」末尾那一行的依据）


def test_buys_resume_after_a_clean_close() -> None:
    """spec「收盘对账以后恢复开仓」。"""
    env = _env(_at(13, 18))
    env.hold_100()
    env.broker.positions["CCC"] = 100
    assert not env.close(date(2026, 10, 13)).result.clean
    env.broker.positions["CCC"] = 0

    record = env.close()

    assert (record.clean, record.resumed) == (True, True)
    assert env.buy_next_day().status is OrderStatus.SUBMITTED


def test_a_startup_mismatch_is_resumed_by_the_close() -> None:
    """spec「启动对账的不一致在收盘对账时恢复」。"""
    env = _env()
    env.hold_100()
    env.broker.positions["CCC"] = 100
    env.restart(_at(14, 17))
    env.broker.positions["CCC"] = 0

    assert env.close().resumed


def test_not_resumed_when_nothing_was_paused() -> None:
    env = _env()
    env.hold_100()

    assert not env.close().resumed


# 收盘对账记录


def test_a_restart_continues_the_streak() -> None:
    """spec「重启以后接着算」。"""
    env = _env()
    env.hold_100()
    env.closes.save(_record(date(2026, 10, 13), 1))
    assert env.close().streak == 2

    env.restart(_at(14, 20, 20))
    assert env.service.close_due() is None
    assert env.close(date(2026, 10, 15)).streak == 3


# 收盘对账的失败


def test_reading_the_positions_fails() -> None:
    """spec「读持仓超时，下一轮再做」里执行模块这一层：报券商不可用，什么都没改，没有记录。"""
    env = _env()
    env.hold_100()
    env.forget_calls()
    env.broker.fail_next("all_positions")

    with pytest.raises(BrokerUnavailableError):
        env.close()

    assert env.broker.calls == [("all_positions", "*")]
    assert env.stops.calls == []
    assert env.closes.get(DAY) is None
    assert env.close().clean


def test_reading_the_working_orders_fails() -> None:
    """spec「对账读挂单时断线」。"""
    env = _env()
    env.hold_100()
    env.forget_calls()
    env.broker.fail_next("working_orders")

    with pytest.raises(BrokerUnavailableError):
        env.close()

    assert env.writes_to_stops() == []
    assert env.closes.get(DAY) is None


def test_tracking_an_order_times_out() -> None:
    """spec「对账跟进订单时超时」。"""
    env = _env()
    p1 = _partially_filled(env)
    env.track(p1, _at(14, 20, 0, 10))
    assert env.broker.cancel_requests == [str(p1.plan_id)]
    env.broker.fail_next("find")

    record = env.close()

    assert env.order(p1).status is OrderStatus.UNKNOWN
    assert record.leftover_orders == (
        LeftoverOrder(p1.plan_id, "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),
    )


def test_saving_the_record_fails() -> None:
    """spec「保存记录时数据库出错」里执行模块这一层：错误原样抛出，没有记录。"""
    env = _env()
    env.hold_100()
    env.closes.fail_next_save = True

    with pytest.raises(RuntimeError):
        env.close()

    assert env.closes.get(DAY) is None
    assert env.close(at=_at(14, 20, 10, 15)).clean
