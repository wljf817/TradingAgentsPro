"""行情模块的配置和默认值，含义见 market-data spec「行情配置」、market-context spec「重要日程」、
price-bars spec「K 线配置」和 quant-indicators spec「量化指标配置」。

日程的日期取自官方日历：FOMC 取自美联储的会议日历，CPI 和非农取自 BLS 的发布日历，到 2027 年底。
"""

from datetime import date, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

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


class IndicatorConfig(BaseModel):
    """量化指标的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每个长度算一条均线，根数按任务的周期
    ma_lengths: tuple[PositiveInt, ...] = Field(default=(20, 50, 200), min_length=1)
    # 均线的斜率和 ATR 的变化比较往前这么多根
    change_bars: int = Field(default=5, gt=0)
    # ATR 按这么多根的真实波幅平均
    atr_bars: int = Field(default=14, gt=0)
    # 一年的交易日数：日线 ATR 的一年位置、跳空缺口和 52 周高低点用这么多根日线
    year_sessions: int = Field(default=252, gt=0)
    # 前高前低和前后这么多根比较
    swing_bars: int = Field(default=5, gt=0)
    # 前高前低和缺口在现价上方、下方各列这么多个
    levels_per_side: int = Field(default=3, gt=0)
    # 自己算量比时，过去每分钟的成交量按当天以前这么多个交易日算
    volume_ratio_sessions: int = Field(default=5, gt=0)
    # 大盘：相对强度和事件反应都和它比
    benchmark: str = Field(default="SPY", min_length=1)


class MarketDataConfig(BaseModel):
    """行情模块的配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 用这只股票的行情更新时刻作为行情最近一次更新的时刻
    reference_symbol: str = Field(default="SPY", min_length=1)
    # 重要日程
    schedule: tuple[ScheduleItem, ...] = DEFAULT_SCHEDULE
    # 列出给定交易日之后这么多个交易日内的重要日程
    schedule_sessions: int = Field(default=10, gt=0)
    # K 线的截止时刻比快照时刻早这么久：免费档取不了最近 15 分钟，回放也按这个时刻截
    bar_delay: timedelta = Field(default=timedelta(minutes=15), ge=timedelta(0))
    # 量化指标
    indicators: IndicatorConfig = IndicatorConfig()
