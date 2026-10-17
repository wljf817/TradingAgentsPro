"""Alpaca 适配器：用 Alpaca Trading API 实现执行模块的券商端口，模拟盘用它；用 Alpaca 市场数据
API 的新闻接口实现新闻和公告模块的新闻源。

见 openspec 的 alpaca-broker spec 和 alpaca-news spec。装配时建一个 AlpacaClient，两个券商适配器
共用；新闻源有自己的配置和连接，key 是同一组。
"""

from tradingagentspro.integrations.alpaca.broker import AlpacaBroker
from tradingagentspro.integrations.alpaca.client import (
    PAPER_URL,
    AlpacaClient,
    AlpacaConfig,
    configure_account,
)
from tradingagentspro.integrations.alpaca.news import DATA_URL, AlpacaNewsConfig, AlpacaNewsSource
from tradingagentspro.integrations.alpaca.stops import AlpacaStopBroker

__all__ = [
    "DATA_URL",
    "PAPER_URL",
    "AlpacaBroker",
    "AlpacaClient",
    "AlpacaConfig",
    "AlpacaNewsConfig",
    "AlpacaNewsSource",
    "AlpacaStopBroker",
    "configure_account",
]
