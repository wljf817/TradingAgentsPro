"""系统时钟：全项目唯一可以读取系统时间的地方。"""

from datetime import UTC, datetime


class SystemClock:
    """从操作系统读取当前时间。"""

    def now(self) -> datetime:
        return datetime.now(UTC)
