"""行情：给交易侧提供股票的实时行情，并给出行情最近一次更新的时刻。对应架构图的「数据源」节点。

规则见 openspec 的 market-data spec 和 market-context spec。行情源和 VIX 源由 bootstrap 注入。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.marketdata.config import MarketDataConfig
from tradingagentspro.modules.marketdata.domain.quote import Quote
from tradingagentspro.modules.marketdata.ports import (
    QuoteSource,
    QuoteUnavailableError,
    VixSource,
    VixUnavailableError,
)
from tradingagentspro.modules.marketdata.service import MarketContextService, MarketDataService

__all__ = [
    "MarketContextService",
    "MarketDataConfig",
    "MarketDataService",
    "Quote",
    "QuoteSource",
    "QuoteUnavailableError",
    "VixSource",
    "VixUnavailableError",
]
