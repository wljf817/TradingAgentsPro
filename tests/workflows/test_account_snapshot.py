"""风控用的账户数据的测试。

场景见 openspec 的 order-flow spec「风控用的账户数据」，做法见 add-order-flow 的 design D7。
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.quotes import FakeQuoteSource
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.workflows.order_builders import (
    AAA_QUOTE,
    BBB_QUOTE,
    NOW,
    at,
    hold_aaa,
    new_execution,
    quote,
)
from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.modules.execution import (
    Account,
    BrokerUnavailableError,
    ExecutionService,
    HeldPosition,
)
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.workflows.account_snapshot import account_snapshot, price_of, read_snapshot

AAA = HeldPosition(
    symbol="AAA",
    sector="科技",
    quantity=10,
    average_cost=Decimal(100),
    stop_price=Decimal(90),
    emergency_threshold=Decimal("0.08"),
    entry_price_today=None,
)


@dataclass
class _Env:
    """执行模块（持有 AAA 10 股）、行情和它们的替身。"""

    broker: FakeBroker = field(default_factory=FakeBroker)
    source: FakeQuoteSource = field(default_factory=lambda: FakeQuoteSource(AAA_QUOTE, BBB_QUOTE))

    def __post_init__(self) -> None:
        clock = FakeClock(NOW)
        self.execution: ExecutionService = new_execution(
            self.broker,
            FakeStopBroker(),
            InMemoryOrderStore(),
            InMemoryProtectionStore(),
            InMemoryTradeStore(),
            clock,
        )
        hold_aaa(self.execution, self.broker, clock)
        self.market_data = MarketDataService(self.source, MarketDataConfig())


def test_price_is_the_last_price() -> None:
    """spec「盘中的账户数据」：有最新价时用最新价。"""
    assert price_of(AAA, AAA_QUOTE) == Decimal(110)


def test_price_before_the_first_trade_is_the_previous_close() -> None:
    """spec「盘前用前一交易日收盘价」：没有最新价时用前一交易日收盘价。"""
    assert price_of(AAA, quote("AAA", None, "108")) == Decimal(108)


def test_price_without_a_quote_is_the_average_cost() -> None:
    """spec「取不到行情时用均价」：没有行情时用均价。"""
    assert price_of(AAA, None) == Decimal(100)


def test_snapshot_of_an_account() -> None:
    """spec 的表：权益、已结算资金、最近成交日取账户数据，持仓加上最新价。"""
    account = Account(
        equity=Decimal(10_000),
        settled_cash=Decimal(9_000),
        previous_close_equity=Decimal(10_500),
        positions=(AAA,),
        last_trade_days={"AAA": date(2026, 10, 12), "ZZZ": date(2026, 10, 9)},
    )

    snapshot = account_snapshot(account, {"AAA": AAA_QUOTE})

    assert snapshot == AccountSnapshot(
        equity=Decimal(10_000),
        settled_cash=Decimal(9_000),
        positions=(
            Position(
                symbol="AAA",
                sector="科技",
                quantity=10,
                average_cost=Decimal(100),
                last_price=Decimal(110),
                stop_price=Decimal(90),
            ),
        ),
        last_trade_days={"AAA": date(2026, 10, 12), "ZZZ": date(2026, 10, 9)},
    )


def test_read_in_session(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘中的账户数据」：只取持仓股票的行情，一次取完。"""
    env = _Env()

    with caplog.at_level(logging.WARNING):
        snapshot, quotes = read_snapshot(env.execution, env.market_data)

    assert snapshot.equity == Decimal(10_000)
    assert snapshot.settled_cash == Decimal(10_000)
    [aaa] = snapshot.positions
    assert (aaa.symbol, aaa.sector, aaa.quantity) == ("AAA", "科技", 10)
    assert (aaa.average_cost, aaa.last_price, aaa.stop_price) == (
        Decimal(100),
        Decimal(110),
        Decimal(90),
    )
    assert snapshot.last_trade_days == {"AAA": date(2026, 10, 12)}
    assert quotes == {"AAA": AAA_QUOTE}
    assert env.source.requests == [("AAA",)]
    assert caplog.records == []


def test_read_with_extra_symbols() -> None:
    """再校验时开仓方案这只股票的行情和持仓的一起取，一次取完。"""
    env = _Env()

    _, quotes = read_snapshot(env.execution, env.market_data, ("BBB",))

    assert quotes == {"AAA": AAA_QUOTE, "BBB": BBB_QUOTE}
    assert env.source.requests == [("AAA", "BBB")]


def test_read_before_the_open_uses_the_previous_close() -> None:
    """spec「盘前用前一交易日收盘价」。"""
    env = _Env(source=FakeQuoteSource(quote("AAA", None, "108", at(12))))

    snapshot, _ = read_snapshot(env.execution, env.market_data)

    assert snapshot.positions[0].last_price == Decimal(108)


def test_read_when_quotes_are_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    """spec「取不到行情时用均价」：用均价，写一条 WARNING 写明股票和原因。"""
    env = _Env()
    env.source.fail_next = True

    with caplog.at_level(logging.WARNING):
        snapshot, quotes = read_snapshot(env.execution, env.market_data)

    assert snapshot.positions[0].last_price == Decimal(100)
    assert quotes == {}
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "AAA" in record.getMessage()
    assert "测试安排的失败" in record.getMessage()


def test_read_when_the_source_lacks_a_symbol(caplog: pytest.LogCaptureFixture) -> None:
    """spec「行情源没有这只股票」：只给缺的那一只写 WARNING。"""
    env = _Env(source=FakeQuoteSource(BBB_QUOTE))

    with caplog.at_level(logging.WARNING):
        snapshot, _ = read_snapshot(env.execution, env.market_data, ("BBB",))

    assert snapshot.positions[0].last_price == Decimal(100)
    [record] = caplog.records
    assert "AAA" in record.getMessage()
    assert "BBB" not in record.getMessage()


def test_read_without_positions_takes_no_quotes() -> None:
    """spec「没有持仓时不取行情」。"""
    env = _Env()
    env.broker.positions["AAA"] = 0

    snapshot, quotes = read_snapshot(env.execution, env.market_data)

    assert snapshot.positions == ()
    assert quotes == {}
    assert env.source.requests == []


def test_read_when_the_broker_is_unavailable() -> None:
    """读券商账户失败时原样抛出，不取行情。"""
    env = _Env()
    env.broker.fail_next("account")

    with pytest.raises(BrokerUnavailableError):
        read_snapshot(env.execution, env.market_data)
    assert env.source.requests == []
