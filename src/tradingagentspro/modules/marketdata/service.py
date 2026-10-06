"""行情的用例：取一组股票的行情，给出行情最近一次更新的时刻。

见 market-data spec。
"""

from collections.abc import Iterable
from datetime import datetime

from tradingagentspro.modules.marketdata.config import MarketDataConfig
from tradingagentspro.modules.marketdata.domain.quote import Quote
from tradingagentspro.modules.marketdata.ports import QuoteSource, QuoteUnavailableError


class MarketDataService:
    """行情的入口。不保存状态，每次调用都向行情源取；行情源的失败原样抛出，不重试。"""

    def __init__(self, source: QuoteSource, config: MarketDataConfig) -> None:
        self._source = source
        self._config = config

    def quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        """这些股票的行情：去重后一次向行情源取；没有股票时直接返回空，不调用行情源。

        行情源没有数据的股票不在结果里。行情源失败时抛 QuoteUnavailableError。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return {}
        return dict(self._source.quotes(unique))

    def last_update(self) -> datetime:
        """行情最近一次更新的时刻：参考股票的行情的更新时刻，给健康监控判断数据延迟。

        见 add-moomoo-quotes 的 design D3。行情源没有参考股票的数据、或者行情源失败时，
        抛 QuoteUnavailableError。
        """
        symbol = self._config.reference_symbol
        quote = self._source.quotes((symbol,)).get(symbol)
        if quote is None:
            raise QuoteUnavailableError(f"行情源没有参考股票 {symbol} 的数据")
        return quote.updated_at
