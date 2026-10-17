"""moomoo 适配器：通过 OpenD 实现行情模块的行情源，以及寻股粗筛模块的股票列表。

见 openspec 的 moomoo-quotes spec 和 moomoo-securities spec。装配时用 open_quote_context 建一个
行情连接，交给 MoomooQuoteSource 和 MoomooSecuritySource，退出时关闭连接。
"""

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
    "MoomooQuoteSource",
    "MoomooSecuritiesConfig",
    "MoomooSecuritySource",
    "SecuritiesContext",
    "SnapshotContext",
    "open_quote_context",
]
