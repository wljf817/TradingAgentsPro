"""系统时钟的测试。"""

from datetime import UTC, timedelta

from tradingagentspro.integrations.system_clock import SystemClock
from tradingagentspro.shared.clock import Clock


def test_system_clock_returns_utc_in_a_utc_plus_14_process() -> None:
    # 测试进程固定在 Pacific/Kiritimati（UTC+14），见 tests/conftest.py。
    clock: Clock = SystemClock()

    now = clock.now()

    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)
