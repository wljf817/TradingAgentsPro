"""装配和启动测试用的外部连接：Alpaca、OpenD、Discord 用替身，时钟和 ID 用测试的。

见 add-bootstrap-wiring 的 design D12。除了这几样，装配、数据库、DBOS 都是真的。
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from moomoo import RET_ERROR, RET_OK

from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIds
from tests.integrations.alpaca.server import FakeAlpaca
from tests.integrations.discord.gateway import FakeGateway
from tests.integrations.moomoo import payloads
from tests.modules.notification.builders import CHANNEL_ID, GUILD_ID, USER_ID
from tradingagentspro.bootstrap.app import Externals
from tradingagentspro.integrations.alpaca import AlpacaClient, AlpacaConfig
from tradingagentspro.integrations.discord import DiscordConfig, Gateway, PlanCommand
from tradingagentspro.integrations.moomoo import MoomooConfig

NOW = datetime(2026, 10, 14, 14, tzinfo=UTC)  # 美东 10:00，开盘后 30 分钟
NEW_YORK = ZoneInfo("America/New_York")

# 假的密钥：只在这里出现，用来检查日志里没有它们
APCA_SECRET = "apca-secret-q7z1"
DISCORD_TOKEN = "discord-token-m4x8"


def environment(database_url: str) -> dict[str, str]:
    """基准数据的环境变量：全部给出，格式正确。"""
    return {
        "TRADINGAGENTSPRO_DATABASE_URL": database_url,
        "APCA_API_KEY_ID": "PK-TEST-ID",
        "APCA_API_SECRET_KEY": APCA_SECRET,
        "DISCORD_BOT_TOKEN": DISCORD_TOKEN,
        "DISCORD_GUILD_ID": str(GUILD_ID),
        "DISCORD_CHANNEL_ID": str(CHANNEL_ID),
        "DISCORD_USER_ID": str(USER_ID),
    }


class FakeOpenD:
    """满足 QuoteContext 的 OpenD 行情连接。

    快照只有 SPY 一行，更新时刻是现在的 10 秒前（按 OpenD 的写法，美东时间）。
    - error：设上以后取快照返回错误和这条信息（OpenD 没开）
    - close_error：设上以后 close 记下关闭再抛它
    - rows：SPY 以外的股票的快照行（按 OpenD 的写法），更新时刻同 SPY
    """

    def __init__(self, clock: FakeClock, rows: list[dict[str, Any]] | None = None) -> None:
        self.clock = clock
        self.rows = rows or []
        self.error: str | None = None
        self.close_error: Exception | None = None
        self.closed = False

    def get_market_snapshot(self, code_list: list[str]) -> tuple[int, Any]:
        if self.error is not None:
            return RET_ERROR, self.error
        updated = (self.clock.now() - timedelta(seconds=10)).astimezone(NEW_YORK)
        text = updated.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        rows = [payloads.row(row, update_time=text) for row in (payloads.SPY, *self.rows)]
        return RET_OK, payloads.snapshot(*rows)

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@dataclass
class World:
    """一次启动面对的外部世界。测试在调用 run 之前安排它们的情况。

    - opend：装配时才建，没有装配时为 None；测试用 opend_error、opend_close_error、opend_rows
      预先安排
    - configs：三个外部系统的工厂收到的配置，按顺序
    - plans：装配时交给 Discord 的 /plan 处理，测试用它运行 /plan
    - sleep：下单流程的持久化休眠，见 WorldSleep
    """

    alpaca: FakeAlpaca = field(default_factory=FakeAlpaca)
    gateway: FakeGateway = field(default_factory=FakeGateway)
    clock: FakeClock = field(default_factory=lambda: FakeClock(NOW))
    ids: FakeIds = field(default_factory=FakeIds)
    opend_error: str | None = None
    opend_close_error: Exception | None = None
    opend_rows: list[dict[str, Any]] = field(default_factory=list)
    opend: FakeOpenD | None = None
    configs: list[object] = field(default_factory=list)
    plans: PlanCommand | None = None
    sleep: "WorldSleep" = field(init=False)

    def __post_init__(self) -> None:
        self.sleep = WorldSleep(self.clock)

    def externals(self) -> Externals:
        return Externals(
            clock=self.clock,
            ids=self.ids,
            alpaca=self._alpaca,
            opend=self._opend,
            discord=self._discord,
            sleep=self.sleep,
        )

    def _alpaca(self, config: AlpacaConfig) -> AlpacaClient:
        self.configs.append(config)
        return AlpacaClient(config, transport=self.alpaca.transport())

    def _opend(self, config: MoomooConfig) -> FakeOpenD:
        self.configs.append(config)
        self.opend = FakeOpenD(self.clock, self.opend_rows)
        self.opend.error = self.opend_error
        self.opend.close_error = self.opend_close_error
        return self.opend

    def _discord(self, config: DiscordConfig, plans: PlanCommand) -> Gateway:
        self.configs.append(config)
        self.plans = plans
        return self.gateway

    def alpaca_writes(self) -> list[tuple[str, str]]:
        """Alpaca 收到的写请求（GET 以外的），按顺序。"""
        return [(r.method, r.url.path) for r in self.alpaca.requests if r.method != "GET"]

    def alpaca_requests(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.alpaca.requests]


class Crash(BaseException):
    """模拟进程在这一刻退出：DBOS 只接 Exception，工作流保持 PENDING，下次启动时恢复。"""


class WorldSleep:
    """下单流程的持久化休眠的替身。

    默认把时钟拨过要睡的秒数、立即返回；hold 时不拨钟、立即返回（重启以后重放的休眠，时钟已经
    设到醒来以后）；crash 时抛 Crash，模拟进程在休眠时退出。calls 记下每次的秒数。
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []
        self.hold = False
        self.crash = False
        self.crashed = threading.Event()

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.crash:
            self.crashed.set()
            raise Crash
        if not self.hold:
            self.clock.advance(timedelta(seconds=seconds))
