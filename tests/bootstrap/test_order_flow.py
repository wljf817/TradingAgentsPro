"""「下单流程」接上之前的提交接口和按钮回调的测试。

场景见 openspec 的 bootstrap spec「下单流程接上之前」，做法见 add-bootstrap-wiring 的 design D9。
`/plan` 的回复经过真实的 PlanCommand，按钮经过真实的 DiscordNotifier，都不连 Discord。
"""

import asyncio
import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import SecretStr

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIds
from tests.integrations.discord.calls import FakeCall
from tests.integrations.discord.gateway import FakeGateway, FakePress
from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, USER_ID
from tradingagentspro.bootstrap.order_flow import (
    AccountPlanIntake,
    OrderFlowMissingError,
    refuse_decision,
)
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.integrations.discord import DiscordConfig, DiscordNotifier, PlanCommand
from tradingagentspro.integrations.discord.messages import button_id
from tradingagentspro.integrations.discord.plans import PlanEntry
from tradingagentspro.modules.execution import Account, BrokerUnavailableError, HeldPosition
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    DecisionHandler,
    PlanIntake,
)

NOW = datetime(2026, 10, 14, 14, tzinfo=UTC)
MISSING = "下单流程还没接上，方案没有提交"


def _account(*positions: HeldPosition) -> Account:
    return Account(
        equity=Decimal(10000),
        settled_cash=Decimal(5000),
        previous_close_equity=Decimal(10000),
        positions=positions,
        last_trade_days={},
    )


AAPL = HeldPosition(
    symbol="AAPL",
    sector="科技",
    quantity=100,
    average_cost=Decimal(180),
    stop_price=Decimal(171),
    emergency_threshold=Decimal("0.08"),
    entry_price_today=None,
)


class Reads:
    """账户数据的来源：按给定的账户回答，记下读了几次；可以安排读的时候报券商不可用。"""

    def __init__(self, account: Account, error: Exception | None = None) -> None:
        self.account = account
        self.error = error
        self.count = 0

    def __call__(self) -> Account:
        self.count += 1
        if self.error is not None:
            raise self.error
        return self.account


def _config() -> DiscordConfig:
    return DiscordConfig(
        token=SecretStr("test-token"), guild_id=GUILD_ID, channel_id=CHANNEL_ID, user_id=USER_ID
    )


def _run(intake: PlanIntake, entry: PlanEntry) -> str:
    """经过真实的 PlanCommand 处理一条本人的 /plan，返回回复。"""
    command = PlanCommand(intake, FakeIds(), sample_nyse_calendar(), _config())
    call = FakeCall(USER_ID)
    asyncio.run(command.handle(call, entry))
    [reply] = call.replies
    return reply


def test_types_satisfy_the_ports() -> None:
    """AccountPlanIntake 满足 PlanIntake，refuse_decision 满足 DecisionHandler。"""
    intake: PlanIntake = AccountPlanIntake(Reads(_account()))
    handler: DecisionHandler = refuse_decision
    assert intake is not None and handler is not None


def test_held_sector_from_account_data() -> None:
    """spec「下单流程接上之前」的表：查持仓的板块是账户数据里这只持仓的板块，没有时为没有。"""
    intake = AccountPlanIntake(Reads(_account(AAPL)))

    assert intake.held_sector("AAPL") == "科技"
    assert intake.held_sector("XYZ") is None


def test_submit_always_refuses() -> None:
    """spec「下单流程接上之前」的表：提交一律报错，不读账户。"""
    reads = Reads(_account(AAPL))
    intake = AccountPlanIntake(reads)
    entry = PlanEntry(TradeAction.CLOSE, "AAPL")

    reply = _run(intake, entry)

    assert MISSING in reply
    assert reads.count == 1  # 只有查持仓读了一次账户


def test_reduce_a_held_stock() -> None:
    """spec「减仓有持仓的股票」：查到板块，方案通过格式检查以后提交，回复提交出错。"""
    reply = _run(
        AccountPlanIntake(Reads(_account(AAPL))),
        PlanEntry(TradeAction.REDUCE, "AAPL", quantity=10),
    )

    assert reply.startswith(f"提交出错：{MISSING}")


def test_close_a_stock_not_held() -> None:
    """spec「清仓没有持仓的股票」。"""
    reply = _run(AccountPlanIntake(Reads(_account(AAPL))), PlanEntry(TradeAction.CLOSE, "XYZ"))

    assert reply == "没有 XYZ 的持仓，不能清仓；没有提交"


def test_lookup_fails_when_the_broker_is_unavailable() -> None:
    """spec「查持仓时 Alpaca 不可用」：回复查持仓出错，没有提交。"""
    reads = Reads(_account(AAPL), BrokerUnavailableError("GET /v2/account：超时"))

    reply = _run(AccountPlanIntake(reads), PlanEntry(TradeAction.CLOSE, "AAPL"))

    assert reply.startswith("查持仓出错：GET /v2/account：超时")
    assert "没有提交" in reply


def test_buy_is_not_submitted() -> None:
    """spec「买入」：回复提交出错，原因是下单流程还没接上；不读账户。"""
    reads = Reads(_account())
    entry = PlanEntry(
        TradeAction.BUY, "AAPL", sector="科技", quantity=100, limit="180.50", stop="171"
    )

    reply = _run(AccountPlanIntake(reads), entry)

    assert reply.startswith(f"提交出错：{MISSING}")
    assert reads.count == 0


def test_refuse_decision_raises() -> None:
    """spec「下单流程接上之前」的表：按钮回调一律报错。"""
    with pytest.raises(OrderFlowMissingError, match="下单流程还没接上，没有方案在等确认"):
        refuse_decision(UUID(int=1), ConfirmationOutcome.CONFIRMED)


@pytest.fixture
def notifier() -> Iterator[tuple[DiscordNotifier, FakeGateway]]:
    """接上 refuse_decision 的真实 DiscordNotifier，用替身 Gateway；测试结束时关闭。"""
    gateway = FakeGateway()
    config = DiscordConfig(
        token=SecretStr("test-token"),
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        user_id=USER_ID,
        connect_timeout=timedelta(seconds=5),
    )
    notifier = DiscordNotifier(gateway, config, FakeClock(NOW), refuse_decision)
    notifier.start()
    yield notifier, gateway
    notifier.close()
    assert [t for t in threading.enumerate() if t.name == "discord" and t.is_alive()] == []


def test_pressing_an_old_confirmation_button(
    notifier: tuple[DiscordNotifier, FakeGateway], caplog: pytest.LogCaptureFixture
) -> None:
    """spec「按下以前留下的确认按钮」：回调报错，提示没有送达，消息上的按钮还在。"""
    _, gateway = notifier
    deadline = NOW + timedelta(minutes=20)
    press = FakePress(USER_ID, button_id(UUID(int=7), ConfirmationOutcome.CONFIRMED, deadline), 99)

    with caplog.at_level(logging.ERROR):
        gateway.press(press)

    assert press.log == ["defer", "reply:没有送达，请再按一次"]
    assert gateway.edits == []
    assert "下单流程还没接上" in caplog.text
