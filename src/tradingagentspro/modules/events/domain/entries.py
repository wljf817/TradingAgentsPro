"""事件检测层的程序日志的说明：检测到事件、丢弃事件，见 program-log spec「事件检测层的动作」。"""

from typing import Final

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome, EventSource
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.events.domain.news import DiscardReason
from tradingagentspro.modules.events.domain.text import number

_CATEGORIES: Final = {
    EventCategory.PRICE_VOLUME: "价格和成交量异动",
    EventCategory.NEWS: "新闻",
    EventCategory.FILING: "公告",
    EventCategory.EARNINGS: "财报发布",
    EventCategory.ENTRY: "入场条件满足",
    EventCategory.THESIS_CONDITION: "达到目标价或证伪条件触发",
}
_STATUSES: Final = {
    StockStatus.CANDIDATE: "候选",
    StockStatus.WATCH: "关注",
    StockStatus.HOLDING: "持仓",
}
_SOURCES: Final = {
    EventSource.QUOTES: "行情",
    EventSource.NEWS: "新闻源",
    EventSource.FILINGS: "公告源",
    EventSource.EARNINGS_CALENDAR: "财报日历",
}
_OUTCOMES: Final = {
    EventOutcome.QUEUED: "排队",
    EventOutcome.EMERGENCY_EXIT: "交给紧急风险退出、不排队",
}
_REASONS: Final = {
    DiscardReason.BEFORE_ADDED: "加入股票池以前发布",
    DiscardReason.SOURCE: "来源不在名单里",
    DiscardReason.TOO_MANY_SYMBOLS: "标注的股票太多",
    DiscardReason.EXCLUDED: "命中排除词",
    DiscardReason.NO_KEYWORD: "没有命中关键词",
    DiscardReason.LEVEL: "级别不够",
    DiscardReason.FORM: "表单不算",
    DiscardReason.SAME_DAY: "当天已有新闻或公告事件",
    DiscardReason.EARNINGS_TODAY: "当天已有财报发布事件",
    DiscardReason.RECENT_EARNINGS: "最近已有财报发布事件",
}


def detected_text(event: Event, status: StockStatus) -> str:
    """检测到事件：类别、股票和这时的状态、是否持仓风险级别、来源、强度、事件的说明和处理。"""
    risk = "持仓风险级别" if event.holding_risk else "不是持仓风险级别"
    strength = "没有强度" if event.strength is None else f"强度 {number(event.strength)}"
    return (
        f"{_CATEGORIES[event.category]}，{event.symbol}（{_STATUSES[status]}），{risk}，"
        f"来源{_SOURCES[event.source]}，{strength}；{event.summary}；"
        f"处理：{_OUTCOMES[event.outcome]}"
    )


def discarded_text(item: str, reason: DiscardReason) -> str:
    """丢弃事件：新闻的标题或公告的表单和 item，以及原因。"""
    return f"{item}；原因：{_REASONS[reason]}"
