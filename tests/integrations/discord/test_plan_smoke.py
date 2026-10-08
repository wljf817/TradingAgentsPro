"""连真实 Discord 的 /plan 冒烟测试：注册、应答和只有本人看得到的回复。

见 add-manual-plan-entry 的 design D11。默认不运行，用
uv run pytest -m discord_bot tests/integrations/discord/test_plan_smoke.py 运行；
DISCORD_BOT_TOKEN、DISCORD_GUILD_ID、DISCORD_CHANNEL_ID、DISCORD_USER_ID 缺一个就跳过。
人工部分要人在 5 分钟内按告警里的提示运行四条 /plan。提交接口是替身，不会下单。

token 只放在环境变量和请求头里：断言里不出现 token，失败时 pytest 不会把它打出来。
"""

import logging
import os
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from tests.fakes.plan_intake import FakePlanIntake, passed
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
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
from tradingagentspro.modules.notification import ConfirmationOutcome


def _config() -> DiscordConfig | None:
    try:
        return DiscordConfig.from_env(os.environ)
    except ValueError:
        return None


CONFIG = _config()
API = "https://discord.com/api/v10"
CLOCK = SystemClock()
RUN_SECONDS = 300  # 人工部分等人运行完四条命令的时长
COMMANDS_LOGGER = "tradingagentspro.integrations.discord.commands"

pytestmark = [
    pytest.mark.discord_bot,
    pytest.mark.skipif(CONFIG is None, reason="没有 Discord 机器人的 4 个环境变量"),
]


def _decide(plan: TradePlan) -> RiskDecision:
    """替身风控：减仓故意拒绝，别的都通过。"""
    if plan.action is TradeAction.REDUCE:
        return RiskDecision(
            outcome=RiskOutcome.REJECTED,
            quantity=None,
            emergency_threshold=None,
            expires_at=None,
            violations=(RiskViolation(rule="冒烟测试", message="故意拒绝"),),
        )
    return passed(plan)


def _ignore(plan_id: object, outcome: ConfirmationOutcome) -> None:
    """这组测试没有确认消息，按钮的回调用不到。"""


def _start() -> tuple[DiscordNotifier, FakePlanIntake]:
    """启动一个真实的连接：/plan 交给替身提交接口，SPY 有持仓，板块「测试」。"""
    assert CONFIG is not None
    intake = FakePlanIntake({"SPY": "测试"})
    intake.decide = _decide
    today = CLOCK.now().date()
    calendar = load_nyse_calendar(today - timedelta(days=7), today + timedelta(days=60))
    plans = PlanCommand(intake, RandomIdGenerator(), calendar, CONFIG)
    notifier = DiscordNotifier(DiscordGateway(CONFIG, plans), CONFIG, CLOCK, _ignore)
    notifier.start()
    return notifier, intake


class _Collector(logging.Handler):
    """收集全部日志，最后检查里面没有 token。"""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def texts(self) -> list[str]:
        return [self.format(r) for r in self.records]

    def command_results(self) -> list[str]:
        """/plan 的处理写下的日志：每条命令一条结果。"""
        return [r.getMessage() for r in self.records if r.name == COMMANDS_LOGGER]


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


def _rest(path: str) -> Any:
    """不经过适配器直接读 Discord，用来核对。"""
    assert CONFIG is not None
    headers = {"Authorization": f"Bot {CONFIG.token.get_secret_value()}"}
    response = httpx.get(f"{API}{path}", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def _registered() -> tuple[list[Any], list[Any]]:
    """这个机器人在配置的服务器里的命令和全局命令。"""
    assert CONFIG is not None
    application = _rest("/applications/@me")["id"]
    guild = _rest(f"/applications/{application}/guilds/{CONFIG.guild_id}/commands")
    return guild, _rest(f"/applications/{application}/commands")


def _until(check: Callable[[], bool], seconds: int) -> bool:
    """每秒检查一次，直到 check 为真；超过 seconds 秒返回 False。"""
    pause = threading.Event()
    for _ in range(seconds):
        if check():
            return True
        pause.wait(1)
    return check()


def test_registered_after_each_start(logs: _Collector) -> None:
    """spec「启动时注册」「重启以后再注册」：服务器里只有一个 /plan，全局命令里没有。"""
    for _ in range(2):
        notifier, _ = _start()
        try:
            guild, global_commands = _registered()
        finally:
            notifier.close()

        [plan] = [c for c in guild if c["name"] == "plan"]
        assert sorted(o["name"] for o in plan["options"]) == ["add", "buy", "close", "reduce"]
        assert len([c for c in guild if c["name"] == "plan"]) == 1
        assert all(c["name"] != "plan" for c in global_commands)


def test_four_commands_by_hand(logs: _Collector) -> None:
    """人在 Discord 里运行四条 /plan：风控通过、格式错误、风控拒绝、没有持仓。

    替身收到的两个方案逐项核对；回复是不是只有本人看得到、文字对不对，由人在 Discord 里看。
    """
    notifier, intake = _start()
    try:
        before = len(logs.command_results())
        notifier.alert(
            "冒烟测试：请在 5 分钟内依次运行下面四条命令，每条回复都应该标着「只有你能看到」。"
            "提交接口是替身，不会下单，也不会有确认消息\n"
            "1. `/plan buy symbol:spy sector:测试 quantity:1 limit:1.00 stop:0.50 emergency:8 "
            "deviation:5%`：风控通过\n"
            "2. `/plan add symbol:SPY sector:测试 quantity:1 limit:400.005 stop:390`：格式错误，"
            "限价不符合最小变动单位\n"
            "3. `/plan reduce symbol:SPY quantity:1`：风控拒绝（冒烟测试：故意拒绝）\n"
            "4. `/plan close symbol:XYZ`：没有 XYZ 的持仓"
        )

        finished = _until(lambda: len(logs.command_results()) - before >= 4, RUN_SECONDS)
        assert finished, "5 分钟内没有运行完四条 /plan"
    finally:
        notifier.close()

    results = logs.command_results()[before:]
    assert any("风控通过" in r for r in results)
    assert any("格式错误" in r and "400.005" in r for r in results)
    assert any("风控拒绝" in r for r in results)
    assert any("没有 XYZ 的持仓" in r for r in results)
    assert not [r for r in results if "回复没有发出去" in r]

    buy, reduce = intake.submitted
    assert (buy.action, buy.symbol, buy.sector, buy.quantity) == (
        TradeAction.BUY,
        "SPY",
        "测试",
        1,
    )
    assert (buy.limit_price, buy.stop_price) == (Decimal("1.00"), Decimal("0.50"))
    assert (buy.emergency_threshold, buy.max_price_deviation) == (Decimal("0.08"), Decimal("0.05"))
    assert (reduce.action, reduce.sector, reduce.quantity, reduce.order_type) == (
        TradeAction.REDUCE,
        "测试",
        1,
        OrderType.MARKET,
    )
    assert buy.plan_id != reduce.plan_id


def test_token_not_in_logs(logs: _Collector) -> None:
    """上面全部测试的日志（DEBUG 级别）里都没有 token。"""
    assert CONFIG is not None
    token = CONFIG.token.get_secret_value()
    leaked = any(token in text for text in logs.texts())
    assert not leaked, "日志里出现了 token"
    assert logs.records  # 确实收集到了日志
