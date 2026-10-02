"""纽交所日历加载的测试。期望值来自 NYSE 官网公布的休市日和提前收盘日。

测试进程固定在 UTC+14（tests/conftest.py），所以这些测试同时确认加载结果不受进程时区影响。
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest

from tests.fakes.calendar import SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY, sample_nyse_calendar
from tradingagentspro.integrations.nyse_calendar import load_nyse_calendar

CALENDAR = load_nyse_calendar(date(2025, 1, 1), date(2027, 12, 31))


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ],
)
def test_2026_holidays_match_the_nyse_announcement(day: date) -> None:
    assert not CALENDAR.is_session(day)


def test_holidays_on_weekends_are_observed() -> None:
    for day in [date(2026, 7, 3), date(2027, 6, 18), date(2027, 7, 5), date(2027, 12, 24)]:
        assert not CALENDAR.is_session(day), day
    # 2028 年元旦是周六，不在前一个周五补休。
    assert CALENDAR.is_session(date(2027, 12, 31))


def test_half_days() -> None:
    for day in [date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)]:
        session = CALENDAR.session(day)
        assert session is not None
        assert session.close == _utc(day, 18), day
    july_2 = CALENDAR.session(date(2026, 7, 2))
    assert july_2 is not None
    assert july_2.close == _utc(date(2026, 7, 2), 20)


@pytest.mark.parametrize(
    ("day", "hour", "minute"),
    [
        (date(2026, 3, 6), 14, 30),
        (date(2026, 3, 9), 13, 30),
        (date(2026, 10, 30), 13, 30),
        (date(2026, 11, 2), 14, 30),
    ],
)
def test_daylight_saving_time_switches(day: date, hour: int, minute: int) -> None:
    session = CALENDAR.session(day)

    assert session is not None
    assert session.open == _utc(day, hour, minute)


def test_recorded_ad_hoc_closure() -> None:
    assert not CALENDAR.is_session(date(2025, 1, 9))


def test_bank_holidays_are_trading_days() -> None:
    assert CALENDAR.is_session(date(2026, 10, 12))
    assert CALENDAR.is_session(date(2026, 11, 11))


def test_times_are_in_utc() -> None:
    for day in _days(CALENDAR.first_session.day, CALENDAR.last_session.day):
        session = CALENDAR.session(day)
        if session is not None:
            assert session.open.tzinfo is UTC
            assert session.close.tzinfo is UTC


def test_sample_calendar_matches_the_loaded_data() -> None:
    sample = sample_nyse_calendar()

    for day in _days(SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY):
        assert sample.session(day) == CALENDAR.session(day), day
