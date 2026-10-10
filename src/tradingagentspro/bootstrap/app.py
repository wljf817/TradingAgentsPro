"""装配和生命周期：按固定的顺序启动交易侧程序（模拟盘），收到停止信号时按相反的顺序关闭。

见 bootstrap spec「启动顺序」「启动失败」「模拟盘的装配」「DBOS」「停止」，以及 add-bootstrap-wiring
的 design D1、D5、D6、D8、D10、D11。DBOS launch 之后开始盘中巡检，停止时最先停它，见
add-session-patrol 的 design D12。启动对账有不一致时，Discord 连上以后推送告警，见
add-close-reconciliation 的 design D10。以后的流程变更在 start 里 DBOS launch 之前注册工作流，
从 TradingApp.lock 取锁。
"""

import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.metadata import version
from typing import Protocol

from dbos import DBOS
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.bootstrap.order_flow import AccountPlanIntake, refuse_decision
from tradingagentspro.bootstrap.settings import Settings, load_settings
from tradingagentspro.bootstrap.startup import judge_health_at_startup, reconcile_at_startup
from tradingagentspro.integrations.alpaca import (
    AlpacaBroker,
    AlpacaClient,
    AlpacaConfig,
    AlpacaStopBroker,
    configure_account,
)
from tradingagentspro.integrations.discord import (
    DiscordConfig,
    DiscordGateway,
    DiscordNotifier,
    Gateway,
    PlanCommand,
)
from tradingagentspro.integrations.moomoo import (
    MoomooConfig,
    MoomooQuoteSource,
    SnapshotContext,
    open_quote_context,
)
from tradingagentspro.integrations.nyse_calendar import (
    DEFAULT_FIRST_DAY,
    DEFAULT_LOOKAHEAD,
    load_nyse_calendar,
)
from tradingagentspro.integrations.random_ids import RandomIdGenerator
from tradingagentspro.integrations.system_clock import SystemClock
from tradingagentspro.modules.emergency import EmergencyConfig, EmergencyService
from tradingagentspro.modules.execution import (
    Broker,
    ExecutionConfig,
    ExecutionService,
    PostgresCloseReconciliationStore,
    PostgresOrderStore,
    PostgresProtectionStore,
    PostgresTradeStore,
    Reconciliation,
    StopBroker,
)
from tradingagentspro.modules.health import HealthConfig, HealthService
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.modules.notification import NotificationConfig, NotificationService
from tradingagentspro.modules.risk import PostgresReservationStore, RiskConfig, RiskService
from tradingagentspro.modules.statestore import connect
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.patrol import PatrolConfig, SessionPatrol
from tradingagentspro.workflows.reconciliation import startup_text

_log = logging.getLogger(__name__)

# DBOS 的应用名：DBOS 用它区分同一个系统数据库里的应用
APP_NAME = "tradingagentspro"


class QuoteContext(SnapshotContext, Protocol):
    """OpenD 行情连接里装配用到的部分：取快照，退出时关闭。"""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Externals:
    """程序和外界接触的地方。运行时用 SYSTEM_EXTERNALS，测试换成替身，其余照常装配。"""

    clock: Clock
    ids: IdGenerator
    alpaca: Callable[[AlpacaConfig], AlpacaClient]
    opend: Callable[[MoomooConfig], QuoteContext]
    discord: Callable[[DiscordConfig, PlanCommand], Gateway]


SYSTEM_EXTERNALS = Externals(
    clock=SystemClock(),
    ids=RandomIdGenerator(),
    alpaca=AlpacaClient,
    opend=open_quote_context,
    discord=DiscordGateway,
)


class BootstrapConfig(BaseModel):
    """启动的配置和默认值，含义见 bootstrap spec「交易日历的加载范围」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 交易日历的起点
    calendar_first_day: date = DEFAULT_FIRST_DAY
    # 交易日历的终点 = 启动时刻往后这么久
    calendar_lookahead: timedelta = Field(default=DEFAULT_LOOKAHEAD, gt=timedelta(0))


DEFAULT_CONFIG = BootstrapConfig()


@dataclass(frozen=True, slots=True)
class TradingApp:
    """启动好的程序：以后的流程变更从这里取用例。"""

    clock: Clock
    ids: IdGenerator
    calendar: TradingCalendar
    execution: ExecutionService
    risk: RiskService
    emergency: EmergencyService
    health: HealthService
    market_data: MarketDataService
    notification: NotificationService
    reconciliation: Reconciliation  # 启动对账的结果
    lock: threading.RLock  # 执行模块和风控的调用串行用的锁，见 session-patrol spec


class StartupError(RuntimeError):
    """启动的某一步失败：信息是「<步骤>失败：<原因>」，__cause__ 是原来的异常。"""


@contextmanager
def step(name: str) -> Iterator[None]:
    """把这一步里的任何异常转成 StartupError，写明步骤和原来的异常类型、信息。"""
    try:
        yield
    except Exception as error:
        raise StartupError(f"{name}失败：{type(error).__name__}: {error}") from error


def on_stop(stack: ExitStack, name: str, close: Callable[[], object]) -> None:
    """登记一项关闭：出错只写 ERROR 日志，不影响别的项；成功写 INFO。"""

    def call() -> None:
        try:
            close()
        except Exception:
            _log.exception("关闭时出错：%s", name)
        else:
            _log.info("已关闭：%s", name)

    stack.callback(call)


def paper_execution(
    client: AlpacaClient, risk: RiskConfig
) -> tuple[Broker, StopBroker, ExecutionConfig]:
    """模拟盘的券商：两个 Alpaca 适配器共用一个客户端，打开买单和止损单错开。

    Alpaca 不允许同一只股票的买单和止损卖单同时挂着；估算费用的每笔佣金和风控估算用同一个值。
    """
    commission = risk.commission_per_order
    return (
        AlpacaBroker(client, commission),
        AlpacaStopBroker(client, commission),
        ExecutionConfig(stagger_buys_and_stops=True),
    )


@dataclass(frozen=True, slots=True)
class _Parts:
    """装配好、还没启动的部件。"""

    alpaca: AlpacaClient
    execution: ExecutionService
    risk: RiskService
    emergency: EmergencyService
    health: HealthService
    market_data: MarketDataService
    notification: NotificationService
    notifier: DiscordNotifier
    lock: threading.RLock
    patrol: SessionPatrol


def _assemble(
    settings: Settings,
    externals: Externals,
    engine: Engine,
    calendar: TradingCalendar,
    stack: ExitStack,
) -> _Parts:
    """按 bootstrap spec「模拟盘的装配」建出全部部件；打开 OpenD 行情连接（不等连上）。"""
    clock = externals.clock
    risk_config = RiskConfig()
    client = externals.alpaca(settings.alpaca)
    broker, stops, execution_config = paper_execution(client, risk_config)
    execution = ExecutionService(
        broker,
        stops,
        PostgresOrderStore(engine, clock),
        PostgresProtectionStore(engine, clock),
        PostgresTradeStore(engine, clock),
        PostgresCloseReconciliationStore(engine, clock),
        clock,
        calendar,
        execution_config,
    )
    risk = RiskService(PostgresReservationStore(engine, clock), clock, calendar, risk_config)
    quotes = externals.opend(MoomooConfig())
    on_stop(stack, "OpenD 行情连接", quotes.close)
    market_data = MarketDataService(MoomooQuoteSource(quotes), MarketDataConfig())
    plans = PlanCommand(
        AccountPlanIntake(execution.account), externals.ids, calendar, settings.discord
    )
    gateway = externals.discord(settings.discord, plans)
    notifier = DiscordNotifier(gateway, settings.discord, clock, refuse_decision)
    notification = NotificationService(notifier, clock, NotificationConfig())
    lock = threading.RLock()
    return _Parts(
        alpaca=client,
        execution=execution,
        risk=risk,
        emergency=EmergencyService(clock, calendar, EmergencyConfig()),
        health=HealthService(clock, calendar, HealthConfig()),
        market_data=market_data,
        notification=notification,
        notifier=notifier,
        lock=lock,
        patrol=SessionPatrol(execution, risk, notification, lock, PatrolConfig()),
    )


def start(
    environ: Mapping[str, str], externals: Externals, config: BootstrapConfig, stack: ExitStack
) -> TradingApp:
    """按 bootstrap spec「启动顺序」启动；打开的部分都登记到 stack，关闭时按相反顺序。

    任何一步失败都抛 StartupError。
    """
    clock = externals.clock
    with step("读取环境变量"):
        settings = load_settings(environ)
    with step("连接和升级数据库"):
        engine = connect(settings.database_url.get_secret_value())
        on_stop(stack, "数据库连接", engine.dispose)
        migrate(engine)
        _log.info("数据库已升级到最新版本：%s", settings.database_label)
    with step("加载交易日历"):
        end = (clock.now() + config.calendar_lookahead).date()  # 范围的边界，不是交易日
        calendar = load_nyse_calendar(config.calendar_first_day, end)
        first, last = calendar.first_session.day, calendar.last_session.day
        _log.info("交易日历：%s 到 %s", first, last)
    with step("装配"):
        parts = _assemble(settings, externals, engine, calendar, stack)
    with step("Alpaca 账户设置"):
        configure_account(parts.alpaca)
        _log.info("Alpaca 账户设置完成：不做空，最大保证金倍数 1")
    with step("启动对账"):
        reconciliation = reconcile_at_startup(parts.execution)
    with step("第一次健康判定"):
        judge_health_at_startup(parts.health, parts.market_data, clock)
    with step("启动 Discord"):
        parts.notifier.start()
        on_stop(stack, "Discord", parts.notifier.close)
        _log.info("Discord 已连接")
    if not reconciliation.clean:  # 告警发不出去时只写错误日志，不算启动失败
        parts.notification.alert(startup_text(reconciliation))
    with step("启动 DBOS"):
        url = settings.database_url.get_secret_value()
        DBOS(config={"name": APP_NAME, "system_database_url": url})
        on_stop(stack, "DBOS", DBOS.destroy)
        DBOS.launch()
        _log.info("DBOS 已启动")
    with step("开始盘中巡检"):
        parts.patrol.start()
        on_stop(stack, "盘中巡检", parts.patrol.stop)
    return TradingApp(
        clock=clock,
        ids=externals.ids,
        calendar=calendar,
        execution=parts.execution,
        risk=parts.risk,
        emergency=parts.emergency,
        health=parts.health,
        market_data=parts.market_data,
        notification=parts.notification,
        reconciliation=reconciliation,
        lock=parts.lock,
    )


def run(
    environ: Mapping[str, str],
    stop: threading.Event,
    externals: Externals = SYSTEM_EXTERNALS,
    config: BootstrapConfig = DEFAULT_CONFIG,
) -> int:
    """启动，等到 stop 被设置，停止。返回退出码：0 正常停止，1 启动失败。"""
    _log.info("启动 tradingagentspro %s（模拟盘）", version("tradingagentspro"))
    with ExitStack() as stack:
        try:
            start(environ, externals, config, stack)
        except StartupError as error:
            _log.error("启动失败：%s", error, exc_info=error.__cause__)
            return 1
        _log.info("启动完成")
        stop.wait()
        _log.info("收到停止信号，开始停止")
    _log.info("已停止")
    return 0
