"""连真实 Discord 的冒烟测试：验证替身只能照文档假设的行为，比如应答、改消息、@ 和按钮。

见 add-discord-notify 的 design D10。默认不运行，用 uv run pytest -m discord_bot 运行；
DISCORD_BOT_TOKEN、DISCORD_GUILD_ID、DISCORD_CHANNEL_ID、DISCORD_USER_ID 缺一个就跳过。
最后两个测试要人在 3 分钟内按 Discord 消息上的按钮。

token 只放在环境变量和请求头里：断言里不出现 token，失败时 pytest 不会把它打出来。
"""

import logging
import os
import secrets
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from tests.fakes.plan_intake import FakePlanIntake
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.integrations.discord import (
    DiscordConfig,
    DiscordGateway,
    DiscordNotifier,
    PlanCommand,
)
from tradingagentspro.integrations.nyse_calendar import load_nyse_calendar
from tradingagentspro.integrations.random_ids import RandomIdGenerator
from tradingagentspro.integrations.system_clock import SystemClock
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    ConfirmationRequest,
    NotificationUnavailableError,
    PendingConfirmation,
)


def _config() -> DiscordConfig | None:
    try:
        return DiscordConfig.from_env(os.environ)
    except ValueError:
        return None


CONFIG = _config()
API = "https://discord.com/api/v10"
CLOCK = SystemClock()
PRESS_SECONDS = 180  # 人工部分等人按按钮的时长

pytestmark = [
    pytest.mark.discord_bot,
    pytest.mark.skipif(CONFIG is None, reason="没有 Discord 机器人的 4 个环境变量"),
]


def _gateway(config: DiscordConfig) -> DiscordGateway:
    """真实的 Gateway；/plan 交给替身提交接口，这组测试不运行 /plan。"""
    today = CLOCK.now().date()
    calendar = load_nyse_calendar(today - timedelta(days=7), today + timedelta(days=60))
    plans = PlanCommand(FakePlanIntake(), RandomIdGenerator(), calendar, config)
    return DiscordGateway(config, plans)


class Decisions:
    """按钮的回调：记下收到的方案和选择，每收到一次设一次 received。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, ConfirmationOutcome]] = []
        self.received = threading.Event()

    def __call__(self, plan_id: UUID, outcome: ConfirmationOutcome) -> None:
        self.calls.append((plan_id, outcome))
        self.received.set()


class _Collector(logging.Handler):
    """收集全部日志的文字，最后检查里面没有 token。"""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.texts: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.texts.append(self.format(record))


@pytest.fixture(scope="module")
def logs() -> Iterator[_Collector]:
    """整个模块的日志，DEBUG 级别，包括 discord.py 自己的。"""
    collector = _Collector()
    root = logging.getLogger()
    level = root.level
    root.addHandler(collector)
    root.setLevel(logging.DEBUG)
    yield collector
    root.removeHandler(collector)
    root.setLevel(level)


@pytest.fixture(scope="module")
def bot(logs: _Collector) -> Iterator[tuple[DiscordNotifier, Decisions]]:
    assert CONFIG is not None
    decisions = Decisions()
    notifier = DiscordNotifier(_gateway(CONFIG), CONFIG, CLOCK, decisions)
    notifier.start()
    yield notifier, decisions
    notifier.close()


def _rest(method: str, path: str) -> Any:
    """不经过适配器直接读写 Discord，用来核对。"""
    assert CONFIG is not None
    headers = {"Authorization": f"Bot {CONFIG.token.get_secret_value()}"}
    response = httpx.request(method, f"{API}{path}", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json() if response.content else None


def _message(message_id: str) -> Any:
    assert CONFIG is not None
    return _rest("GET", f"/channels/{CONFIG.channel_id}/messages/{message_id}")


def _until(check: Callable[[], bool], seconds: int = 10) -> None:
    """每秒检查一次，直到 check 为真；超过 seconds 秒就失败。"""
    pause = threading.Event()
    for _ in range(seconds):
        if check():
            return
        pause.wait(1)
    pytest.fail(f"等了 {seconds} 秒仍然没有达到")


def _request(minutes: int) -> ConfirmationRequest:
    """买入 SPY 1 股的方案，方案 ID 每次都不同；截止时刻是 minutes 分钟以后。"""
    now = CLOCK.now()
    plan = TradePlan(
        plan_id=UUID(hex=secrets.token_hex(16)),
        symbol="SPY",
        sector="指数",
        action=TradeAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1.00"),
        stop_price=Decimal("0.50"),
    )
    decision = RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=1,
        emergency_threshold=Decimal("0.08"),
        expires_at=now + timedelta(hours=1),
        violations=(),
    )
    return ConfirmationRequest(
        plan=plan, decision=decision, deadline=now + timedelta(minutes=minutes)
    )


def _settled_as(message_id: str, title: str) -> Callable[[], bool]:
    def check() -> bool:
        message = _message(message_id)
        embeds = message["embeds"]
        return message["components"] == [] and bool(embeds) and embeds[0]["title"] == title

    return check


def test_invalid_token(logs: _Collector) -> None:
    """spec「token 无效」：启动失败，原因是登录失败。"""
    assert CONFIG is not None
    config = CONFIG.model_copy(update={"token": SecretStr("invalid-token")})
    notifier = DiscordNotifier(_gateway(config), config, CLOCK, Decisions())

    with pytest.raises(NotificationUnavailableError, match="登录失败"):
        notifier.start()
    notifier.close()


def test_wrong_channel(logs: _Collector) -> None:
    """spec「频道不对」：把频道 ID 换成用户 ID，启动失败，错误信息里有这个 ID。"""
    assert CONFIG is not None
    config = CONFIG.model_copy(update={"channel_id": CONFIG.user_id})
    notifier = DiscordNotifier(_gateway(config), config, CLOCK, Decisions())

    with pytest.raises(NotificationUnavailableError, match=str(CONFIG.user_id)):
        notifier.start()
    notifier.close()


def test_alert_notifies_only_the_user(bot: tuple[DiscordNotifier, Decisions]) -> None:
    """spec「推送告警」「文字里有 @everyone」：消息 @ 本人，@everyone 不通知任何人。"""
    assert CONFIG is not None
    notifier, _ = bot
    marker = secrets.token_hex(4)

    notifier.alert(f"冒烟测试 {marker}：告警，不用处理 @everyone")

    recent = _rest("GET", f"/channels/{CONFIG.channel_id}/messages?limit=10")
    [message] = [m for m in recent if marker in m["content"]]
    assert message["content"].startswith(f"<@{CONFIG.user_id}> ")
    assert message["mention_everyone"] is False
    assert [user["id"] for user in message["mentions"]] == [str(CONFIG.user_id)]


def test_confirmation_message_then_voided(bot: tuple[DiscordNotifier, Decisions]) -> None:
    """spec「买入方案通过风控」「报告作废」：正文、@ 和两个按钮；报告作废后按钮没了。"""
    assert CONFIG is not None
    notifier, _ = bot
    request = _request(minutes=30)

    pending = notifier.request_confirmation(request)

    message = _message(pending.message_id)
    assert message["content"].startswith(f"<@{CONFIG.user_id}> 请确认：买入 SPY 1 股\n")
    [row] = message["components"]
    custom_ids = [button["custom_id"] for button in row["components"]]
    assert [c.split(":")[1] for c in custom_ids] == ["confirmed", "rejected"]
    assert all(str(request.plan.plan_id) in c for c in custom_ids)

    notifier.finish_confirmation(pending.message_id, ConfirmationOutcome.VOIDED)

    _until(_settled_as(pending.message_id, "已作废"))


def test_finish_on_deleted_message(bot: tuple[DiscordNotifier, Decisions]) -> None:
    """spec「消息已被删掉」：报告超时什么也不做，不报错。"""
    assert CONFIG is not None
    notifier, _ = bot
    pending = notifier.request_confirmation(_request(minutes=30))
    _rest("DELETE", f"/channels/{CONFIG.channel_id}/messages/{pending.message_id}")

    notifier.finish_confirmation(pending.message_id, ConfirmationOutcome.TIMED_OUT)


def _press_by_hand(
    bot: tuple[DiscordNotifier, Decisions], button: str, outcome: ConfirmationOutcome, title: str
) -> None:
    """发一条确认消息，请人按 button，等回调收到 outcome，再核对消息显示 title。"""
    notifier, decisions = bot
    request = _request(minutes=PRESS_SECONDS // 60)
    decisions.received.clear()
    pending: PendingConfirmation = notifier.request_confirmation(request)
    notifier.alert(f"冒烟测试：请在 3 分钟内按上面那条消息的「{button}」")

    assert decisions.received.wait(PRESS_SECONDS), f"3 分钟内没有人按「{button}」"
    assert decisions.calls[-1] == (request.plan.plan_id, outcome)
    _until(_settled_as(pending.message_id, title))


def test_confirm_by_hand(bot: tuple[DiscordNotifier, Decisions]) -> None:
    """spec「本人按确认」：人按「确认」，回调收到确认，消息去掉按钮、显示已确认。"""
    _press_by_hand(bot, "确认", ConfirmationOutcome.CONFIRMED, "已确认")


def test_reject_by_hand(bot: tuple[DiscordNotifier, Decisions]) -> None:
    """spec「本人按拒绝」。"""
    _press_by_hand(bot, "拒绝", ConfirmationOutcome.REJECTED, "已拒绝")


def test_token_not_in_logs(logs: _Collector) -> None:
    """spec「Discord 配置」：上面全部测试的日志（DEBUG 级别）里都没有 token。"""
    assert CONFIG is not None
    token = CONFIG.token.get_secret_value()
    leaked = any(token in text for text in logs.texts)
    assert not leaked, "日志里出现了 token"
    assert logs.texts  # 确实收集到了日志
