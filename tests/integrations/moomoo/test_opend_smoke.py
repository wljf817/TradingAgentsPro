"""连本机 OpenD 的冒烟测试：验证文档没写清、替身只能照录下的快照假设的行为。

见 add-moomoo-quotes 的 design D8。默认不运行，用 uv run pytest -m moomoo_opend 运行；
本机 127.0.0.1:11111 没有开着 OpenD 时跳过。只读快照，不订阅、不下单。
按系统时钟和纽交所日历分三组：任何时候、常规交易时段内、开盘前 30 分钟内，不在对应时段时跳过那一组。
"""

import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from tradingagentspro.integrations.moomoo import MoomooConfig, MoomooQuoteSource, open_quote_context
from tradingagentspro.integrations.nyse_calendar import load_nyse_calendar
from tradingagentspro.integrations.system_clock import SystemClock
from tradingagentspro.modules.marketdata import MarketDataConfig, MarketDataService
from tradingagentspro.shared.trading_calendar import TradingCalendar

pytestmark = pytest.mark.moomoo_opend

CONFIG = MoomooConfig()
CLOCK = SystemClock()


def _opend_listening() -> bool:
    try:
        with socket.create_connection((CONFIG.host, CONFIG.port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def service() -> Iterator[MarketDataService]:
    if not _opend_listening():
        pytest.skip(f"{CONFIG.host}:{CONFIG.port} 没有开着 OpenD")
    context = open_quote_context(CONFIG)
    try:
        yield MarketDataService(MoomooQuoteSource(context), MarketDataConfig())
    finally:
        context.close()


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    """覆盖现在前后 10 天的纽交所日历；只用来判断现在在哪个时段。"""
    now = CLOCK.now()
    return load_nyse_calendar((now - timedelta(days=10)).date(), (now + timedelta(days=10)).date())


def test_quotes_at_any_time(service: MarketDataService) -> None:
    """任何时候：AAPL、SPY 有行情，不存在的代码没有；更新时刻换算成 UTC 后不晚于现在。"""
    quotes = service.quotes(["AAPL", "SPY", "ZZZZQQ"])
    now = CLOCK.now()

    assert set(quotes) == {"AAPL", "SPY"}
    for quote in quotes.values():
        assert quote.previous_close > 0
        assert quote.updated_at.tzinfo is UTC
        # 时区换算错了会差几个小时：既不能晚于现在，也不能早于几天前
        assert now - timedelta(days=5) < quote.updated_at <= now + timedelta(seconds=5)
    assert service.last_update() >= quotes["SPY"].updated_at


def test_real_time_in_session(service: MarketDataService, calendar: TradingCalendar) -> None:
    """常规交易时段内：SPY 有最新价，更新时刻和现在相差不超过 10 秒，说明拿到的是实时行情。"""
    if calendar.session_at(CLOCK.now()) is None:
        pytest.skip("不在常规交易时段内")

    spy = service.quotes(["SPY"])["SPY"]
    now = CLOCK.now()

    assert spy.last_price is not None
    assert abs(now - spy.updated_at) <= timedelta(seconds=10)


def test_no_last_price_before_open(service: MarketDataService, calendar: TradingCalendar) -> None:
    """开盘前 30 分钟内：SPY 没有最新价。

    验证开盘前 OpenD 已经把常规时段的成交量清零（design D5）。
    """
    now: datetime = CLOCK.now()
    until_open = calendar.next_session_after(now).open - now
    if not timedelta(0) < until_open <= timedelta(minutes=30):
        pytest.skip("不在开盘前 30 分钟内")

    assert service.quotes(["SPY"])["SPY"].last_price is None
