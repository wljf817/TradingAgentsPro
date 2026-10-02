"""交易日历的测试。场景见 openspec 的 trading-calendar spec。"""

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.calendar import sample_nyse_calendar
from tests.shared.strategies import session_lists
from tradingagentspro.shared.trading_calendar import (
    CalendarRangeError,
    TradingCalendar,
    TradingSession,
)

# spec 里的"纽交所日历"：2026-09-01 到 2027-01-29。
NYSE = sample_nyse_calendar()
# 覆盖范围场景用的日历：2026-10-01 到 2026-12-31。
NYSE_Q4 = sample_nyse_calendar(date(2026, 10, 1), date(2026, 12, 31))


def _session(day: date, open_hour: int, open_minute: int, close_hour: int) -> TradingSession:
    return TradingSession(
        day=day,
        open=datetime(day.year, day.month, day.day, open_hour, open_minute, tzinfo=UTC),
        close=datetime(day.year, day.month, day.day, close_hour, tzinfo=UTC),
    )


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


# 用交易时段构造日历


def test_first_and_last_session_are_the_ends_of_the_data() -> None:
    sessions = [
        _session(date(2026, 10, 1), 13, 30, 20),
        _session(date(2026, 10, 2), 13, 30, 20),
        _session(date(2026, 10, 5), 13, 30, 20),
    ]

    calendar = TradingCalendar(sessions)

    assert calendar.first_session == sessions[0]
    assert calendar.last_session == sessions[2]


def test_coverage_runs_from_the_first_to_the_last_session() -> None:
    assert NYSE_Q4.first_session.day == date(2026, 10, 1)
    assert NYSE_Q4.last_session.day == date(2026, 12, 31)


# 按日期判断交易日


def test_ordinary_trading_day() -> None:
    day = date(2026, 10, 1)

    assert NYSE.is_session(day)
    assert NYSE.session(day) == TradingSession(day, _utc(day, 13, 30), _utc(day, 20))


def test_bank_holiday_is_still_a_trading_day() -> None:
    assert NYSE.is_session(date(2026, 10, 12))


@pytest.mark.parametrize("day", [date(2026, 10, 3), date(2026, 11, 26)])
def test_weekend_and_holiday_are_not_trading_days(day: date) -> None:
    assert not NYSE.is_session(day)
    assert NYSE.session(day) is None


def test_half_day() -> None:
    day = date(2026, 11, 27)

    assert NYSE.session(day) == TradingSession(day, _utc(day, 14, 30), _utc(day, 18))


# 按日期推算交易日


def test_next_session_skips_the_holiday() -> None:
    assert NYSE.next_session(date(2026, 11, 25)).day == date(2026, 11, 27)


def test_next_session_from_a_weekend() -> None:
    assert NYSE.next_session(date(2026, 10, 3)).day == date(2026, 10, 5)


def test_previous_session() -> None:
    assert NYSE.previous_session(date(2026, 11, 30)).day == date(2026, 11, 27)


def test_nth_session_after() -> None:
    assert NYSE.add_sessions(date(2026, 10, 5), 5).day == date(2026, 10, 12)


def test_counting_from_a_holiday() -> None:
    assert NYSE.add_sessions(date(2026, 11, 26), 1).day == date(2026, 11, 27)


def test_across_the_new_year() -> None:
    assert NYSE.add_sessions(date(2026, 12, 31), 1).day == date(2027, 1, 4)


@pytest.mark.parametrize(
    ("start", "end", "count"),
    [
        (date(2026, 10, 5), date(2026, 10, 12), 5),
        (date(2026, 11, 26), date(2026, 11, 28), 1),
        (date(2026, 10, 5), date(2026, 10, 5), 0),
    ],
)
def test_count_sessions_between_two_days(start: date, end: date, count: int) -> None:
    assert NYSE.count_sessions(start, end) == count


@given(sessions=session_lists(), data=st.data())
def test_adding_and_counting_are_inverse(
    sessions: list[TradingSession], data: st.DataObject
) -> None:
    calendar = TradingCalendar(sessions)
    first, last = sessions[0].day, sessions[-1].day
    day = data.draw(st.dates(min_value=first, max_value=last - timedelta(days=1)))
    sessions_after = sum(1 for s in sessions if s.day > day)
    n = data.draw(st.integers(1, sessions_after))

    result = calendar.add_sessions(day, n)

    assert result == [s for s in sessions if s.day > day][n - 1]
    assert calendar.count_sessions(day, result.day) == n


# 超出覆盖范围的查询报错：日期


def test_day_outside_the_coverage() -> None:
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.is_session(date(2026, 9, 30))
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.count_sessions(date(2026, 12, 1), date(2027, 1, 4))


def test_result_outside_the_data() -> None:
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.next_session(date(2026, 12, 31))
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.add_sessions(date(2026, 12, 1), 60)
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.previous_session(date(2026, 10, 1))


# 按时刻判断交易时段


def test_during_the_session() -> None:
    moment = _utc(date(2026, 10, 1), 15)

    assert NYSE.session_at(moment) == NYSE.session(date(2026, 10, 1))
    assert NYSE.next_session_after(moment).day == date(2026, 10, 2)
    assert NYSE.last_session_before(moment).day == date(2026, 9, 30)


def test_the_open_itself_is_in_the_session() -> None:
    assert NYSE.session_at(_utc(date(2026, 10, 1), 13, 30)) == NYSE.session(date(2026, 10, 1))


def test_one_microsecond_before_the_open_is_not_in_the_session() -> None:
    moment = _utc(date(2026, 10, 1), 13, 30) - timedelta(microseconds=1)

    assert NYSE.session_at(moment) is None
    assert NYSE.next_session_after(moment).open == _utc(date(2026, 10, 1), 13, 30)


def test_the_close_itself_is_not_in_the_session() -> None:
    moment = _utc(date(2026, 10, 1), 20)

    assert NYSE.session_at(moment) is None
    assert NYSE.last_session_before(moment).day == date(2026, 10, 1)


def test_half_day_closes_early() -> None:
    day = date(2026, 11, 27)

    assert NYSE.session_at(_utc(day, 18) - timedelta(seconds=1)) == NYSE.session(day)
    assert NYSE.session_at(_utc(day, 18)) is None


def test_utc_date_differs_from_new_york_date() -> None:
    # 美东时间 2026-10-01 20:30。
    moment = _utc(date(2026, 10, 2), 0, 30)

    assert NYSE.session_at(moment) is None
    assert NYSE.last_session_before(moment).day == date(2026, 10, 1)
    assert NYSE.next_session_after(moment).open == _utc(date(2026, 10, 2), 13, 30)


def test_daylight_saving_time_ends() -> None:
    next_session = NYSE.next_session_after(_utc(date(2026, 10, 30), 20))

    assert next_session.day == date(2026, 11, 2)
    assert next_session.open == _utc(date(2026, 11, 2), 14, 30)


def test_weekend() -> None:
    moment = _utc(date(2026, 10, 3), 12)

    assert NYSE.session_at(moment) is None
    assert NYSE.last_session_before(moment).day == date(2026, 10, 2)
    assert NYSE.next_session_after(moment).day == date(2026, 10, 5)


@given(sessions=session_lists(), data=st.data())
def test_any_moment_is_in_a_session_or_between_adjacent_sessions(
    sessions: list[TradingSession], data: st.DataObject
) -> None:
    calendar = TradingCalendar(sessions)
    start, end = sessions[1].open, sessions[-2].close
    offset = data.draw(st.integers(0, (end - start) // timedelta(microseconds=1) - 1))
    moment = start + timedelta(microseconds=offset)

    current = calendar.session_at(moment)

    if current is not None:
        assert current.open <= moment < current.close
    else:
        before = calendar.last_session_before(moment)
        after = calendar.next_session_after(moment)
        assert sessions.index(after) == sessions.index(before) + 1
        assert before.close <= moment < after.open


# 超出覆盖范围的查询报错：时刻


def test_moment_or_its_answer_outside_the_data() -> None:
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.session_at(_utc(date(2026, 9, 30), 15))
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.session_at(_utc(date(2026, 12, 31), 21))
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.next_session_after(_utc(date(2026, 12, 31), 15))
    with pytest.raises(CalendarRangeError):
        NYSE_Q4.last_session_before(_utc(date(2026, 10, 1), 15))
