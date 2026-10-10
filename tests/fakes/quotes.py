"""测试用的行情源：按股票保存行情，测试用它安排行情和调用失败。"""

from collections.abc import Mapping, Sequence

from tradingagentspro.modules.marketdata import Quote, QuoteUnavailableError


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
