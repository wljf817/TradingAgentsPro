"""盘中巡检测试的数据：session-patrol spec 的基准数据，以及一套用替身装好的巡检。

基准数据见 session-patrol spec「巡检的开始、间隔和停止」：order-lifecycle spec 的基准数据和
stop-protection spec 的约定，券商没有 AAA 的持仓；执行配置和巡检配置都用默认值。健康监控用默认
配置，行情源只有 SPY，更新时刻是时钟的现在之前 10 秒（health-monitoring spec 的基准数据）。
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.broker import FakeBroker
from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.closes import InMemoryCloseReconciliationStore
from tests.fakes.notifier import FakeNotifier
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.quotes import ClockQuoteSource
from tests.fakes.reservations import InMemoryReservationStore
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.modules.execution.builders import BASELINE_NOW, baseline_plan, decision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import ExecutionConfig, ExecutionService, Order
from tradingagentspro.modules.health import HealthConfig, HealthService
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.modules.notification import NotificationConfig, NotificationService
from tradingagentspro.modules.risk import Reservation, RiskConfig, RiskService
from tradingagentspro.workflows.health import HealthMonitor
from tradingagentspro.workflows.patrol import PatrolConfig, SessionPatrol

NYSE = sample_nyse_calendar()


def at(hour: int, minute: int = 0, second: int = 0, day: int = 14) -> datetime:
    """2026-10 某一天（默认 14 日，周三）的某个 UTC 时刻。"""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def p5() -> TradePlan:
    """P5：买入 CCC（能源）100 股，限价 20，止损价 18。"""
    return baseline_plan(
        symbol="CCC", sector="能源", limit_price=Decimal(20), stop_price=Decimal(18)
    )


@dataclass
class World:
    """一套巡检：执行模块、风控、通知、行情、健康监控和它们的替身、存储、测试时钟和一把锁。

    restart 用同一套存储和替身重新建执行模块、风控、健康监控和巡检，相当于进程重启。
    """

    clock: FakeClock = field(default_factory=lambda: FakeClock(BASELINE_NOW))
    broker: FakeBroker = field(default_factory=FakeBroker)
    stops: FakeStopBroker = field(default_factory=FakeStopBroker)
    orders: InMemoryOrderStore = field(default_factory=InMemoryOrderStore)
    protections: InMemoryProtectionStore = field(init=False)
    trades: InMemoryTradeStore = field(default_factory=InMemoryTradeStore)
    closes: InMemoryCloseReconciliationStore = field(
        default_factory=InMemoryCloseReconciliationStore
    )
    reservations: InMemoryReservationStore = field(default_factory=InMemoryReservationStore)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    lock: threading.RLock = field(default_factory=threading.RLock)
    config: PatrolConfig = field(default_factory=PatrolConfig)

    def __post_init__(self) -> None:
        self.protections = InMemoryProtectionStore(self.clock)  # 带时钟：收盘对账要读历史
        self.quotes = ClockQuoteSource(self.clock)
        self.market_data = MarketDataService(self.quotes, MarketDataConfig())
        self.notification = NotificationService(self.notifier, self.clock, NotificationConfig())
        self._build()
        self.execution.reconcile()  # 基准数据：执行模块已经完成一次对账
        self.forget_calls()

    def restart(self) -> None:
        """进程重启：重新建执行模块（还没对过账）、风控、健康监控和巡检，清掉之前的调用记录。"""
        self._build()
        self.forget_calls()

    def _build(self) -> None:
        """建执行模块、风控、健康监控（状态是正常）、健康监控接线和巡检。"""
        self.execution = self._execution()
        self.risk = RiskService(self.reservations, self.clock, NYSE, RiskConfig())
        self.health = HealthService(self.clock, NYSE, HealthConfig())
        self.monitor = HealthMonitor(
            self.health, self.execution, self.market_data, self.notification, self.lock, self.clock
        )
        self.patrol = self._patrol()

    def forget_calls(self) -> None:
        self.broker.calls.clear()
        self.stops.calls.clear()

    def reserve(self, plan: TradePlan, quantity: int = 100) -> None:
        """这个方案已经通过风控，预留 quantity 股，还没有上报过成交。"""
        self.reservations.save(Reservation(plan=plan, decision=decision(quantity=quantity)))

    def submit(self, plan: TradePlan, quantity: int = 100) -> Order:
        """预留并提交：方案已经通过风控和再校验，按 quantity 股下单。之后清掉调用记录。"""
        self.reserve(plan, quantity)
        order = self.execution.submit(plan, decision(quantity=quantity))
        self.forget_calls()
        return order

    def remaining(self, plan: TradePlan) -> int | None:
        """这个方案有效预留的剩余股数；没有有效预留时为 None。"""
        reservation = self.reservations.get(plan.plan_id)
        return reservation.remaining if reservation and reservation.is_active else None

    def order(self, plan: TradePlan) -> Order:
        order = self.orders.get(plan.plan_id)
        assert order is not None
        return order

    def _execution(self) -> ExecutionService:
        return ExecutionService(
            broker=self.broker,
            stops=self.stops,
            store=self.orders,
            protections=self.protections,
            trades=self.trades,
            closes=self.closes,
            clock=self.clock,
            calendar=NYSE,
            config=ExecutionConfig(),
        )

    def _patrol(self) -> SessionPatrol:
        return SessionPatrol(
            self.execution, self.risk, self.notification, self.monitor, self.lock, self.config
        )
