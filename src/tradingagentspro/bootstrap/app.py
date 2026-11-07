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
取，见 update-order-flow-case-plans 的 design D9。案件那一侧（Agent 网关和替身、新闻和公告、公司
数据、寻股粗筛、快照、案件工作流和交接、排队运行）也在这里装配：DBOS launch 以前装上案件工作流，
launch 以后开始排队运行，再开始盘中巡检；健康监控读网关的模型和预算、排队运行的心跳。以后的触发源
从 TradingApp.queue 排队。见 add-queue-runner 的 design D8、D12。收盘心跳的部件在 DBOS launch 以前
install，事件检测之后开始、盘中巡检之后关闭，见 add-close-heartbeat 的 design D6。配置文件和模型文件
在启动的第一步读完，各部件的配置都来自它们，见 add-settings-file 的 design D7。
"""

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol
from uuid import UUID

from dbos import DBOS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from tradingagentspro.bootstrap.charter import (
    CHARTER_FILE,
    FUNDAMENTALS_PROMPT_FILE,
    NEWS_PROMPT_FILE,
    PM_PROMPT_FILE,
    load_charter,
    load_role_prompt,
)
from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.bootstrap.models import (
    MODELS_FILE,
    ModelFile,
    RealVendor,
    endpoints,
    ensure_models_file,
    load_models,
    used_vendors,
)
from tradingagentspro.bootstrap.settings import SETTINGS_FILE, Settings, load_settings
from tradingagentspro.bootstrap.settings_text import ensure_settings_file, overridden
from tradingagentspro.bootstrap.startup import (
    judge_health_at_startup,
    reconcile_at_startup,
    record_shutdown,
    record_startup,
)
from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.charter import Charter
from tradingagentspro.integrations.alpaca import (
    AlpacaBarSource,
    AlpacaBroker,
    AlpacaClient,
    AlpacaConfig,
    AlpacaNewsConfig,
    AlpacaNewsSource,
    AlpacaStopBroker,
    configure_account,
)
from tradingagentspro.integrations.cboe import CboeConfig, CboeVixSource
from tradingagentspro.integrations.discord import (
    DiscordConfig,
    DiscordGateway,
    DiscordNotifier,
    Gateway,
    PlanCommand,
)
from tradingagentspro.integrations.finnhub import FinnhubConfig, FinnhubSource
from tradingagentspro.integrations.llm import (
    LlmBackend,
    LlmClient,
    SubAgentSetup,
    ToolBuilder,
    VendorRouter,
    combined,
    filing_tools,
    financials_tools,
    news_tools,
)
from tradingagentspro.integrations.moomoo import (
    MoomooConfig,
    MoomooPriceAlerts,
    MoomooQuoteSource,
    MoomooSecuritySource,
    ReminderContext,
    SecuritiesContext,
    SnapshotContext,
    open_quote_context,
)
from tradingagentspro.integrations.nyse_calendar import load_nyse_calendar
from tradingagentspro.integrations.random_ids import RandomIdGenerator
from tradingagentspro.integrations.sec import SecConfig, SecFilingSource
from tradingagentspro.integrations.standin import StandInBackend
from tradingagentspro.integrations.system_clock import SystemClock
from tradingagentspro.modules.caserecords import CaseRecordService, PostgresCaseRecordStore
from tradingagentspro.modules.companydata import (
    CompanyDataService,
    CompanyDataSource,
    FinancialFactSource,
    FinancialsService,
    PostgresEarningsStore,
    PostgresFinancialFactStore,
)
from tradingagentspro.modules.emergency import EmergencyService
from tradingagentspro.modules.events import (
    AlertService,
    EventService,
    PostgresEventStore,
)
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
    FollowUpService,
    PostgresFollowUpStore,
)
from tradingagentspro.modules.gateway import (
    AgentCallService,
    CallBudgetService,
    HistoryQueryService,
    ModelFallbackService,
    PostgresCostStore,
    PostgresModelCallStore,
    PostgresTaskStore,
    TaskDispatchService,
)
from tradingagentspro.modules.health import HealthService
from tradingagentspro.modules.marketdata import (
    MarketContextService,
    MarketDataService,
    VixSource,
)
from tradingagentspro.modules.news import (
    FilingLookupService,
    FilingSource,
    NewsLookupService,
    NewsService,
    NewsSource,
    PostgresNewsStore,
)
from tradingagentspro.modules.notification import (
    ConfirmationOutcome,
    DecisionHandler,
    NotificationService,
)
from tradingagentspro.modules.programlog import (
    ACCOUNT,
    WHOLE_SYSTEM,
    PostgresProgramLogStore,
    ProgramLog,
)
from tradingagentspro.modules.risk import PostgresReservationStore, RiskConfig, RiskService
from tradingagentspro.modules.scheduler import PostgresQueueStore, QueueService
from tradingagentspro.modules.screener import PostgresScreenStore, ScreenerService
from tradingagentspro.modules.statestore import PostgresSnapshotStore, SnapshotService, connect
from tradingagentspro.modules.stockpool import (
    PostgresStockPoolStore,
    StockPoolService,
)
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows import case_workflow, close_heartbeat, order_workflow
from tradingagentspro.workflows.case_conclusion import CheckConfigs
from tradingagentspro.workflows.case_flow import CaseFlow, CaseServices
from tradingagentspro.workflows.case_history import CaseHistory
from tradingagentspro.workflows.close_heartbeat import (
    CloseHeartbeat,
    DbosHeartbeatRuns,
    HeartbeatParts,
)
from tradingagentspro.workflows.emergency_flow import EmergencyDecisions, EmergencyFlow
from tradingagentspro.workflows.event_detection import DetectionParts, EventDetector
from tradingagentspro.workflows.health import HealthMonitor, degraded_text
from tradingagentspro.workflows.order_flow import OrderFlow, TradeServices
from tradingagentspro.workflows.order_workflow import (
    CasePlanHandoff,
    OrderPlanIntake,
    deliver_decision,
    wait_decision,
)
from tradingagentspro.workflows.patrol import SessionPatrol
from tradingagentspro.workflows.pool_sync import PoolSync
from tradingagentspro.workflows.queue_runner import DbosCases, QueueParts, QueueRunner
from tradingagentspro.workflows.reconciliation import startup_text
from tradingagentspro.workflows.snapshot_flow import SnapshotFlow

_log = logging.getLogger(__name__)

# DBOS 的应用名：DBOS 用它区分同一个系统数据库里的应用
APP_NAME = "tradingagentspro"


class SecSource(FilingSource, FinancialFactSource, Protocol):
    """SEC 适配器：同一个实例既是公告源、也是财务事实源，请求一个接一个地发，见
    add-financial-figures 的 design D10。
    """


class QuoteContext(SnapshotContext, SecuritiesContext, ReminderContext, Protocol):
    """OpenD 行情连接里装配用到的部分：取快照、条件选股和股票的静态信息、到价提醒，退出时关闭。"""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Externals:
    """程序和外界接触的地方。运行时用 SYSTEM_EXTERNALS，测试换成替身，其余照常装配。

    wait 是下单流程等开盘、等重试用的持久化等待，期间收到作废就提前返回，见 add-order-flow 的
    design D13 和 add-queue-runner 的 design D6。news、filings 是新闻源和公告源：事件检测在运行中
    取新闻和公告，测试换成替身，见 add-event-detection 的 design D15。sleep 是收盘心跳相邻两次刷新
    之间的持久化等待，finnhub 是公司数据源：收盘心跳在运行中刷新财报，测试换成替身，见
    add-close-heartbeat 的 design D6。vix 是 VIX 源：生成快照时读，测试换成替身，见
    add-market-context 的 design D1。
    """

    clock: Clock
    ids: IdGenerator
    alpaca: Callable[[AlpacaConfig], AlpacaClient]
    opend: Callable[[MoomooConfig], QuoteContext]
    discord: Callable[[DiscordConfig, PlanCommand], Gateway]
    wait: Callable[[float], str | None]
    news: Callable[[AlpacaNewsConfig], NewsSource]
    filings: Callable[[SecConfig], SecSource]
    sleep: Callable[[float], None]
    finnhub: Callable[[FinnhubConfig], CompanyDataSource]
    vix: Callable[[CboeConfig], VixSource]


SYSTEM_EXTERNALS = Externals(
    clock=SystemClock(),
    ids=RandomIdGenerator(),
    alpaca=AlpacaClient,
    opend=open_quote_context,
    discord=DiscordGateway,
    wait=wait_decision,
    news=AlpacaNewsSource,
    filings=SecFilingSource,
    sleep=DBOS.sleep,
    finnhub=FinnhubSource,
    vix=CboeVixSource,
)


class BootstrapConfig(BaseModel):
    """配置文件和模型文件的路径，见 settings-file spec 和 model-config spec。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    settings_file: Path = SETTINGS_FILE
    models_file: Path = MODELS_FILE


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
    queue: QueueRunner  # 排队运行：触发源从这里排进案件
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
    client: AlpacaClient, risk: RiskConfig, execution: ExecutionConfig
) -> tuple[Broker, StopBroker, ExecutionConfig]:
    """模拟盘的券商：两个 Alpaca 适配器共用一个客户端，打开买单和止损单错开。

    Alpaca 不允许同一只股票的买单和止损卖单同时挂着；估算费用的每笔佣金和风控估算用同一个值。
    """
    commission = risk.commission_per_order
    return (
        AlpacaBroker(client, commission),
        AlpacaStopBroker(client, commission),
        execution.model_copy(update={"stagger_buys_and_stops": True}),
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
    case_flow: CaseFlow
    queue: QueueRunner
    detector: EventDetector
    heartbeat_parts: HeartbeatParts
    heartbeat: CloseHeartbeat


@dataclass(frozen=True, slots=True)
class _CaseSide:
    """案件那一侧装配好的部件：案件工作流的用例，健康监控要读的备用模型和预算，事件检测要用的
    新闻和公告、公司数据和事件。
    """

    flow: CaseFlow
    fallback: ModelFallbackService
    budget: CallBudgetService
    news: NewsService
    company_data: CompanyDataService
    events: EventService


def _sub_agent_setup(charter: Charter, prompt_file: Path, tools: ToolBuilder) -> SubAgentSetup:
    """子 agent 的系统指令和读取工具，见 sub-agent-calls spec「调用子 agent 的消息」。"""
    return SubAgentSetup(f"{charter.text}\n\n{load_role_prompt(prompt_file)}", tools)


def sub_agent_setups(
    charter: Charter,
    news: NewsLookupService,
    filings: FilingLookupService,
    financials: FinancialsService,
) -> dict[SubAgent, SubAgentSetup]:
    """接上真实模型的子 agent：News 有新闻工具，Fundamentals 有公告工具和 financials。"""
    fundamentals = combined(filing_tools(filings), financials_tools(financials))
    return {
        SubAgent.NEWS: _sub_agent_setup(charter, NEWS_PROMPT_FILE, news_tools(news)),
        SubAgent.FUNDAMENTALS: _sub_agent_setup(charter, FUNDAMENTALS_PROMPT_FILE, fundamentals),
    }


def news_lookup(news: NewsService, clock: Clock, settings: Settings) -> NewsLookupService:
    """新闻工具的查询：补抓的界线用事件检测的回看时长，见 add-news-tools 的 design D2。"""
    return NewsLookupService(news, clock, settings.news, settings.event_detection.lookback)


def filing_lookup(
    news: NewsService, clock: Clock, settings: Settings, pool: StockPoolService
) -> FilingLookupService:
    """公告工具的查询：补抓的界线用事件检测的回看时长，股票在不在股票池问股票池，见
    add-filing-tools 的 design D4。
    """
    return FilingLookupService(
        news,
        clock,
        settings.filings,
        settings.event_detection.lookback,
        lambda symbol: pool.entry(symbol) is not None,
    )


def _model_backend(
    file: ModelFile,
    stand_in: StandInBackend,
    llm: LlmClient,
    charter: Charter,
    history: HistoryQueryService,
    lookups: tuple[NewsLookupService, FilingLookupService, FinancialsService],
) -> VendorRouter:
    """按 model-adapter spec「按厂商分派」：替身的厂商交给替身，其他交给真实模型的后端。"""
    real = LlmBackend(
        llm,
        f"{charter.text}\n\n{load_role_prompt(PM_PROMPT_FILE)}",
        history,
        file.gateway.max_history_queries,
        sub_agent_setups(charter, *lookups),
        file.gateway.max_sub_agent_tool_calls,
    )
    return VendorRouter(
        {
            vendor: real if isinstance(file.vendors[vendor], RealVendor) else stand_in
            for vendor in used_vendors(file.models)
        }
    )


def _case_side(
    settings: Settings,
    externals: Externals,
    engine: Engine,
    calendar: TradingCalendar,
    program_log: ProgramLog,
    trade: tuple[
        ExecutionService, RiskService, MarketDataService, QuoteContext, NotificationService
    ],
    records: tuple[StockPoolService, FollowUpService, CaseRecordService],
    configured: ModelFile,
    llm: LlmClient,
) -> _CaseSide:
    """按 bootstrap spec「模拟盘的装配」建出案件那一侧：网关（模型和额度来自模型文件，调用替身）、
    新闻和公告、公司数据、寻股粗筛、快照和案件工作流的用例，见 add-queue-runner 的 design D12。
    """
    clock, ids = externals.clock, externals.ids
    execution, risk, market_data, quotes, notification = trade
    stock_pool, follow_ups, case_records = records
    models, gateway = configured.models, configured.gateway
    fallback = ModelFallbackService(clock, models, gateway)
    budget = CallBudgetService(PostgresCostStore(engine), clock, calendar, models, gateway)
    tasks = TaskDispatchService(PostgresTaskStore(engine), clock, ids, gateway, program_log)
    charter = load_charter(CHARTER_FILE, settings.risk)
    case_history = CaseHistory(case_records, execution)
    sec = externals.filings(settings.sec)
    news = NewsService(externals.news(settings.alpaca_news), sec, PostgresNewsStore(engine, clock))
    company_data = CompanyDataService(
        externals.finnhub(settings.finnhub),
        PostgresEarningsStore(engine, clock),
        clock,
        settings.companydata,
    )
    financials = FinancialsService(
        company_data, sec, PostgresFinancialFactStore(engine, clock), settings.companydata
    )
    lookups = (
        news_lookup(news, clock, settings),
        filing_lookup(news, clock, settings, stock_pool),
        financials,
    )
    backend = _model_backend(
        configured,
        StandInBackend(calendar, settings.standin),
        llm,
        charter,
        HistoryQueryService(case_history, gateway, program_log),
        lookups,
    )
    agents = AgentCallService(
        backend, fallback, budget, tasks, PostgresModelCallStore(engine), clock
    )
    screener = ScreenerService(
        MoomooSecuritySource(quotes, settings.moomoo_securities),
        AlpacaBarSource(settings.alpaca_bars),
        company_data,
        PostgresScreenStore(engine, clock),
        clock,
        calendar,
        settings.screener,
        settings.stockpool,
    )
    snapshots = SnapshotService(PostgresSnapshotStore(engine, clock), program_log)
    events = EventService(PostgresEventStore(engine), program_log)
    market_context = MarketContextService(
        externals.vix(settings.cboe), calendar, settings.marketdata
    )
    snapshot_flow = SnapshotFlow(
        execution,
        risk,
        market_data,
        market_context,
        stock_pool,
        news,
        company_data,
        screener,
        events,
        snapshots,
        clock,
        calendar,
        settings.snapshot,
    )
    services = CaseServices(
        agents=agents,
        tasks=tasks,
        budget=budget,
        fallback=fallback,
        records=case_records,
        history=case_history,
        snapshots=snapshots,
        snapshot_flow=snapshot_flow,
        pool=stock_pool,
        follow_ups=follow_ups,
        events=events,
        notification=notification,
        log=program_log,
        clock=clock,
        calendar=calendar,
        ids=ids,
    )
    checks = CheckConfigs(gateway, settings.scheduler, settings.heartbeat)
    flow = CaseFlow(services, checks, settings.case_flow, charter.version)
    return _CaseSide(flow, fallback, budget, news, company_data, events)


def _assemble(
    settings: Settings,
    externals: Externals,
    engine: Engine,
    calendar: TradingCalendar,
    stack: ExitStack,
    program_log: ProgramLog,
    configured: ModelFile,
    llm: LlmClient,
) -> _Parts:
    """按 bootstrap spec「模拟盘的装配」建出全部部件；打开 OpenD 行情连接（不等连上）。

    program_log 交给通知和各流程。
    """
    clock = externals.clock
    client = externals.alpaca(settings.alpaca)
    broker, stops, execution_config = paper_execution(client, settings.risk, settings.execution)
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
    risk = RiskService(PostgresReservationStore(engine, clock), clock, calendar, settings.risk)
    emergency = EmergencyService(clock, calendar, settings.emergency)
    health = HealthService(clock, calendar, settings.health)
    quotes = externals.opend(settings.moomoo)
    on_stop(stack, "OpenD 行情连接", quotes.close)
    market_data = MarketDataService(MoomooQuoteSource(quotes), settings.marketdata)
    lock = threading.RLock()
    services = TradeServices(execution, risk, emergency, health, market_data, lock, program_log)
    plans = PlanCommand(OrderPlanIntake(services), externals.ids, calendar, settings.discord)
    gateway = externals.discord(settings.discord, plans)
    decisions = EmergencyDecisions()  # 紧急退出在等的确认，按钮的回调先送到这里
    notifier = DiscordNotifier(gateway, settings.discord, clock, decision_handler(decisions))
    notification = NotificationService(notifier, clock, settings.notification, program_log)
    stock_pool = StockPoolService(
        PostgresStockPoolStore(engine, clock), clock, calendar, settings.stockpool, program_log
    )
    follow_ups = FollowUpService(
        PostgresFollowUpStore(engine, clock),
        clock,
        calendar,
        externals.ids,
        settings.followups,
        program_log,
    )
    case_records = CaseRecordService(PostgresCaseRecordStore(engine, clock), clock)
    cases = _case_side(
        settings,
        externals,
        engine,
        calendar,
        program_log,
        (execution, risk, market_data, quotes, notification),
        (stock_pool, follow_ups, case_records),
        configured,
        llm,
    )
    # 案件工作流和下单流程的 DBOS 实现：紧急退出读运行中的案件、交出中止，排队运行启动和作废
    running = DbosCases()
    emergency_flow = EmergencyFlow(
        services,
        notification,
        clock,
        calendar,
        settings.emergency,
        settings.notification,
        decisions,
        running,
    )
    queued = QueueService(
        PostgresQueueStore(engine), clock, externals.ids
    )  # 排队运行和收盘心跳共用
    queue = QueueRunner(
        QueueParts(
            queue=queued,
            cases=running,
            pool=stock_pool,
            health=health,
            budget=cases.budget,
            gateway=configured.gateway,
            records=case_records,
            follow_ups=follow_ups,
            exiting=emergency_flow.exiting,
            scheduler=settings.scheduler,
            log=program_log,
            clock=clock,
        ),
        settings.queue_runner,
    )
    detector = EventDetector(
        DetectionParts(
            pool=stock_pool,
            execution=execution,
            market_data=market_data,
            news=cases.news,
            company_data=cases.company_data,
            events=cases.events,
            intake=queue,
            alerts=AlertService(MoomooPriceAlerts(quotes)),
            clock=clock,
            calendar=calendar,
            rules=settings.events,
        ),
        settings.event_detection,
    )
    heartbeat_parts = HeartbeatParts(
        pool=stock_pool,
        company_data=cases.company_data,
        records=case_records,
        queue=queued,
        running=running.running,
        intake=queue,
        log=program_log,
        clock=clock,
        calendar=calendar,
        heartbeat=settings.heartbeat,
        config=settings.close_heartbeat,
    )
    heartbeat = CloseHeartbeat(DbosHeartbeatRuns(), clock, calendar, heartbeat_parts.config)
    monitor = HealthMonitor(
        health,
        execution,
        market_data,
        notification,
        lock,
        program_log,
        fallback=cases.fallback,
        budget=cases.budget,
        heartbeat=queue.heartbeat,
    )
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
            settings.patrol,
            emergency=emergency_flow,
            pool=PoolSync(execution, stock_pool, follow_ups, case_records, calendar),
            log=program_log,
        ),
        flow=OrderFlow(
            services,
            notification,
            settings.notification,
            case_records,
            clock,
            calendar,
            settings.order_flow,
        ),
        case_flow=cases.flow,
        queue=queue,
        detector=detector,
        heartbeat_parts=heartbeat_parts,
        heartbeat=heartbeat,
    )


def _read_files(config: BootstrapConfig) -> tuple[Settings, ModelFile]:
    """按 bootstrap spec「读配置文件」：先生成还不存在的两个文件，再读。"""
    if ensure_settings_file(config.settings_file):
        _log.info("已生成配置文件：%s", config.settings_file)
    if ensure_models_file(config.models_file):
        _log.info("已生成模型文件：%s", config.models_file)
    settings = load_settings(config.settings_file)
    changed = overridden(settings)
    if changed:
        _log.info("和默认值不同的配置项：%s", "、".join(changed))
    return settings, load_models(config.models_file, settings.gateway)


def start(externals: Externals, config: BootstrapConfig, stack: ExitStack) -> TradingApp:
    """按 bootstrap spec「启动顺序」启动；打开的部分都登记到 stack，关闭时按相反顺序。

    任何一步失败都抛 StartupError。
    """
    clock = externals.clock
    with step("读取配置文件"):
        settings, configured = _read_files(config)
    with step("连接和升级数据库"):
        database = settings.database
        engine = connect(database.url.get_secret_value())
        on_stop(stack, "数据库连接", engine.dispose)
        migrate(engine)
        _log.info("数据库已升级到最新版本：%s", database.label)
        program_log = ProgramLog(PostgresProgramLogStore(engine), clock)
        record_startup(program_log, f"tradingagentspro {version('tradingagentspro')}，模拟盘")
        # 登记在数据库连接的关闭项之后：关闭时排在别的各项之后、数据库连接之前写停止
        stack.callback(record_shutdown, program_log)
    with step("加载交易日历"):
        end = (clock.now() + settings.calendar.lookahead).date()  # 范围的边界，不是交易日
        calendar = load_nyse_calendar(settings.calendar.first_day, end)
        first, last = calendar.first_session.day, calendar.last_session.day
        _log.info("交易日历：%s 到 %s", first, last)
    with step("装配"):
        llm = LlmClient(endpoints(configured), settings.llm)
        on_stop(stack, "模型调用", llm.close)
        parts = _assemble(
            settings, externals, engine, calendar, stack, program_log, configured, llm
        )
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
        url = database.url.get_secret_value()
        # enable_patching：程序更新以后照样恢复进行中的下单流程，见 add-order-flow 的 design D10
        DBOS(config={"name": APP_NAME, "system_database_url": url, "enable_patching": True})
        on_stop(stack, "DBOS", DBOS.destroy)
        order_workflow.install(parts.flow, externals.wait)
        case_workflow.install(parts.case_flow, CasePlanHandoff())
        close_heartbeat.install(parts.heartbeat_parts, externals.sleep)
        DBOS.launch()
        _log.info("DBOS 已启动")
    with step("开始排队运行"):
        parts.queue.start()
        on_stop(stack, "排队运行", parts.queue.stop)
    with step("开始事件检测"):
        parts.detector.start()
        on_stop(stack, "事件检测", parts.detector.stop)
    with step("开始收盘心跳"):
        parts.heartbeat.start()
        on_stop(stack, "收盘心跳", parts.heartbeat.stop)
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
        queue=parts.queue,
        reconciliation=reconciliation,
        lock=parts.lock,
        log=program_log,
    )


def run(
    stop: threading.Event,
    externals: Externals = SYSTEM_EXTERNALS,
    config: BootstrapConfig = DEFAULT_CONFIG,
) -> int:
    """启动，等到 stop 被设置，停止。返回退出码：0 正常停止，1 启动失败。"""
    _log.info("启动 tradingagentspro %s（模拟盘）", version("tradingagentspro"))
    with ExitStack() as stack:
        try:
            start(externals, config, stack)
        except StartupError as error:
            _log.error("启动失败：%s", error, exc_info=error.__cause__)
            return 1
        _log.info("启动完成")
        stop.wait()
        _log.info("收到停止信号，开始停止")
    _log.info("已停止")
    return 0
