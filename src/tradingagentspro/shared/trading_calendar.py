"""交易日历：哪天开市、几点开盘收盘，以及按交易日推算。纯计算，不做时区换算。"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class TradingSession:
    """一个交易日的常规时段。开盘和收盘是带时区的 UTC 时间。"""

    day: date
    open: datetime
    close: datetime


class CalendarRangeError(LookupError):
    """查询超出了交易日数据的覆盖范围，答案无法确定。"""


class OutsideSessionError(RuntimeError):
    """在常规交易时段之外调用了只能在盘中做的操作，比如下单前再校验、提交新订单。"""


class TradingCalendar:
    """用一段连续区间里的全部交易时段构造，交易时段按交易日的顺序传入。

    区间内没有出现的日期都是休市日。覆盖范围从第一个交易日到最后一个交易日。
    """

    def __init__(self, sessions: Iterable[TradingSession]) -> None:
        self._sessions = tuple(sessions)
        self._days = tuple(s.day for s in self._sessions)
        self._opens = tuple(s.open for s in self._sessions)
        self._closes = tuple(s.close for s in self._sessions)

    @property
    def first_session(self) -> TradingSession:
        return self._sessions[0]

    @property
    def last_session(self) -> TradingSession:
        return self._sessions[-1]

    def is_session(self, day: date) -> bool:
        return self.session(day) is not None

    def session(self, day: date) -> TradingSession | None:
        """day 的交易时段；不是交易日时为 None。"""
        self._check_day(day)
        i = bisect_left(self._days, day)
        if i < len(self._days) and self._days[i] == day:
            return self._sessions[i]
        return None

    def next_session(self, day: date) -> TradingSession:
        """day 之后的第一个交易日，不含 day。"""
        return self.add_sessions(day, 1)

    def previous_session(self, day: date) -> TradingSession:
        """day 之前的最后一个交易日，不含 day。"""
        self._check_day(day)
        return self._at(bisect_left(self._days, day) - 1)

    def add_sessions(self, day: date, n: int) -> TradingSession:
        """day 之后第 n 个交易日（n ≥ 1），不含 day。"""
        self._check_day(day)
        return self._at(bisect_right(self._days, day) + n - 1)

    def count_sessions(self, start: date, end: date) -> int:
        """start 到 end 之间的交易日个数，不含 start、含 end；要求 start ≤ end。"""
        self._check_day(start)
        self._check_day(end)
        return bisect_right(self._days, end) - bisect_right(self._days, start)

    def session_at(self, moment: datetime) -> TradingSession | None:
        """moment 所在的常规时段，按开盘 ≤ moment < 收盘；不在盘中时为 None。"""
        self._check_moment(moment)
        i = bisect_right(self._opens, moment) - 1
        return self._sessions[i] if moment < self._closes[i] else None

    def next_session_after(self, moment: datetime) -> TradingSession:
        """开盘时间晚于 moment 的第一个时段。"""
        self._check_moment(moment)
        return self._at(bisect_right(self._opens, moment))

    def last_session_before(self, moment: datetime) -> TradingSession:
        """收盘时间不晚于 moment 的最后一个时段。"""
        self._check_moment(moment)
        return self._at(bisect_right(self._closes, moment) - 1)

    def _check_moment(self, moment: datetime) -> None:
        if not self._opens[0] <= moment < self._closes[-1]:
            raise CalendarRangeError(
                f"{moment} 不在交易日数据的覆盖范围 {self._opens[0]} 到 {self._closes[-1]} 之内"
            )

    def _check_day(self, day: date) -> None:
        if not self._days[0] <= day <= self._days[-1]:
            raise CalendarRangeError(
                f"{day} 不在交易日数据的覆盖范围 {self._days[0]} 到 {self._days[-1]} 之内"
            )

    def _at(self, index: int) -> TradingSession:
        """按下标取交易时段；下标落在数据之外时报错，避免负下标从末尾取值。"""
        if not 0 <= index < len(self._sessions):
            raise CalendarRangeError(
                f"答案超出交易日数据的范围 {self._days[0]} 到 {self._days[-1]}"
            )
        return self._sessions[index]
