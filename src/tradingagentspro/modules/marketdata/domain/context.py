"""市场背景的规则：最近的 VIX 收盘值和窗口里的重要日程，见 market-context spec。"""

from collections.abc import Iterable
from datetime import date

from tradingagentspro.contracts.snapshot import ScheduleItem, VixClose


def latest_close(closes: Iterable[VixClose], day: date) -> VixClose | None:
    """日期早于 day 的最近一个收盘值；没有时为 None。"""
    return max((c for c in closes if c.day < day), key=lambda c: c.day, default=None)


def upcoming(schedule: Iterable[ScheduleItem], first: date, last: date) -> tuple[ScheduleItem, ...]:
    """日期在 first 和 last 之间（含两端）的项，按日期；日期相同的保持给出的顺序。"""
    return tuple(sorted((i for i in schedule if first <= i.day <= last), key=lambda i: i.day))
