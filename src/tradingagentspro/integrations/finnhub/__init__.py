"""Finnhub 适配器：用 Finnhub 免费档的 REST API 实现公司数据模块的数据源。

见 openspec 的 finnhub-company-data spec。装配时用环境变量 FINNHUB_API_KEY 建 FinnhubConfig，交给
FinnhubSource。
"""

from tradingagentspro.integrations.finnhub.source import FINNHUB_URL, FinnhubConfig, FinnhubSource

__all__ = ["FINNHUB_URL", "FinnhubConfig", "FinnhubSource"]
