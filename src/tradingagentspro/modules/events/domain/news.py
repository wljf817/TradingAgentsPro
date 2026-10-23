"""新闻和公告的初筛：event-rules spec「新闻的初筛」「新闻关键词」，以及新闻和公告共用的级别和
持仓风险的判断、没成为事件的原因。不用模型：强度是标题命中的关键词的级别，见 add-event-detection 的
design D7。
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome, EventSource
from tradingagentspro.contracts.news import NewsArticle
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.events.config import EventRulesConfig
from tradingagentspro.modules.events.domain.ids import Detection, EventKind, event_id
from tradingagentspro.modules.events.domain.text import LEVELS, negative_text

_MAJOR: Final = 2  # 重大
_ORDINARY: Final = 1  # 一般


@dataclass(frozen=True, slots=True)
class Monitored:
    """一只监控股票：股票池里的股票、它这一轮的状态和加入股票池的时刻。"""

    symbol: str
    status: StockStatus
    added_at: datetime


class DiscardReason(StrEnum):
    """一条新闻或一份公告没有成为事件的原因。"""

    BEFORE_ADDED = "before_added"  # 加入股票池以前发布
    SOURCE = "source"  # 来源不在名单里
    TOO_MANY_SYMBOLS = "too_many_symbols"  # 标注的股票太多
    EXCLUDED = "excluded"  # 命中排除词
    NO_KEYWORD = "no_keyword"  # 没有命中关键词
    LEVEL = "level"  # 级别不够
    FORM = "form"  # 表单不算
    SAME_DAY = "same_day"  # 当天已有新闻或公告事件
    EARNINGS_TODAY = "earnings_today"  # 当天已有财报发布事件
    RECENT_EARNINGS = "recent_earnings"  # 最近已有财报发布事件


@dataclass(frozen=True, slots=True)
class Discard:
    """没有成为事件。"""

    reason: DiscardReason


@dataclass(frozen=True, slots=True)
class Screened:
    """初筛的结果：级别（1 一般、2 重大）和是否负面，以及事件要用的来源、发生时刻、引用和说明。"""

    category: EventCategory
    source: EventSource
    level: int
    negative: bool
    occurred_at: datetime
    refs: tuple[str, ...]
    summary: str  # 说明里级别、负面之前的部分


def matches(word: str, text: str) -> bool:
    """word 出现在 text 里，并且前面是开头或者不是字母、数字；不分大小写，可以只是一个词的开头。"""
    pattern = r"(?<![0-9a-z])" + re.escape(word.lower())
    return re.search(pattern, text.lower()) is not None


def judge_article(
    article: NewsArticle, stock: Monitored, at: Detection, config: EventRulesConfig
) -> Event | Discard:
    """一条新闻对一只监控股票：按 spec「新闻的初筛」依次检查，通过的是新闻事件。"""
    if article.published_at < stock.added_at:
        return Discard(DiscardReason.BEFORE_ADDED)
    if article.source.lower() not in {source.lower() for source in config.news_sources}:
        return Discard(DiscardReason.SOURCE)
    if len(article.symbols) > config.max_symbols:
        return Discard(DiscardReason.TOO_MANY_SYMBOLS)
    headline = article.headline
    if any(matches(word, headline) for word in config.exclusions):
        return Discard(DiscardReason.EXCLUDED)
    hits = [rule for rule in config.keywords if matches(rule.keyword, headline)]
    if not hits:
        return Discard(DiscardReason.NO_KEYWORD)
    keywords = "、".join(dict.fromkeys(rule.keyword for rule in hits))
    screened = Screened(
        category=EventCategory.NEWS,
        source=EventSource.NEWS,
        level=max(rule.level for rule in hits),
        negative=any(rule.negative for rule in hits),
        occurred_at=article.published_at,
        refs=(str(article.id),),
        summary=f"新闻「{headline}」：命中关键词 {keywords}",
    )
    return screened_event(screened, stock, at)


def screened_event(screened: Screened, stock: Monitored, at: Detection) -> Event | Discard:
    """级别达到这只股票的状态的最低级别（候选是重大，其他是一般）时是事件；持仓并且负面时是持仓风险
    级别，不看级别。
    """
    risk = stock.status is StockStatus.HOLDING and screened.negative
    minimum = _MAJOR if stock.status is StockStatus.CANDIDATE else _ORDINARY
    if not risk and screened.level < minimum:
        return Discard(DiscardReason.LEVEL)
    level, negative = LEVELS[screened.level], negative_text(screened.negative)
    return Event(
        event_id=event_id(stock.symbol, at.day, EventKind.NEWS_RISK if risk else EventKind.NEWS),
        symbol=stock.symbol,
        category=screened.category,
        holding_risk=risk,
        source=screened.source,
        strength=Decimal(screened.level),
        occurred_at=screened.occurred_at,
        detected_at=at.at,
        summary=f"{screened.summary}，{level}，{negative}",
        refs=screened.refs,
        outcome=EventOutcome.QUEUED,
    )
