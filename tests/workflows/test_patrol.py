"""盘中巡检的一轮：跟进订单、同步预留、对齐止损单、无保护仓位的告警、日志、失败和锁。

场景见 openspec 的 session-patrol spec。测试直接调用 run_round，不开线程；"做一轮"就是在测试时钟
的当前时刻调用一次 run_round，"之后一轮"先把时钟拨后 15 秒。
"""

import logging
import threading
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from tests.fakes.broker import FakeBroker
from tests.fakes.notifier import FakeNotifier
from tests.fakes.orders import InMemoryOrderStore
from tests.modules.execution.builders import add_bbb, baseline_plan
from tests.workflows.builders import World, at, p5
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import BrokerOrder, BrokerOrderStatus, Order, OrderStatus
from tradingagentspro.workflows.patrol import PatrolConfig

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
INTERVAL = timedelta(seconds=15)
PATROL = "tradingagentspro.workflows.patrol"
STOP_1, STOP_2, STOP_3 = "BBB-STOP-1", "BBB-STOP-2", "BBB-STOP-3"


def _next_round(world: World) -> None:
    """之后一轮：时钟拨后 15 秒，做一轮。"""
    world.clock.advance(INTERVAL)
    world.patrol.run_round()


def _fill(world: World, client_order_id: str, status: BrokerOrderStatus, filled: int) -> None:
    """券商回报这笔订单变为 status、累计成交 filled，券商持仓 BBB 变为 filled 股。"""
    world.broker.update(client_order_id, status, filled, "50")
    world.broker.positions["BBB"] = filled


# 巡检配置


def test_default_config() -> None:
    """spec「默认配置」：间隔是 15 秒。"""
    assert PatrolConfig().interval == timedelta(seconds=15)


@pytest.mark.parametrize("seconds", [0, -1])
def test_interval_must_be_positive(seconds: int) -> None:
    """spec「间隔不大于 0」：配置报错。"""
    with pytest.raises(ValidationError):
        PatrolConfig(interval=timedelta(seconds=seconds))


# 每一轮：跟进订单和同步预留


def test_partial_fill_then_full_fill() -> None:
    """spec「部分成交到全部成交」：订单和预留的部分。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)

    _fill(world, str(p1.plan_id), WORKING, 30)
    world.patrol.run_round()

    order = world.order(p1)
    assert (order.status, order.filled) == (OrderStatus.PARTIALLY_FILLED, 30)
    assert world.remaining(p1) == 70
    [stop] = world.stops.placed
    assert (stop.client_order_id, stop.quantity, stop.stop_price) == (STOP_1, 30, Decimal(45))

    _fill(world, str(p1.plan_id), FILLED, 100)
    _next_round(world)

    assert world.order(p1).status is OrderStatus.FILLED
    assert world.remaining(p1) is None
    assert world.stops.modify_requests == [(STOP_1, 100, Decimal(45))]


def test_order_rejected_at_submit_releases_its_reservation() -> None:
    """spec「提交时被拒的订单也释放预留」。"""
    world = World()
    p1 = baseline_plan()
    world.broker.reject_next_place()
    world.submit(p1)
    assert world.order(p1).status is OrderStatus.REJECTED
    assert world.remaining(p1) == 100

    world.patrol.run_round()

    assert world.broker.calls_for(str(p1.plan_id)) == []
    assert world.remaining(p1) is None


def test_fills_during_downtime_are_reported_after_a_restart() -> None:
    """spec「重启后补上停机期间的成交」：启动对账跟进 P1，巡检的第一轮上报成交。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    _fill(world, str(p1.plan_id), FILLED, 100)

    world.restart()
    world.execution.reconcile()
    assert world.order(p1).status is OrderStatus.FILLED
    world.patrol.run_round()

    assert world.remaining(p1) is None
    assert world.broker.order_count(str(p1.plan_id)) == 1


def test_plan_without_an_order_is_left_alone() -> None:
    """spec「还没有订单的方案不动」。"""
    world = World()
    plan = p5()
    world.reserve(plan)

    world.patrol.run_round()

    assert world.remaining(plan) == 100
    assert all(key not in ("CCC", str(plan.plan_id)) for _, key in world.broker.calls)
    assert all(key != "CCC-STOP-1" for _, key in world.stops.calls)


def test_broker_timeout_while_tracking() -> None:
    """spec「跟进时券商超时」：状态未知，满 30 秒才再查询。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    world.broker.fail_next("find")

    world.patrol.run_round()

    assert world.order(p1).status is OrderStatus.UNKNOWN
    assert world.remaining(p1) == 100

    world.forget_calls()
    _next_round(world)

    assert ("find", str(p1.plan_id)) not in world.broker.calls

    _fill(world, str(p1.plan_id), WORKING, 30)
    _next_round(world)

    assert ("find", str(p1.plan_id)) in world.broker.calls
    assert world.order(p1).status is OrderStatus.PARTIALLY_FILLED
    assert world.remaining(p1) == 70


def test_tracking_continues_after_the_close() -> None:
    """spec「收盘以后照样跟进」：过了截止时刻撤单，券商回报撤销以后过期并释放预留。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    _fill(world, str(p1.plan_id), WORKING, 30)
    world.patrol.run_round()  # 15:00 部分成交，挂上 BBB-STOP-1 30 股
    assert world.stops.stop("BBB-STOP-1").quantity == 30

    world.clock.set(at(20, 0, 10))
    world.patrol.run_round()

    assert world.broker.cancel_requests == [str(p1.plan_id)]

    world.broker.update(str(p1.plan_id), CANCELLED, 30, "50")
    world.clock.set(at(20, 0, 25))
    world.patrol.run_round()

    order = world.order(p1)
    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 30)
    assert world.remaining(p1) is None


# 订单和止损单变化的日志


def _info(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and r.name == "tradingagentspro.workflows.patrol"
    ]


def test_order_change_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「订单成交写日志」：一条 INFO，不推送 Discord。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    _fill(world, str(p1.plan_id), WORKING, 30)

    with caplog.at_level(logging.INFO):
        world.patrol.run_round()

    [line] = [m for m in _info(caplog) if m.startswith("订单")]
    assert line == f"订单 BBB 买入 {p1.plan_id}：已提交 → 部分成交，累计成交 30 / 100"
    assert world.notifier.alerts == []


# 一轮里的失败


class _ExplodingBroker(FakeBroker):
    """按某个客户端订单 ID 查询时抛一个意外的异常（比如券商回答 401）。"""

    def __init__(self) -> None:
        super().__init__()
        self.explode_on: str | None = None

    def find(self, order: Order) -> BrokerOrder | None:
        if order.client_order_id == self.explode_on:
            self.calls.append(("find", order.client_order_id))
            raise RuntimeError("401 unauthorized")
        return super().find(order)


def test_unexpected_error_while_tracking_one_order(caplog: pytest.LogCaptureFixture) -> None:
    """spec「跟进一笔订单时出了意外的错」：写 ERROR，接着跟进 P5，同步预留；下一轮再跟进 P1。"""
    broker = _ExplodingBroker()
    world = World(broker=broker)
    p1, plan5 = baseline_plan(), p5()
    world.submit(p1)
    world.clock.advance(timedelta(seconds=1))
    world.submit(plan5)
    world.broker.update(str(plan5.plan_id), WORKING, 20, "20")
    world.broker.positions["CCC"] = 20
    broker.explode_on = str(p1.plan_id)

    world.patrol.run_round()

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert str(p1.plan_id) in error.getMessage()
    assert error.exc_info is not None
    assert ("find", str(plan5.plan_id)) in world.broker.calls
    assert world.remaining(plan5) == 80
    assert ("all_positions", "*") in world.broker.calls  # 第 3 步照常做
    assert world.stops.stop("CCC-STOP-1").quantity == 20

    world.forget_calls()
    _next_round(world)

    assert ("find", str(p1.plan_id)) in world.broker.calls


# 每一轮：对齐止损单


def _hold_bbb(world: World) -> TradePlan:
    """持有 BBB 100 股，止损单已对齐：P1 全部成交，巡检做过一轮，挂着 BBB-STOP-1 100 股。"""
    p1 = baseline_plan()
    world.submit(p1)
    _fill(world, str(p1.plan_id), FILLED, 100)
    world.patrol.run_round()
    assert world.stops.stop(STOP_1).quantity == 100
    world.forget_calls()
    return p1


def _placed(world: World) -> list[tuple[str, int, Decimal]]:
    return [(s.client_order_id, s.quantity, s.stop_price) for s in world.stops.placed]


def test_stop_cancelled_by_the_broker_is_placed_again() -> None:
    """spec「止损单被券商撤销后重挂」。"""
    world = World()
    _hold_bbb(world)

    world.stops.update(STOP_1, CANCELLED)
    _next_round(world)

    assert _placed(world)[-1] == (STOP_2, 100, Decimal(45))


def test_position_without_a_stop_record_gets_a_stop() -> None:
    """spec「有持仓、还没有止损单的股票」：跟进成交以后查持仓断线，没有留下止损保护的记录。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    _fill(world, str(p1.plan_id), FILLED, 100)
    world.broker.fail_next("position")
    world.execution.track(p1.plan_id)  # 别的调用方跟进，P1 全部成交
    assert world.protections.get("BBB") is None

    world.patrol.run_round()

    assert _placed(world) == [(STOP_1, 100, Decimal(45))]


def _unprotected_after_rejections(world: World, rejections: int) -> TradePlan:
    """P1 全部成交，券商持仓 BBB 100 股；接下来 rejections 次挂单被拒。别的调用方在 15:00 跟进 P1，
    挂单两次被拒，BBB 从 15:00 起是无保护仓位。
    """
    p1 = baseline_plan()
    world.submit(p1)
    world.stops.reject_next("place", rejections)
    _fill(world, str(p1.plan_id), FILLED, 100)
    world.execution.track(p1.plan_id)
    protection = world.protections.get("BBB")
    assert protection is not None and protection.unprotected_since == at(15)
    world.forget_calls()
    return p1


def test_next_round_places_the_stop_after_rejections() -> None:
    """spec「挂单被拒以后下一轮补挂」。"""
    world = World()
    _unprotected_after_rejections(world, 2)

    world.patrol.run_round()

    assert _placed(world) == [(STOP_2, 100, Decimal(45))]
    protection = world.protections.get("BBB")
    assert protection is not None and protection.unprotected_since is None


def test_round_without_positions_or_orders() -> None:
    """spec「没有持仓也没有订单」：第 1 到 3 步券商只收到一次读取全部持仓。

    之后的紧急退出（盘中）读一次账户，见 add-emergency-exit-flow 的 session-patrol delta。
    """
    world = World()

    world.patrol.run_round()

    assert world.broker.calls == [("all_positions", "*"), ("account", "*")]
    assert world.stops.calls == []


def test_items_after_step_3() -> None:
    """spec「第 3 步之后的项」：读全部持仓（第 3 步）以后，紧急退出才读账户。"""
    world = World()
    _hold_bbb(world)

    world.patrol.run_round()

    calls = world.broker.calls
    assert calls.index(("all_positions", "*")) < calls.index(("account", "*"))


def test_emergency_exit_comes_before_the_close_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emergency-exit-flow spec「巡检里的紧急退出」：对齐止损单之后、收盘对账之前。"""
    world = World()
    order: list[str] = []
    judge = world.flow.judge

    def judge_and_note() -> object:
        order.append("紧急退出")
        return judge()

    monkeypatch.setattr(world.flow, "judge", judge_and_note)
    monkeypatch.setattr(
        "tradingagentspro.workflows.patrol.close_reconcile_if_due",
        lambda *args: order.append("收盘对账"),
    )

    world.patrol.run_round()

    assert order == ["紧急退出", "收盘对账"]


def test_no_account_read_outside_the_session() -> None:
    """emergency-exit-flow spec「盘外不判定」经过巡检：盘外一轮不读账户。"""
    world = World()
    world.clock.set(at(21))

    world.patrol.run_round()

    assert ("account", "*") not in world.broker.calls


def test_stops_are_aligned_on_weekends() -> None:
    """spec「周末照样对齐止损单」。"""
    world = World()
    _hold_bbb(world)

    world.clock.set(at(15, day=17))  # 周六
    world.stops.update(STOP_1, CANCELLED)
    world.patrol.run_round()

    assert _placed(world)[-1] == (STOP_2, 100, Decimal(45))


# 无保护仓位的告警

ALWAYS = 1_000  # "券商会一直拒绝"


def _protection_since(world: World) -> object:
    protection = world.protections.get("BBB")
    assert protection is not None
    return protection.unprotected_since


def _first_alert(world: World) -> TradePlan:
    """spec「第一次发现时告警」的前提和那一轮：P1 部分成交 30，券商一直拒绝挂单，15:00 做一轮。"""
    p1 = baseline_plan()
    world.submit(p1)
    world.stops.reject_next("place", ALWAYS)
    _fill(world, str(p1.plan_id), WORKING, 30)
    world.patrol.run_round()
    return p1


def test_first_unprotected_round_alerts() -> None:
    """spec「第一次发现时告警」。"""
    world = World()

    _first_alert(world)

    places = [key for op, key in world.stops.calls if op == "place"]
    assert places == [STOP_1, STOP_1]  # 跟进时两次被拒，第 3 步没有再对齐 BBB
    assert _protection_since(world) == at(15)
    [alert] = world.notifier.alerts
    assert alert.startswith("BBB 是无保护仓位")
    assert "2026-10-14 15:00 UTC" in alert
    assert "止损单挂单或改单被券商拒绝" in alert
    assert "补挂成功之前不能加仓" in alert


def test_same_unprotected_period_alerts_once() -> None:
    """spec「同一段无保护不重复告警」。"""
    world = World()
    _first_alert(world)

    _next_round(world)

    assert _protection_since(world) == at(15)
    assert len(world.notifier.alerts) == 1


def test_recovery_is_only_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「补挂成功只写日志」。"""
    world = World()
    _first_alert(world)
    world.stops.clear_failures()

    with caplog.at_level(logging.INFO):
        _next_round(world)

    assert _protection_since(world) is None
    assert "BBB 不再是无保护仓位" in _info(caplog)
    assert len(world.notifier.alerts) == 1


def test_new_unprotected_period_alerts_again() -> None:
    """spec「新的一段无保护再告警」：15:00 的一段已经告警、15:05 补挂成功，15:10 改单被拒。"""
    world = World()
    _unprotected_after_rejections(world, ALWAYS)
    world.patrol.run_round()
    assert len(world.notifier.alerts) == 1
    world.stops.clear_failures()
    world.clock.set(at(15, 5))
    world.patrol.run_round()
    assert _protection_since(world) is None
    p2 = add_bbb()
    world.submit(p2, 50)
    world.stops.reject_next("modify", 2)
    world.broker.update(str(p2.plan_id), WORKING, 20, "52")
    world.broker.positions["BBB"] = 120

    world.clock.set(at(15, 10))
    world.patrol.run_round()

    assert _protection_since(world) == at(15, 10)
    assert len(world.notifier.alerts) == 2
    assert "2026-10-14 15:10 UTC" in world.notifier.alerts[-1]


def test_unprotected_position_from_another_caller_alerts() -> None:
    """spec「别处产生的无保护仓位」：别的调用方跟进 P2 时改单被拒。"""
    world = World()
    _hold_bbb(world)
    p2 = add_bbb()
    world.submit(p2, 50)
    world.broker.update(str(p2.plan_id), FILLED, 50, "52")
    world.broker.positions["BBB"] = 150
    world.stops.reject_next("modify", 2)
    world.clock.set(at(15, 0, 5))
    world.execution.track(p2.plan_id)
    assert _protection_since(world) == at(15, 0, 5)
    world.stops.reject_next("modify", 2)

    world.clock.set(at(15, 0, 15))
    world.patrol.run_round()

    [alert] = world.notifier.alerts
    assert alert.startswith("BBB 是无保护仓位")
    assert "2026-10-14 15:00 UTC" in alert


def test_rejected_while_tracking_then_placed_while_tracking_next_round(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spec「跟进时被拒，下一轮跟进时补挂」：第 3 步不再对齐跟进过的股票。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    world.stops.reject_next("place", 2)
    _fill(world, str(p1.plan_id), WORKING, 30)

    world.patrol.run_round()

    assert [key for op, key in world.stops.calls if op == "place"] == [STOP_1, STOP_1]
    assert _protection_since(world) == at(15)
    assert len(world.notifier.alerts) == 1

    with caplog.at_level(logging.INFO):
        _next_round(world)

    assert _placed(world) == [(STOP_2, 30, Decimal(45))]
    assert _protection_since(world) is None
    assert "BBB 不再是无保护仓位" in _info(caplog)
    assert len(world.notifier.alerts) == 1


def test_alerts_again_once_after_a_restart() -> None:
    """spec「重启以后再告警一次」：告警记录只在巡检的内存里。"""
    world = World()
    _unprotected_after_rejections(world, ALWAYS)
    world.patrol.run_round()
    assert len(world.notifier.alerts) == 1

    world.restart()
    world.patrol.run_round()

    assert len(world.notifier.alerts) == 2
    assert "2026-10-14 15:00 UTC" in world.notifier.alerts[-1]

    _next_round(world)

    assert len(world.notifier.alerts) == 2


def test_alert_that_cannot_be_sent_is_not_resent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」：写错误日志，巡检照常做完；Discord 恢复以后同一段不再告警。"""
    world = World()
    _unprotected_after_rejections(world, ALWAYS)
    world.notifier.fail_next = True

    world.patrol.run_round()

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "告警没有发出去" in error.getMessage()
    assert "BBB 是无保护仓位" in error.getMessage()

    _next_round(world)

    assert len(world.notifier.alerts) == 1  # 只有发不出去的那一次


# 订单和止损单变化的日志


def test_new_stop_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「换了一张止损单写日志」。"""
    world = World()
    _hold_bbb(world)
    world.stops.update(STOP_1, CANCELLED)

    with caplog.at_level(logging.INFO):
        _next_round(world)

    assert "止损单 BBB BBB-STOP-2：挂着，100 股，止损价 45" in _info(caplog)


def test_nothing_is_logged_without_changes(caplog: pytest.LogCaptureFixture) -> None:
    """spec「没有变化不写日志」。"""
    world = World()
    _hold_bbb(world)

    with caplog.at_level(logging.INFO):
        _next_round(world)

    assert _info(caplog) == []


# 一轮里的失败：读持仓、状态库


def test_round_skips_alignment_when_positions_cannot_be_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spec「读全部持仓失败时这一轮不对齐」。"""
    world = World()
    _hold_bbb(world)
    world.stops.update(STOP_1, CANCELLED)
    world.broker.fail_next("all_positions")

    _next_round(world)

    assert len(world.stops.placed) == 1
    # 只看巡检自己的 WARNING：读持仓失败也让健康判定进入降级，见 health-monitoring spec
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == PATROL]
    assert "读取券商持仓失败" in warning.getMessage()
    assert "这一轮没有对齐止损单" in warning.getMessage()

    _next_round(world)

    assert _placed(world)[-1] == (STOP_2, 100, Decimal(45))


class _BrokenOrderStore(InMemoryOrderStore):
    """读状态库时出错。"""

    broken = False

    def open_orders(self) -> tuple[Order, ...]:
        self._check()
        return super().open_orders()

    def get(self, plan_id: UUID) -> Order | None:
        self._check()
        return super().get(plan_id)

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        self._check()
        return super().of_symbol(symbol)

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("数据库连不上")


def test_state_store_errors_do_not_stop_the_round(caplog: pytest.LogCaptureFixture) -> None:
    """spec「状态库出错」：写 ERROR，带异常栈；run_round 正常返回。"""
    store = _BrokenOrderStore()
    world = World(orders=store)
    _hold_bbb(world)
    p5_plan = p5()
    world.reserve(p5_plan)
    store.broken = True

    world.patrol.run_round()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and all(r.exc_info is not None for r in errors)


# 执行模块和风控的调用串行


def test_patrol_waits_while_another_caller_holds_the_lock() -> None:
    """spec「别的调用拿着锁时巡检等待」。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    round_ = threading.Thread(target=world.patrol.run_round)

    with world.lock:
        round_.start()
        round_.join(0.2)
        assert round_.is_alive()
        assert ("find", str(p1.plan_id)) not in world.broker.calls

    round_.join(5)
    assert not round_.is_alive()
    assert ("find", str(p1.plan_id)) in world.broker.calls


class _BlockingNotifier(FakeNotifier):
    """推送告警时一直等，直到测试放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def alert(self, text: str) -> None:
        self.entered.set()
        self.release.wait(5)
        super().alert(text)


def test_lock_is_free_while_alerting() -> None:
    """spec「推送告警时不拿锁」。"""
    notifier = _BlockingNotifier()
    world = World(notifier=notifier)
    _unprotected_after_rejections(world, ALWAYS)
    round_ = threading.Thread(target=world.patrol.run_round)
    round_.start()

    try:
        assert notifier.entered.wait(5)
        assert world.lock.acquire(blocking=False)
        world.lock.release()
    finally:
        notifier.release.set()
        round_.join(5)
    assert not round_.is_alive()


def test_holder_of_the_lock_can_take_it_again() -> None:
    """spec「拿着锁的线程再次拿锁」：拿着锁的线程里做一轮不会卡住。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)

    with world.lock:
        world.patrol.run_round()

    assert ("find", str(p1.plan_id)) in world.broker.calls


def test_alert_records_survive_rounds_without_alignment(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """列不出要对齐的股票的那一轮不动告警记录（读持仓失败、意外的错），之后同一段不再告警（D8）。"""
    world = World()
    _unprotected_after_rejections(world, ALWAYS)
    world.patrol.run_round()
    world.broker.fail_next("all_positions")

    with caplog.at_level(logging.INFO):
        _next_round(world)
        with monkeypatch.context() as patch:
            patch.setattr(world.execution, "symbols_to_protect", _raise)
            _next_round(world)
        _next_round(world)

    assert "BBB 不再是无保护仓位" not in _info(caplog)
    # 读持仓失败的那一轮健康判定另有降级的告警，见 health-monitoring spec
    assert len([a for a in world.notifier.alerts if "无保护仓位" in a]) == 1


def _raise() -> tuple[str, ...]:
    raise RuntimeError("意外的错")
