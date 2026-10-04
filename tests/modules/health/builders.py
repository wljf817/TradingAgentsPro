"""健康监控测试的数据：degraded-mode spec「健康检查」里的基准数据。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tradingagentspro.modules.health import HealthConfig, HealthService, HealthSignals

NYSE = sample_nyse_calendar()


def at(hour: int, minute: int = 0, second: int = 0, *, day: int = 14, month: int = 10) -> datetime:
    """2026 年某一天的某个 UTC 时刻，默认是基准交易日 2026-10-14。"""
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


BASELINE_NOW = at(15)  # 周三，盘中；常规时段 13:30Z 到 20:00Z


def signals(now: datetime = BASELINE_NOW, **changes: Any) -> HealthSignals:
    """在 now 的基准信号，五项检查都正常：行情在 10 秒前更新，调度器在 2 分钟前心跳。

    changes 覆盖其中的字段。
    """
    baseline = HealthSignals(
        pm_model_available=True,
        critic_model_available=True,
        broker_connected=True,
        last_quote_at=now - timedelta(seconds=10),
        reserve_budget_exhausted=False,
        scheduler_heartbeat_at=now - timedelta(minutes=2),
    )
    return replace(baseline, **changes)


def health_service(clock: FakeClock | None = None, **config: Any) -> HealthService:
    """健康监控用例：测试时钟（没有传入时停在基准时刻）、纽交所样例日历、默认配置。

    config 覆盖其中的配置项。
    """
    return HealthService(clock or FakeClock(BASELINE_NOW), NYSE, HealthConfig(**config))
