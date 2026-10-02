"""Hypothesis 策略：任意的交易时段数据。"""

from datetime import UTC, date, datetime, timedelta

from hypothesis import strategies as st

from tradingagentspro.shared.trading_calendar import TradingSession

_MINUTES_PER_DAY = 24 * 60


@st.composite
def session_lists(draw: st.DrawFn) -> list[TradingSession]:
    """至少 3 个交易时段，按交易日排好序；每个时段的开盘和收盘都在当天的 UTC 范围内。"""
    days = sorted(
        draw(
            st.lists(
                st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
                min_size=3,
                max_size=40,
                unique=True,
            )
        )
    )
    sessions = []
    for day in days:
        open_minute = draw(st.integers(0, _MINUTES_PER_DAY - 2))
        close_minute = draw(st.integers(open_minute + 1, _MINUTES_PER_DAY - 1))
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        sessions.append(
            TradingSession(
                day=day,
                open=midnight + timedelta(minutes=open_minute),
                close=midnight + timedelta(minutes=close_minute),
            )
        )
    return sessions
