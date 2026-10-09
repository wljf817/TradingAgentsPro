"""冒烟测试里两次启动机器人之间的间隔。

每次启动都会覆盖注册 /plan，Discord 限制这个接口每 30 秒 2 次。额度用完以后，discord.py 要等到
额度恢复才继续连接，会超过 30 秒的连接超时。所以每次启动前，等到距离上一次启动满 31 秒，
30 秒内最多注册一次。见 add-manual-plan-entry 的 design「Risks」。
"""

import threading
from datetime import datetime, timedelta

from tradingagentspro.integrations.system_clock import SystemClock

INTERVAL = timedelta(seconds=31)
_CLOCK = SystemClock()
_starts: list[datetime] = []


def wait_for_registration() -> None:
    """等到距离上一次启动满 INTERVAL，再记下这一次启动的时刻。"""
    if _starts:
        remaining = _starts[-1] + INTERVAL - _CLOCK.now()
        if remaining > timedelta(0):
            threading.Event().wait(remaining.total_seconds())
    _starts.append(_CLOCK.now())
