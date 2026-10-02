"""确认测试进程运行在 UTC+14 时区，时区守卫见 tests/conftest.py。"""

from datetime import UTC, datetime, timedelta


def test_local_timezone_is_utc_plus_14() -> None:
    moment = datetime(2026, 10, 1, tzinfo=UTC)
    assert moment.astimezone().utcoffset() == timedelta(hours=14)
