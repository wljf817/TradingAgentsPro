"""一套用替身装好的下单流程：真实的执行模块、风控、紧急退出、健康监控、行情和通知，
替身的券商、止损单、存储、行情源和通知通道，测试时钟和一把锁。基准数据见 order_builders.py。
"""

import threading
from dataclasses import dataclass, field
from decimal import Decimal

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.notifier import FakeNotifier
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.quotes import FakeQuoteSource
from tests.fakes.reservations import InMemoryReservationStore
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.workflows.order_builders import (
    AAA_QUOTE,
    BBB_QUOTE,
    NOW,
    NYSE,
    hold_aaa,
    new_execution,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import EmergencyConfig, EmergencyService
from tradingagentspro.modules.execution import Order
from tradingagentspro.modules.health import HealthConfig, HealthService, HealthSignals
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.modules.notification import NotificationConfig, NotificationService
from tradingagentspro.modules.risk import RiskConfig, RiskService
from tradingagentspro.workflows.order_flow import OrderFlow, OrderFlowConfig, TradeServices


@dataclass
class Desk:
    """order-flow spec 的基准数据上装好的下单流程。restart 用同一套存储和替身重新建各个用例。"""

    clock: FakeClock = field(default_factory=lambda: FakeClock(NOW))
    broker: FakeBroker = field(default_factory=FakeBroker)
    stops: FakeStopBroker = field(default_factory=FakeStopBroker)
    orders: InMemoryOrderStore = field(default_factory=InMemoryOrderStore)
    protections: InMemoryProtectionStore = field(default_factory=InMemoryProtectionStore)
    trades: InMemoryTradeStore = field(default_factory=InMemoryTradeStore)
    reservations: InMemoryReservationStore = field(default_factory=InMemoryReservationStore)
    source: FakeQuoteSource = field(default_factory=lambda: FakeQuoteSource(AAA_QUOTE, BBB_QUOTE))
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    lock: threading.RLock = field(default_factory=threading.RLock)
    config: OrderFlowConfig = field(default_factory=OrderFlowConfig)
    notification_config: NotificationConfig = field(default_factory=NotificationConfig)

    def __post_init__(self) -> None:
        self.execution = new_execution(
            self.broker, self.stops, self.orders, self.protections, self.trades, self.clock
        )
        hold_aaa(self.execution, self.broker, self.clock)
        self._build()
        self.forget_calls()

    def restart(self, *, reconcile: bool = True) -> None:
        """进程重启：用同一套存储和替身重新建各个用例；reconcile 时执行模块先对账一次。"""
        self.execution = new_execution(
            self.broker, self.stops, self.orders, self.protections, self.trades, self.clock
        )
        if reconcile:
            self.execution.reconcile()
        self._build()
        self.forget_calls()

    def forget_calls(self) -> None:
        """清掉替身里的调用记录，包括券商受理的提交和挂单。"""
        self.broker.calls.clear()
        self.broker.placed.clear()
        self.stops.calls.clear()
        self.stops.placed.clear()
        self.source.requests.clear()

    def degrade(self) -> None:
        """进入降级模式：券商连接异常。"""
        now = self.clock.now()
        self.health.check(
            HealthSignals(
                pm_model_available=True,
                critic_model_available=True,
                broker_connected=False,
                last_quote_at=now,
                reserve_budget_exhausted=False,
                scheduler_heartbeat_at=now,
            )
        )
        assert self.health.status.degraded

    def trip_breaker(self) -> None:
        """今天的单日亏损熔断触发：权益比前一交易日收盘低 10%。"""
        self.emergency.check((), Decimal(9_000), Decimal(10_000))
        assert self.emergency.breaker_tripped()

    def remaining(self, plan: TradePlan) -> int | None:
        """这个方案有效预留的剩余股数；没有有效预留时为 None。"""
        reservation = self.reservations.get(plan.plan_id)
        return reservation.remaining if reservation and reservation.is_active else None

    def order(self, plan: TradePlan) -> Order | None:
        return self.orders.get(plan.plan_id)

    def _build(self) -> None:
        self.risk = RiskService(self.reservations, self.clock, NYSE, RiskConfig())
        self.emergency = EmergencyService(self.clock, NYSE, EmergencyConfig())
        self.health = HealthService(self.clock, NYSE, HealthConfig())
        self.market_data = MarketDataService(self.source, MarketDataConfig())
        self.notification = NotificationService(self.notifier, self.clock, self.notification_config)
        self.services = TradeServices(
            execution=self.execution,
            risk=self.risk,
            emergency=self.emergency,
            health=self.health,
            market_data=self.market_data,
            lock=self.lock,
        )
        self.flow = OrderFlow(self.services, self.notification, self.clock, NYSE, self.config)
