"""财报发布：event-rules spec「财报发布」。两路先到的算：8-K 的 item 2.02，或者财报日历上一次发布的
预计发布时刻过了。同一只股票「财报发布的间隔」个交易日里最多一个，这一条要读已经记下的事件，由事件
检测用 earnings_window_start 判断。见 add-event-detection 的 design D9。
"""

from dataclasses import dataclass
from datetime import date, datetime

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome, EventSource
from tradingagentspro.contracts.news import Filing
from tradingagentspro.contracts.snapshot import EarningsTime
from tradingagentspro.modules.events.config import EventRulesConfig
from tradingagentspro.modules.events.domain.ids import Detection, EventKind, event_id
from tradingagentspro.modules.events.domain.text import RELEASE_TIMES, utc
from tradingagentspro.shared.trading_calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class CalendarRelease:
    """财报日历上一次发布的最新版本：发布日是美东日期，发布时段没写时为 None。"""

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    day: date
    time: EarningsTime | None

    @property
    def key(self) -> str:
        """财报发布的键，和公司数据模块的写法相同，比如 AAPL:2026Q4。"""
        return f"{self.symbol}:{self.fiscal_year}Q{self.fiscal_quarter}"


def filing_earnings_event(filing: Filing, symbol: str, at: Detection) -> Event:
    """8-K 或 8-K/A 带 item 2.02：这只股票的财报发布事件，发生时刻是受理时刻。"""
    return Event(
        event_id=event_id(symbol, at.day, EventKind.EARNINGS),
        symbol=symbol,
        category=EventCategory.EARNINGS,
        holding_risk=False,
        source=EventSource.FILINGS,
        strength=None,
        occurred_at=filing.accepted_at,
        detected_at=at.at,
        summary=(
            f"财报公告 {filing.form}，item {'、'.join(filing.items)}"
            f"（{filing.accession_number}），受理时刻 {utc(filing.accepted_at)}"
        ),
        refs=(filing.accession_number,),
        outcome=EventOutcome.QUEUED,
    )


def expected_release(
    day: date, time: EarningsTime | None, calendar: TradingCalendar, config: EventRulesConfig
) -> datetime:
    """预计发布时刻：盘前是发布日的开盘，盘中是收盘，盘后和没写是收盘加「盘后发布的等待」；
    发布日不是交易日时按之后的第一个交易日算。
    """
    session = calendar.session(day) or calendar.next_session(day)
    match time:
        case EarningsTime.BEFORE_OPEN:
            return session.open
        case EarningsTime.DURING_MARKET:
            return session.close
        case _:
            return session.close + config.after_close_wait


def release_event(
    release: CalendarRelease,
    at: Detection,
    since: datetime,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None:
    """财报日历这一路：预计发布时刻不晚于检测时刻、不早于回看起点 since 时是财报发布事件。"""
    moment = expected_release(release.day, release.time, calendar, config)
    if not since <= moment <= at.at:
        return None
    slot = "没写发布时段" if release.time is None else RELEASE_TIMES[release.time]
    return Event(
        event_id=event_id(release.symbol, at.day, EventKind.EARNINGS),
        symbol=release.symbol,
        category=EventCategory.EARNINGS,
        holding_risk=False,
        source=EventSource.EARNINGS_CALENDAR,
        strength=None,
        occurred_at=moment,
        detected_at=at.at,
        summary=(
            f"财报日历：{release.fiscal_year} 财年第 {release.fiscal_quarter} 季度，"
            f"发布日 {release.day.isoformat()}，{slot}，预计发布时刻 {utc(moment)}"
        ),
        refs=(release.key,),
        outcome=EventOutcome.QUEUED,
    )


def earnings_window_start(at: datetime, calendar: TradingCalendar, sessions: int) -> datetime:
    """财报发布的间隔的起点：从 at 归属的交易日往前数 sessions 个交易日，那一天的收盘时刻。"""
    session = calendar.session_closing_after(at)
    for _ in range(sessions):
        session = calendar.previous_session(session.day)
    return session.close
