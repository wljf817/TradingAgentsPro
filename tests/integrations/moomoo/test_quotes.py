"""moomoo 行情源的测试：请求、快照的映射、查不到的股票、调用失败。

每个测试对应 openspec 的 moomoo-quotes spec 里的一个场景。快照的基准数据见 payloads.py。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from moomoo import RET_ERROR, RET_OK

from tests.integrations.moomoo.payloads import AAPL, BRK_B, SPY, ZZZZQQ, row, snapshot
from tradingagentspro.integrations.moomoo import MoomooQuoteSource
from tradingagentspro.modules.marketdata import Quote, QuoteSource, QuoteUnavailableError


class FakeOpenD:
    """满足 SnapshotContext 的 OpenD 行情连接。

    - received：每次快照请求收到的代码列表
    - 构造时给出 data 就返回这个快照；给出 error 就返回错误和这条信息
    """

    def __init__(self, data: Any = None, error: str | None = None) -> None:
        self.received: list[list[str]] = []
        self._data = data
        self._error = error

    def get_market_snapshot(self, code_list: list[str]) -> tuple[int, Any]:
        self.received.append(list(code_list))
        if self._error is not None:
            return RET_ERROR, self._error
        return RET_OK, self._data


def _source(opend: FakeOpenD) -> QuoteSource:
    """适配器，按端口类型返回：让 mypy 检查适配器满足端口。"""
    source: QuoteSource = MoomooQuoteSource(opend)
    return source


def _quote(*rows: dict[str, Any], symbol: str = "AAPL") -> Quote:
    """OpenD 返回 rows 时，取 symbol 一只股票的行情。"""
    return _source(FakeOpenD(snapshot(*rows))).quotes([symbol])[symbol]


# 请求


def test_one_request_matched_by_code() -> None:
    """spec「一次请求，按代码对应」：OpenD 按 US.SPY、US.AAPL 的顺序返回。"""
    opend = FakeOpenD(snapshot(SPY, AAPL))

    quotes = _source(opend).quotes(["AAPL", "SPY"])

    assert opend.received == [["US.AAPL", "US.SPY"]]
    assert set(quotes) == {"AAPL", "SPY"}
    assert quotes["AAPL"].last_price == Decimal("333.63")
    assert quotes["SPY"].last_price == Decimal("779.09")


def test_symbol_with_dot() -> None:
    """spec「带点的股票代码」。"""
    opend = FakeOpenD(snapshot(BRK_B))

    quotes = _source(opend).quotes(["BRK.B"])

    assert opend.received == [["US.BRK.B"]]
    assert set(quotes) == {"BRK.B"}
    assert quotes["BRK.B"].last_price == Decimal("505.54")


# 快照的映射


def test_daylight_saving_snapshot() -> None:
    """spec「夏令时的快照」：盘后价 333.85 和盘前价 332.01 都没有用到。"""
    quote = _quote(AAPL)

    assert quote == Quote(
        symbol="AAPL",
        last_price=Decimal("333.63"),
        previous_close=Decimal("332.89"),
        updated_at=datetime(2026, 10, 6, 21, 53, 59, 407000, tzinfo=UTC),
    )
    assert quote.updated_at.tzinfo is UTC


def test_standard_time_without_milliseconds() -> None:
    """spec「冬令时、不带毫秒的更新时刻」。"""
    quote = _quote(row(AAPL, update_time="2026-12-01 10:00:00"))

    assert quote.updated_at == datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
    assert quote.updated_at.tzinfo is UTC


def test_no_trade_yet_after_open() -> None:
    """spec「开盘后还没有成交」：成交量 0，last_price 仍是 333.63。"""
    quote = _quote(row(AAPL, volume=0.0, update_time="2026-10-07 09:29:58.120"))

    assert quote == Quote(
        symbol="AAPL",
        last_price=None,
        previous_close=Decimal("332.89"),
        updated_at=datetime(2026, 10, 7, 13, 29, 58, 120000, tzinfo=UTC),
    )


def test_low_priced_stock_exact_prices() -> None:
    """spec「低价股的价格精确读取」。"""
    quote = _quote(row(AAPL, last_price=0.1234, prev_close_price=0.1199))

    assert quote.last_price is not None
    assert quote.last_price.as_tuple() == Decimal("0.1234").as_tuple()
    assert quote.previous_close.as_tuple() == Decimal("0.1199").as_tuple()


# 查不到的股票


def test_unknown_symbol_left_out() -> None:
    """spec「不存在的代码」。"""
    quotes = _source(FakeOpenD(snapshot(AAPL, ZZZZQQ))).quotes(["AAPL", "ZZZZQQ"])

    assert set(quotes) == {"AAPL"}


# 调用失败


def test_opend_error() -> None:
    """spec「OpenD 返回错误」：报行情不可用，带上 OpenD 的错误信息，不重试。"""
    opend = FakeOpenD(error="请求频率太高")

    with pytest.raises(QuoteUnavailableError, match="请求频率太高"):
        _source(opend).quotes(["AAPL"])
    assert len(opend.received) == 1
