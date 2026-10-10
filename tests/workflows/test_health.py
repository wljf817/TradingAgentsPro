"""健康监控接线：健康信号、状态变化的告警和日志、恢复后的对账、巡检里的健康判定和它的失败。

场景见 openspec 的 health-monitoring spec。基准数据在 tests/workflows/builders.py 的 World：
2026-10-14 15:00Z（盘中），行情源只有 SPY，更新时刻是时钟的现在之前 10 秒。"做一轮"就是在
测试时钟的当前时刻调用一次 run_round；"券商断开"在只有持仓、没有订单时就是第 3 步读全部持仓
超时。停止时正在恢复后的对账的场景在 test_patrol_thread.py。
"""

import logging
import threading
from datetime import date, datetime

import pytest

from tests.fakes.notifier import FakeNotifier
from tests.modules.execution.builders import baseline_plan
from tests.workflows.builders import World, at, p5
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    OrderStatus,
    ReconcileMismatchError,
    Reconciliation,
    WorkingOrder,
)
from tradingagentspro.modules.health import HealthCheck, HealthMode, HealthSignals, HealthStatus
from tradingagentspro.workflows.health import checks_text, degraded_text, recovered_text

FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
WORKING_ORDERS = ("working_orders", "*")
LOGGER = "tradingagentspro.workflows.health"
PATROL = "tradingagentspro.workflows.patrol"
NOTICE = (
    "已暂停开仓，已经确认、还没下单的开仓方案下单时作废；止损单照常保留。"
    "各项恢复并和券商对账以后恢复开仓"
)


def _logs(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == level]


def _signals_at(world: World, moment: datetime, broker_connected: bool = True) -> HealthSignals:
    world.clock.set(moment)
    return world.monitor.signals(broker_connected)


def _judge_signals_at(world: World, moment: datetime) -> HealthStatus:
    """在 moment 拼一次信号（券商连接），让健康监控判定，不经过巡检。"""
    return world.health.check(_signals_at(world, moment))


def _degraded(*checks: HealthCheck, since: datetime | None = None) -> HealthStatus:
    return HealthStatus(
        mode=HealthMode.DEGRADED,
        abnormal=frozenset(checks),
        degraded_since=since or at(15),
    )


# 健康信号


def test_signals_follow_the_table() -> None:
    """spec「健康信号」的表：模型和预算按正常，心跳是现在，券商取参数，行情是 SPY 的更新时刻。"""
    world = World()

    connected = _signals_at(world, at(15))
    disconnected = world.monitor.signals(False)

    assert connected == HealthSignals(
        pm_model_available=True,
        critic_model_available=True,
        broker_connected=True,
        last_quote_at=at(14, 59, 50),
        reserve_budget_exhausted=False,
        scheduler_heartbeat_at=at(15),
    )
    assert disconnected.broker_connected is False


def test_quote_unavailable_keeps_the_last_time(caplog: pytest.LogCaptureFixture) -> None:
    """spec「一次取不到行情按上一次取到的时刻」：沿用上一次取到的更新时刻，满上限才降级。"""
    world = World()
    world.quotes.frozen_at = at(14, 59, 35)
    _signals_at(world, at(14, 59, 45))
    world.quotes.frozen_at = None
    world.quotes.error = "OpenD 连接超时"

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_signals_at(world, at(15))

    [warning] = _logs(caplog, logging.WARNING)
    assert "取不到行情" in warning and "OpenD 连接超时" in warning
    assert "2026-10-14 14:59:35 UTC" in warning
    assert world.health.status.mode is HealthMode.NORMAL
    assert status.abnormal == frozenset()
    assert _judge_signals_at(world, at(15, 0, 30)).mode is HealthMode.NORMAL  # 延迟 55 秒
    status = _judge_signals_at(world, at(15, 0, 45))  # 延迟 70 秒
    assert status.mode is HealthMode.DEGRADED
    assert status.abnormal == {HealthCheck.DATA_DELAY}


def test_no_quote_since_start(caplog: pytest.LogCaptureFixture) -> None:
    """spec「启动以后还没取到过行情」：按没有，从当天开盘起算，降级。"""
    world = World()
    world.quotes.error = "OpenD 没开"

    caplog.clear()
    with caplog.at_level(logging.INFO):
        signals = _signals_at(world, at(15, 0, 10))
    status = world.health.check(signals)

    assert signals.last_quote_at is None
    [warning] = _logs(caplog, logging.WARNING)
    assert "取不到行情" in warning and "OpenD 没开" in warning and "没有" in warning
    assert status.mode is HealthMode.DEGRADED
    assert status.abnormal == {HealthCheck.DATA_DELAY}


def test_quote_stuck(caplog: pytest.LogCaptureFixture) -> None:
    """spec「行情停更」：没有取不到行情的 WARNING，数据延迟异常。"""
    world = World()
    world.quotes.frozen_at = at(14, 58)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_signals_at(world, at(15))

    assert _logs(caplog, logging.WARNING) == []
    assert status.mode is HealthMode.DEGRADED
    assert status.abnormal == {HealthCheck.DATA_DELAY}


def test_quote_unavailable_off_hours(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘外取不到行情不算异常」：照样取、写 WARNING，状态正常。"""
    world = World()
    world.quotes.error = "OpenD 没开"

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_signals_at(world, at(21))

    assert world.quotes.calls == 1
    assert len(_logs(caplog, logging.WARNING)) == 1
    assert status.mode is HealthMode.NORMAL


def test_slow_round_is_not_a_scheduler_problem() -> None:
    """spec「一轮做得久不算调度器心跳异常」：心跳按判定的时刻。"""
    world = World()

    signals = _signals_at(world, at(15, 6))
    status = world.health.check(signals)

    assert signals.scheduler_heartbeat_at == at(15, 6)
    assert HealthCheck.SCHEDULER not in status.abnormal


# 告警文字


def test_checks_text_follows_the_table_order() -> None:
    """spec「状态变化的告警和日志」：异常的检查按 degraded-mode spec 表的顺序列出。"""
    everything = "模型 API、券商连接、数据延迟、调用预算、调度器心跳"
    assert checks_text(frozenset(HealthCheck)) == everything
    assert checks_text({HealthCheck.SCHEDULER, HealthCheck.BROKER}) == "券商连接、调度器心跳"


def test_degraded_text() -> None:
    """spec「状态变化的告警和日志」的进入降级模式，以及 bootstrap spec 启动时的第一行。"""
    status = _degraded(HealthCheck.BROKER, HealthCheck.DATA_DELAY, since=at(15, 0, 40))

    assert degraded_text(status, at_startup=False) == (
        f"进入降级模式（2026-10-14 15:00 UTC 起），异常的检查：券商连接、数据延迟\n{NOTICE}"
    )
    assert degraded_text(status, at_startup=True) == (
        f"启动时处于降级模式（2026-10-14 15:00 UTC 起），异常的检查：券商连接、数据延迟\n{NOTICE}"
    )


def test_recovered_text_without_mismatch() -> None:
    """spec「状态变化的告警和日志」的降级模式结束：对账没有不一致时写明已恢复开仓。"""
    status = HealthStatus(HealthMode.AWAITING_RECONCILIATION, frozenset(), at(15))

    assert recovered_text(status, Reconciliation()) == (
        "降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账没有不一致，已恢复开仓"
    )


def test_recovered_text_with_mismatch() -> None:
    """spec「状态变化的告警和日志」的降级模式结束：有不一致时每条一行，最后一行暂停开仓。"""
    status = HealthStatus(HealthMode.AWAITING_RECONCILIATION, frozenset(), at(15))

    text = recovered_text(status, Reconciliation(unexplained=(("CCC", 100),)))

    assert text == (
        "降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账有不一致：\n"
        "- 来历不明的持仓：CCC 100 股\n"
        "已暂停开仓，之后的对账没有不一致时恢复"
    )


# 状态变化的告警和日志、恢复后的对账：直接调用 judge，不经过巡检


def _hold_bbb(world: World) -> None:
    """持有 BBB 100 股，止损单已对齐（P1 全部成交，BBB-STOP-1 挂着 100 股），不留调用记录。"""
    p1 = baseline_plan()
    world.submit(p1)
    world.broker.update(str(p1.plan_id), FILLED, 100, "50")
    world.broker.positions["BBB"] = 100
    world.execution.track(p1.plan_id)
    world.forget_calls()


def _world() -> World:
    world = World()
    _hold_bbb(world)
    return world


def _judge_at(world: World, moment: datetime, broker_connected: bool = True) -> HealthStatus:
    world.clock.set(moment)
    world.monitor.judge(broker_connected)
    return world.health.status


def _awaiting(world: World) -> None:
    """券商 15:00:00Z 断开，进入降级；15:00:15Z 恢复，等待对账。清掉推送和调用记录。"""
    _judge_at(world, at(15), broker_connected=False)
    _judge_at(world, at(15, 0, 15))
    assert world.health.status.mode is HealthMode.AWAITING_RECONCILIATION
    world.notifier.alerts.clear()
    world.forget_calls()


def test_normal_stays_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """spec「状态变化的告警和日志」正常 → 正常：不推送，不写日志。"""
    world = _world()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15))

    assert status.mode is HealthMode.NORMAL
    assert world.notifier.alerts == []
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_entering_degraded_mode_alerts(caplog: pytest.LogCaptureFixture) -> None:
    """spec「券商断开时进入降级并告警」：正常 → 降级，推送一条，WARNING 含全文。"""
    world = _world()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15), broker_connected=False)

    assert status == _degraded(HealthCheck.BROKER, since=at(15))
    expected = f"进入降级模式（2026-10-14 15:00 UTC 起），异常的检查：券商连接\n{NOTICE}"
    assert world.notifier.alerts == [expected]
    assert _logs(caplog, logging.WARNING) == [expected]


def test_no_repeated_alert_while_degraded(caplog: pytest.LogCaptureFixture) -> None:
    """spec「降级期间不重复告警」：降级 → 降级、异常的检查不变，不推送，不写日志。"""
    world = _world()
    _judge_at(world, at(15), broker_connected=False)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 15), broker_connected=False)

    assert status == _degraded(HealthCheck.BROKER, since=at(15))
    assert len(world.notifier.alerts) == 1
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_changed_checks_while_degraded_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「降级期间异常的检查变了只写日志」：WARNING 写明现在异常的检查，不推送。"""
    world = _world()
    _judge_at(world, at(15), broker_connected=False)
    world.quotes.frozen_at = at(14, 58)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 15), broker_connected=False)

    assert status.abnormal == {HealthCheck.BROKER, HealthCheck.DATA_DELAY}
    assert len(world.notifier.alerts) == 1
    [warning] = _logs(caplog, logging.WARNING)
    assert "现在异常的检查是券商连接、数据延迟" in warning


def test_recovered_checks_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「各项恢复以后只写日志」和「恢复后的对账」降级 → 等待对账：不对账，不推送，写 INFO。"""
    world = _world()
    _judge_at(world, at(15), broker_connected=False)
    world.forget_calls()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 15))

    assert status.mode is HealthMode.AWAITING_RECONCILIATION
    assert len(world.notifier.alerts) == 1
    assert _logs(caplog, logging.INFO) == ["各项检查已恢复，下一轮和券商对账"]
    assert WORKING_ORDERS not in world.broker.calls


def test_abnormal_again_while_awaiting(caplog: pytest.LogCaptureFixture) -> None:
    """spec「等待对账时又有检查异常只写日志」和「恢复后的对账」等待对账 → 降级：不对账。"""
    world = _world()
    _awaiting(world)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 30), broker_connected=False)

    assert status == _degraded(HealthCheck.BROKER, since=at(15))
    assert world.notifier.alerts == []
    assert _logs(caplog, logging.WARNING) == ["等待对账时又有检查异常：券商连接"]
    assert WORKING_ORDERS not in world.broker.calls


def test_reconcile_one_round_after_recovery(caplog: pytest.LogCaptureFixture) -> None:
    """spec「各项恢复以后下一轮对账」和「恢复后的对账」等待对账 → 等待对账：对账，回到正常。"""
    world = _world()
    _awaiting(world)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 30))

    assert WORKING_ORDERS in world.broker.calls
    assert ("working_stops", "*") in world.stops.calls
    assert status.mode is HealthMode.NORMAL and not status.degraded
    expected = (
        "降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账没有不一致，已恢复开仓"
    )
    assert world.notifier.alerts == [expected]
    assert _logs(caplog, logging.INFO) == [expected]


def test_reconcile_with_a_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """spec「恢复后的对账有不一致」：回到正常，告警列出不一致，买单仍因对账不一致被拒。"""
    world = _world()
    _awaiting(world)
    world.broker.positions["CCC"] = 100

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 30))

    assert status.mode is HealthMode.NORMAL
    [message] = world.notifier.alerts
    assert message.splitlines() == [
        "降级模式结束（2026-10-14 15:00 UTC 起）：各项检查已恢复，和券商对账有不一致：",
        "- 来历不明的持仓：CCC 100 股",
        "已暂停开仓，之后的对账没有不一致时恢复",
    ]
    assert _logs(caplog, logging.WARNING) == [message]
    with pytest.raises(ReconcileMismatchError):
        world.submit(p5())
    assert world.broker.calls_for(str(p5().plan_id)) == []


def test_broker_drops_during_reconciliation(caplog: pytest.LogCaptureFixture) -> None:
    """spec「对账时券商又断开」：WARNING，留在等待对账；再断开时降级不告警，恢复两轮后对账。"""
    world = _world()
    _awaiting(world)
    world.broker.fail_next("working_orders")

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15, 0, 30))

    assert status.mode is HealthMode.AWAITING_RECONCILIATION
    [warning] = _logs(caplog, logging.WARNING)
    assert "恢复后的对账没做成" in warning and "券商不可用" in warning
    assert world.notifier.alerts == []
    assert _judge_at(world, at(15, 0, 45), broker_connected=False).mode is HealthMode.DEGRADED
    assert world.notifier.alerts == []
    assert _judge_at(world, at(15, 1)).mode is HealthMode.AWAITING_RECONCILIATION
    assert _judge_at(world, at(15, 1, 15)).mode is HealthMode.NORMAL
    [message] = world.notifier.alerts
    assert message.startswith("降级模式结束（2026-10-14 15:00 UTC 起）")


def test_unexpected_error_during_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「恢复后的对账出了意外的错」的 judge 一层：异常往外抛，留在等待对账，不推送。"""
    world = _world()
    _awaiting(world)

    def unauthorized() -> None:
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(world.broker, "working_orders", unauthorized)
    with pytest.raises(RuntimeError, match="401"):
        _judge_at(world, at(15, 0, 30))

    assert world.health.status.mode is HealthMode.AWAITING_RECONCILIATION
    assert world.notifier.alerts == []


def test_alert_that_cannot_be_sent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」：写错误日志（含文字和原因），状态照常变化，之后不重发。"""
    world = _world()
    world.notifier.fail_next = True

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _judge_at(world, at(15), broker_connected=False)

    assert status.mode is HealthMode.DEGRADED
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("告警没有发出去" in e and "进入降级模式" in e for e in errors)
    _judge_at(world, at(15, 0, 15), broker_connected=False)
    assert len(world.notifier.alerts) == 1  # 只有发不出去的那一次


# 锁


def test_judging_does_not_take_the_lock() -> None:
    """spec「判定时不拿锁」的 judge 一层：别的线程拿着锁时，不用对账的判定照样做完。"""
    world = _world()
    world.clock.set(at(15))
    judge = threading.Thread(target=world.monitor.judge, args=(False,))

    with world.lock:
        judge.start()
        judge.join(5)
        assert not judge.is_alive()
        assert world.health.status.mode is HealthMode.DEGRADED
        assert len(world.notifier.alerts) == 1


def test_reconciliation_waits_for_the_lock() -> None:
    """spec「对账时别的调用拿着锁」：放开锁之前不对账，放开以后对账、回到正常。"""
    world = _world()
    _awaiting(world)
    world.clock.set(at(15, 0, 30))
    judge = threading.Thread(target=world.monitor.judge, args=(True,))

    with world.lock:
        judge.start()
        judge.join(0.2)
        assert judge.is_alive()
        assert WORKING_ORDERS not in world.broker.calls
        assert world.health.status.mode is HealthMode.AWAITING_RECONCILIATION

    judge.join(5)
    assert not judge.is_alive()
    assert not world.health.status.degraded  # 回到正常


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


@pytest.mark.parametrize("recovering", [False, True], ids=["entering", "recovering"])
def test_lock_is_free_while_alerting(recovering: bool) -> None:
    """spec「推送告警时不拿锁」：进入降级和降级模式结束的推送都在锁外。"""
    notifier = _BlockingNotifier()
    world = World(notifier=notifier)
    _hold_bbb(world)
    if recovering:
        notifier.release.set()
        _awaiting(world)
        notifier.release.clear()
        notifier.entered.clear()
    world.clock.set(at(15, 0, 30))
    judge = threading.Thread(target=world.monitor.judge, args=(recovering,))
    judge.start()

    try:
        assert notifier.entered.wait(5)
        assert world.lock.acquire(blocking=False)
        world.lock.release()
    finally:
        notifier.release.set()
        judge.join(5)
    assert not judge.is_alive()


# 巡检里的健康判定：调用 run_round


def _round_at(world: World, moment: datetime) -> HealthStatus:
    world.clock.set(moment)
    world.patrol.run_round()
    return world.health.status


def _down_round_at(world: World, moment: datetime) -> HealthStatus:
    """券商断开的一轮：没有未结束的订单时，券商只收到第 3 步读全部持仓，让它超时。"""
    world.broker.fail_next("all_positions")
    return _round_at(world, moment)


def test_judged_after_aligning_stops(caplog: pytest.LogCaptureFixture) -> None:
    """spec「一轮对齐止损单以后判定，正常时不告警」：不另外调用券商。"""
    world = _world()
    world.stops.update("BBB-STOP-1", CANCELLED)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _round_at(world, at(15))

    assert world.stops.placed[-1].client_order_id == "BBB-STOP-2"  # 第 3 步照常对齐
    assert world.quotes.calls == 1
    assert status.mode is HealthMode.NORMAL and status.abnormal == frozenset()
    assert world.notifier.alerts == []
    assert [r for r in caplog.records if r.name == LOGGER] == []
    assert world.broker.calls.count(("all_positions", "*")) == 1


def test_round_judges_without_the_lock() -> None:
    """spec「判定时不拿锁」：别的线程拿着锁，第 3 步读持仓超时，健康判定照样做完。"""
    world = World()
    world.clock.set(at(15))
    world.broker.fail_next("all_positions")
    round_ = threading.Thread(target=world.patrol.run_round)

    with world.lock:
        round_.start()
        round_.join(5)
        assert not round_.is_alive()
        assert world.health.status.mode is HealthMode.DEGRADED
        assert len(world.notifier.alerts) == 1


def test_positions_timeout_means_broker_disconnected(caplog: pytest.LogCaptureFixture) -> None:
    """spec「读全部持仓超时算券商断开」：巡检写 WARNING，异常的检查是券商连接。"""
    world = _world()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        status = _down_round_at(world, at(15))

    assert any(
        "读取券商持仓失败" in r.getMessage()
        for r in caplog.records
        if r.name == PATROL and r.levelno == logging.WARNING
    )
    assert status.abnormal == {HealthCheck.BROKER}


def test_tracking_timeout_is_not_a_disconnection() -> None:
    """spec「跟进订单超时不算券商断开」：P1 状态未知，状态仍是正常。"""
    world = World()
    p1 = baseline_plan()
    world.submit(p1)
    world.broker.fail_next("find")

    status = _round_at(world, at(15))

    assert world.order(p1).status is OrderStatus.UNKNOWN
    assert status.mode is HealthMode.NORMAL


def test_quote_outage_and_recovery_through_rounds() -> None:
    """spec「一次取不到行情按上一次取到的时刻」：满 60 秒才降级，OpenD 恢复两轮以后回到正常。"""
    world = _world()
    world.quotes.frozen_at = at(14, 59, 35)
    _round_at(world, at(14, 59, 45))
    world.quotes.frozen_at = None
    world.quotes.error = "OpenD 连接超时"

    assert _round_at(world, at(15)).mode is HealthMode.NORMAL
    assert _round_at(world, at(15, 0, 30)).mode is HealthMode.NORMAL
    status = _round_at(world, at(15, 0, 45))
    assert status.mode is HealthMode.DEGRADED and status.abnormal == {HealthCheck.DATA_DELAY}
    assert len(world.notifier.alerts) == 1
    world.quotes.error = None
    assert _round_at(world, at(15, 1)).mode is HealthMode.AWAITING_RECONCILIATION
    assert _round_at(world, at(15, 1, 15)).mode is HealthMode.NORMAL
    assert len(world.notifier.alerts) == 2


def test_broker_outage_and_recovery_through_rounds() -> None:
    """spec「券商断开时进入降级并告警」「降级期间不重复告警」「各项恢复以后下一轮对账」经过巡检。"""
    world = _world()

    assert _down_round_at(world, at(15)).mode is HealthMode.DEGRADED
    assert _down_round_at(world, at(15, 0, 15)).mode is HealthMode.DEGRADED
    assert len(world.notifier.alerts) == 1
    world.forget_calls()
    assert _round_at(world, at(15, 0, 30)).mode is HealthMode.AWAITING_RECONCILIATION
    assert WORKING_ORDERS not in world.broker.calls
    assert _round_at(world, at(15, 0, 45)).mode is HealthMode.NORMAL
    assert WORKING_ORDERS in world.broker.calls
    heads = [m[:6] for m in world.notifier.alerts]
    assert heads == ["进入降级模式", "降级模式结束"]


def test_changes_during_the_outage_are_handled_before_reconciling() -> None:
    """spec「断线期间的变化在对账以前已经补上」：恢复的那一轮重挂，下一轮对账时什么都不用改。"""
    world = _world()
    _down_round_at(world, at(15))
    world.stops.update("BBB-STOP-1", CANCELLED)

    assert _round_at(world, at(15, 0, 15)).mode is HealthMode.AWAITING_RECONCILIATION
    assert world.stops.placed[-1].client_order_id == "BBB-STOP-2"
    placed = len(world.stops.placed)
    world.forget_calls()
    assert _round_at(world, at(15, 0, 30)).mode is HealthMode.NORMAL

    assert WORKING_ORDERS in world.broker.calls
    assert len(world.stops.placed) == placed
    assert world.stops.modify_requests == [] and world.stops.cancel_requests == []


def test_data_delay_flapping() -> None:
    """spec「数据延迟来回抖动」：降级和等待对账之间来回不对账、不告警；连续两轮正常才回到正常。"""
    world = _world()
    world.quotes.frozen_at = at(14, 58)
    assert _round_at(world, at(15)).mode is HealthMode.DEGRADED
    world.forget_calls()

    modes: list[HealthMode] = []
    for second, stale in [(15, False), (30, True), (45, False), (59, True)]:
        world.quotes.frozen_at = at(14, 58) if stale else None
        modes.append(_round_at(world, at(15, 0, second)).mode)

    awaiting, degraded = HealthMode.AWAITING_RECONCILIATION, HealthMode.DEGRADED
    assert modes == [awaiting, degraded, awaiting, degraded]
    assert WORKING_ORDERS not in world.broker.calls
    assert len(world.notifier.alerts) == 1
    world.quotes.frozen_at = None
    assert _round_at(world, at(15, 1, 15)).mode is awaiting
    assert _round_at(world, at(15, 1, 30)).mode is HealthMode.NORMAL
    assert len(world.notifier.alerts) == 2


def test_one_broker_timeout() -> None:
    """spec「券商偶发一次超时」：进入降级、告警；下一轮等待对账，再下一轮对账、回到正常、告警。"""
    world = _world()

    assert _down_round_at(world, at(15)).mode is HealthMode.DEGRADED
    assert _round_at(world, at(15, 0, 15)).mode is HealthMode.AWAITING_RECONCILIATION
    assert _round_at(world, at(15, 0, 30)).mode is HealthMode.NORMAL
    assert len(world.notifier.alerts) == 2


def test_data_delay_not_checked_after_the_close() -> None:
    """spec「收盘以后数据延迟不再检查」：盘后回到正常，下一个交易日开盘满 60 秒又降级。"""
    world = _world()
    _round_at(world, at(19, 57, 45))
    world.quotes.error = "OpenD 断开"
    assert _round_at(world, at(19, 59, 30)).abnormal == {HealthCheck.DATA_DELAY}
    assert len(world.notifier.alerts) == 1

    assert _round_at(world, at(20, 0, 15)).mode is HealthMode.AWAITING_RECONCILIATION
    assert _round_at(world, at(20, 0, 30)).mode is HealthMode.NORMAL
    assert world.notifier.alerts[-1].startswith("降级模式结束")

    status = _round_at(world, at(13, 31, 1, day=15))
    assert status.abnormal == {HealthCheck.DATA_DELAY}
    assert world.notifier.alerts[-1].startswith("进入降级模式（2026-10-15 13:31 UTC 起）")


def test_degraded_at_startup() -> None:
    """spec「程序启动时处于降级模式」：巡检第一轮仍是降级不再告警；OpenD 连上两轮以后回到正常。"""
    world = _world()
    world.quotes.error = "OpenD 没开"
    world.clock.set(at(15))
    assert world.health.check(world.monitor.signals(True)).degraded  # 启动时的第一次健康判定

    assert _round_at(world, at(15, 0, 10)).mode is HealthMode.DEGRADED
    assert world.notifier.alerts == []
    world.quotes.error = None
    assert _round_at(world, at(15, 0, 25)).mode is HealthMode.AWAITING_RECONCILIATION
    assert _round_at(world, at(15, 0, 40)).mode is HealthMode.NORMAL
    [message] = world.notifier.alerts
    assert message.startswith("降级模式结束（2026-10-14 15:00 UTC 起）")


# 健康判定的失败


def test_auth_error_reading_positions_skips_the_judgement(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """spec「读全部持仓时认证错误」：巡检写 ERROR，这一轮不判定，状态不变。"""
    world = _world()

    def unauthorized() -> dict[str, int]:
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(world.broker, "all_positions", unauthorized)
    caplog.clear()
    status = _round_at(world, at(15))

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("列出要对齐止损单的股票" in e for e in errors)
    assert world.quotes.calls == 0
    assert status.mode is HealthMode.NORMAL
    assert world.notifier.alerts == []


def test_unexpected_error_in_the_recovery_round(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """spec「恢复后的对账出了意外的错」：ERROR 写明健康判定出错，留在等待对账；下一轮再对账。"""
    world = _world()
    _down_round_at(world, at(15))
    _round_at(world, at(15, 0, 15))
    world.notifier.alerts.clear()
    listing = world.broker.working_orders

    def unauthorized() -> tuple[WorkingOrder, ...]:
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(world.broker, "working_orders", unauthorized)
    caplog.clear()
    status = _round_at(world, at(15, 0, 30))

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "健康判定" in error.getMessage() and error.exc_info is not None
    assert status.mode is HealthMode.AWAITING_RECONCILIATION
    assert world.notifier.alerts == []
    monkeypatch.setattr(world.broker, "working_orders", listing)
    assert _round_at(world, at(15, 0, 45)).mode is HealthMode.NORMAL
    assert len(world.notifier.alerts) == 1


def test_close_reconciliation_stays_last(caplog: pytest.LogCaptureFixture) -> None:
    """spec「巡检里的健康判定」：同一轮里恢复后的对账先于收盘对账。"""
    world = _world()
    _down_round_at(world, at(20, 9, 30))
    _round_at(world, at(20, 9, 45))

    _round_at(world, at(20, 10))

    assert world.health.status.mode is HealthMode.NORMAL
    assert world.closes.get(date(2026, 10, 14)) is not None
    assert [m[:6] for m in world.notifier.alerts] == ["进入降级模式", "降级模式结束", "收盘对账 2"]
