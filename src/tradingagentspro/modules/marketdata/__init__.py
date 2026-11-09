"""行情：给交易侧提供股票的实时行情，并给出行情最近一次更新的时刻；给 Quant 提供 K 线和量化指标。
对应架构图的「数据源」节点。

规则见 openspec 的 market-data spec、market-context spec、price-bars spec 和 quant-indicators
spec。行情源、VIX 源和 K 线源由 bootstrap 注入。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.marketdata.config import IndicatorConfig, MarketDataConfig
from tradingagentspro.modules.marketdata.domain.bars import (
    Bar,
    Bars,
    SourceBar,
    SourceDayBar,
    Split,
)
from tradingagentspro.modules.marketdata.domain.indicators import EventTime, IndicatorRequest
from tradingagentspro.modules.marketdata.domain.quote import Quote
from tradingagentspro.modules.marketdata.ports import (
    BarHistorySource,
    BarsUnavailableError,
    QuoteSource,
    QuoteUnavailableError,
    VixSource,
    VixUnavailableError,
)
from tradingagentspro.modules.marketdata.service import (
    BarService,
    IndicatorService,
    MarketContextService,
    MarketDataService,
)

__all__ = [
    "Bar",
    "BarHistorySource",
    "BarService",
    "Bars",
    "BarsUnavailableError",
    "EventTime",
    "IndicatorConfig",
    "IndicatorRequest",
    "IndicatorService",
    "MarketContextService",
    "MarketDataConfig",
    "MarketDataService",
    "Quote",
    "QuoteSource",
    "QuoteUnavailableError",
    "SourceBar",
    "SourceDayBar",
    "Split",
    "VixSource",
    "VixUnavailableError",
]
