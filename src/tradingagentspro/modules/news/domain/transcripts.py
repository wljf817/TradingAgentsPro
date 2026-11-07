"""电话会文字稿的规则：查找的范围、哪条新闻算文字稿、取最新一篇，见 filing-tools spec「读电话会
文字稿」和 add-filing-tools 的 design D8。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from tradingagentspro.contracts.news import NewsArticle
from tradingagentspro.modules.news.config import FilingConfig

_TRANSCRIPT: Final = "transcript"
_CALLS: Final = ("earnings call", "earnings conference call")


@dataclass(frozen=True, slots=True)
class TranscriptWindow:
    """要找的范围，两端都含。"""

    start: datetime
    end: datetime
    floor: datetime | None  # 起始被裁到的时刻；没有裁时为 None
    empty: bool  # 结束早于最早


@dataclass(frozen=True, slots=True)
class TranscriptSearch:
    """一次查找的结果。"""

    article: NewsArticle | None  # 找到的文字稿；没有时为 None
    start: datetime
    end: datetime
    floor: datetime | None
    empty_range: bool
    incomplete: tuple[datetime, datetime] | None  # 补抓失败的时段


def transcript_window(
    before: datetime, end: datetime | None, config: FilingConfig
) -> TranscriptWindow:
    """before 是快照时刻；end 是截止日结束的时刻，没写截止日时为 None。"""
    last = before if end is None else min(end, before)
    earliest = before - timedelta(days=config.transcript_max_days)
    first = last - timedelta(days=config.transcript_window_days)
    if last < earliest:
        return TranscriptWindow(earliest, last, None, True)
    if first < earliest:
        return TranscriptWindow(earliest, last, earliest, False)
    return TranscriptWindow(first, last, None, False)


def is_transcript(article: NewsArticle, min_chars: int) -> bool:
    """标题含 transcript 和 earnings call 或 earnings conference call，存下的正文够长。"""
    headline = article.headline.casefold()
    return (
        _TRANSCRIPT in headline
        and any(call in headline for call in _CALLS)
        and len(article.content) >= min_chars
    )


def latest_transcript(
    stored: Iterable[NewsArticle], window: TranscriptWindow, min_chars: int
) -> NewsArticle | None:
    """范围内发布时刻最晚的文字稿，相同的取新闻 ID 大的；没有时为 None。"""
    found = [
        article
        for article in stored
        if window.start <= article.published_at <= window.end and is_transcript(article, min_chars)
    ]
    return max(found, key=lambda a: (a.published_at, a.id), default=None)
