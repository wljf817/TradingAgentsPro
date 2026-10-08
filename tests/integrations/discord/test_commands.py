"""PlanCommand 的测试：只接受本人、先应答再提交、查持仓、方案 ID、提交出错和超时、回复和日志。

用替身命令、替身提交接口和替身 ID，不连 Discord。每个测试对应 openspec 的 manual-plans spec
里的一个场景，线程和超时见 add-manual-plan-entry 的 design D9。
"""

import asyncio
import logging
import threading
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.ids import FakeIds
from tests.fakes.plan_intake import WAIT_SECONDS, FakePlanIntake
from tests.integrations.discord.calls import FakeCall
from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, OTHER_USER_ID, USER_ID
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.integrations.discord import DiscordConfig, PlanCommand
from tradingagentspro.integrations.discord.commands import CommandCall
from tradingagentspro.integrations.discord.plans import (
    PlanEntry,
    decision_reply,
    lookup_error_reply,
    no_position_reply,
    refusal_reply,
    submit_error_reply,
    timeout_reply,
)
from tradingagentspro.modules.notification import PlanIntake

BUY, ADD, REDUCE, CLOSE = TradeAction.BUY, TradeAction.ADD, TradeAction.REDUCE, TradeAction.CLOSE
U1, U2 = UUID(int=1), UUID(int=2)
FAST = timedelta(milliseconds=200)  # 测试里的提交超时，让超时的场景跑得快
LOGGER = "tradingagentspro.integrations.discord.commands"

BUY_AAPL = PlanEntry(
    action=BUY, symbol="AAPL", sector="科技", quantity=100, limit="180.50", stop="171"
)
REDUCE_AAPL = PlanEntry(action=REDUCE, symbol="AAPL", quantity=60)
CLOSE_AAPL = PlanEntry(action=CLOSE, symbol="AAPL")


class Setup:
    """一个 PlanCommand 和它的替身：提交接口（AAPL 有持仓，板块科技）和 ID 生成端口。"""

    def __init__(self, submit_timeout: timedelta = timedelta(seconds=60)) -> None:
        self.log: list[str] = []
        self.intake = FakePlanIntake({"AAPL": "科技"}, self.log)
        self.ids = FakeIds()
        config = DiscordConfig(
            token=SecretStr("test-token"),
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            user_id=USER_ID,
            submit_timeout=submit_timeout,
        )
        checked: PlanIntake = self.intake  # 让 mypy 检查替身满足端口
        self.command = PlanCommand(checked, self.ids, sample_nyse_calendar(), config)

    def call(self, user_id: int = USER_ID) -> FakeCall:
        return FakeCall(user_id, self.log)

    def run(self, entry: PlanEntry, user_id: int = USER_ID) -> FakeCall:
        """在一个新的事件循环上处理一条命令，等它回复完。"""
        call = self.call(user_id)
        asyncio.run(self.command.handle(call, entry))
        return call

    def start(self, entry: PlanEntry) -> tuple[FakeCall, threading.Thread]:
        """在另一个线程的事件循环上处理一条命令，不等它。"""
        call = self.call()
        thread = threading.Thread(target=asyncio.run, args=(self.command.handle(call, entry),))
        thread.start()
        return call, thread


def _decision(
    outcome: RiskOutcome, quantity: int | None, *violations: tuple[str, str]
) -> RiskDecision:
    return RiskDecision(
        outcome=outcome,
        quantity=quantity,
        emergency_threshold=None,
        expires_at=None,
        violations=tuple(RiskViolation(rule=r, message=m) for r, m in violations),
    )


def _messages(caplog: pytest.LogCaptureFixture, level: int = logging.INFO) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno >= level]


def _until(check: threading.Event) -> None:
    assert check.wait(WAIT_SECONDS)


@pytest.fixture(autouse=True)
def _info_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """命令的处理结果写在 INFO 级别；测试结束时不留下运行中的工作线程。"""
    caplog.set_level(logging.INFO, logger=LOGGER)
    yield
    workers = [t for t in threading.enumerate() if t.name.startswith("asyncio_")]
    assert workers == []


def test_fake_call_satisfies_the_protocol() -> None:
    call: CommandCall = FakeCall(USER_ID)
    assert call.user_id == USER_ID


# 只接受本人


def test_someone_else_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    """spec「别人运行命令」：立即回复拒绝，不取 ID、不提交，写警告日志。"""
    setup = Setup()

    call = setup.run(BUY_AAPL, user_id=OTHER_USER_ID)

    assert call.log == [f"refuse:{refusal_reply(USER_ID)}"]
    assert (setup.ids.issued, setup.intake.looked_up, setup.intake.submitted) == (0, [], [])
    [warning] = _messages(caplog, logging.WARNING)
    assert str(OTHER_USER_ID) in warning


# 应答和处理的先后


def test_defer_before_lookup_and_submit() -> None:
    """spec「先应答再提交」：查持仓和提交在应答之后，不在事件循环所在的线程里。"""
    setup = Setup()

    call = setup.run(REDUCE_AAPL)

    assert [entry.split(":")[0] for entry in call.log] == [
        "defer",
        "held_sector",
        "submit",
        "reply",
    ]
    assert len(setup.intake.threads) == 2
    assert all(t is not threading.main_thread() for t in setup.intake.threads)


def test_other_interactions_go_on_while_submitting() -> None:
    """spec「提交期间照常处理别的交互」：提交停住时，别人的命令和事件循环上的别的协程照常完成。"""
    setup = Setup()
    setup.intake.hold = threading.Event()
    mine, other = setup.call(), FakeCall(OTHER_USER_ID)

    async def scenario() -> None:
        pending = asyncio.create_task(setup.command.handle(mine, REDUCE_AAPL))
        for _ in range(WAIT_SECONDS * 100):
            if setup.intake.submitted:
                break
            await asyncio.sleep(0.01)
        assert setup.intake.submitted
        await asyncio.wait_for(setup.command.handle(other, BUY_AAPL), 1)
        await asyncio.wait_for(asyncio.sleep(0), 1)
        assert not pending.done()
        assert setup.intake.hold is not None
        setup.intake.hold.set()
        await asyncio.wait_for(pending, WAIT_SECONDS)

    asyncio.run(scenario())

    assert other.log == [f"refuse:{refusal_reply(USER_ID)}"]
    assert len(mine.replies) == 1


def test_defer_fails(caplog: pytest.LogCaptureFixture) -> None:
    """spec「延迟应答失败」：写错误日志，不取 ID、不提交。"""
    setup = Setup()
    call = setup.call()
    call.fail_defer = True

    asyncio.run(setup.command.handle(call, BUY_AAPL))

    assert call.log == ["defer"]
    assert (setup.ids.issued, setup.intake.submitted) == (0, [])
    assert _messages(caplog, logging.ERROR)


# 减仓和清仓的板块


def test_reduce_takes_the_held_sector() -> None:
    """spec「市价减仓」：板块取持仓的，股票代码先转大写再查。"""
    setup = Setup()

    setup.run(PlanEntry(action=REDUCE, symbol="aapl", quantity=60))

    assert setup.intake.looked_up == ["AAPL"]
    [plan] = setup.intake.submitted
    assert (plan.plan_id, plan.sector, plan.quantity) == (U1, "科技", 60)


def test_no_position() -> None:
    """spec「没有持仓」：回复没有持仓，不提交。"""
    setup = Setup()

    call = setup.run(PlanEntry(action=CLOSE, symbol="MSFT"))

    assert call.replies == [no_position_reply(CLOSE, "MSFT")]
    assert setup.intake.looked_up == ["MSFT"]
    assert setup.intake.submitted == []


def test_lookup_fails(caplog: pytest.LogCaptureFixture) -> None:
    """spec「查持仓出错」：回复出错和原因，不提交，写错误日志。"""
    setup = Setup()
    error = RuntimeError("数据库暂时连不上")
    setup.intake.lookup_error = error

    call = setup.run(REDUCE_AAPL)

    assert call.replies == [lookup_error_reply(error)]
    assert setup.intake.submitted == []
    assert _messages(caplog, logging.ERROR)


def test_buy_does_not_look_up() -> None:
    """spec「买入不查持仓」：板块是填的。"""
    setup = Setup()

    setup.run(BUY_AAPL)

    assert setup.intake.looked_up == []
    [plan] = setup.intake.submitted
    assert plan.sector == "科技"


def test_blank_symbol_on_close_is_a_format_error() -> None:
    """spec「股票代码和板块的空白」：清仓的股票只有空格时不查持仓，回复格式错误。"""
    setup = Setup()

    call = setup.run(PlanEntry(action=CLOSE, symbol="   "))

    assert setup.intake.looked_up == []
    assert setup.intake.submitted == []
    [reply] = call.replies
    assert reply.startswith("方案格式不对")
    assert "股票不能为空" in reply


# 方案 ID


def test_same_command_twice_gets_two_plan_ids() -> None:
    """spec「同样的命令运行两次」。"""
    setup = Setup()

    setup.run(BUY_AAPL)
    setup.run(BUY_AAPL)

    first, second = setup.intake.submitted
    assert (first.plan_id, second.plan_id) == (U1, U2)
    assert first.model_copy(update={"plan_id": U2}) == second


# 提交接口


def test_submit_fails(caplog: pytest.LogCaptureFixture) -> None:
    """spec「提交接口抛错」：回复出错和方案可能已经提交，错误日志有方案 ID。"""
    setup = Setup()
    error = RuntimeError("数据库暂时连不上")
    setup.intake.submit_error = error

    call = setup.run(BUY_AAPL)

    assert call.replies == [submit_error_reply(U1, error)]
    [message] = _messages(caplog, logging.ERROR)
    assert str(U1) in message


def test_submit_times_out(caplog: pytest.LogCaptureFixture) -> None:
    """spec「提交超时」「改提交超时」：到提交超时就回复，之后返回的结果只写日志。"""
    setup = Setup(submit_timeout=FAST)
    hold = setup.intake.hold = threading.Event()

    call, thread = setup.start(BUY_AAPL)
    _until(call.replied)

    assert call.replies == [timeout_reply(U1, FAST)]
    assert not setup.intake.returned.is_set()
    hold.set()
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive()
    assert len(call.replies) == 1
    assert any(str(U1) in m and "风控通过" in m for m in _messages(caplog))


# 回复


def test_passed_reply() -> None:
    """spec「风控通过」。"""
    setup = Setup()

    call = setup.run(BUY_AAPL)

    [plan] = setup.intake.submitted
    assert call.replies == [decision_reply(plan, _passed(plan), CHANNEL_ID)]
    assert call.replies[0].startswith("风控通过：买入 AAPL 100 股")


def _passed(plan: TradePlan) -> RiskDecision:
    return RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=plan.quantity or 100,
        emergency_threshold=None,
        expires_at=None,
        violations=(),
    )


def test_adjusted_reply() -> None:
    """spec「风控调整」。"""
    setup = Setup()
    setup.intake.decide = lambda _: _decision(RiskOutcome.ADJUSTED, 120, ("单笔计划风险", "超"))

    call = setup.run(
        PlanEntry(action=ADD, symbol="MSFT", sector="科技", quantity=200, limit="410", stop="395")
    )

    assert call.replies[0].startswith("风控调整：加仓 MSFT 200 股 → 120 股")


def test_rejected_reply() -> None:
    """spec「风控拒绝」。"""
    setup = Setup()
    setup.intake.decide = lambda _: _decision(
        RiskOutcome.REJECTED, None, ("降级模式", "系统处于降级模式，暂停开仓")
    )

    call = setup.run(BUY_AAPL)

    assert call.replies[0].splitlines()[:2] == [
        "风控拒绝：买入 AAPL 100 股，不会下单",
        "- 降级模式：系统处于降级模式，暂停开仓",
    ]


def test_close_reply_takes_the_decision_quantity() -> None:
    """spec「清仓的股数取风控结果」。"""
    setup = Setup()

    call = setup.run(CLOSE_AAPL)

    assert call.replies[0].startswith("风控通过：清仓 AAPL 100 股")


# 回复发不出去


def test_reply_fails(caplog: pytest.LogCaptureFixture) -> None:
    """spec「回复失败」：写错误日志，有方案 ID 和回复的内容；只提交一次。"""
    setup = Setup()
    call = setup.call()
    call.fail_reply = True

    asyncio.run(setup.command.handle(call, BUY_AAPL))

    [message] = _messages(caplog, logging.ERROR)
    assert str(U1) in message
    assert "风控通过：买入 AAPL 100 股" in message
    assert len(setup.intake.submitted) == 1


# 日志


def test_format_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「格式错误的日志」。"""
    setup = Setup()
    entry = PlanEntry(
        action=BUY, symbol="AAPL", sector="科技", quantity=100, limit="abc", stop="171"
    )

    call = setup.run(entry)

    assert call.replies[0].startswith("方案格式不对")
    [message] = _messages(caplog)
    assert all(part in message for part in (str(USER_ID), "buy", str(U1), "格式错误"))


def test_decision_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """spec「风控结果的日志」。"""
    setup = Setup()

    setup.run(CLOSE_AAPL)

    [message] = _messages(caplog)
    assert all(part in message for part in (str(USER_ID), "close", str(U1), "风控通过", "100 股"))
