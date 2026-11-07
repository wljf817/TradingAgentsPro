"""新闻工具查询的范围和筛选，见 news-tools spec「列出新闻」。"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradingagentspro.contracts.news import NewsArticle
from tradingagentspro.modules.news.config import NewsConfig


@dataclass(frozen=True, slots=True)
class NewsWindow:
    """要查的范围，两端都含。"""

    start: datetime
    end: datetime
    floor: datetime | None  # 起始被裁到的时刻；没有裁时为 None

    @property
    def empty(self) -> bool:
        return self.start > self.end


@dataclass(frozen=True, slots=True)
class NewsSearch:
    """一次查询的结果。"""

    articles: tuple[NewsArticle, ...]  # 从晚到早
    total: int  # 范围内符合条件的一共多少条
    floor: datetime | None  # 这个时刻以前的查不到；起始没有被裁时为 None
    empty_range: bool
    incomplete: tuple[datetime, datetime] | None  # 补抓失败的时段


def news_window(
    before: datetime, start: datetime | None, end: datetime | None, config: NewsConfig
) -> NewsWindow:
    """before 是快照时刻；start、end 是模型写的范围，没写时为 None。"""
    last = before if end is None else min(end, before)
    first = last - timedelta(days=config.tool_default_days) if start is None else start
    floor = before - timedelta(days=config.tool_max_days)
    if first < floor:
        return NewsWindow(floor, last, floor)
    return NewsWindow(first, last, None)


def newest_articles(
    stored: Iterable[NewsArticle], window: NewsWindow, keyword: str | None, limit: int
) -> tuple[tuple[NewsArticle, ...], int]:
    """范围内符合关键词的最新 limit 条，从晚到早，和一共多少条。"""
    found = [
        article
        for article in stored
        if window.start <= article.published_at <= window.end and _matches(article, keyword)
    ]
    found.sort(key=lambda a: (a.published_at, a.id), reverse=True)
    return tuple(found[:limit]), len(found)


def _matches(article: NewsArticle, keyword: str | None) -> bool:
    if keyword is None:
        return True
    wanted = keyword.casefold()
    return wanted in article.headline.casefold() or wanted in article.summary.casefold()
