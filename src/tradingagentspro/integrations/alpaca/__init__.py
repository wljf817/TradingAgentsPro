"""Alpaca 适配器：用 Alpaca Trading API 实现执行模块的券商端口，模拟盘用它。

见 openspec 的 alpaca-broker spec。装配时建一个 AlpacaClient，两个适配器共用。
"""

from tradingagentspro.integrations.alpaca.broker import AlpacaBroker
from tradingagentspro.integrations.alpaca.client import (
    PAPER_URL,
    AlpacaClient,
    AlpacaConfig,
    configure_account,
)
from tradingagentspro.integrations.alpaca.stops import AlpacaStopBroker

__all__ = [
    "PAPER_URL",
    "AlpacaBroker",
    "AlpacaClient",
    "AlpacaConfig",
    "AlpacaStopBroker",
    "configure_account",
]
