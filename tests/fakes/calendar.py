"""样例日历：2026-09-01 到 2027-01-29 的纽交所交易日，供单元测试使用，不需要日历库。

和日历库加载的结果是否一致，由 tests/integrations/test_nyse_calendar.py 检查。
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

_NEW_YORK = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16)
_HOLIDAYS = {
    date(2026, 9, 7),  # 劳动节
    date(2026, 11, 26),  # 感恩节
    date(2026, 12, 25),  # 圣诞节
    date(2027, 1, 1),  # 元旦
    date(2027, 1, 18),  # 马丁·路德·金纪念日
}
_EARLY_CLOSES = {
    date(2026, 11, 27): time(13),  # 感恩节次日
    date(2026, 12, 24): time(13),  # 平安夜
}

SAMPLE_FIRST_DAY = date(2026, 9, 1)
SAMPLE_LAST_DAY = date(2027, 1, 29)


def _new_york_to_utc(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=_NEW_YORK).astimezone(UTC)


def sample_nyse_calendar(
    start: date = SAMPLE_FIRST_DAY, end: date = SAMPLE_LAST_DAY
) -> TradingCalendar:
    """返回 start 到 end（含两端）的纽交所日历；范围必须在样例数据之内。"""
    assert SAMPLE_FIRST_DAY <= start <= end <= SAMPLE_LAST_DAY
    sessions = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in _HOLIDAYS:
            close = _EARLY_CLOSES.get(day, _CLOSE)
            sessions.append(
                TradingSession(
                    day=day,
                    open=_new_york_to_utc(day, _OPEN),
                    close=_new_york_to_utc(day, close),
                )
            )
        day += timedelta(days=1)
    return TradingCalendar(sessions)
