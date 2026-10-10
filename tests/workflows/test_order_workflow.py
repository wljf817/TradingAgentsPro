"""下单流程的 DBOS 工作流的测试：真实的 DBOS，连 testcontainers 的空数据库（要开着 Docker）。

场景见 openspec 的 order-flow spec，做法见 add-order-flow 的 design D2、D3、D9、D10、D15。
每一步的细节在 test_order_flow.py 里测；这里测把它们串起来以后的流程、按钮回调和重启恢复。
休眠用 FakeSleep：把测试时钟拨到醒来的时刻、立即返回，像 DBOS 的持久化休眠一样按第一次记下的
醒来时刻算。
"""

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID

import pytest
from dbos import DBOS
from sqlalchemy import create_engine, text

from tests.fakes.notifier import FakeNotifier
from tests.workflows.order_builders import at, p1, p2, p4, quote
from tests.workflows.order_desk import Desk
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import BrokerOrderStatus, OrderStatus
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    NotificationConfig,
)
from tradingagentspro.shared.trading_calendar import OutsideSessionError
from tradingagentspro.workflows.order_flow import OrderFlowConfig, PlanEnd, Stage
from tradingagentspro.workflows.order_workflow import (
    CONFIRMATION,
    DECISION_TOPIC,
    NotAwaitingConfirmationError,
    OrderPlanIntake,
    deliver_decision,
    install,
)

_POLL = 0.02  # 轮询的间隔（秒）
_POLLS = 750  # 最多轮询这么多次（15 秒）：只在测试失败时才会等满


class Crash(BaseException):
    """模拟进程在这一刻退出：DBOS 只接 Exception，这一步不记结果，工作流保持 PENDING。"""


class FakeSleep:
    """满足 install 的 sleep：把时钟拨到醒来的时刻、立即返回。

    一次运行里第 k 次调用的醒来时刻在第一次调用时记下（调用时刻加秒数），重启（rewind）以后重放到
    第 k 次时用记下的那个时刻，和 DBOS 的持久化休眠一样；时钟已经过了它时不往回拨。hooks 按调用的
    顺序在拨钟以前运行，可以抛 Crash 模拟进程在休眠时退出。
    """

    def __init__(self, desk: Desk) -> None:
        self.desk = desk
        self.calls: list[float] = []
        self.wakes: list[datetime] = []
        self.hooks: list[Callable[[], None] | None] = []
        self._index = 0

    def rewind(self) -> None:
        """进程重启：恢复的工作流从第一次休眠开始重放。"""
        self._index = 0

    def __call__(self, seconds: float) -> None:
        clock = self.desk.clock
        index = self._index
        self._index += 1
        self.calls.append(seconds)
        if index == len(self.wakes):
            self.wakes.append(clock.now() + timedelta(seconds=seconds))
        if self.hooks:
            hook = self.hooks.pop(0)
            if hook is not None:
                hook()
        clock.set(max(clock.now(), self.wakes[index]))


class Harness:
    """一个测试里的 DBOS：launch 时 install 这套下单流程，close 时 destroy。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self.launched = False

    def launch(self, desk: Desk, sleep: FakeSleep, *, patching: bool = True) -> None:
        DBOS(
            config={
                "name": "order-flow-test",
                "system_database_url": self.url,
                "enable_patching": patching,
            }
        )
        install(desk.flow, sleep)
        DBOS.launch()
        self.launched = True

    def close(self) -> None:
        if self.launched:
            DBOS.destroy()
            self.launched = False

    def restart(self, desk: Desk, sleep: FakeSleep, *, patching: bool = True) -> None:
        """进程重启：destroy 以后用同一套存储和替身重新建各个用例，再 launch。"""
        self.close()
        desk.restart()
        sleep.rewind()
        self.launch(desk, sleep, patching=patching)

    def set_pending(self, plan: TradePlan) -> None:
        """把这个方案跑完的工作流改回 PENDING：恢复时会再跑一次。"""
        engine = create_engine(self.url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "update dbos.workflow_status set status = 'PENDING' where workflow_uuid = :id"
                ),
                {"id": str(plan.plan_id)},
            )
        engine.dispose()


@pytest.fixture
def harness(empty_database: str) -> Iterator[Harness]:
    harness = Harness(empty_database)
    yield harness
    harness.close()


@pytest.fixture(autouse=True)
def no_threads_left_behind() -> Iterator[None]:
    """每个测试结束时，没有留下运行中的非守护线程（工作流都已经结束，DBOS 已经关闭）。"""
    before = set(threading.enumerate())
    yield
    left = [t for t in threading.enumerate() if t not in before and not t.daemon and t.is_alive()]
    for thread in left:
        thread.join(5)
    assert [t.name for t in left if t.is_alive()] == []


def _pause() -> None:
    threading.Event().wait(_POLL)


def _until(condition: Callable[[], bool]) -> None:
    for _ in range(_POLLS):
        if condition():
            return
        _pause()
    raise AssertionError("等不到条件成立")


def _status(plan: TradePlan) -> str | None:
    status = DBOS.get_workflow_status(str(plan.plan_id))
    return None if status is None else status.status


def _end(plan: TradePlan) -> PlanEnd:
    """等这个方案的下单流程结束，返回它怎样结束。"""
    _until(lambda: _status(plan) in ("SUCCESS", "ERROR"))
    assert _status(plan) == "SUCCESS"
    result: PlanEnd = DBOS.retrieve_workflow(str(plan.plan_id)).get_result()
    return result


def _stage(plan: TradePlan) -> Any:
    return DBOS.get_event(str(plan.plan_id), CONFIRMATION, timeout_seconds=0)


def _await_confirmation(desk: Desk, plan: TradePlan) -> None:
    """等这个方案的下单流程发出确认消息、在等确认。"""
    _until(lambda: any(r.plan.plan_id == plan.plan_id for r in desk.notifier.requests))
    assert _stage(plan) == Stage.WAITING


def _submit(desk: Desk, plan: TradePlan) -> None:
    result = OrderPlanIntake(desk.services).submit(plan)
    assert result.outcome is not RiskOutcome.REJECTED


def _confirm(desk: Desk, plan: TradePlan, at_moment: datetime | None = None) -> None:
    """等它在等确认，拨钟到 at_moment（有的话），按确认。"""
    _await_confirmation(desk, plan)
    if at_moment is not None:
        desk.clock.set(at_moment)
    deliver_decision(plan.plan_id, ConfirmationOutcome.CONFIRMED)


def _launched(harness: Harness, desk: Desk | None = None) -> tuple[Desk, FakeSleep]:
    desk = desk or Desk()
    sleep = FakeSleep(desk)
    harness.launch(desk, sleep)
    return desk, sleep


# 提交、确认、下单


def test_submit_confirm_and_order(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """spec「通过后启动下单流程」「盘中确认后立即下单」「在等确认时送达」「提交以后交给巡检」
    「盘中下单的日志」。"""
    desk, _ = _launched(harness)

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        _submit(desk, p1())
        assert desk.remaining(p1()) == 20
        _confirm(desk, p1(), at(15, 5))
        assert _end(p1()) is PlanEnd.SUBMITTED

    [request] = desk.notifier.requests
    assert request.deadline == at(15, 30)
    [placed] = desk.broker.placed
    assert (placed.client_order_id, placed.quantity, placed.limit_price) == (
        str(p1().plan_id),
        20,
        Decimal(50),
    )
    assert desk.remaining(p1()) == 20
    assert desk.notifier.alerts == []
    assert _stage(p1()) == Stage.CONFIRMED
    messages = [r.getMessage() for r in caplog.records]
    expected = ["风控通过，20 股", "请求确认，消息 M1，截止 2026-10-14 15:30 UTC", "已确认"]
    expected += ["再校验通过，20 股", "订单已提交，委托 20 股"]
    found = [next(i for i, m in enumerate(messages) if part in m) for part in expected]
    assert found == sorted(found)


def test_start_failure_releases(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「启动下单流程失败」：报错，预留释放，没有确认消息。"""
    desk, _ = _launched(harness)

    def broken(*args: object) -> None:
        raise RuntimeError("数据库出错")

    monkeypatch.setattr(DBOS, "start_workflow", broken)

    with pytest.raises(RuntimeError, match="数据库出错"):
        OrderPlanIntake(desk.services).submit(p1())
    assert desk.remaining(p1()) is None
    assert desk.notifier.requests == []


def test_submit_twice(harness: Harness) -> None:
    """spec「同一个方案提交两次」：同样的结果，一份预留，一条确认消息。"""
    desk, _ = _launched(harness)
    intake = OrderPlanIntake(desk.services)

    first = intake.submit(p1())
    second = intake.submit(p1())
    _confirm(desk, p1())

    assert first == second
    assert _end(p1()) is PlanEnd.SUBMITTED
    assert len(desk.notifier.requests) == 1
    assert desk.broker.order_count(str(p1().plan_id)) == 1


def test_confirmation_cannot_be_sent(harness: Harness) -> None:
    """spec「确认请求发不出去」「确认请求超时以后消息才发出去」：按确认超时处理，释放预留，
    流程结束；之后（消息后来才出现时）按钮不送达。"""
    desk, _ = _launched(harness)
    desk.notifier.fail_next = True

    _submit(desk, p1())

    assert _end(p1()) is PlanEnd.UNSENT
    assert desk.remaining(p1()) is None
    assert desk.broker.placed == []
    with pytest.raises(NotAwaitingConfirmationError):
        deliver_decision(p1().plan_id, ConfirmationOutcome.CONFIRMED)


def test_rejected_by_hand(harness: Harness) -> None:
    """spec「人工拒绝」：释放全部预留，不下单，不告警。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())
    _await_confirmation(desk, p1())

    deliver_decision(p1().plan_id, ConfirmationOutcome.REJECTED)

    assert _end(p1()) is PlanEnd.REJECTED
    assert desk.remaining(p1()) is None
    assert desk.broker.placed == [] and desk.notifier.alerts == []


def _short_timeout() -> Desk:
    return Desk(notification_config=NotificationConfig(confirmation_timeout=timedelta(seconds=1)))


def test_confirmation_times_out(harness: Harness) -> None:
    """spec「确认超时」「人工拒绝和确认超时不告警」：报告已超时，释放预留，不下单。"""
    desk, _ = _launched(harness, _short_timeout())

    _submit(desk, p1())

    assert _end(p1()) is PlanEnd.TIMED_OUT
    assert desk.notifier.finished == [("M1", ConfirmationOutcome.TIMED_OUT)]
    assert desk.remaining(p1()) is None
    assert desk.broker.placed == [] and desk.notifier.alerts == []


def test_press_after_the_timeout(harness: Harness) -> None:
    """spec「按钮和超时同时」：工作流得出超时以后才送到的确认没有效果，消息最后是已超时。"""
    desk, _ = _launched(harness, _short_timeout())
    _submit(desk, p1())
    assert _end(p1()) is PlanEnd.TIMED_OUT

    DBOS.send(str(p1().plan_id), ConfirmationOutcome.CONFIRMED.value, topic=DECISION_TOPIC)
    with pytest.raises(NotAwaitingConfirmationError):
        deliver_decision(p1().plan_id, ConfirmationOutcome.CONFIRMED)

    assert desk.notifier.finished == [("M1", ConfirmationOutcome.TIMED_OUT)]
    assert desk.broker.placed == []


def test_confirm_a_second_before_the_deadline(harness: Harness) -> None:
    """spec「截止前一刻确认」：15:29:59Z 按确认，照样下单。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())

    _confirm(desk, p1(), at(15, 29, 59))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert desk.order(p1()) is not None


def test_adjusted_plan(harness: Harness) -> None:
    """spec「调整的方案按调整后的股数确认」：调整为 10 股，确认以后下单 10 股。"""
    desk, _ = _launched(harness)
    desk.broker.cash = Decimal(505)

    result = OrderPlanIntake(desk.services).submit(p1())
    _confirm(desk, p1())

    assert (result.outcome, result.quantity) == (RiskOutcome.ADJUSTED, 10)
    assert desk.notifier.requests[0].decision.quantity == 10
    assert _end(p1()) is PlanEnd.SUBMITTED
    assert desk.broker.placed[0].quantity == 10


# 按钮的结果只送给在等确认的方案


def test_press_for_a_plan_without_a_flow(harness: Harness) -> None:
    """spec「方案没有下单流程」：回调报错。"""
    _launched(harness)

    with pytest.raises(NotAwaitingConfirmationError, match="没有在等确认"):
        deliver_decision(UUID(int=77), ConfirmationOutcome.CONFIRMED)


def test_press_again_after_confirming(harness: Harness) -> None:
    """spec「已经确认过的方案」：再按报错，券商仍然只有一笔订单。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())
    _confirm(desk, p1())
    assert _end(p1()) is PlanEnd.SUBMITTED

    with pytest.raises(NotAwaitingConfirmationError):
        deliver_decision(p1().plan_id, ConfirmationOutcome.CONFIRMED)
    assert desk.broker.order_count(str(p1().plan_id)) == 1


# 等开盘


def test_confirm_after_the_close(harness: Harness) -> None:
    """spec「盘后确认，下一个交易日开盘下单」：等到 2026-10-15 13:30Z，截止时刻 10-15 20:00Z。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21))
    _submit(desk, p1())

    _confirm(desk, p1(), at(21, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(13, 30, day=15)]
    order = desk.order(p1())
    assert order is not None and order.deadline == at(20, day=15)
    assert order.created_at == at(13, 30, day=15)


def test_confirm_on_friday_after_the_close(harness: Harness) -> None:
    """spec「周五盘后确认，等到周一开盘」。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21, day=16))
    _submit(desk, p1())

    _confirm(desk, p1(), at(21, 5, day=16))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(13, 30, day=19)]


def test_confirm_before_the_open(harness: Harness) -> None:
    """spec「盘前确认等当天开盘」。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(12))
    _submit(desk, p1())

    _confirm(desk, p1(), at(12, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(13, 30)]


def test_confirm_after_the_expiry(harness: Harness) -> None:
    """spec「确认时已经过了有效期」：作废、释放、告警，不下单。"""
    desk, _ = _launched(harness)
    desk.clock.set(at(19, 50))
    _submit(desk, p1())

    _confirm(desk, p1(), at(20, 5))

    assert _end(p1()) is PlanEnd.VOIDED
    assert desk.remaining(p1()) is None
    [alert] = desk.notifier.alerts
    assert "方案作废，不会下单" in alert and "到了有效期 2026-10-14 20:00 UTC 还没下单" in alert
    assert desk.broker.placed == []


# 下单前再校验和提交


def test_price_gap_at_the_open(harness: Harness) -> None:
    """spec「开盘跳空作废」「再校验拒绝的告警」。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21))
    _submit(desk, p1())
    sleep.hooks.append(lambda: desk.source.put(quote("BBB", "53", "49.5")))

    _confirm(desk, p1(), at(21, 5))

    assert _end(p1()) is PlanEnd.VOIDED
    assert desk.remaining(p1()) is None
    [alert] = desk.notifier.alerts
    assert "价格偏离：实时价 53 偏离限价 50 超过允许的 0.03" in alert
    assert desk.broker.placed == []


def test_no_trade_yet_at_the_open(harness: Harness) -> None:
    """spec「刚开盘还没有成交」：等 30 秒，有了最新价以后下单。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21))
    _submit(desk, p1())
    sleep.hooks += [
        lambda: desk.source.put(quote("BBB", None, "49.5")),
        lambda: desk.source.put(quote("BBB", "50", "49.5")),
    ]

    _confirm(desk, p1(), at(21, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(13, 30, day=15), at(13, 30, 30, day=15)]
    assert desk.broker.placed[0].quantity == 20


def test_quotes_unavailable_until_the_expiry(harness: Harness) -> None:
    """spec「行情一直不可用到有效期」：19:59:10、19:59:40 各重试一次，20:00:10 按有效期作废。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(19, 59))
    _submit(desk, p1())
    desk.source.failing = True

    _confirm(desk, p1(), at(19, 59, 10))

    assert _end(p1()) is PlanEnd.VOIDED
    assert sleep.wakes == [at(19, 59, 40), at(20, 0, 10)]
    [alert] = desk.notifier.alerts
    assert "最近一次没能下单的原因：取不到 BBB 的行情" in alert
    assert desk.remaining(p1()) is None


def test_account_read_fails_once(harness: Harness) -> None:
    """spec「读券商账户超时后重试」：30 秒以后下单。"""
    desk, sleep = _launched(harness)
    _submit(desk, p1())
    desk.broker.fail_next("account")

    _confirm(desk, p1(), at(15, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(15, 5, 30)]
    assert desk.order(p1()) is not None


def test_changed_retry_delay(harness: Harness) -> None:
    """spec「修改重试间隔」：10 秒以后再读一次账户。"""
    desk, sleep = _launched(
        harness, Desk(config=OrderFlowConfig(retry_delay=timedelta(seconds=10)))
    )
    _submit(desk, p1())
    desk.broker.fail_next("account")

    _confirm(desk, p1(), at(15, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.calls == [10]


# 提交的结果


def test_broker_rejects_the_order(harness: Harness) -> None:
    """spec「券商拒绝受理」：告警，流程结束。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())
    desk.broker.reject_next_place()

    _confirm(desk, p1())

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert desk.notifier.alerts == [f"下单被券商拒绝：买入 BBB 20 股（方案 ID {p1().plan_id}）"]


def test_not_reconciled_yet(harness: Harness) -> None:
    """spec「还没完成过对账」：等 30 秒；对账以后下单。"""
    desk = Desk()
    desk.restart(reconcile=False)
    desk, sleep = _launched(harness, desk)
    _submit(desk, p1())
    sleep.hooks.append(lambda: _reconcile(desk))

    _confirm(desk, p1(), at(15, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(15, 5, 30)]


def _reconcile(desk: Desk) -> None:
    assert desk.execution.reconcile().clean


def _make_aaa_unprotected(desk: Desk) -> None:
    desk.stops.update("AAA-STOP-1", BrokerOrderStatus.CANCELLED)
    desk.stops.reject_next("place", 2)
    protection = desk.execution.protect("AAA")
    assert protection is not None and protection.unprotected_since is not None


def test_add_waits_for_the_stop(harness: Harness) -> None:
    """spec「加仓遇到无保护仓位」：补挂成功以后下单，没有告警。"""
    desk, sleep = _launched(harness)
    _submit(desk, p4())
    _make_aaa_unprotected(desk)
    sleep.hooks.append(lambda: _protect_aaa(desk))

    _confirm(desk, p4(), at(15, 5))

    assert _end(p4()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(15, 5, 30)]
    assert [(o.symbol, o.quantity) for o in desk.broker.placed] == [("AAA", 5)]
    assert desk.notifier.alerts == []


def _protect_aaa(desk: Desk) -> None:
    protection = desk.execution.protect("AAA")
    assert protection is not None and protection.unprotected_since is None


def test_unprotected_until_the_expiry(harness: Harness) -> None:
    """spec「无保护仓位一直持续到有效期」「到有效期的告警」。"""
    desk, _ = _launched(harness)
    desk.clock.set(at(19, 59))
    _submit(desk, p4())
    _make_aaa_unprotected(desk)

    _confirm(desk, p4(), at(19, 59))

    assert _end(p4()) is PlanEnd.VOIDED
    [alert] = desk.notifier.alerts
    assert alert.startswith(f"方案作废，不会下单：加仓 AAA 5 股（方案 ID {p4().plan_id}）")
    assert "最近一次没能下单的原因：AAA 是无保护仓位" in alert
    assert desk.remaining(p4()) is None


def test_close_between_recheck_and_submit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec「再校验以后刚好收盘」：等到下一个开盘再下单，截止时刻 2026-10-15 20:00Z。"""
    desk, sleep = _launched(harness)
    plan = p1(expires_at=at(20, day=15))
    _submit(desk, plan)
    submit = desk.execution.submit
    calls: list[int] = []

    def closing(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            desk.clock.set(at(20))
            raise OutsideSessionError("刚好收盘")
        return submit(*args)

    monkeypatch.setattr(desk.execution, "submit", closing)

    _confirm(desk, plan, at(19, 59, 59))

    assert _end(plan) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(13, 30, day=15)]
    order = desk.order(plan)
    assert order is not None and order.deadline == at(20, day=15)


def test_mismatch_voids_the_opening_plan(harness: Harness) -> None:
    """spec「对账不一致时开仓方案作废」：P1 作废并告警，P2 照常下单。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())
    _submit(desk, p2())
    desk.broker.positions["CCC"] = 100
    assert not desk.execution.reconcile().clean

    _confirm(desk, p1())
    _confirm(desk, p2())

    assert _end(p1()) is PlanEnd.VOIDED
    assert _end(p2()) is PlanEnd.SUBMITTED
    [alert] = desk.notifier.alerts
    assert "最近一次对账有不一致，暂停开仓" in alert
    order = desk.order(p2())
    assert order is not None and order.quantity == 4


def test_unexpected_error_on_submit(harness: Harness) -> None:
    """spec「提交时出了意外的错」：告警一次；30 秒以后已经有订单，交给巡检。"""
    desk, sleep = _launched(harness)
    _submit(desk, p1())
    desk.broker.raise_next("place", RuntimeError("401 Unauthorized"))

    _confirm(desk, p1(), at(15, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(15, 5, 30)]
    [alert] = desk.notifier.alerts
    assert alert.startswith(f"下单出错：买入 BBB 20 股（方案 ID {p1().plan_id}）")
    order = desk.order(p1())
    assert order is not None and order.status is OrderStatus.PENDING
    assert desk.remaining(p1()) == 20


def test_store_errors_alert_once(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「再校验时状态库出错」：两次出错只告警一次，恢复以后下单。"""
    desk, sleep = _launched(harness)
    _submit(desk, p1())
    original = desk.reservations.get
    failures = [1, 1]

    def flaky(plan_id: UUID) -> Any:
        if failures:
            failures.pop()
            raise RuntimeError("数据库连不上")
        return original(plan_id)

    monkeypatch.setattr(desk.reservations, "get", flaky)

    _confirm(desk, p1(), at(15, 5))

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert sleep.wakes == [at(15, 5, 30), at(15, 6)]
    assert len(desk.notifier.alerts) == 1


# 持久化和恢复


def test_replay_does_not_repeat_steps(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """spec「持久化和恢复」「重启以后不重复写日志」：跑完的工作流再恢复一次，已经做完的步骤
    都不再做：不再请求确认、不再提交、不再写日志。"""
    desk, _ = _launched(harness)
    _submit(desk, p1())
    _confirm(desk, p1())
    assert _end(p1()) is PlanEnd.SUBMITTED
    harness.set_pending(p1())

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        before = len(caplog.records)
        DBOS._recover_pending_workflows()
        assert _end(p1()) is PlanEnd.SUBMITTED

    assert len(desk.notifier.requests) == 1
    assert desk.broker.order_count(str(p1().plan_id)) == 1
    replayed = [r for r in caplog.records[before:] if r.name.startswith("tradingagentspro")]
    assert replayed == []


class _CrashingNotifier(FakeNotifier):
    """第一次报告结果时模拟进程退出。"""

    def __init__(self) -> None:
        super().__init__()
        self.crash_on_finish = True

    def finish_confirmation(self, message_id: str, outcome: ConfirmationOutcome) -> None:
        if self.crash_on_finish:
            self.crash_on_finish = False
            raise Crash
        super().finish_confirmation(message_id, outcome)


def test_down_past_the_deadline(harness: Harness) -> None:
    """spec「停机超过确认的截止时刻」：重启以后按确认超时处理。"""
    notifier = _CrashingNotifier()
    desk = Desk(
        notifier=notifier,
        notification_config=NotificationConfig(confirmation_timeout=timedelta(seconds=1)),
    )
    desk, sleep = _launched(harness, desk)
    _submit(desk, p1())
    _until(lambda: not notifier.crash_on_finish)
    assert _status(p1()) == "PENDING"

    harness.restart(desk, sleep)

    assert _end(p1()) is PlanEnd.TIMED_OUT
    assert notifier.finished == [("M1", ConfirmationOutcome.TIMED_OUT)]
    assert len(notifier.requests) == 1
    assert desk.remaining(p1()) is None


def _crash() -> None:
    raise Crash


def test_restart_while_waiting_for_the_open(harness: Harness) -> None:
    """spec「等开盘时重启」：重启以后照样在 2026-10-15 13:30Z 下单，此前没有下单。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21))
    _submit(desk, p1())
    sleep.hooks.append(_crash)
    _confirm(desk, p1(), at(21, 5))
    _until(lambda: not sleep.hooks)
    assert _status(p1()) == "PENDING"
    assert desk.broker.placed == []

    harness.restart(desk, sleep)

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert desk.clock.now() == at(13, 30, day=15)
    assert len(desk.notifier.requests) == 1
    assert desk.broker.order_count(str(p1().plan_id)) == 1


def test_down_past_the_open(harness: Harness) -> None:
    """spec「停机期间过了开盘」：重启以后立即再校验下单。"""
    desk, sleep = _launched(harness)
    desk.clock.set(at(21))
    _submit(desk, p1())
    sleep.hooks.append(_crash)
    _confirm(desk, p1(), at(21, 5))
    _until(lambda: not sleep.hooks)

    desk.clock.set(at(14, day=15))
    harness.restart(desk, sleep)

    assert _end(p1()) is PlanEnd.SUBMITTED
    order = desk.order(p1())
    assert order is not None and order.created_at == at(14, day=15)


def test_crash_after_the_broker_accepts(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「提交以后、记下结果之前进程退出」：重启以后不再校验、不再提交。"""
    desk, sleep = _launched(harness)
    _submit(desk, p1())
    submit = desk.execution.submit
    crashed: list[bool] = []

    def crashing(*args: Any) -> Any:
        submit(*args)
        crashed.append(True)
        raise Crash

    monkeypatch.setattr(desk.execution, "submit", crashing)
    _confirm(desk, p1())
    _until(lambda: bool(crashed))
    monkeypatch.undo()

    harness.restart(desk, sleep)

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert desk.broker.order_count(str(p1().plan_id)) == 1
    assert len(desk.notifier.requests) == 1


def test_restart_while_waiting_for_confirmation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec「等确认时重启，重启以后按确认」：重启以后仍在等确认，按确认照样送达并下单，
    只有一条确认消息。

    进程内没法打断 recv 里的旧线程，所以让第一次运行在开始 recv 时退出（确认消息已经发出、
    事件已经是 waiting），见 design D15。
    """
    desk, sleep = _launched(harness)
    entered: list[bool] = []

    def crash(*args: object, **kwargs: object) -> None:
        entered.append(True)
        raise Crash

    monkeypatch.setattr(DBOS, "recv", crash)
    _submit(desk, p1())
    _until(lambda: bool(entered))
    monkeypatch.undo()

    harness.restart(desk, sleep)
    assert _stage(p1()) == Stage.WAITING
    deliver_decision(p1().plan_id, ConfirmationOutcome.CONFIRMED)

    assert _end(p1()) is PlanEnd.SUBMITTED
    assert len(desk.notifier.requests) == 1
    assert desk.broker.order_count(str(p1().plan_id)) == 1


_extra_workflows = count(1)


def _register_another_workflow() -> None:
    """程序更新：多登记一个工作流，工作流源码的哈希就变了。每次用一个新名字。"""

    def extra() -> None:
        """测试「程序更新以后恢复」时多登记的工作流。"""

    DBOS.workflow(name=f"order_flow_test.extra.{next(_extra_workflows)}")(extra)


def _recover_after_an_update(harness: Harness, *, patching: bool) -> None:
    desk = Desk()
    sleep = FakeSleep(desk)
    harness.launch(desk, sleep, patching=patching)
    desk.clock.set(at(21))
    _submit(desk, p1())
    sleep.hooks.append(_crash)
    _confirm(desk, p1(), at(21, 5))
    _until(lambda: not sleep.hooks)
    _register_another_workflow()

    harness.restart(desk, sleep, patching=patching)

    if patching:
        assert _end(p1()) is PlanEnd.SUBMITTED
    else:
        for _ in range(50):
            _pause()
        assert _status(p1()) == "PENDING"
        assert desk.broker.placed == []


def test_recover_after_an_update(harness: Harness) -> None:
    """spec「程序更新以后恢复」：打开 patching 时，工作流源码变了照样恢复。"""
    _recover_after_an_update(harness, patching=True)


def test_without_patching_an_update_strands_the_flow(harness: Harness) -> None:
    """对照：不打开 patching 时，源码变了以后不恢复，证明上一个测试测到了版本。"""
    _recover_after_an_update(harness, patching=False)
