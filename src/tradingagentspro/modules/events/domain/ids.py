"""事件 ID 和检测时刻：event-rules spec「事件 ID 和去重」。

事件 ID 写成 `<股票>:<交易日>:<种类>`，同时是去重的键：同一个事件 ID 只能记下一次
（add-event-detection 的 design D3）。交易日是检测时刻归属的交易日。
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from tradingagentspro.shared.trading_calendar import TradingCalendar


class EventKind(StrEnum):
    """事件 ID 里的种类：同一只股票同一个交易日每种最多一个事件。"""

    PRICE_UP = "price_up"  # 价格上涨
    PRICE_DOWN = "price_down"  # 价格下跌，包括交给紧急风险退出的
    VOLUME = "volume"  # 成交量异动
    NEWS = "news"  # 新闻或公告，不是持仓风险级别的
    NEWS_RISK = "news_risk"  # 新闻或公告，持仓风险级别的
    EARNINGS = "earnings"  # 财报发布


@dataclass(frozen=True, slots=True)
class Detection:
    """一轮检测：开始时读的时刻，和它归属的交易日。"""

    at: datetime
    day: date


def detection(at: datetime, calendar: TradingCalendar) -> Detection:
    """在 at 检测：盘前和盘中是当天的交易日，收盘以后和休市日是之后的第一个交易日。"""
    return Detection(at, calendar.session_closing_after(at).day)


def event_id(symbol: str, day: date, kind: EventKind) -> str:
    """这只股票这个交易日这种事件的事件 ID，比如 CCC:2026-10-14:price_down。"""
    return f"{symbol}:{day.isoformat()}:{kind}"
