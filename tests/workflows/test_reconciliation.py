"""巡检里的收盘对账：一轮最后的那一项、锁、结果消息、失败，以及消息和启动对账告警的文字。

场景见 openspec 的 close-reconciliation spec「巡检里的收盘对账」「结果消息」「收盘对账的失败」，
以及「收盘对账的时机」「连续天数」里经过巡检的场景。"做一轮"就是在测试时钟的当前时刻调用一次
run_round；收盘对账的延迟用默认值 10 分钟，2026-10-14 的收盘对账在 20:10Z 到期。停止时正在做
收盘对账的场景在 test_patrol_thread.py。
"""

import logging
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from tests.fakes.notifier import FakeNotifier
from tests.modules.execution.builders import baseline_plan, decision
from tests.workflows.builders import World, at
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    CloseReconciliation,
    LeftoverOrder,
    NewStop,
    OrderSide,
    OrderStatus,
    Reconciliation,
    UnprotectedSpell,
    WorkingOrder,
)
from tradingagentspro.workflows.reconciliation import (
    close_reconcile_if_due,
    close_text,
    startup_text,
)

FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
DAY = date(2026, 10, 14)
WORKING_ORDERS = ("working_orders", "*")
LOGGER = "tradingagentspro.workflows.reconciliation"
PAUSED = "已暂停开仓，之后的对账没有不一致时恢复"
RESUMED = "没有不一致，已恢复开仓"


def _record(day: date = DAY, **changes: Any) -> CloseReconciliation:
    """没有差异、1 只持仓、连续天数 1 的记录。"""
    record = CloseReconciliation(
        day=day,
        finished_at=datetime(day.year, day.month, day.day, 20, 10, tzinfo=UTC),
        result=Reconciliation(),
        leftover_orders=(),
        new_stops=(),
        unprotected=(),
        holdings=1,
        resumed=False,
        streak=1,
    )
    return replace(record, **changes)


def _hold_bbb(world: World) -> TradePlan:
    """持有 BBB 100 股，止损单已对齐（P1 全部成交，BBB-STOP-1 挂着 100 股），不留调用记录。"""
    p1 = baseline_plan()
    world.submit(p1)
    world.broker.update(str(p1.plan_id), FILLED, 100, "50")
    world.broker.positions["BBB"] = 100
    world.execution.track(p1.plan_id)
    world.forget_calls()
    return p1


def _round_at(world: World, moment: datetime) -> None:
    world.clock.set(moment)
    world.patrol.run_round()


def _logs(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == level]


# 巡检里的收盘对账


def test_close_reconciliation_after_aligning_stops() -> None:
    """spec「一轮对齐止损单以后做收盘对账」：第 3 步挂上的止损单不算对账补挂的。"""
    world = World()
    _hold_bbb(world)
    world.stops.update("BBB-STOP-1", CANCELLED)

    _round_at(world, at(20, 10))

    assert world.stops.placed[-1].client_order_id == "BBB-STOP-2"
    record = world.closes.get(DAY)
    assert record is not None and record.clean and record.new_stops == ()
    [message] = world.notifier.alerts
    assert "没有差异" in message


def test_not_before_it_is_due() -> None:
    """spec「不到时刻的轮次不做」。"""
    world = World()
    _hold_bbb(world)

    _round_at(world, at(20, 9, 45))

    assert WORKING_ORDERS not in world.broker.calls
    assert world.notifier.alerts == []
    assert world.closes.get(DAY) is None


def test_waits_for_the_lock() -> None:
    """spec「别的调用拿着锁时等待」。"""
    world = World()
    _hold_bbb(world)
    world.clock.set(at(20, 10))
    item = threading.Thread(
        target=close_reconcile_if_due, args=(world.execution, world.notification, world.lock)
    )

    with world.lock:
        item.start()
        item.join(0.2)
        assert item.is_alive()
        assert WORKING_ORDERS not in world.broker.calls

    item.join(5)
    assert not item.is_alive()
    assert world.closes.get(DAY) is not None


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


def test_lock_is_free_while_sending_the_result() -> None:
    """spec「推送结果时不拿锁」。"""
    notifier = _BlockingNotifier()
    world = World(notifier=notifier)
    _hold_bbb(world)
    world.clock.set(at(20, 10))
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


# 结果消息


def test_message_without_differences(caplog: pytest.LogCaptureFixture) -> None:
    """spec「没有差异」：消息和 INFO 日志写明交易日、持仓只数、连续天数。"""
    world = World()
    _hold_bbb(world)
    world.closes.save(_record(date(2026, 10, 13), streak=2))

    with caplog.at_level(logging.INFO):
        _round_at(world, at(20, 10))

    [message] = world.notifier.alerts
    assert message == "收盘对账 2026-10-14：没有差异，持仓 1 只，连续 3 个交易日无差异"
    assert _logs(caplog, logging.INFO) == [message]


def test_message_without_holdings() -> None:
    """spec「收盘时没有持仓」。"""
    world = World()

    _round_at(world, at(20, 10))

    assert world.notifier.alerts == ["收盘对账 2026-10-14：没有差异，收盘时没有持仓，连续天数清零"]


def test_message_with_a_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """spec「有不一致」：列出差异，最后一行写明已暂停开仓；WARNING 含全文。"""
    world = World()
    _hold_bbb(world)
    world.broker.positions["CCC"] = 100

    _round_at(world, at(20, 10))

    [message] = world.notifier.alerts
    lines = message.split("\n")
    assert lines[0] == "收盘对账 2026-10-14：有差异，连续天数清零"
    assert "- 来历不明的持仓：CCC 100 股" in lines
    assert lines[-1] == PAUSED
    assert _logs(caplog, logging.WARNING) == [message]


def _unprotected(world: World, start: datetime, end: datetime) -> None:
    """BBB 从 start 起是无保护仓位，end 补挂成功（不经过巡检，所以没有无保护告警）。"""
    protection = world.protections.get("BBB")
    assert protection is not None and protection.stop is not None
    world.stops.update(protection.stop.client_order_id, CANCELLED)
    world.stops.reject_next("place", 2)
    world.clock.set(start)
    world.execution.protect("BBB")
    world.clock.set(end)
    world.execution.protect("BBB")
    world.forget_calls()


def test_message_with_differences_but_no_mismatch() -> None:
    """spec「有差异、没有不一致」：没有暂停开仓那一行。"""
    world = World()
    _hold_bbb(world)
    _unprotected(world, at(15), at(15, 5))

    _round_at(world, at(20, 10))

    [message] = world.notifier.alerts
    assert "- 当天出现过的无保护仓位：BBB，从 2026-10-14 15:00 UTC 起" in message.split("\n")
    assert "暂停开仓" not in message


def _buy_next_day(world: World) -> OrderStatus:
    """2026-10-15 14:00Z（盘中）提交买入 DDD，有效期到当天收盘，返回订单的状态。"""
    world.clock.set(at(14, day=15))
    plan = baseline_plan(symbol="DDD")
    return world.execution.submit(plan, decision(expires_at=at(20, day=15))).status


def test_buys_resume_after_the_close() -> None:
    """spec「收盘对账以后恢复开仓」：最近一次对账（这里直接对账一次，代替 10-13 的收盘对账）
    有来历不明的持仓，之后 CCC 卖光。
    """
    world = World()
    _hold_bbb(world)
    world.broker.positions["CCC"] = 100
    assert not world.execution.reconcile().clean
    world.broker.positions["CCC"] = 0

    _round_at(world, at(20, 10))

    [message] = world.notifier.alerts
    assert message.split("\n")[-1] == RESUMED
    assert "没有差异" in message
    assert _buy_next_day(world) is OrderStatus.SUBMITTED


def test_a_startup_mismatch_is_resumed_by_the_close() -> None:
    """spec「启动对账的不一致在收盘对账时恢复」。"""
    world = World()
    _hold_bbb(world)
    world.broker.positions["CCC"] = 100
    world.clock.set(at(17))
    world.restart()
    assert not world.execution.reconcile().clean  # 启动对账
    world.broker.positions["CCC"] = 0

    _round_at(world, at(20, 10))

    [message] = world.notifier.alerts
    assert message.split("\n")[-1] == RESUMED


def test_result_that_cannot_be_sent_is_not_resent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「推送发不出去」。"""
    world = World()
    _hold_bbb(world)
    world.notifier.fail_next = True

    _round_at(world, at(20, 10))

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "收盘对账 2026-10-14" in error.getMessage() and "测试安排的失败" in error.getMessage()
    assert world.closes.get(DAY) is not None

    _round_at(world, at(20, 10, 15))
    assert len(world.notifier.alerts) == 1  # 失败的那次也记着，之后没有再推送


# 收盘对账的失败


def test_positions_time_out_then_next_round(caplog: pytest.LogCaptureFixture) -> None:
    """spec「读持仓超时，下一轮再做」：第 3 步和收盘对账各读一次全部持仓，都超时。"""
    world = World()
    _hold_bbb(world)
    world.broker.fail_next("all_positions")
    world.broker.fail_next("all_positions")

    _round_at(world, at(20, 10))

    [warning] = _logs(caplog, logging.WARNING)
    assert "2026-10-14" in warning and "券商不可用" in warning
    assert world.closes.get(DAY) is None
    assert world.notifier.alerts == []

    _round_at(world, at(20, 10, 15))

    assert world.closes.get(DAY) is not None
    assert len(world.notifier.alerts) == 1


def test_broker_down_until_the_open() -> None:
    """spec「券商到开盘都不可用」：2026-10-14 没有记录，2026-10-15 从 1 算起。"""
    world = World()
    _hold_bbb(world)
    world.closes.save(_record(date(2026, 10, 13), streak=4))
    for moment in (at(20, 10), at(23), at(3, day=15), at(13, 29, 45, day=15)):
        world.broker.fail_next("all_positions")
        world.broker.fail_next("all_positions")
        _round_at(world, moment)

    _round_at(world, at(13, 30, 10, day=15))

    assert world.closes.get(DAY) is None
    assert WORKING_ORDERS not in world.broker.calls

    _round_at(world, at(20, 10, day=15))

    record = world.closes.get(date(2026, 10, 15))
    assert record is not None and record.streak == 1


def test_saving_the_record_fails_then_next_round(caplog: pytest.LogCaptureFixture) -> None:
    """spec「保存记录时数据库出错」：ERROR 带异常栈，不推送；之后一轮保存、推送。"""
    world = World()
    _hold_bbb(world)
    world.closes.fail_next_save = True

    _round_at(world, at(20, 10))

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "收盘对账" in error.getMessage() and error.exc_info is not None
    assert world.notifier.alerts == []

    _round_at(world, at(20, 10, 15))

    assert world.closes.get(DAY) is not None
    assert len(world.notifier.alerts) == 1


# 经过巡检的时机和连续天数


def test_friday_made_up_on_saturday() -> None:
    """spec「周末补做周五的」：周六重启，巡检第一轮做周五的收盘对账。"""
    world = World()
    world.clock.set(at(15, day=17))
    world.restart()
    world.execution.reconcile()  # 启动对账

    _round_at(world, at(15, 0, 10, day=17))

    assert world.closes.get(date(2026, 10, 16)) is not None


def test_degraded_mode_does_not_reset_the_streak() -> None:
    """spec「降级模式不清零」：巡检不看健康状态，到期照常做收盘对账。"""
    world = World()
    _hold_bbb(world)
    world.closes.save(_record(date(2026, 10, 13), streak=4))

    _round_at(world, at(20, 10))

    record = world.closes.get(DAY)
    assert record is not None and record.streak == 5


# 消息的文字


def test_text_of_every_difference() -> None:
    """spec「结果消息」：每一条差异一行；时刻换成 UTC、到分钟。"""
    plan_id = UUID(int=7)
    record = _record(
        result=Reconciliation(
            stray_orders=(WorkingOrder("CCC", "X1", "B9"),),
            extra_stops=(WorkingOrder("BBB", "BBB-STOP-1", "S1"), WorkingOrder("BBB", "X2", "S9")),
            unexplained=(("CCC", 100),),
        ),
        leftover_orders=(LeftoverOrder(plan_id, "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),),
        new_stops=(NewStop("BBB", "BBB-STOP-2"),),
        unprotected=(
            UnprotectedSpell(
                "BBB", datetime(2026, 10, 15, 5, tzinfo=timezone(timedelta(hours=14)))
            ),
        ),
        streak=0,
    )

    assert close_text(record).split("\n") == [
        "收盘对账 2026-10-14：有差异，连续天数清零",
        "- 不认得的挂单：CCC，客户端订单 ID X1，券商订单号 B9",
        "- 多出来的止损单：BBB，BBB-STOP-1（S1）、X2（S9）",
        "- 来历不明的持仓：CCC 100 股",
        f"- 收盘时没结束的订单：BBB 买入，方案 {plan_id}，对账以后是状态未知",
        "- 对账补挂或重挂的止损单：BBB，BBB-STOP-2",
        "- 当天出现过的无保护仓位：BBB，从 2026-10-14 15:00 UTC 起",
        PAUSED,
    ]


def test_text_of_differences_resumed() -> None:
    """有差异但没有不一致、对账以前暂停开仓：最后一行是恢复开仓。"""
    record = _record(new_stops=(NewStop("BBB", "BBB-STOP-2"),), resumed=True, streak=0)

    assert close_text(record).split("\n") == [
        "收盘对账 2026-10-14：有差异，连续天数清零",
        "- 对账补挂或重挂的止损单：BBB，BBB-STOP-2",
        RESUMED,
    ]


def test_startup_text() -> None:
    """bootstrap spec「启动对账」：不一致每条一行，最后写明已暂停开仓。"""
    result = Reconciliation(
        stray_orders=(WorkingOrder("DDD", "X1", "B9"),), unexplained=(("CCC", 100),)
    )

    assert startup_text(result).split("\n") == [
        "启动对账有不一致：",
        "- 不认得的挂单：DDD，客户端订单 ID X1，券商订单号 B9",
        "- 来历不明的持仓：CCC 100 股",
        PAUSED,
    ]
