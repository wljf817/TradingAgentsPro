"""行情模块的配置和默认值，含义见 market-data spec「行情配置」和 market-context spec「重要日程」。

日程的日期取自官方日历：FOMC 取自美联储的会议日历，CPI 和非农取自 BLS 的发布日历，到 2027 年底。
"""

from datetime import date
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.snapshot import ScheduleItem, ScheduleKind


def _items(kind: ScheduleKind, *days: str) -> tuple[ScheduleItem, ...]:
    return tuple(ScheduleItem(day=date.fromisoformat(day), kind=kind) for day in days)


DEFAULT_SCHEDULE: Final = (
    *_items(
        ScheduleKind.FOMC,
        "2026-12-09",
        "2027-01-27",
        "2027-03-17",
        "2027-04-28",
        "2027-06-09",
        "2027-07-28",
        "2027-09-15",
        "2027-10-27",
        "2027-12-08",
    ),
    *_items(
        ScheduleKind.CPI,
        "2026-11-10",
        "2026-12-10",
        "2027-01-13",
        "2027-02-11",
        "2027-03-10",
        "2027-04-13",
        "2027-05-12",
        "2027-06-10",
        "2027-07-14",
        "2027-08-11",
        "2027-09-14",
        "2027-10-14",
        "2027-11-10",
        "2027-12-10",
    ),
    *_items(
        ScheduleKind.PAYROLLS,
        "2026-11-06",
        "2026-12-04",
        "2027-01-08",
        "2027-02-05",
        "2027-03-05",
        "2027-04-02",
        "2027-05-07",
        "2027-06-04",
        "2027-07-02",
        "2027-08-06",
        "2027-09-03",
        "2027-10-08",
        "2027-11-05",
        "2027-12-03",
    ),
)


class MarketDataConfig(BaseModel):
    """行情模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 用这只股票的行情更新时刻作为行情最近一次更新的时刻
    reference_symbol: str = Field(default="SPY", min_length=1)
    # 重要日程
    schedule: tuple[ScheduleItem, ...] = DEFAULT_SCHEDULE
    # 列出给定交易日之后这么多个交易日内的重要日程
    schedule_sessions: int = Field(default=10, gt=0)
