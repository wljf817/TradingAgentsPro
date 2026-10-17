"""新闻和公告的用例：取新闻、取公告，存下还没存过的；读一只股票的新闻和公告。

见 news-and-filings spec 和 add-news-and-filings 的 design D3。
"""

from collections.abc import Callable, Iterable
from datetime import datetime

from tradingagentspro.contracts.news import Filing, NewsArticle
from tradingagentspro.modules.news.ports import FilingSource, NewsSource, NewsStore


class NewsService:
    """新闻和公告的入口。数据源的失败原样抛出，不重试，这次什么都不存。"""

    def __init__(self, news: NewsSource, filings: FilingSource, store: NewsStore) -> None:
        self._news = news
        self._filings = filings
        self._store = store

    def fetch_articles(self, symbols: Iterable[str], since: datetime) -> tuple[NewsArticle, ...]:
        """since（含）以来发布、标注了这些股票的新闻：存下还没存过的，返回这次新存下的。

        股票去重；没有股票时直接返回空，不调用新闻源。新闻源给出的同一个新闻 ID 只留第一条。
        返回的按发布时刻、新闻 ID 排序。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return ()
        fetched = _first_of_each(self._news.articles(unique, since), lambda a: a.id)
        added = self._store.add_articles(fetched)
        return tuple(sorted(added, key=lambda a: (a.published_at, a.id)))

    def fetch_filings(self, symbols: Iterable[str], since: datetime) -> tuple[Filing, ...]:
        """这些股票所属公司 since（含）以来受理的公告：存下还没存过的，返回这次新存下的。

        规则同 fetch_articles，按 accession number 去重；返回的按受理时刻、accession number 排序。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return ()
        fetched = _first_of_each(self._filings.filings(unique, since), lambda f: f.accession_number)
        added = self._store.add_filings(fetched)
        return tuple(sorted(added, key=lambda f: (f.accepted_at, f.accession_number)))

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]:
        """已经存下的、标注了这只股票、发布时刻不早于 since 的新闻，按发布时刻、新闻 ID 排序。"""
        return self._store.articles(symbol, since)

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]:
        """已经存下的、标注了这只股票、受理时刻不早于 since 的公告，按受理时刻、accession number
        排序。
        """
        return self._store.filings(symbol, since)


def _first_of_each[T, K](items: Iterable[T], key: Callable[[T], K]) -> tuple[T, ...]:
    """每个键只留第一次出现的那条，顺序不变。"""
    first: dict[K, T] = {}
    for item in items:
        first.setdefault(key(item), item)
    return tuple(first.values())
