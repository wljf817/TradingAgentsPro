"""新闻和公告：把池内股票和持仓的新闻、SEC 公告取下来，存进共享状态库（当前市场事实，写入者是
数据层），并告诉调用方这次新存下了哪些。

规则见 openspec 的 news-and-filings spec、news-tools spec 和 filing-tools spec。新闻源、公告源
和存储由 bootstrap 注入；条目的格式在 contracts.news。新闻存在 news_articles 表里，公告存在
news_filings 表里，公告的文件存在 news_filing_documents 表里，数据库实现是 PostgresNewsStore。模块
之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.news.config import FilingConfig, NewsConfig
from tradingagentspro.modules.news.domain.filings import (
    FilingMatch,
    FilingMatches,
    FilingPage,
    FilingSearch,
)
from tradingagentspro.modules.news.domain.lookup import NewsSearch
from tradingagentspro.modules.news.domain.transcripts import TranscriptSearch
from tradingagentspro.modules.news.ports import (
    FilingSource,
    NewsSource,
    NewsStore,
    SourceUnavailableError,
)
from tradingagentspro.modules.news.repo import PostgresNewsStore
from tradingagentspro.modules.news.service import (
    FilingLookupService,
    NewsLookupService,
    NewsService,
)

__all__ = [
    "FilingConfig",
    "FilingLookupService",
    "FilingMatch",
    "FilingMatches",
    "FilingPage",
    "FilingSearch",
    "FilingSource",
    "NewsConfig",
    "NewsLookupService",
    "NewsSearch",
    "NewsService",
    "NewsSource",
    "NewsStore",
    "PostgresNewsStore",
    "SourceUnavailableError",
    "TranscriptSearch",
]
