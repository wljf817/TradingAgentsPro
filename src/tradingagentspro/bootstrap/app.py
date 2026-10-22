"""装配和生命周期：按固定的顺序启动交易侧程序（模拟盘），收到停止信号时按相反的顺序关闭。

见 bootstrap spec「启动顺序」「启动失败」「模拟盘的装配」「DBOS」「停止」，以及 add-bootstrap-wiring
的 design D1、D5、D6、D8、D10、D11。DBOS launch 之后开始盘中巡检，停止时最先停它，见
add-session-patrol 的 design D12。启动对账有不一致、第一次健康判定是降级时，Discord 连上以后
推送告警，见 add-close-reconciliation 的 design D10 和 add-health-monitoring 的 design D8。
下单流程的部件在 DBOS launch 之前 install，/plan 的提交接口和确认按钮的回调接上下单流程，见
add-order-flow 的 design D13。盘中巡检的每一轮里有紧急退出，见 add-emergency-exit-flow 的
design D10。确认按钮的结果先送给紧急退出流程，它不在等的再送给下单流程；通知和紧急退出流程用同一份
通知配置，见 update-confirmation-rules 的 design D4、D7。以后的流程从 TradingApp.lock 取锁。
数据库升级以后建程序日志、写启动（需要时先写崩溃恢复），关闭数据库连接以前写停止；装配好的通知、
各流程都写进这一个程序日志，以后的流程从 TradingApp.log 取，见 add-program-log 的 design D7。
股票池、跟进和案件记录交给下单流程（案件的方案）和巡检里的股票池跟上持仓，以后的流程从 TradingApp
取；案件工作流和交接还不装配，见 update-order-flow-case-plans 的 design D9。
"""

import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.metadata import version
from typing import Protocol
from uuid import UUID

from dbos import DBOS
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.bootstrap.settings import Settings, load_settings
from tradingagentspro.bootstrap.startup import (
    judge_health_at_startup,
    reconcile_at_startup,
    record_shutdown,
    record_startup,
)
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
from tradingagentspro.modules.caserecords import CaseRecordService, PostgresCaseRecordStore
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
from tradingagentspro.modules.followups import (
    FollowUpConfig,
    FollowUpService,
    PostgresFollowUpStore,
)
from tradingagentspro.modules.health import HealthConfig, HealthService
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    DecisionHandler,
    NotificationConfig,
    NotificationService,
)
from tradingagentspro.modules.programlog import (
    ACCOUNT,
    WHOLE_SYSTEM,
    PostgresProgramLogStore,
    ProgramLog,
)
from tradingagentspro.modules.risk import PostgresReservationStore, RiskConfig, RiskService
from tradingagentspro.modules.statestore import connect
from tradingagentspro.modules.stockpool import (
    PostgresStockPoolStore,
    StockPoolConfig,
    StockPoolService,
)
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.emergency_flow import EmergencyDecisions, EmergencyFlow
from tradingagentspro.workflows.health import HealthMonitor, degraded_text
from tradingagentspro.workflows.order_flow import OrderFlow, OrderFlowConfig, TradeServices
from tradingagentspro.workflows.order_workflow import (
    OrderPlanIntake,
    deliver_decision,
    install,
    wait_decision,
)
from tradingagentspro.workflows.patrol import PatrolConfig, SessionPatrol
from tradingagentspro.workflows.pool_sync import PoolSync
from tradingagentspro.workflows.reconciliation import startup_text

_log = logging.getLogger(__name__)

# DBOS 的应用名：DBOS 用它区分同一个系统数据库里的应用
APP_NAME = "tradingagentspro"


class QuoteContext(SnapshotContext, Protocol):
    """OpenD 行情连接里装配用到的部分：取快照，退出时关闭。"""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Externals:
    """程序和外界接触的地方。运行时用 SYSTEM_EXTERNALS，测试换成替身，其余照常装配。

    wait 是下单流程等开盘、等重试用的持久化等待，期间收到作废就提前返回，见 add-order-flow 的
    design D13 和 add-queue-runner 的 design D6。
    """

    clock: Clock
    ids: IdGenerator
    alpaca: Callable[[AlpacaConfig], AlpacaClient]
    opend: Callable[[MoomooConfig], QuoteContext]
    discord: Callable[[DiscordConfig, PlanCommand], Gateway]
    wait: Callable[[float], str | None]


SYSTEM_EXTERNALS = Externals(
    clock=SystemClock(),
    ids=RandomIdGenerator(),
    alpaca=AlpacaClient,
    opend=open_quote_context,
    discord=DiscordGateway,
    wait=wait_decision,
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
    stock_pool: StockPoolService
    follow_ups: FollowUpService
    case_records: CaseRecordService
    reconciliation: Reconciliation  # 启动对账的结果
    lock: threading.RLock  # 执行模块和风控的调用串行用的锁，见 session-patrol spec
    log: ProgramLog  # 程序日志，见 program-log spec


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


def decision_handler(decisions: EmergencyDecisions) -> DecisionHandler:
    """确认按钮的回调：紧急退出流程在等的方案送给它，其余送给下单流程（不在等时抛错）。

    见 bootstrap spec「模拟盘的装配」和 update-confirmation-rules 的 design D4。
    """

    def deliver(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
        if not decisions.deliver(plan_id, outcome):
            deliver_decision(plan_id, outcome)

    return deliver


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
    stock_pool: StockPoolService
    follow_ups: FollowUpService
    case_records: CaseRecordService
    notifier: DiscordNotifier
    lock: threading.RLock
    monitor: HealthMonitor
    patrol: SessionPatrol
    flow: OrderFlow


def _assemble(
    settings: Settings,
    externals: Externals,
    engine: Engine,
    calendar: TradingCalendar,
    stack: ExitStack,
    program_log: ProgramLog,
) -> _Parts:
    """按 bootstrap spec「模拟盘的装配」建出全部部件；打开 OpenD 行情连接（不等连上）。

    program_log 交给通知和各流程。
    """
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
    emergency_config = EmergencyConfig()  # 紧急退出模块和紧急退出流程共用
    emergency = EmergencyService(clock, calendar, emergency_config)
    health = HealthService(clock, calendar, HealthConfig())
    quotes = externals.opend(MoomooConfig())
    on_stop(stack, "OpenD 行情连接", quotes.close)
    market_data = MarketDataService(MoomooQuoteSource(quotes), MarketDataConfig())
    lock = threading.RLock()
    services = TradeServices(execution, risk, emergency, health, market_data, lock, program_log)
    plans = PlanCommand(OrderPlanIntake(services), externals.ids, calendar, settings.discord)
    gateway = externals.discord(settings.discord, plans)
    notification_config = NotificationConfig()  # 通知和紧急退出流程共用
    decisions = EmergencyDecisions()  # 紧急退出在等的确认，按钮的回调先送到这里
    notifier = DiscordNotifier(gateway, settings.discord, clock, decision_handler(decisions))
    notification = NotificationService(notifier, clock, notification_config, program_log)
    monitor = HealthMonitor(health, execution, market_data, notification, lock, clock, program_log)
    stock_pool = StockPoolService(
        PostgresStockPoolStore(engine, clock), clock, calendar, StockPoolConfig(), program_log
    )
    follow_ups = FollowUpService(
        PostgresFollowUpStore(engine, clock),
        clock,
        calendar,
        externals.ids,
        FollowUpConfig(),
        program_log,
    )
    case_records = CaseRecordService(PostgresCaseRecordStore(engine, clock), clock)
    return _Parts(
        alpaca=client,
        execution=execution,
        risk=risk,
        emergency=emergency,
        health=health,
        market_data=market_data,
        notification=notification,
        stock_pool=stock_pool,
        follow_ups=follow_ups,
        case_records=case_records,
        notifier=notifier,
        lock=lock,
        monitor=monitor,
        patrol=SessionPatrol(
            execution,
            risk,
            notification,
            monitor,
            lock,
            PatrolConfig(),
            emergency=EmergencyFlow(
                services,
                notification,
                clock,
                calendar,
                emergency_config,
                notification_config,
                decisions,
            ),
            pool=PoolSync(execution, stock_pool, follow_ups, case_records, calendar),
            log=program_log,
        ),
        flow=OrderFlow(
            services,
            notification,
            notification_config,
            case_records,
            clock,
            calendar,
            OrderFlowConfig(),
        ),
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
        program_log = ProgramLog(PostgresProgramLogStore(engine), clock)
        record_startup(program_log, f"tradingagentspro {version('tradingagentspro')}，模拟盘")
        # 登记在数据库连接的关闭项之后：关闭时排在别的各项之后、数据库连接之前写停止
        stack.callback(record_shutdown, program_log)
    with step("加载交易日历"):
        end = (clock.now() + config.calendar_lookahead).date()  # 范围的边界，不是交易日
        calendar = load_nyse_calendar(config.calendar_first_day, end)
        first, last = calendar.first_session.day, calendar.last_session.day
        _log.info("交易日历：%s 到 %s", first, last)
    with step("装配"):
        parts = _assemble(settings, externals, engine, calendar, stack, program_log)
    with step("Alpaca 账户设置"):
        configure_account(parts.alpaca)
        _log.info("Alpaca 账户设置完成：不做空，最大保证金倍数 1")
    with step("启动对账"):
        reconciliation = reconcile_at_startup(parts.execution, program_log)
    with step("第一次健康判定"):
        health_status = judge_health_at_startup(parts.health, parts.monitor, program_log)
    with step("启动 Discord"):
        parts.notifier.start()
        on_stop(stack, "Discord", parts.notifier.close)
        _log.info("Discord 已连接")
    if not reconciliation.clean:  # 告警发不出去时只写错误日志，不算启动失败
        parts.notification.alert(ACCOUNT, startup_text(reconciliation))
    if health_status.degraded:  # 同上；见 add-health-monitoring 的 design D8
        parts.notification.alert(WHOLE_SYSTEM, degraded_text(health_status, at_startup=True))
    with step("启动 DBOS"):
        url = settings.database_url.get_secret_value()
        # enable_patching：程序更新以后照样恢复进行中的下单流程，见 add-order-flow 的 design D10
        DBOS(config={"name": APP_NAME, "system_database_url": url, "enable_patching": True})
        on_stop(stack, "DBOS", DBOS.destroy)
        install(parts.flow, externals.wait)
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
        stock_pool=parts.stock_pool,
        follow_ups=parts.follow_ups,
        case_records=parts.case_records,
        reconciliation=reconciliation,
        lock=parts.lock,
        log=program_log,
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
