"""K 线：Quant 用的 15 分钟线、60 分钟线、日线和周线，见 price-bars spec 和 add-price-bars 的
design。

纯计算：数据源给的原始 K 线和拆股记录由行情服务取来传入，截止时刻、范围和口径由交易日历推算。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import prod
from typing import Final

from tradingagentspro.contracts.task import BarPeriod
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

_QUARTER: Final = timedelta(minutes=15)
_HOUR: Final = timedelta(minutes=60)
_PRICE_STEP: Final = Decimal("0.0001")  # 拆股换算以后价格保留四位小数
_SHARE: Final = Decimal(1)


@dataclass(frozen=True, slots=True)
class SourceBar:
    """数据源的一根原始 15 分钟线：不按拆股和分红调整。start 是开始时刻（UTC）。"""

    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class SourceDayBar:
    """数据源的一根原始日线：不按拆股和分红调整。day 是交易日。"""

    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class Split:
    """一次拆股：除权日和拆股比例（新股数除以旧股数，1 拆 2 是 2，8 合 1 是 0.125）。"""

    ex_date: date
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class Bar:
    """一根 K 线，见 price-bars spec「K 线的请求和结果」。

    价格和成交量已经按拆股换到快照那天的口径。没走完的 K 线只含已经走完的 15 分钟线，end 是它本来
    要到的结束时刻。
    """

    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    finished: bool


@dataclass(frozen=True, slots=True)
class Bars:
    """一次取到的 K 线：截止时刻，和每只股票按开始时刻排列的 K 线（没有时为空）。"""

    cutoff: datetime
    by_symbol: Mapping[str, tuple[Bar, ...]]


@dataclass(frozen=True, slots=True)
class BarPlan:
    """一次取 K 线的截止时刻、要用的交易日和口径，见 price-bars spec「截止时刻」「K 线的范围」
    「拆股换算」。
    """

    cutoff: datetime
    sessions: tuple[TradingSession, ...]  # 范围里的交易日；周线从第一周的第一个交易日算起
    price_day: date  # 快照那天：开盘时刻不晚于快照时刻的最近一个交易日

    @property
    def first(self) -> TradingSession:
        return self.sessions[0]

    @property
    def last(self) -> TradingSession:
        return self.sessions[-1]

    @property
    def last_open(self) -> bool:
        """范围的最后一天在截止时刻还没收盘。"""
        return self.last.close > self.cutoff


def plan_bars(
    calendar: TradingCalendar,
    period: BarPeriod,
    sessions: int,
    taken_at: datetime,
    delay: timedelta,
) -> BarPlan:
    """按快照时刻、延迟和范围推算截止时刻、交易日和快照那天。"""
    cutoff = taken_at - delay
    days = [opened_by(calendar, cutoff - _QUARTER)]  # 最后一天要有一根走完的 15 分钟线
    while len(days) < sessions:
        days.append(calendar.previous_session(days[-1].day))
    days.reverse()
    if period is BarPeriod.WEEK:
        monday = days[0].day - timedelta(days=days[0].day.weekday())
        while (earlier := calendar.previous_session(days[0].day)).day >= monday:
            days.insert(0, earlier)
    return BarPlan(cutoff, tuple(days), opened_by(calendar, taken_at).day)


def build_bars(
    calendar: TradingCalendar,
    period: BarPeriod,
    plan: BarPlan,
    quarters: Sequence[SourceBar],
    days: Sequence[SourceDayBar],
    splits: Sequence[Split],
) -> tuple[Bar, ...]:
    """一只股票的 K 线：数据源的原始 15 分钟线、原始日线和拆股记录按规则拼好。"""
    adjust = _Adjuster(tuple(s for s in splits if s.ex_date <= plan.price_day))
    regular = _regular_quarters(calendar, plan, quarters, adjust)
    match period:
        case BarPeriod.QUARTER_HOUR:
            return tuple(bar for _, bar in regular)
        case BarPeriod.HOUR:
            return _hours(regular, plan.cutoff)
        case BarPeriod.DAY:
            return tuple(bar for _, bar in _days(plan, regular, days, adjust))
        case BarPeriod.WEEK:
            return _weeks(calendar, _days(plan, regular, days, adjust), plan.cutoff)


type _Dated = tuple[TradingSession, Bar]  # 一根 K 线和它所在的交易日


class _Adjuster:
    """按拆股把一个交易日的原始价格和成交量换到快照那天的口径，见 spec「拆股换算」。"""

    def __init__(self, splits: tuple[Split, ...]) -> None:
        self._splits = splits

    def bar(
        self,
        day: date,
        span: tuple[datetime, datetime],
        prices: tuple[Decimal, Decimal, Decimal, Decimal],
        volume: int,
    ) -> Bar:
        factor = prod((s.ratio for s in self._splits if s.ex_date > day), start=Decimal(1))
        if factor != 1:
            prices = _divide(prices, factor)
            volume = int((volume * factor).quantize(_SHARE, ROUND_HALF_UP))
        return Bar(*span, *prices, volume, finished=True)


def _divide(
    prices: tuple[Decimal, Decimal, Decimal, Decimal], factor: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    o, h, low, c = ((p / factor).quantize(_PRICE_STEP, ROUND_HALF_UP) for p in prices)
    return o, h, low, c


def opened_by(calendar: TradingCalendar, moment: datetime) -> TradingSession:
    """开盘时刻不晚于 moment 的最近一个交易日。"""
    return calendar.session_at(moment) or calendar.last_session_before(moment)


def _regular_quarters(
    calendar: TradingCalendar, plan: BarPlan, quarters: Sequence[SourceBar], adjust: _Adjuster
) -> tuple[_Dated, ...]:
    """范围里、整根落在常规时段里、截止以前走完的 15 分钟线，见 spec「15 分钟线」。"""
    in_range = {session.day for session in plan.sessions}
    kept: list[_Dated] = []
    for q in quarters:
        end = q.start + _QUARTER
        session = calendar.session_at(q.start)
        if session is None or session.day not in in_range or end > min(session.close, plan.cutoff):
            continue
        prices = (q.open, q.high, q.low, q.close)
        kept.append((session, adjust.bar(session.day, (q.start, end), prices, q.volume)))
    return tuple(kept)


def _merge(parts: Sequence[Bar], start: datetime, end: datetime, cutoff: datetime) -> Bar:
    """几根拼成一根，见 spec「60 分钟线」的拼法；结束时刻晚于截止时刻时标明没走完。"""
    return Bar(
        start=start,
        end=end,
        open=parts[0].open,
        high=max(p.high for p in parts),
        low=min(p.low for p in parts),
        close=parts[-1].close,
        volume=sum(p.volume for p in parts),
        finished=end <= cutoff,
    )


def _hours(regular: tuple[_Dated, ...], cutoff: datetime) -> tuple[Bar, ...]:
    """每个交易日从开盘起每 60 分钟一组，最后一组到收盘为止，见 spec「60 分钟线」。"""
    groups: dict[tuple[TradingSession, int], list[Bar]] = {}
    for session, bar in regular:
        groups.setdefault((session, (bar.start - session.open) // _HOUR), []).append(bar)
    hours = []
    for (session, index), parts in groups.items():
        start = session.open + index * _HOUR
        hours.append(_merge(parts, start, min(start + _HOUR, session.close), cutoff))
    return tuple(hours)


def _days(
    plan: BarPlan,
    regular: tuple[_Dated, ...],
    days: Sequence[SourceDayBar],
    adjust: _Adjuster,
) -> tuple[_Dated, ...]:
    """收盘了的交易日用数据源的日线，最后一天没收盘时由它的 15 分钟线拼成，见 spec「日线」。"""
    by_day = {d.day: d for d in days}
    bars: list[_Dated] = []
    for session in plan.sessions:
        span = (session.open, session.close)
        if session.close <= plan.cutoff:
            if (d := by_day.get(session.day)) is not None:
                prices = (d.open, d.high, d.low, d.close)
                bars.append((session, adjust.bar(session.day, span, prices, d.volume)))
        elif parts := [bar for s, bar in regular if s == session]:
            bars.append((session, _merge(parts, *span, plan.cutoff)))
    return tuple(bars)


def _weeks(
    calendar: TradingCalendar, days: tuple[_Dated, ...], cutoff: datetime
) -> tuple[Bar, ...]:
    """日线按周一到周日拼成周线，开始和结束按交易日历上这一周的交易日，见 spec「周线」。"""
    groups: dict[date, list[Bar]] = {}
    for session, bar in days:
        groups.setdefault(session.day - timedelta(days=session.day.weekday()), []).append(bar)
    weeks = []
    for monday, parts in groups.items():
        sessions = [
            s for s in map(calendar.session, (monday + timedelta(days=i) for i in range(7))) if s
        ]
        weeks.append(_merge(parts, sessions[0].open, sessions[-1].close, cutoff))
    return tuple(weeks)
