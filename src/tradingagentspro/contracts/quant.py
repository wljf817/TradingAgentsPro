"""量化指标：行情模块按一个 Quant 任务算出的数字，见 quant-indicators spec；Agent 网关把它存成这个
任务的量化指标条目，见 task-dispatch spec「Quant 任务的量化指标」。

价格按快照那天的口径；涨跌、偏离、斜率、变化、占比和位置写成小数（0.0123 是 1.23%），量比写成倍数；
都保留四位小数。算不出的数字为 None。
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from tradingagentspro.contracts.signal import EntryId
from tradingagentspro.contracts.task import BarWindow


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MovingAverage(_Frozen):
    """一条均线，见 spec「均线」。"""

    length: int  # 根数，按任务的周期
    value: Decimal | None  # 均线值
    deviation: Decimal | None  # 偏离：现价除以均线值减 1
    slope: Decimal | None  # 斜率：比往前变化根数那一根的均线高低多少


class Comparison(_Frozen):
    """股票、板块 ETF、SPY 各自的涨跌，和股票相对板块 ETF、相对 SPY 的强弱，见 spec「相对强度」。"""

    stock: Decimal
    sector: Decimal | None  # 没有板块 ETF、或者它没有 K 线时为 None
    benchmark: Decimal  # SPY
    vs_sector: Decimal | None
    vs_benchmark: Decimal


class Atr(_Frozen):
    """任务周期的 ATR，见 spec「ATR」。"""

    value: Decimal
    percent: Decimal  # 占现价
    change: Decimal | None  # 变化：比往前变化根数那一根的 ATR 高低多少


class DailyAtr(_Frozen):
    """日线 ATR，见 spec「日线 ATR 和一年位置」。"""

    value: Decimal
    percent: Decimal  # 占现价
    year_rank: Decimal | None  # 一年位置：一年里不大于它的 ATR 的占比


class LevelKind(StrEnum):
    """关键价位的种类。"""

    HIGH = "high"  # 前高
    LOW = "low"  # 前低


class PriceLevel(_Frozen):
    """一个前高或前低，见 spec「前高前低」。"""

    kind: LevelKind
    start: AwareDatetime  # 这根 K 线的开始时刻
    price: Decimal
    distance: Decimal  # 价格除以现价减 1


class GapDirection(StrEnum):
    """跳空缺口的方向。"""

    UP = "up"
    DOWN = "down"


class PriceGap(_Frozen):
    """一个还没补上的跳空缺口：还剩下的部分，见 spec「跳空缺口」。"""

    day: date  # 跳空的交易日
    direction: GapDirection
    low: Decimal  # 下沿
    high: Decimal  # 上沿
    distance: Decimal  # 上方的是下沿、下方的是上沿除以现价减 1；现价在缺口里时为 0


class YearExtreme(_Frozen):
    """52 周高点或低点，见 spec「52 周高低点」。"""

    day: date
    price: Decimal
    distance: Decimal  # 价格除以现价减 1


class EventReaction(_Frozen):
    """一个事件之后的涨跌，见 spec「事件反应」；股票没有参照的 15 分钟线时，后三项为 None。"""

    event_id: str
    reference_end: AwareDatetime | None  # 股票参照的那根 15 分钟线的结束时刻
    reference_price: Decimal | None  # 股票的参照价
    change: Comparison | None


class QuantIndicators(_Frozen):
    """一个 Quant 任务的量化指标，见 spec「量化指标的请求和结果」。"""

    symbol: str
    sector: str | None  # 板块 ETF
    bars: BarWindow  # 任务的周期和范围
    cutoff: AwareDatetime  # 截止时刻
    price: Decimal | None  # 现价，见 spec「现价」
    moving_averages: tuple[MovingAverage, ...]  # 按配置的均线长度
    range_change: Comparison | None  # 区间涨跌
    atr: Atr | None
    daily_atr: DailyAtr | None
    volume_ratio: Decimal | None  # 量比
    levels_above: tuple[PriceLevel, ...]  # 现价上方的前高前低，从近到远
    levels_below: tuple[PriceLevel, ...]
    gaps_above: tuple[PriceGap, ...]  # 现价上方的缺口，从近到远
    gaps_below: tuple[PriceGap, ...]
    year_high: YearExtreme | None
    year_low: YearExtreme | None
    events: tuple[EventReaction, ...]  # 按请求里事件的顺序


def quant_entry_id(task_id: UUID) -> str:
    """一个 Quant 任务的量化指标条目的 ID：quant/ 加上 task_id。"""
    return f"quant/{task_id}"


class QuantEntry(_Frozen):
    """一个 Quant 任务的量化指标条目：信号可以引用它，随 Task 和 Result 交给 PM 和 Critic。"""

    entry_id: EntryId  # quant_entry_id(task_id)
    indicators: QuantIndicators
