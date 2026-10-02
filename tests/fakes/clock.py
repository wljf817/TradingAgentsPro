"""测试用的时钟：时间由测试设定，不会自己走。"""

from datetime import datetime, timedelta


class FakeClock:
    """从给定的时间开始；测试用 set 设定时间，用 advance 拨动时间。传入的一律是 UTC 时间。"""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
