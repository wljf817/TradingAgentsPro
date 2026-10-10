"""下单流程每一步的测试：不起 DBOS，直接调用 check_plan、OrderPlanIntake.held_sector 和 OrderFlow。

场景见 openspec 的 order-flow spec，做法见 add-order-flow 的 design D1、D2、D4 到 D8、D12 到 D15。
基准数据见 tests/workflows/order_builders.py。工作流把这些步骤串起来以后的场景在
test_order_workflow.py 里。
"""

import asyncio
import logging
import threading
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from tests.fakes.ids import FakeIds
from tests.fakes.notifier import FakeNotifier
from tests.fakes.plan_intake import passed
from tests.integrations.discord.calls import FakeCall
from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, USER_ID
from tests.workflows.order_builders import NYSE, at, p1, p2, p3, p4, quote
from tests.workflows.order_desk import Desk
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.integrations.discord import DiscordConfig, PlanCommand
from tradingagentspro.integrations.discord.plans import PlanEntry
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    OrderSide,
    OrderStatus,
)
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    ConfirmationRequest,
    PendingConfirmation,
)
from tradingagentspro.shared.trading_calendar import OutsideSessionError
from tradingagentspro.workflows.order_flow import (
    Attempt,
    AttemptKind,
    OrderFlowConfig,
    PlanEnd,
    Requested,
    Stage,
    Timing,
    check_plan,
)
from tradingagentspro.workflows.order_workflow import OrderPlanIntake

WAIT = 0.2  # 等另一个线程的最长时间（秒）；只在测试失败时才会等满


def _locked_elsewhere(lock: threading.RLock) -> bool:
    """另一个线程这时拿不拿得到锁：拿得到时立即放开。"""
    result: list[bool] = []

    def probe() -> None:
        got = lock.acquire(blocking=False)
        if got:
            lock.release()
        result.append(not got)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return result[0]


def _confirmed(desk: Desk, plan: TradePlan) -> RiskDecision:
    """提交方案：检查并预留，返回风控结果。"""
    return check_plan(desk.services, plan)


# 配置


def test_default_config() -> None:
    """spec「默认配置」：重试间隔 30 秒。"""
    assert OrderFlowConfig().retry_delay == timedelta(seconds=30)


@pytest.mark.parametrize("seconds", [0, -1])
def test_retry_delay_must_be_positive(seconds: int) -> None:
    """spec「重试间隔不大于 0」。"""
    with pytest.raises(ValidationError):
        OrderFlowConfig(retry_delay=timedelta(seconds=seconds))


# 提交方案：首次检查


def test_check_passes_and_reserves(caplog: pytest.LogCaptureFixture) -> None:
    """spec「通过后启动下单流程」的检查部分：通过，最终 20 股，BBB 预留 20 股，写 INFO。"""
    desk = Desk()

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        result = check_plan(desk.services, p1())

    assert (result.outcome, result.quantity) == (RiskOutcome.PASSED, 20)
    assert result.expires_at == at(20)
    assert desk.remaining(p1()) == 20
    assert any("风控通过，20 股" in r.getMessage() for r in caplog.records)


def test_check_rejects_when_degraded() -> None:
    """spec「风控拒绝时不启动」：降级模式下开仓方案被拒，没有预留。"""
    desk = Desk()
    desk.degrade()

    result = check_plan(desk.services, p1())

    assert result.outcome is RiskOutcome.REJECTED
    assert [v.rule for v in result.violations] == ["降级模式"]
    assert desk.reservations.get(p1().plan_id) is None


def test_check_rejects_after_the_breaker_trips() -> None:
    """spec「熔断触发后提交开仓方案」。"""
    desk = Desk()
    desk.trip_breaker()

    result = check_plan(desk.services, p1())

    assert result.outcome is RiskOutcome.REJECTED
    assert [v.rule for v in result.violations] == ["单日亏损熔断"]


def test_exit_passes_when_degraded() -> None:
    """spec「降级模式下提交退出方案」：清仓 AAA 通过，最终 10 股。"""
    desk = Desk()
    desk.degrade()

    result = check_plan(desk.services, p3())

    assert (result.outcome, result.quantity) == (RiskOutcome.PASSED, 10)


def test_check_adjusts() -> None:
    """spec「风控调整的方案」：现金 505 时调整为 10 股，原因是"已结算资金"。"""
    desk = Desk()
    desk.broker.cash = Decimal(505)

    result = check_plan(desk.services, p1())

    assert (result.outcome, result.quantity) == (RiskOutcome.ADJUSTED, 10)
    assert [v.rule for v in result.violations] == ["已结算资金"]
    assert desk.remaining(p1()) == 10


def test_check_fails_when_the_broker_is_unavailable() -> None:
    """spec「读券商账户超时」：报错，不检查、不预留。"""
    desk = Desk()
    desk.broker.fail_next("account")

    with pytest.raises(BrokerUnavailableError):
        check_plan(desk.services, p1())
    assert desk.reservations.get(p1().plan_id) is None


def test_check_waits_for_the_lock() -> None:
    """spec「别的调用拿着锁时检查等待」：那个线程放开锁之前没有预留，放开以后通过。"""
    desk = Desk()
    results: list[RiskDecision] = []
    desk.lock.acquire()
    try:
        thread = threading.Thread(target=lambda: results.append(check_plan(desk.services, p1())))
        thread.start()
        thread.join(WAIT)
        assert thread.is_alive()
        assert desk.reservations.get(p1().plan_id) is None
    finally:
        desk.lock.release()
    thread.join()
    assert results[0].quantity == 20
    assert desk.remaining(p1()) == 20


# 查持仓的板块


class _RecordingIntake(OrderPlanIntake):
    """真实的 held_sector；submit 只记下方案、返回通过，不启动工作流。"""

    def __init__(self, desk: Desk) -> None:
        super().__init__(desk.services)
        self.plans: list[TradePlan] = []

    def submit(self, plan: TradePlan) -> RiskDecision:
        self.plans.append(plan)
        return passed(plan)


def _run_plan(intake: OrderPlanIntake, entry: PlanEntry) -> str:
    """经过真实的 PlanCommand 处理一条本人的 /plan，返回回复。"""
    config = DiscordConfig(
        token=SecretStr("test-token"), guild_id=GUILD_ID, channel_id=CHANNEL_ID, user_id=USER_ID
    )
    command = PlanCommand(intake, FakeIds(), NYSE, config)
    call = FakeCall(USER_ID)
    asyncio.run(command.handle(call, entry))
    [reply] = call.replies
    return reply


def test_reduce_a_held_stock() -> None:
    """spec「减仓有持仓的股票」：查到板块"科技"，提交接口收到减仓 AAA 4 股。"""
    desk = Desk()
    intake = _RecordingIntake(desk)

    _run_plan(intake, PlanEntry(TradeAction.REDUCE, "AAA", quantity=4))

    [plan] = intake.plans
    assert (plan.action, plan.symbol, plan.sector, plan.quantity) == (
        TradeAction.REDUCE,
        "AAA",
        "科技",
        4,
    )


def test_close_a_stock_not_held() -> None:
    """spec「清仓没有持仓的股票」。"""
    desk = Desk()
    intake = _RecordingIntake(desk)

    reply = _run_plan(intake, PlanEntry(TradeAction.CLOSE, "XYZ"))

    assert reply == "没有 XYZ 的持仓，不能清仓；没有提交"
    assert intake.plans == []


def test_lookup_when_the_broker_is_unavailable() -> None:
    """spec「查持仓时券商不可用」：回复查持仓出错，没有提交。"""
    desk = Desk()
    desk.broker.fail_next("account")
    intake = _RecordingIntake(desk)

    reply = _run_plan(intake, PlanEntry(TradeAction.CLOSE, "AAA"))

    assert reply.startswith("查持仓出错：")
    assert intake.plans == []


def test_rejected_plan_starts_no_workflow() -> None:
    """spec「风控拒绝时不启动」：拒绝时提交接口直接返回，不碰 DBOS（这里没有起 DBOS）。"""
    desk = Desk()
    desk.degrade()

    result = OrderPlanIntake(desk.services).submit(p1())

    assert result.outcome is RiskOutcome.REJECTED


# 请求人工确认


def test_request_confirmation(caplog: pytest.LogCaptureFixture) -> None:
    """spec「请求人工确认」：截止时刻 15:30Z，等 1800 秒；写 INFO。"""
    desk = Desk()
    decision = _confirmed(desk, p1())

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        requested = desk.flow.request_confirmation(p1(), decision)

    assert requested == Requested(message_id="M1", deadline=at(15, 30), wait_seconds=1800)
    [request] = desk.notifier.requests
    assert request.plan == p1() and request.decision == decision
    assert any("请求确认" in r.getMessage() for r in caplog.records)


def test_four_actions_request_confirmation() -> None:
    """spec「四种动作都请求确认」：买入 20、减仓 4、清仓 6（扣除 P2 的卖出预留）、加仓 5。"""
    desk = Desk()

    for plan in (p1(), p2(), p3(), p4()):
        desk.flow.request_confirmation(plan, _confirmed(desk, plan))

    assert [(r.plan.action, r.decision.quantity) for r in desk.notifier.requests] == [
        (TradeAction.BUY, 20),
        (TradeAction.REDUCE, 4),
        (TradeAction.CLOSE, 6),
        (TradeAction.ADD, 5),
    ]
    assert {r.deadline for r in desk.notifier.requests} == {at(15, 30)}


def test_confirmation_that_cannot_be_sent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「确认请求发不出去」：返回没有，写一条 ERROR 写明方案 ID 和原因。"""
    desk = Desk()
    decision = _confirmed(desk, p1())
    desk.notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        requested = desk.flow.request_confirmation(p1(), decision)

    assert requested is None
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert str(p1().plan_id) in record.getMessage()
    assert "测试安排的失败" in record.getMessage()


class _ProbingNotifier(FakeNotifier):
    """每次请求确认和推送告警时，看另一个线程拿不拿得到锁，记进 locked。"""

    def __init__(self) -> None:
        super().__init__()
        self.lock: threading.RLock | None = None
        self.locked: list[bool] = []

    def request_confirmation(self, request: ConfirmationRequest) -> PendingConfirmation:
        self._probe()
        return super().request_confirmation(request)

    def alert(self, text: str) -> None:
        self._probe()
        super().alert(text)

    def _probe(self) -> None:
        assert self.lock is not None
        self.locked.append(_locked_elsewhere(self.lock))


def test_no_lock_while_requesting_confirmation() -> None:
    """spec「请求确认时不拿锁」。"""
    notifier = _ProbingNotifier()
    desk = Desk(notifier=notifier)
    notifier.lock = desk.lock

    desk.flow.request_confirmation(p1(), _confirmed(desk, p1()))

    assert notifier.locked == [False]


# 确认的结果


def test_settle_confirmed(caplog: pytest.LogCaptureFixture) -> None:
    """spec「确认的结果」的表：确认以后写 INFO，预留不动。"""
    desk = Desk()
    decision = _confirmed(desk, p1())

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        desk.flow.settle(p1(), decision, Stage.CONFIRMED, "M1")

    assert desk.remaining(p1()) == 20
    assert any("已确认" in r.getMessage() for r in caplog.records)
    assert desk.notifier.finished == []


def test_settle_rejected() -> None:
    """spec「人工拒绝」：释放全部预留，不告警。"""
    desk = Desk()
    decision = _confirmed(desk, p1())

    desk.flow.settle(p1(), decision, Stage.REJECTED, "M1")

    assert desk.remaining(p1()) is None
    assert desk.notifier.alerts == [] and desk.notifier.finished == []


def test_settle_timed_out() -> None:
    """spec「确认超时」：报告确认消息已超时，释放全部预留，不告警。"""
    desk = Desk()
    decision = _confirmed(desk, p1())

    desk.flow.settle(p1(), decision, Stage.TIMED_OUT, "M1")

    assert desk.notifier.finished == [("M1", ConfirmationOutcome.TIMED_OUT)]
    assert desk.remaining(p1()) is None
    assert desk.notifier.alerts == []


def test_settle_unsent() -> None:
    """spec「确认请求发不出去」：按确认超时释放全部预留。"""
    desk = Desk()
    decision = _confirmed(desk, p1())

    desk.flow.settle(p1(), decision, Stage.UNSENT, None)

    assert desk.remaining(p1()) is None
    assert desk.notifier.finished == [] and desk.notifier.alerts == []


# 等开盘


def test_timing_in_session() -> None:
    """spec「等开盘」的表：盘中立即做。"""
    desk = Desk()

    assert desk.flow.timing(p1(), _confirmed(desk, p1())) == Timing(expired=False, wait_seconds=0)


def test_timing_after_the_close(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘后确认，下一个交易日开盘下单」：盘后等到 2026-10-15 13:30Z，写 INFO。"""
    desk = Desk()
    desk.clock.set(at(21))
    decision = _confirmed(desk, p1())
    assert decision.expires_at == at(20, day=15)
    desk.clock.set(at(21, 5))

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        timing = desk.flow.timing(p1(), decision)

    wait = (at(13, 30, day=15) - at(21, 5)).total_seconds()
    assert timing == Timing(expired=False, wait_seconds=wait)
    assert any("2026-10-15 13:30 UTC" in r.getMessage() for r in caplog.records)


def test_timing_waits_over_the_weekend() -> None:
    """spec「周五盘后确认，等到周一开盘」。"""
    desk = Desk()
    desk.clock.set(at(21, day=16))
    decision = _confirmed(desk, p1())
    assert decision.expires_at == at(20, day=19)
    desk.clock.set(at(21, 5, day=16))

    timing = desk.flow.timing(p1(), decision)

    assert timing.wait_seconds == (at(13, 30, day=19) - at(21, 5, day=16)).total_seconds()


def test_timing_before_the_open() -> None:
    """spec「盘前确认等当天开盘」。"""
    desk = Desk()
    desk.clock.set(at(12))
    decision = _confirmed(desk, p1())
    desk.clock.set(at(12, 5))

    assert desk.flow.timing(p1(), decision) == Timing(expired=False, wait_seconds=85 * 60)


@pytest.mark.parametrize("now", [at(20), at(20, 5), at(14, day=15)])
def test_timing_at_or_after_the_expiry(now: object) -> None:
    """spec「等开盘」的表第一行：不早于生效的有效期就作废，不论在不在盘中。"""
    desk = Desk()
    decision = _confirmed(desk, p1())
    desk.clock.set(now)  # type: ignore[arg-type]

    assert desk.flow.timing(p1(), decision) == Timing(expired=True, wait_seconds=0)


# 下单前再校验和提交


def _attempt(desk: Desk, plan: TradePlan, *, alert_error: bool = True) -> Attempt:
    return desk.flow.attempt(plan, _decision_of(desk, plan), alert_error=alert_error)


def _decision_of(desk: Desk, plan: TradePlan) -> RiskDecision:
    """这个方案首次检查的结果（预留里的）。"""
    reservation = desk.reservations.get(plan.plan_id)
    assert reservation is not None
    return reservation.decision


def test_attempt_submits(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘中确认后立即下单」：券商收到 P1 的订单，买入 BBB 20 股，限价 50；写日志。"""
    desk = Desk()
    _confirmed(desk, p1())

    with caplog.at_level(logging.INFO, logger="tradingagentspro.workflows.order_flow"):
        attempt = _attempt(desk, p1())

    assert attempt == Attempt(AttemptKind.SUBMITTED)
    [placed] = desk.broker.placed
    assert (placed.client_order_id, placed.side, placed.symbol) == (
        str(p1().plan_id),
        OrderSide.BUY,
        "BBB",
    )
    assert (placed.quantity, placed.limit_price) == (20, Decimal(50))
    assert desk.remaining(p1()) == 20
    messages = [r.getMessage() for r in caplog.records]
    assert any("再校验通过，20 股" in m for m in messages)
    assert any("订单已提交，委托 20 股" in m for m in messages)
    assert desk.notifier.alerts == []


def test_attempt_with_an_existing_order() -> None:
    """spec「下单前再校验和提交」第 1 步：已经有订单就不再校验、不再提交，也不读账户。"""
    desk = Desk()
    _confirmed(desk, p1())
    _attempt(desk, p1())
    desk.forget_calls()

    assert _attempt(desk, p1()) == Attempt(AttemptKind.SUBMITTED)
    assert desk.broker.calls == []
    assert desk.source.requests == []


def test_attempt_adjusts_without_confirming_again() -> None:
    """spec「再校验调整后照常下单」：现金变为 505，下单 10 股，预留 10 股，没有新的确认。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.broker.cash = Decimal(505)

    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED

    assert desk.broker.placed[0].quantity == 10
    assert desk.remaining(p1()) == 10
    assert desk.notifier.requests == []


def test_attempt_voids_on_a_price_gap() -> None:
    """spec「开盘跳空作废」「再校验拒绝的告警」：作废、释放、告警写明每条规则；没有订单。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.source.put(quote("BBB", "53", "49.5"))

    assert _attempt(desk, p1()).kind is AttemptKind.VOIDED

    assert desk.remaining(p1()) is None
    assert desk.order(p1()) is None
    [alert] = desk.notifier.alerts
    assert alert.startswith(f"方案作废，不会下单：买入 BBB 20 股（方案 ID {p1().plan_id}）")
    assert "- 价格偏离：实时价 53 偏离限价 50 超过允许的 0.03" in alert


def test_attempt_voids_an_opening_plan_when_degraded() -> None:
    """spec「降级时开仓方案作废，退出方案照常下单」。"""
    desk = Desk()
    _confirmed(desk, p1())
    _confirmed(desk, p2())
    desk.degrade()

    assert _attempt(desk, p1()).kind is AttemptKind.VOIDED
    assert _attempt(desk, p2()).kind is AttemptKind.SUBMITTED

    assert "降级模式" in desk.notifier.alerts[0]
    assert desk.order(p1()) is None
    order = desk.order(p2())
    assert order is not None and (order.side, order.quantity) == (OrderSide.SELL, 4)


def test_attempt_voids_an_opening_plan_after_the_breaker_trips() -> None:
    """spec「熔断触发后开仓方案作废」。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.trip_breaker()

    assert _attempt(desk, p1()).kind is AttemptKind.VOIDED
    assert "单日亏损熔断" in desk.notifier.alerts[0]
    assert desk.order(p1()) is None


def test_attempt_before_the_first_trade(caplog: pytest.LogCaptureFixture) -> None:
    """spec「刚开盘还没有成交」：没有当天的最新价，WARNING，等重试。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.source.put(quote("BBB", None, "49.5"))

    with caplog.at_level(logging.WARNING):
        attempt = _attempt(desk, p1())

    assert attempt == Attempt(AttemptKind.RETRY, "BBB 还没有当天的最新价")
    assert desk.order(p1()) is None
    assert any("BBB 还没有当天的最新价" in r.getMessage() for r in caplog.records)
    assert desk.remaining(p1()) == 20


def test_attempt_when_quotes_are_unavailable() -> None:
    """spec「行情一直不可用到有效期」的一次：取不到 BBB 的行情，等重试。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.source.failing = True

    assert _attempt(desk, p1()) == Attempt(AttemptKind.RETRY, "取不到 BBB 的行情")
    assert desk.order(p1()) is None


def test_exit_does_not_need_its_price() -> None:
    """spec「退出方案不需要这只股票的最新价」：行情不可用时 P2 照常下单。"""
    desk = Desk()
    _confirmed(desk, p2())
    desk.source.failing = True

    assert _attempt(desk, p2()).kind is AttemptKind.SUBMITTED
    order = desk.order(p2())
    assert order is not None and order.quantity == 4


def test_attempt_when_the_account_cannot_be_read(caplog: pytest.LogCaptureFixture) -> None:
    """spec「读券商账户超时后重试」。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.broker.fail_next("account")

    with caplog.at_level(logging.WARNING):
        attempt = _attempt(desk, p1())

    assert attempt.kind is AttemptKind.RETRY
    assert attempt.reason is not None and attempt.reason.startswith("读券商账户失败")
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED


def test_attempt_before_any_reconciliation() -> None:
    """spec「还没完成过对账」：等重试；对账以后下单。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.restart(reconcile=False)

    attempt = _attempt(desk, p1())

    assert attempt.kind is AttemptKind.RETRY
    assert attempt.reason is not None and "对账" in attempt.reason
    assert desk.broker.placed == []
    assert desk.execution.reconcile().clean
    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED


def test_attempt_with_another_order_waiting() -> None:
    """spec「同一股票有还没提交出去的订单」：P2 没提交出去时 P3 等重试；P2 成交以后卖 6 股。"""
    desk = Desk()
    _confirmed(desk, p2())
    assert _confirmed(desk, p3()).quantity == 6
    assert _attempt(desk, p2()).kind is AttemptKind.SUBMITTED
    p2_order = desk.order(p2())
    assert p2_order is not None and p2_order.status is OrderStatus.PENDING

    attempt = _attempt(desk, p3())

    assert attempt.kind is AttemptKind.RETRY
    assert desk.order(p3()) is None
    # 盘中巡检：止损单让出股数以后提交 P2，P2 全部成交，上报成交
    desk.stops.settle()
    desk.execution.track(p2().plan_id)
    desk.broker.update(str(p2().plan_id), BrokerOrderStatus.FILLED, 4, "110")
    desk.broker.positions["AAA"] = 6
    desk.execution.track(p2().plan_id)
    desk.risk.record_fill(p2().plan_id, 4)
    assert _attempt(desk, p3()).kind is AttemptKind.SUBMITTED
    order = desk.order(p3())
    assert order is not None and order.quantity == 6


def _make_aaa_unprotected(desk: Desk) -> None:
    """AAA 的止损单被撤销，重挂两次都被拒：AAA 是无保护仓位。"""
    desk.stops.update("AAA-STOP-1", BrokerOrderStatus.CANCELLED)
    desk.stops.reject_next("place", 2)
    protection = desk.execution.protect("AAA")
    assert protection is not None and protection.unprotected_since is not None


def test_add_waits_for_an_unprotected_position() -> None:
    """spec「加仓遇到无保护仓位」：等重试，不告警；补挂成功以后下单。"""
    desk = Desk()
    _confirmed(desk, p4())
    _make_aaa_unprotected(desk)

    attempt = _attempt(desk, p4())

    assert attempt.kind is AttemptKind.RETRY
    assert attempt.reason is not None and "AAA 是无保护仓位" in attempt.reason
    assert desk.notifier.alerts == []
    desk.execution.protect("AAA")
    assert _attempt(desk, p4()).kind is AttemptKind.SUBMITTED
    assert [(o.symbol, o.quantity) for o in desk.broker.placed] == [("AAA", 5)]


def test_attempt_after_the_close() -> None:
    """再校验时已经收盘：回到等开盘。"""
    desk = Desk()
    decision = check_plan(desk.services, p1(expires_at=at(20, day=15)))
    desk.clock.set(at(20))

    attempt = desk.flow.attempt(p1(expires_at=at(20, day=15)), decision, alert_error=True)

    assert attempt.kind is AttemptKind.NEXT_OPEN
    assert desk.broker.placed == []


def test_submit_right_at_the_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「再校验以后刚好收盘」：提交时不在交易时段，回到等开盘，预留不动。"""
    desk = Desk()
    plan = p1(expires_at=at(20, day=15))
    decision = check_plan(desk.services, plan)

    def closed(*args: object) -> None:
        raise OutsideSessionError("刚好收盘")

    monkeypatch.setattr(desk.execution, "submit", closed)

    assert desk.flow.attempt(plan, decision, alert_error=True).kind is AttemptKind.NEXT_OPEN
    assert desk.remaining(plan) == 20


def test_mismatch_voids_opening_plans() -> None:
    """spec「对账不一致时开仓方案作废」：P1 作废、释放、告警；P2 照常下单。"""
    desk = Desk()
    _confirmed(desk, p1())
    _confirmed(desk, p2())
    desk.broker.positions["CCC"] = 100
    assert not desk.execution.reconcile().clean

    assert _attempt(desk, p1()).kind is AttemptKind.VOIDED
    assert _attempt(desk, p2()).kind is AttemptKind.SUBMITTED

    assert desk.remaining(p1()) is None
    [alert] = desk.notifier.alerts
    assert "最近一次对账有不一致，暂停开仓" in alert
    assert desk.order(p2()) is not None


def test_broker_rejects_the_order() -> None:
    """spec「券商拒绝受理」：订单已拒绝，告警，预留留给巡检释放。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.broker.reject_next_place()

    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED

    order = desk.order(p1())
    assert order is not None and order.status is OrderStatus.REJECTED
    assert desk.notifier.alerts == [f"下单被券商拒绝：买入 BBB 20 股（方案 ID {p1().plan_id}）"]
    assert desk.remaining(p1()) == 20


def test_broker_times_out_on_submit() -> None:
    """spec「提交时券商超时」：订单状态未知，交给巡检，不告警。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.broker.fail_next("place")

    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED

    order = desk.order(p1())
    assert order is not None and order.status is OrderStatus.UNKNOWN
    assert desk.notifier.alerts == []


def test_unexpected_error_on_submit(caplog: pytest.LogCaptureFixture) -> None:
    """spec「提交时出了意外的错」：ERROR 带异常栈，告警；下一次已经有订单，交给巡检。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.broker.raise_next("place", RuntimeError("401 Unauthorized"))

    with caplog.at_level(logging.ERROR):
        attempt = _attempt(desk, p1())

    assert attempt.kind is AttemptKind.RETRY and attempt.alerted
    assert attempt.reason == "RuntimeError: 401 Unauthorized"
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert record.exc_info is not None
    [alert] = desk.notifier.alerts
    assert alert.startswith(f"下单出错：买入 BBB 20 股（方案 ID {p1().plan_id}）")
    assert "RuntimeError: 401 Unauthorized" in alert and "每 30 秒重试一次" in alert
    order = desk.order(p1())
    assert order is not None and order.status is OrderStatus.PENDING
    assert _attempt(desk, p1()) == Attempt(AttemptKind.SUBMITTED)
    assert desk.remaining(p1()) == 20


def test_store_error_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「再校验时状态库出错」：ERROR，alert_error 为真才告警；恢复以后下单。"""
    desk = Desk()
    decision = _confirmed(desk, p1())
    original = desk.reservations.get

    def broken(plan_id: UUID) -> object:
        raise RuntimeError("数据库连不上")

    monkeypatch.setattr(desk.reservations, "get", broken)
    first = desk.flow.attempt(p1(), decision, alert_error=True)
    second = desk.flow.attempt(p1(), decision, alert_error=False)
    monkeypatch.setattr(desk.reservations, "get", original)
    third = desk.flow.attempt(p1(), decision, alert_error=False)

    assert (first.kind, first.alerted) == (AttemptKind.RETRY, True)
    assert (second.kind, second.alerted) == (AttemptKind.RETRY, False)
    assert len(desk.notifier.alerts) == 1
    assert third.kind is AttemptKind.SUBMITTED


def test_no_lock_between_recheck_and_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec「再校验和提交之间别人拿不到锁」：提交时别的线程拿不到锁。"""
    desk = Desk()
    _confirmed(desk, p1())
    submit = desk.execution.submit
    seen: list[bool] = []

    def probing(plan: TradePlan, decision: RiskDecision) -> object:
        seen.append(_locked_elsewhere(desk.lock))
        return submit(plan, decision)

    monkeypatch.setattr(desk.execution, "submit", probing)

    assert _attempt(desk, p1()).kind is AttemptKind.SUBMITTED
    assert seen == [True]


def test_alerts_are_sent_outside_the_lock() -> None:
    """spec「执行模块和风控的调用串行」：推送告警时不拿锁。"""
    notifier = _ProbingNotifier()
    desk = Desk(notifier=notifier)
    notifier.lock = desk.lock
    _confirmed(desk, p1())
    desk.source.put(quote("BBB", "53", "49.5"))

    assert _attempt(desk, p1()).kind is AttemptKind.VOIDED
    assert notifier.locked == [False]


def test_alert_failure_does_not_stop_the_flow(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」：写错误日志，照常作废。"""
    desk = Desk()
    _confirmed(desk, p1())
    desk.source.put(quote("BBB", "53", "49.5"))
    desk.notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        assert _attempt(desk, p1()).kind is AttemptKind.VOIDED

    assert desk.remaining(p1()) is None
    assert any("告警没有发出去" in r.getMessage() for r in caplog.records)


# 到有效期作废


def test_void_expired_releases_and_alerts() -> None:
    """spec「到有效期的告警」：释放、告警写明有效期和最近一次没能下单的原因。"""
    desk = Desk()
    decision = _confirmed(desk, p4())
    desk.clock.set(at(20))

    end = desk.flow.void_expired(p4(), decision, "AAA 是无保护仓位，补挂成功之前不能买入")

    assert end is PlanEnd.VOIDED
    assert desk.remaining(p4()) is None
    [alert] = desk.notifier.alerts
    assert alert.startswith(f"方案作废，不会下单：加仓 AAA 5 股（方案 ID {p4().plan_id}）")
    assert "到了有效期 2026-10-14 20:00 UTC 还没下单" in alert
    assert "最近一次没能下单的原因：AAA 是无保护仓位" in alert


def test_void_expired_without_a_failure() -> None:
    """spec「确认时已经过了有效期」：没有没能下单的原因时只写有效期。"""
    desk = Desk()
    decision = _confirmed(desk, p1())
    desk.clock.set(at(20, 5))

    assert desk.flow.void_expired(p1(), decision, None) is PlanEnd.VOIDED
    assert "最近一次" not in desk.notifier.alerts[0]


def test_void_expired_with_an_order_hands_it_over() -> None:
    """spec「提交的结果」：作废时已经有订单，不释放、不告警，交给巡检。"""
    desk = Desk()
    decision = _confirmed(desk, p1())
    desk.broker.raise_next("place", RuntimeError("401 Unauthorized"))
    _attempt(desk, p1())
    desk.notifier.alerts.clear()
    desk.clock.set(at(20))

    assert desk.flow.void_expired(p1(), decision, "RuntimeError: 401") is PlanEnd.SUBMITTED
    assert desk.remaining(p1()) == 20
    assert desk.notifier.alerts == []
