"""SEC EDGAR 适配器：用 SEC 的股票代码表和 submissions 实现新闻和公告模块的公告源。

见 openspec 的 sec-filings spec。装配时用 SecConfig（User-Agent 来自环境变量 SEC_USER_AGENT）建一个
SecFilingSource，同一个进程只建一个，对 SEC 的请求才会依次发出。
"""

from tradingagentspro.integrations.sec.edgar import SecConfig, SecFilingSource

__all__ = ["SecConfig", "SecFilingSource"]
