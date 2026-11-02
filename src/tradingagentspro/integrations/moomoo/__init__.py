"""moomoo 适配器：通过 OpenD 实现行情模块的行情源、寻股粗筛模块的股票列表，以及事件模块的到价提醒。

见 openspec 的 moomoo-quotes spec、moomoo-securities spec 和 moomoo-price-alerts spec。装配时用
open_quote_context 建一个行情连接，交给 MoomooQuoteSource、MoomooSecuritySource 和
MoomooPriceAlerts，退出时关闭连接。
"""

from tradingagentspro.integrations.moomoo.alerts import MoomooPriceAlerts, ReminderContext
from tradingagentspro.integrations.moomoo.quotes import (
    MoomooConfig,
    MoomooQuoteSource,
    SnapshotContext,
    open_quote_context,
)
from tradingagentspro.integrations.moomoo.securities import (
    MoomooSecuritiesConfig,
    MoomooSecuritySource,
    SecuritiesContext,
)

__all__ = [
    "MoomooConfig",
    "MoomooPriceAlerts",
    "MoomooQuoteSource",
    "MoomooSecuritiesConfig",
    "MoomooSecuritySource",
    "ReminderContext",
    "SecuritiesContext",
    "SnapshotContext",
    "open_quote_context",
]
