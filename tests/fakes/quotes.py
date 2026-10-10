"""测试用的行情源：按股票保存行情，或者只给跟着测试时钟走的 SPY；测试用它安排行情和调用失败。"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagentspro.modules.marketdata import Quote, QuoteUnavailableError
from tradingagentspro.shared.clock import Clock


class FakeQuoteSource:
    """满足行情模块的 QuoteSource。

    - requests：按顺序记录每次被请求的股票；失败的调用也记录
    - fail_next：设为真时，下一次调用抛 QuoteUnavailableError，之后恢复正常
    - failing：设为真时，每一次调用都抛 QuoteUnavailableError，直到设回假
    - put、drop：之后的调用给出新的行情，或者不再有这只股票的数据
    """

    def __init__(self, *quotes: Quote) -> None:
        self.requests: list[tuple[str, ...]] = []
        self.fail_next = False
        self.failing = False
        self._quotes = {quote.symbol: quote for quote in quotes}

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Quote]:
        self.requests.append(tuple(symbols))
        if self.fail_next or self.failing:
            self.fail_next = False
            raise QuoteUnavailableError("测试安排的失败")
        return {symbol: self._quotes[symbol] for symbol in symbols if symbol in self._quotes}

    def put(self, *quotes: Quote) -> None:
        for quote in quotes:
            self._quotes[quote.symbol] = quote

    def drop(self, symbol: str) -> None:
        self._quotes.pop(symbol, None)


class ClockQuoteSource:
    """满足行情模块的 QuoteSource：只有 SPY 一只，更新时刻跟着测试时钟走。

    - frozen_at：为 None 时 SPY 的更新时刻是时钟的现在之前 10 秒；设上以后停在这个时刻（行情停更）
    - error：设上以后每次调用都抛 QuoteUnavailableError，信息是它（OpenD 断开）；设回 None 恢复
    - calls：调用次数，失败的调用也算
    """

    def __init__(self, clock: Clock) -> None:
        self.frozen_at: datetime | None = None
        self.error: str | None = None
        self.calls = 0
        self._clock = clock

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Quote]:
        self.calls += 1
        if self.error is not None:
            raise QuoteUnavailableError(self.error)
        updated = self.frozen_at or self._clock.now() - timedelta(seconds=10)
        spy = Quote("SPY", Decimal("580.25"), Decimal("578.1"), updated)
        return {symbol: spy for symbol in symbols if symbol == "SPY"}
