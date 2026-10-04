"""紧急退出测试的数据：emergency-exit spec「判定的时机和输入」里的基准数据。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tradingagentspro.modules.emergency import EmergencyConfig, EmergencyService, Holding

NYSE = sample_nyse_calendar()
BASELINE_NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)  # 周三，盘中；常规时段 13:30Z 到 20:00Z
BASELINE_DAY = date(2026, 10, 14)
NEXT_DAY = date(2026, 10, 15)

PREVIOUS_CLOSE_EQUITY = Decimal(100_000)
EQUITY = Decimal(99_000)

# 今天都没有开仓成交
AAA = Holding(
    symbol="AAA",
    sector="科技",
    emergency_threshold=Decimal("0.08"),
    previous_close=Decimal(100),
    last_price=Decimal(95),
)
BBB = Holding(
    symbol="BBB",
    sector="医疗",
    emergency_threshold=Decimal("0.10"),
    previous_close=Decimal(50),
    last_price=Decimal(49),
)


def emergency_service(clock: FakeClock | None = None, **config: Any) -> EmergencyService:
    """紧急退出用例：测试时钟（没有传入时停在基准时刻）、纽交所样例日历、默认配置。

    config 覆盖其中的配置项。
    """
    return EmergencyService(clock or FakeClock(BASELINE_NOW), NYSE, EmergencyConfig(**config))
