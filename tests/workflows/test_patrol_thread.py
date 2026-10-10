"""盘中巡检的线程：开始、间隔、停止和出错以后继续。

场景见 openspec 的 session-patrol spec「巡检的开始、间隔和停止」「巡检配置」「一轮里的失败」。
间隔用很短或很长的值，用 threading.Event 同步，不按墙上时间断言具体的时刻；每次等待都有上限，
失败时不会卡住。
"""

import logging
import threading
from collections.abc import Iterator
from datetime import date, timedelta

import pytest

from tests.fakes.broker import FakeBroker
from tests.modules.execution.builders import baseline_plan
from tests.workflows.builders import World, at, p5
from tradingagentspro.modules.execution import (
    BrokerOrder,
    BrokerOrderStatus,
    Order,
    OrderStatus,
    WorkingOrder,
)
from tradingagentspro.workflows.patrol import PatrolConfig, SessionPatrol

SHORT = PatrolConfig(interval=timedelta(seconds=0.3))
LONG = PatrolConfig(interval=timedelta(seconds=30))
WAIT = 5  # 等某件事发生的上限（秒）


def _pause(seconds: float) -> None:
    """让别的线程跑一会儿。"""
    threading.Event().wait(seconds)


def _patrol_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "session-patrol"]


class _CountingPatrol(SessionPatrol):
    """记下每一轮的开始和做完；fail_first 为真时第一轮抛一个意外的异常。"""

    fail_first = False

    def __init__(self, world: World, config: PatrolConfig) -> None:
        super().__init__(world.execution, world.risk, world.notification, world.lock, config)
        self.started = 0
        self.finished: list[threading.Event] = [threading.Event() for _ in range(3)]

    def run_round(self) -> None:
        self.started += 1
        if self.started == 1 and self.fail_first:
            self.finished[0].set()
            raise RuntimeError("意外的错")
        super().run_round()
        if self.started <= len(self.finished):
            self.finished[self.started - 1].set()


@pytest.fixture
def stop_later() -> list[SessionPatrol]:
    """测试里开始的巡检，测试结束时都停下，避免留下线程。"""
    return []


@pytest.fixture(autouse=True)
def _stop_patrols(stop_later: list[SessionPatrol]) -> Iterator[None]:
    yield None
    for patrol in stop_later:
        patrol.stop()
    assert _patrol_threads() == []


def _start[P: SessionPatrol](patrol: P, stop_later: list[SessionPatrol]) -> P:
    stop_later.append(patrol)
    patrol.start()
    return patrol


def _stop_in_background(patrol: SessionPatrol) -> threading.Thread:
    stopper = threading.Thread(target=patrol.stop)
    stopper.start()
    return stopper


# 巡检的开始、间隔和停止


def test_first_round_starts_at_once(stop_later: list[SessionPatrol]) -> None:
    """spec「开始后立即做第一轮」：间隔是 30 秒，也立即查询 P1。"""
    world = World(config=LONG)
    p1 = baseline_plan()
    world.submit(p1)
    patrol = _CountingPatrol(world, LONG)

    _start(patrol, stop_later)

    assert patrol.finished[0].wait(WAIT)
    assert ("find", str(p1.plan_id)) in world.broker.calls


def test_next_round_waits_for_the_interval_after_a_round(stop_later: list[SessionPatrol]) -> None:
    """spec「每一轮做完以后等间隔」：做完以后才开始等，等够间隔再做下一轮。"""
    world = World()
    patrol = _start(_CountingPatrol(world, SHORT), stop_later)

    assert patrol.finished[0].wait(WAIT)
    _pause(0.1)
    assert patrol.started == 1
    assert patrol.finished[1].wait(WAIT)


def test_changed_interval_is_used(stop_later: list[SessionPatrol]) -> None:
    """spec「修改间隔」：间隔 30 秒时，第一轮之后的一秒里没有第二轮。"""
    world = World()
    patrol = _start(_CountingPatrol(world, LONG), stop_later)

    assert patrol.finished[0].wait(WAIT)
    _pause(1)

    assert patrol.started == 1


def test_stopping_while_waiting_returns_at_once(stop_later: list[SessionPatrol]) -> None:
    """spec「等下一轮时停止」：不等 30 秒的间隔，停止以后没有再调用券商。"""
    world = World()
    patrol = _start(_CountingPatrol(world, LONG), stop_later)
    assert patrol.finished[0].wait(WAIT)
    world.forget_calls()

    stopper = _stop_in_background(patrol)
    stopper.join(WAIT)

    assert not stopper.is_alive()
    assert _patrol_threads() == []
    assert world.broker.calls == []


class _SlowBroker(FakeBroker):
    """按某个客户端订单 ID 查询时停住，直到测试放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.slow_on: str | None = None
        self.entered = threading.Event()
        self.release = threading.Event()

    def find(self, order: Order) -> BrokerOrder | None:
        if order.client_order_id == self.slow_on:
            self.entered.set()
            self.release.wait(WAIT)
        return super().find(order)


def test_stopping_during_an_item_finishes_that_item(stop_later: list[SessionPatrol]) -> None:
    """spec「做某一项时停止」：P1 的查询回来、订单保存以后才结束；不跟进 P5，不做后面两步。"""
    broker = _SlowBroker()
    world = World(broker=broker, config=LONG)
    p1, plan5 = baseline_plan(), p5()
    world.submit(p1)
    world.clock.advance(timedelta(seconds=1))
    world.submit(plan5)
    world.broker.update(str(p1.plan_id), BrokerOrderStatus.WORKING, 30, "50")
    broker.slow_on = str(p1.plan_id)
    patrol = _start(_CountingPatrol(world, LONG), stop_later)
    assert broker.entered.wait(WAIT)

    stopper = _stop_in_background(patrol)
    _pause(0.2)
    assert stopper.is_alive()  # 还在等 P1 这一项
    broker.release.set()
    stopper.join(WAIT)

    assert not stopper.is_alive()
    assert world.order(p1).status is OrderStatus.PARTIALLY_FILLED
    assert ("find", str(plan5.plan_id)) not in world.broker.calls
    assert ("all_positions", "*") not in world.broker.calls
    assert world.remaining(p1) == 100  # 没有同步预留


class _SlowListing(FakeBroker):
    """slow 为真时，读取全部挂单停住，直到测试放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.slow = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def working_orders(self) -> tuple[WorkingOrder, ...]:
        if self.slow:
            self.entered.set()
            self.release.wait(WAIT)
        return super().working_orders()


def test_stopping_during_the_close_reconciliation(stop_later: list[SessionPatrol]) -> None:
    """close-reconciliation spec「停止时正在收盘对账」：对账做完、记录保存、结果推送以后才结束。"""
    broker = _SlowListing()
    world = World(broker=broker, config=LONG)
    p1 = baseline_plan()
    world.submit(p1)
    world.broker.update(str(p1.plan_id), BrokerOrderStatus.FILLED, 100, "50")
    world.broker.positions["BBB"] = 100
    world.execution.track(p1.plan_id)
    world.clock.set(at(20, 10))
    broker.slow = True
    patrol = _start(_CountingPatrol(world, LONG), stop_later)
    assert broker.entered.wait(WAIT)

    stopper = _stop_in_background(patrol)
    _pause(0.2)
    assert stopper.is_alive()  # 还在等收盘对账这一项
    assert world.closes.get(date(2026, 10, 14)) is None
    broker.release.set()
    stopper.join(WAIT)

    assert not stopper.is_alive()
    assert world.closes.get(date(2026, 10, 14)) is not None
    assert len(world.notifier.alerts) == 1


# 一轮里的失败


def test_round_after_an_unexpected_error(
    stop_later: list[SessionPatrol], caplog: pytest.LogCaptureFixture
) -> None:
    """一轮抛出意外的异常：写 ERROR，照常等间隔做下一轮（spec「一轮里的失败」）。"""
    world = World()
    patrol = _CountingPatrol(world, SHORT)
    patrol.fail_first = True
    _start(patrol, stop_later)

    assert patrol.finished[1].wait(WAIT)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info is not None


# 线程本身


def test_thread_name_daemon_and_start_log(
    stop_later: list[SessionPatrol], caplog: pytest.LogCaptureFixture
) -> None:
    """线程名 session-patrol、守护线程；开始时写「盘中巡检已开始，间隔 15 秒」。"""
    world = World()
    with caplog.at_level(logging.INFO):
        patrol = _start(_CountingPatrol(world, PatrolConfig()), stop_later)
    assert patrol.finished[0].wait(WAIT)

    [thread] = _patrol_threads()
    assert thread.daemon
    assert "盘中巡检已开始，间隔 15 秒" in [r.getMessage() for r in caplog.records]

    patrol.stop()
    assert _patrol_threads() == []


def test_stop_without_start_does_nothing() -> None:
    world = World()

    world.patrol.stop()

    assert _patrol_threads() == []
