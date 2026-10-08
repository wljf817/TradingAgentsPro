"""启动对账和第一次健康判定的测试。

场景见 openspec 的 bootstrap spec「启动对账」「第一次健康判定」，做法见 add-bootstrap-wiring 的
design D7。用假券商、内存存储、假行情源和测试时钟，不连外部系统。
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.quotes import FakeQuoteSource
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import execution_service
from tradingagentspro.bootstrap.startup import judge_health_at_startup, reconcile_at_startup
from tradingagentspro.modules.execution import BrokerUnavailableError, ExecutionService
from tradingagentspro.modules.health import (
    HealthCheck,
    HealthConfig,
    HealthMode,
    HealthService,
    HealthSignals,
    HealthStatus,
)
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService, Quote

IN_SESSION = datetime(2026, 10, 14, 14, tzinfo=UTC)  # 美东 10:00，开盘后 30 分钟
BEFORE_OPEN = datetime(2026, 10, 14, 12, tzinfo=UTC)  # 美东 8:00
STARTUP = "tradingagentspro.bootstrap.startup"


@dataclass
class _Broker:
    broker: FakeBroker = field(default_factory=FakeBroker)
    stops: FakeStopBroker = field(default_factory=FakeStopBroker)

    def service(self) -> ExecutionService:
        return execution_service(
            InMemoryOrderStore(),
            self.broker,
            FakeClock(IN_SESSION),
            stops=self.stops,
            reconciled=False,
        )


def _records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == STARTUP and r.levelno == level]


# 启动对账


def test_reconcile_without_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    """spec「启动对账」的「没有不一致」：写 INFO。"""
    env = _Broker()

    with caplog.at_level(logging.INFO):
        result = reconcile_at_startup(env.service())

    assert result.clean
    assert _records(caplog, logging.WARNING) == []
    assert any("没有不一致" in m for m in _records(caplog, logging.INFO))


def test_reconcile_with_unexplained_position(caplog: pytest.LogCaptureFixture) -> None:
    """spec「来历不明的持仓」：WARNING 写明 CCC 100 股，结果里有这项不一致。"""
    env = _Broker()
    env.broker.positions["CCC"] = 100

    with caplog.at_level(logging.INFO):
        result = reconcile_at_startup(env.service())

    assert result.unexplained == (("CCC", 100),)
    [warning] = _records(caplog, logging.WARNING)
    assert "启动对账有不一致" in warning
    assert "CCC" in warning and "100" in warning


def test_reconcile_with_stray_order(caplog: pytest.LogCaptureFixture) -> None:
    """spec「不认得的挂单」：WARNING 写明 DDD、X1，没有撤单。"""
    env = _Broker()
    env.broker.add_working("DDD", "X1")

    with caplog.at_level(logging.INFO):
        result = reconcile_at_startup(env.service())

    assert [w.client_order_id for w in result.stray_orders] == ["X1"]
    [warning] = _records(caplog, logging.WARNING)
    assert "DDD" in warning and "X1" in warning
    assert env.broker.cancel_requests == []


def test_reconcile_read_failure_is_raised() -> None:
    """spec「启动对账」的「读取券商失败」：BrokerUnavailableError 原样抛出，由启动顺序处理。"""
    env = _Broker()
    env.broker.fail_next("working_orders")

    with pytest.raises(BrokerUnavailableError):
        reconcile_at_startup(env.service())


# 第一次健康判定


class RecordingHealth(HealthService):
    """真实的健康监控，另外记下收到的信号。"""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, sample_nyse_calendar(), HealthConfig())
        self.signals: list[HealthSignals] = []

    def check(self, signals: HealthSignals) -> HealthStatus:
        self.signals.append(signals)
        return super().check(signals)


def _spy(updated_at: datetime) -> Quote:
    return Quote(
        symbol="SPY",
        last_price=Decimal("580.25"),
        previous_close=Decimal("578.1"),
        updated_at=updated_at,
    )


def _judge(now: datetime, source: FakeQuoteSource) -> tuple[HealthStatus, RecordingHealth]:
    clock = FakeClock(now)
    health = RecordingHealth(clock)
    market_data = MarketDataService(source, MarketDataConfig())
    return judge_health_at_startup(health, market_data, clock), health


def test_signals_follow_the_table() -> None:
    """spec「第一次健康判定」的信号表。"""
    updated = IN_SESSION - timedelta(seconds=10)

    _, health = _judge(IN_SESSION, FakeQuoteSource(_spy(updated)))

    assert health.signals == [
        HealthSignals(
            pm_model_available=True,
            critic_model_available=True,
            broker_connected=True,
            last_quote_at=updated,
            reserve_budget_exhausted=False,
            scheduler_heartbeat_at=IN_SESSION,
        )
    ]


def test_quotes_fine_in_session(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘中行情正常」：健康是正常，写 INFO。"""
    with caplog.at_level(logging.INFO):
        status, _ = _judge(IN_SESSION, FakeQuoteSource(_spy(IN_SESSION - timedelta(seconds=10))))

    assert status.mode is HealthMode.NORMAL
    assert _records(caplog, logging.WARNING) == []
    assert any("正常" in m for m in _records(caplog, logging.INFO))


def test_opend_down_in_session(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘中 OpenD 没开」：取不到行情写 WARNING，进入降级，异常的检查是数据延迟。"""
    source = FakeQuoteSource()
    source.fail_next = True

    with caplog.at_level(logging.INFO):
        status, health = _judge(IN_SESSION, source)

    assert health.signals[0].last_quote_at is None
    assert status.mode is HealthMode.DEGRADED
    assert status.abnormal == {HealthCheck.DATA_DELAY}
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 2
    assert "取不到行情" in warnings[0] and "测试安排的失败" in warnings[0]
    assert "降级" in warnings[1] and "data_delay" in warnings[1]


def test_opend_down_before_open(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘外 OpenD 没开」：写取不到行情的 WARNING；盘外不检查数据延迟，健康是正常。"""
    source = FakeQuoteSource()
    source.fail_next = True

    with caplog.at_level(logging.INFO):
        status, _ = _judge(BEFORE_OPEN, source)

    assert status.mode is HealthMode.NORMAL
    [warning] = _records(caplog, logging.WARNING)
    assert "取不到行情" in warning


def test_quote_stuck_at_previous_close() -> None:
    """spec「开盘后行情一直没有更新」：参考股票停在前一个交易日收盘，进入降级。"""
    previous_close = datetime(2026, 10, 13, 20, tzinfo=UTC)

    status, _ = _judge(IN_SESSION, FakeQuoteSource(_spy(previous_close)))

    assert status.mode is HealthMode.DEGRADED
    assert status.abnormal == {HealthCheck.DATA_DELAY}


def test_reference_symbol_missing_counts_as_no_quote() -> None:
    """spec「第一次健康判定」的表：行情源没有参考股票的数据，也按取不到处理。"""
    status, health = _judge(IN_SESSION, FakeQuoteSource())

    assert health.signals[0].last_quote_at is None
    assert status.mode is HealthMode.DEGRADED
