"""事件：对应架构图的「事件检测层」。按代码规则判断价格和成交量、新闻、公告、财报发布什么时候成为
事件，给每个事件一个事件 ID，记下事件并写事件检测层的程序日志。

规则见 openspec 的 event-rules spec 和 event-detection spec「事件的存储」「读取事件」，做法见
add-event-detection 的 design D1 到 D11、D14、D16。事件存在 events_detected 表里，数据库实现是
PostgresEventStore；存储由 bootstrap 注入。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.events.config import EventRulesConfig, FilingTiers, KeywordRule
from tradingagentspro.modules.events.domain.earnings import (
    CalendarRelease,
    earnings_window_start,
    expected_release,
    release_event,
)
from tradingagentspro.modules.events.domain.filings import judge_filing
from tradingagentspro.modules.events.domain.ids import Detection, EventKind, detection, event_id
from tradingagentspro.modules.events.domain.news import (
    Discard,
    DiscardReason,
    Monitored,
    judge_article,
)
from tradingagentspro.modules.events.domain.price import (
    PricedStock,
    Reading,
    price_event,
    volume_event,
)
from tradingagentspro.modules.events.ports import EventStore
from tradingagentspro.modules.events.repo import PostgresEventStore
from tradingagentspro.modules.events.service import EventService

__all__ = [
    "CalendarRelease",
    "Detection",
    "Discard",
    "DiscardReason",
    "EventKind",
    "EventRulesConfig",
    "EventService",
    "EventStore",
    "FilingTiers",
    "KeywordRule",
    "Monitored",
    "PostgresEventStore",
    "PricedStock",
    "Reading",
    "detection",
    "earnings_window_start",
    "event_id",
    "expected_release",
    "judge_article",
    "judge_filing",
    "price_event",
    "release_event",
    "volume_event",
]
