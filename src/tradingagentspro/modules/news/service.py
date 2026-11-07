"""新闻和公告的用例：取新闻、取公告，存下还没存过的；读一只股票的新闻和公告、公告的文件；新闻
工具和公告工具的查询。

见 news-and-filings spec、news-tools spec、filing-tools spec、add-news-and-filings 的 design D3、
add-news-tools 的 design D2 和 add-filing-tools 的 design D4、D5、D8。
"""

from collections.abc import Callable, Collection, Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from tradingagentspro.contracts.news import Filing, FilingDocument, NewsArticle
from tradingagentspro.modules.news.config import FilingConfig, NewsConfig
from tradingagentspro.modules.news.domain.filings import (
    READABLE_WITH_AMENDMENTS,
    FilingMatches,
    FilingPage,
    FilingSearch,
    filing_page,
    filing_window,
    find_in_text,
    listed_form,
    newest_filings,
    readable,
)
from tradingagentspro.modules.news.domain.lookup import (
    NewsSearch,
    NewsWindow,
    newest_articles,
    news_window,
)
from tradingagentspro.modules.news.domain.transcripts import (
    TranscriptSearch,
    latest_transcript,
    transcript_window,
)
from tradingagentspro.modules.news.ports import (
    FilingSource,
    NewsSource,
    NewsStore,
    SourceUnavailableError,
)
from tradingagentspro.shared.clock import Clock

_EASTERN: Final = ZoneInfo("America/New_York")


class NewsService:
    """新闻和公告的入口。数据源的失败原样抛出，不重试，这次什么都不存。"""

    def __init__(self, news: NewsSource, filings: FilingSource, store: NewsStore) -> None:
        self._news = news
        self._filings = filings
        self._store = store

    def fetch_articles(
        self, symbols: Iterable[str], since: datetime, until: datetime | None = None
    ) -> tuple[NewsArticle, ...]:
        """since（含）以来发布、标注了这些股票的新闻：存下还没存过的，返回这次新存下的。

        给了 until 时只取 until（含）以前发布的。股票去重；没有股票时直接返回空，不调用新闻源。
        新闻源给出的同一个新闻 ID 只留第一条。返回的按发布时刻、新闻 ID 排序。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return ()
        fetched = _first_of_each(self._news.articles(unique, since, until), lambda a: a.id)
        added = self._store.add_articles(fetched)
        return tuple(sorted(added, key=lambda a: (a.published_at, a.id)))

    def fetch_filings(
        self,
        symbols: Iterable[str],
        since: datetime,
        until: datetime | None = None,
        forms: Collection[str] | None = None,
    ) -> tuple[Filing, ...]:
        """这些股票所属公司 since（含）以来受理的公告：存下还没存过的，返回这次新存下的。

        给了 until 时只留 until（含）以前受理的，给了 forms 时只留表单类型在里面的；留下的才存。
        其余规则同 fetch_articles，按 accession number 去重；返回的按受理时刻、accession number
        排序。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return ()
        kept = (
            f
            for f in self._filings.filings(unique, since)
            if (until is None or f.accepted_at <= until) and (forms is None or f.form in forms)
        )
        fetched = _first_of_each(kept, lambda f: f.accession_number)
        added = self._store.add_filings(fetched)
        return tuple(sorted(added, key=lambda f: (f.accepted_at, f.accession_number)))

    def articles(self, symbol: str, since: datetime) -> tuple[NewsArticle, ...]:
        """已经存下的、标注了这只股票、发布时刻不早于 since 的新闻，按发布时刻、新闻 ID 排序。"""
        return self._store.articles(symbol, since)

    def article(self, article_id: int) -> NewsArticle | None:
        """已经存下的这条新闻；没有时为 None。"""
        return self._store.article(article_id)

    def filings(self, symbol: str, since: datetime) -> tuple[Filing, ...]:
        """已经存下的、标注了这只股票、受理时刻不早于 since 的公告，按受理时刻、accession number
        排序。
        """
        return self._store.filings(symbol, since)

    def filing(self, accession_number: str) -> Filing | None:
        """已经存下的这份公告；没有时为 None。"""
        return self._store.filing(accession_number)

    def documents(self, filing: Filing) -> tuple[FilingDocument, ...]:
        """这份公告的文件：存下过就读状态库；没有时向公告源取一次，有文件就全部存下再返回。"""
        stored = self._store.documents(filing.accession_number)
        if stored:
            return stored
        fetched = tuple(self._filings.documents(filing))
        if fetched:
            self._store.add_documents(filing.accession_number, fetched)
        return fetched


class NewsLookupService:
    """新闻工具的查询：截到快照时刻，早于界线的部分向新闻源补抓，见 news-tools spec。

    lookback 是事件检测的回看时长：界线以后的新闻由事件检测写入和判断。
    """

    def __init__(
        self, news: NewsService, clock: Clock, config: NewsConfig, lookback: timedelta
    ) -> None:
        self._news = news
        self._clock = clock
        self._config = config
        self._lookback = lookback

    def search(
        self,
        symbol: str,
        before: datetime,
        since: date | None,
        until: date | None,
        keyword: str | None,
    ) -> NewsSearch:
        """before 是快照时刻；since、until 是美东日期，都含这一天。"""
        window = news_window(before, _day_start(since), _day_end(until), self._config)
        if window.empty:
            return NewsSearch((), 0, None, True, None)
        incomplete = self._backfill(symbol, window)
        stored = self._news.articles(symbol, window.start)
        articles, total = newest_articles(stored, window, keyword, self._config.max_tool_rows)
        return NewsSearch(articles, total, window.floor, False, incomplete)

    def read(self, symbol: str, before: datetime, article_id: int) -> NewsArticle | None:
        """这条新闻，没有标注这只股票或者晚于快照时刻发布的算没有。"""
        article = self._news.article(article_id)
        if article is None or symbol not in article.symbols or article.published_at > before:
            return None
        return article

    def _backfill(self, symbol: str, window: NewsWindow) -> tuple[datetime, datetime] | None:
        """向新闻源取范围里不晚于界线的部分；新闻源不可用时给出没有取到的时段。"""
        until = min(window.end, self._clock.now() - self._lookback)
        if window.start > until:
            return None
        try:
            self._news.fetch_articles([symbol], window.start, until)
        except SourceUnavailableError:
            return window.start, until
        return None


class FilingLookupService:
    """公告工具的查询：截到快照时刻，向公告源和新闻源补抓，见 filing-tools spec。

    lookback 是事件检测的回看时长；pooled 回答一只股票现在在不在股票池：在的只补到界线，界线以后的
    公告由事件检测写入和判断（design D4）。
    """

    def __init__(
        self,
        news: NewsService,
        clock: Clock,
        config: FilingConfig,
        lookback: timedelta,
        pooled: Callable[[str], bool],
    ) -> None:
        self._news = news
        self._clock = clock
        self._config = config
        self._lookback = lookback
        self._pooled = pooled

    def search(
        self,
        symbol: str,
        before: datetime,
        form: str | None,
        since: date | None,
        until: date | None,
    ) -> FilingSearch:
        """before 是快照时刻；form 是模型写的表单类型；since、until 是美东日期，都含这一天。"""
        listed = None if form is None else listed_form(form)
        if form is not None and listed is None:
            return FilingSearch((), 0, None, False, True, None)
        window = filing_window(before, _day_start(since), _day_end(until), self._config)
        if window.empty:
            return FilingSearch((), 0, None, True, False, None)
        end = window.end
        if self._pooled(symbol):
            end = min(end, self._clock.now() - self._lookback)
        incomplete = None
        if window.start <= end:
            try:
                self._news.fetch_filings([symbol], window.start, end, READABLE_WITH_AMENDMENTS)
            except SourceUnavailableError:
                incomplete = (window.start, end)
        stored = self._news.filings(symbol, window.start)
        filings, total = newest_filings(stored, window, listed, self._config.max_filing_rows)
        return FilingSearch(filings, total, window.floor, False, False, incomplete)

    def filing(self, symbol: str, before: datetime, accession_number: str) -> Filing | None:
        """这份公告；没有、没有标注这只股票、晚于快照时刻受理、表单不能读的都算没有。"""
        filing = self._news.filing(accession_number)
        if (
            filing is None
            or symbol not in filing.symbols
            or filing.accepted_at > before
            or not readable(filing.form)
        ):
            return None
        return filing

    def documents(self, filing: Filing) -> tuple[FilingDocument, ...] | None:
        """这份公告的文件；公告源不可用时为 None。"""
        try:
            return self._news.documents(filing)
        except SourceUnavailableError:
            return None

    def page(self, text: str, start: int) -> FilingPage:
        """从 start 起最多 max_filing_chars 字。"""
        return filing_page(text, start, self._config.max_filing_chars)

    def find(self, text: str, phrase: str, start: int) -> FilingMatches:
        """从 start 往后查找 phrase，最多 max_filing_matches 处。"""
        return find_in_text(text, phrase, start, self._config.max_filing_matches)

    def transcript(self, symbol: str, before: datetime, until: date | None) -> TranscriptSearch:
        """before 是快照时刻；until 是美东截止日，含这一天。"""
        window = transcript_window(before, _day_end(until), self._config)
        if window.empty:
            return TranscriptSearch(None, window.start, window.end, None, True, None)
        end = min(window.end, self._clock.now() - self._lookback)
        incomplete = None
        if window.start <= end:
            try:
                self._news.fetch_articles([symbol], window.start, end)
            except SourceUnavailableError:
                incomplete = (window.start, end)
        stored = self._news.articles(symbol, window.start)
        article = latest_transcript(stored, window, self._config.min_transcript_chars)
        return TranscriptSearch(article, window.start, window.end, window.floor, False, incomplete)


def _day_start(day: date | None) -> datetime | None:
    if day is None:
        return None
    return datetime.combine(day, time.min, _EASTERN).astimezone(UTC)


def _day_end(day: date | None) -> datetime | None:
    if day is None:
        return None
    next_day = datetime.combine(day + timedelta(days=1), time.min, _EASTERN)
    return next_day.astimezone(UTC) - timedelta(microseconds=1)


def _first_of_each[T, K](items: Iterable[T], key: Callable[[T], K]) -> tuple[T, ...]:
    """每个键只留第一次出现的那条，顺序不变。"""
    first: dict[K, T] = {}
    for item in items:
        first.setdefault(key(item), item)
    return tuple(first.values())
