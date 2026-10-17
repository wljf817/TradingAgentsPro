"""新闻和公告模块需要的外部接口：新闻源、公告源和存储。"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from tradingagentspro.contracts.news import Filing, NewsArticle


class SourceUnavailableError(RuntimeError):
    """新闻源或公告源超时、断线、限频或出错：这次没有取到。"""


class NewsSource(Protocol):
    """新闻源。

    symbols 非空、不重复，由模块保证。失败时抛 SourceUnavailableError，不重试。
    """

    def articles(self, symbols: Sequence[str], since: datetime) -> Sequence[NewsArticle]:
        """since（含）以来发布、标注了这些股票中至少一只的新闻，全部取完。"""
        ...


class FilingSource(Protocol):
    """公告源。

    symbols 非空、不重复，由模块保证。失败时抛 SourceUnavailableError，不重试。
    """

    def filings(self, symbols: Sequence[str], since: datetime) -> Sequence[Filing]:
        """这些股票所属公司 since（含）以来受理的公告；查不到公司的股票没有公告。"""
        ...


class NewsStore(Protocol):
    """新闻和公告的存储：只追加，一条一行，见 news-and-filings spec「新闻和公告的存储」。

    数据库的错误和读不出来的条目原样抛出，不当作没有条目，也不当作已经存下。
    """

    def add_articles(self, articles: Sequence[NewsArticle]) -> tuple[NewsArticle, ...]:
        """写入还没存过的新闻（按新闻 ID），按传入的顺序给出这次真正写入的。

        已经存过的不写、不改；同时写入同一条时只有一个调用给出它。
        """
        ...

    def add_filings(self, filings: Sequence[Filing]) -> tuple[Filing, ...]:
        """写入还没存过的公告（按 accession number），规则同 add_articles。"""
        ...

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]:
        """标注了 symbol、发布时刻不早于 since 的新闻，按发布时刻、新闻 ID 排序。"""
        ...

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]:
        """标注了 symbol、受理时刻不早于 since 的公告，按受理时刻、accession number 排序。"""
        ...
