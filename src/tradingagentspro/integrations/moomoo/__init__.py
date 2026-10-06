"""moomoo 适配器：通过 OpenD 实现行情模块的行情源。

见 openspec 的 moomoo-quotes spec。装配时用 open_quote_context 建一个行情连接，交给
MoomooQuoteSource，退出时关闭连接。
"""

from tradingagentspro.integrations.moomoo.quotes import (
    MoomooConfig,
    MoomooQuoteSource,
    SnapshotContext,
    open_quote_context,
)

__all__ = [
    "MoomooConfig",
    "MoomooQuoteSource",
    "SnapshotContext",
    "open_quote_context",
]
