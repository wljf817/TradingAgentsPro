"""行情用例的测试：取行情、行情最近一次更新的时刻。

每个测试对应 openspec 的 market-data spec 里的一个场景。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.fakes.quotes import FakeQuoteSource
from tradingagentspro.modules.marketdata import (
    MarketDataConfig,
    MarketDataService,
    Quote,
    QuoteSource,
    QuoteUnavailableError,
)

# 基准数据：行情源有 AAA、BBB、SPY，没有 CCC
AAA = Quote(
    symbol="AAA",
    last_price=Decimal("105.5"),
    previous_close=Decimal(100),
    updated_at=datetime(2026, 10, 14, 14, 59, 59, 500000, tzinfo=UTC),
)
BBB = Quote(
    symbol="BBB",
    last_price=None,  # 今天开盘后还没有成交
    previous_close=Decimal(50),
    updated_at=datetime(2026, 10, 14, 13, 29, 58, tzinfo=UTC),
)
SPY = Quote(
    symbol="SPY",
    last_price=Decimal("580.25"),
    previous_close=Decimal("578.1"),
    updated_at=datetime(2026, 10, 14, 14, 59, 59, 900000, tzinfo=UTC),
)


def _service(
    *quotes: Quote, config: MarketDataConfig | None = None
) -> tuple[MarketDataService, FakeQuoteSource]:
    """默认是基准数据的行情源；给出 quotes 时，行情源只有这些股票。"""
    source = FakeQuoteSource(*(quotes or (AAA, BBB, SPY)))
    checked: QuoteSource = source  # 让 mypy 检查替身满足端口
    return MarketDataService(checked, config or MarketDataConfig()), source


# 取行情


def test_two_symbols() -> None:
    """spec「取两只股票」。"""
    service, _ = _service()

    assert service.quotes(["AAA", "BBB"]) == {"AAA": AAA, "BBB": BBB}


def test_symbol_without_data_is_left_out() -> None:
    """spec「行情源没有的股票」。"""
    service, _ = _service()

    assert service.quotes(["AAA", "CCC"]) == {"AAA": AAA}


def test_no_symbols() -> None:
    """spec「没有给出股票」：结果为空，没有向行情源取。"""
    service, source = _service()

    assert service.quotes([]) == {}
    assert source.requests == []


def test_duplicate_symbols_are_requested_once() -> None:
    """spec「同一只股票给出多次」。"""
    service, source = _service()

    assert service.quotes(["AAA", "AAA", "BBB"]) == {"AAA": AAA, "BBB": BBB}
    assert source.requests == [("AAA", "BBB")]


def test_source_unavailable() -> None:
    """spec「行情源不可用」。"""
    service, source = _service()
    source.fail_next = True

    with pytest.raises(QuoteUnavailableError):
        service.quotes(["AAA"])


# 行情最近一次更新的时刻


def test_last_update_is_the_reference_update_time() -> None:
    """spec「参考股票有行情」：结果是 SPY 的更新时刻，只向行情源取 SPY。"""
    service, source = _service()

    assert service.last_update() == datetime(2026, 10, 14, 14, 59, 59, 900000, tzinfo=UTC)
    assert source.requests == [("SPY",)]


def test_last_update_without_reference_data() -> None:
    """spec「行情源没有参考股票的数据」。"""
    service, _ = _service(AAA, BBB)

    with pytest.raises(QuoteUnavailableError):
        service.last_update()


def test_last_update_when_source_unavailable() -> None:
    """spec「取更新时刻时行情源不可用」。"""
    service, source = _service()
    source.fail_next = True

    with pytest.raises(QuoteUnavailableError):
        service.last_update()


def test_changed_reference_symbol() -> None:
    """spec「改参考股票」：参考股票配置为 QQQ。"""
    qqq = Quote(
        symbol="QQQ",
        last_price=Decimal("495.3"),
        previous_close=Decimal("493.8"),
        updated_at=datetime(2026, 10, 14, 14, 59, 58, tzinfo=UTC),
    )
    service, source = _service(AAA, BBB, SPY, qqq, config=MarketDataConfig(reference_symbol="QQQ"))

    assert service.last_update() == datetime(2026, 10, 14, 14, 59, 58, tzinfo=UTC)
    assert source.requests == [("QQQ",)]
