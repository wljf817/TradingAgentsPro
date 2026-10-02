"""Clock：全系统唯一的"现在"。"""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """当前时间的来源。service 和 workflow 通过注入拿到它；domain 不接收它，"现在"作为参数传入。"""

    def now(self) -> datetime:
        """返回带时区的 UTC 时间。

        不保证两次读取的结果递增：系统校时可能让时间回退。需要判断先后顺序时，
        用保存下来的截止时刻和当前时间比较。
        """
        ...
