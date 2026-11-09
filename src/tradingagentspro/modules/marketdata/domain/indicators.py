"""量化指标：按一个 Quant 任务算出趋势、相对强度、波动、量比、关键价位和事件反应，见
quant-indicators spec 和 add-quant-indicators 的 design。

纯计算：K 线由量化指标的用例按 spec「取数」取来传入，这里也推算要取多少个交易日。全程用不舍入的
Decimal，只在写进结果时四舍五入到四位小数（design D11）。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Final

from tradingagentspro.contracts.quant import (
    Atr,
    Comparison,
    DailyAtr,
    EventReaction,
    GapDirection,
    LevelKind,
    MovingAverage,
    PriceGap,
    PriceLevel,
    QuantIndicators,
    YearExtreme,
)
from tradingagentspro.contracts.task import BarPeriod, BarWindow
from tradingagentspro.modules.marketdata.config import IndicatorConfig
from tradingagentspro.modules.marketdata.domain.bars import Bar, BarPlan, opened_by
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

_STEP: Final = Decimal("0.0001")
_QUARTER: Final = timedelta(minutes=15)
_MINUTE: Final = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class EventTime:
    """要看反应的一个事件：事件 ID 和发生时刻（UTC）。"""

    event_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IndicatorRequest:
    """算一只股票的量化指标的请求，见 spec「量化指标的请求和结果」。"""

    symbol: str
    sector: str | None  # 板块 ETF
    window: BarWindow  # Quant 任务的周期和范围
    taken_at: datetime  # 快照时刻
    prices: Mapping[str, Decimal]  # 快照里有最新价的股票的最新价
    volume_ratio: Decimal | None  # 快照的行情里这只股票的量比
    events: tuple[EventTime, ...]


@dataclass(frozen=True, slots=True)
class IndicatorBars:
    """算一次量化指标用的 K 线，见 spec「取数」。"""

    period: Mapping[str, tuple[Bar, ...]]  # 任务周期：股票、板块 ETF、大盘
    daily: tuple[Bar, ...]  # 股票的日线
    quarters: Mapping[str, tuple[Bar, ...]]  # 15 分钟线；没有取时为空


def history_sessions(
    calendar: TradingCalendar, period: BarPeriod, bars: int, last: TradingSession
) -> int:
    """spec「均线要的交易日数」：最后一天以前凑够 bars 根这个周期的 K 线要的交易日，加上最后一天。

    周线按周数，数到的最早一周只含它最后一个交易日：取 K 线时会从这一周的第一个交易日算起。
    """
    sessions, total, day = 1, 0, last
    week = _monday(last.day)
    while total < bars:
        day = calendar.previous_session(day.day)
        sessions += 1
        if period is not BarPeriod.WEEK:
            total += _bars_in(day, period)
        elif (monday := _monday(day.day)) != week:
            total, week = total + 1, monday
    return sessions


def quarter_sessions(
    calendar: TradingCalendar,
    request: IndicatorRequest,
    cutoff: datetime,
    last: TradingSession,
    config: IndicatorConfig,
) -> int:
    """spec「取数」的 15 分钟线要取的交易日数；不用取时为 0。"""
    sessions = 0 if request.volume_ratio is not None else config.volume_ratio_sessions + 1
    for event in request.events:
        first = opened_by(calendar, min(event.occurred_at, cutoff) - _QUARTER)
        sessions = max(sessions, calendar.count_sessions(first.day, last.day) + 1)
    return sessions


def compute_indicators(
    request: IndicatorRequest,
    plan: BarPlan,
    bars: IndicatorBars,
    calendar: TradingCalendar,
    config: IndicatorConfig,
) -> QuantIndicators:
    """全部数字。plan 是任务范围的 K 线计划：截止时刻、范围的第一天和最后一天。"""
    symbol, sector, benchmark = request.symbol, request.sector, config.benchmark
    stock = bars.period[symbol]
    now = {
        s: price
        for s, period_bars in bars.period.items()
        if (price := current_price(s, request.prices, period_bars)) is not None
    }
    price = now.get(symbol)
    if price is None:
        return _without_price(request, plan, config)
    above, below = swing_levels(stock, price, plan.first.open, config)
    gaps_above, gaps_below = gaps(bars.daily, price, calendar, config)
    year_high, year_low = year_extremes(bars.daily, price, calendar, config)
    quarters = bars.quarters
    return QuantIndicators(
        symbol=symbol,
        sector=sector,
        bars=request.window,
        cutoff=plan.cutoff,
        price=_round(price),
        moving_averages=moving_averages(stock, price, config),
        range_change=range_change(symbol, sector, benchmark, bars.period, now, plan.first.open),
        atr=atr(stock, price, config),
        daily_atr=daily_atr(bars.daily, price, config),
        volume_ratio=volume_ratio(
            request.volume_ratio, quarters.get(symbol, ()), plan.last, plan.cutoff, calendar, config
        ),
        levels_above=above,
        levels_below=below,
        gaps_above=gaps_above,
        gaps_below=gaps_below,
        year_high=year_high,
        year_low=year_low,
        events=event_reactions(
            request.events, symbol, sector, benchmark, quarters, now, plan.cutoff
        ),
    )


def current_price(
    symbol: str, prices: Mapping[str, Decimal], bars: Sequence[Bar]
) -> Decimal | None:
    """spec「现价」：请求里的最新价，没有时用最后一根 K 线的收盘价；也没有 K 线时为 None。"""
    if symbol in prices:
        return prices[symbol]
    return bars[-1].close if bars else None


def moving_averages(
    bars: Sequence[Bar], price: Decimal, config: IndicatorConfig
) -> tuple[MovingAverage, ...]:
    """spec「均线」：每个配置的长度一条，只用走完的 K 线。"""
    closes = [b.close for b in bars if b.finished]
    change = config.change_bars
    averages = []
    for length in config.ma_lengths:
        if len(closes) < length:
            averages.append(MovingAverage(length=length, value=None, deviation=None, slope=None))
            continue
        value = _mean(closes[-length:])
        slope = None
        if len(closes) >= length + change:
            slope = _round(_change(value, _mean(closes[-length - change : -change])))
        averages.append(
            MovingAverage(
                length=length,
                value=_round(value),
                deviation=_round(_change(price, value)),
                slope=slope,
            )
        )
    return tuple(averages)


def range_change(
    symbol: str,
    sector: str | None,
    benchmark: str,
    bars: Mapping[str, Sequence[Bar]],
    now: Mapping[str, Decimal],
    range_start: datetime,
) -> Comparison:
    """spec「相对强度」：现价除以范围里第一根的开盘价减 1；板块 ETF 没有 K 线时当作没有。"""

    def since_start(s: str) -> Decimal:
        first = next(b for b in bars[s] if b.start >= range_start)
        return _change(now[s], first.open)

    sector_change = since_start(sector) if sector is not None and bars.get(sector) else None
    return _comparison(since_start(symbol), sector_change, since_start(benchmark))


def atr(bars: Sequence[Bar], price: Decimal, config: IndicatorConfig) -> Atr | None:
    """spec「ATR」：任务周期走完的 K 线的 ATR。"""
    atrs = _atrs([b for b in bars if b.finished], config.atr_bars)
    if not atrs or (value := atrs[-1]) is None:
        return None
    earlier = len(atrs) - 1 - config.change_bars
    before = atrs[earlier] if earlier >= 0 else None
    return Atr(
        value=_round(value),
        percent=_round(value / price),
        change=None if before is None else _round(_change(value, before)),
    )


def daily_atr(bars: Sequence[Bar], price: Decimal, config: IndicatorConfig) -> DailyAtr | None:
    """spec「日线 ATR 和一年位置」：走完的日线的 ATR，和它在最后一年里的位置。"""
    atrs = _atrs([b for b in bars if b.finished], config.atr_bars)
    if not atrs or (value := atrs[-1]) is None:
        return None
    year = [a for a in atrs[-config.year_sessions :] if a is not None]
    rank = None
    if len(year) == config.year_sessions:
        rank = _round(Decimal(sum(1 for a in year if a <= value)) / len(year))
    return DailyAtr(value=_round(value), percent=_round(value / price), year_rank=rank)


def volume_ratio(
    given: Decimal | None,
    quarters: Sequence[Bar],
    today: TradingSession,
    cutoff: datetime,
    calendar: TradingCalendar,
    config: IndicatorConfig,
) -> Decimal | None:
    """spec「量比」：请求里有就用；没有时按当天和过去几个交易日的 15 分钟线算。"""
    if given is not None:
        return _round(given)
    past = [calendar.previous_session(today.day)]
    while len(past) < config.volume_ratio_sessions:
        past.append(calendar.previous_session(past[-1].day))
    past_bars = [b for b in quarters if past[-1].open <= b.start < today.open]
    if not past_bars:
        return None
    past_minutes = sum((s.close - s.open) // _MINUTE for s in past)
    past_rate = Decimal(sum(b.volume for b in past_bars)) / past_minutes
    minutes = (min(today.close, cutoff) - today.open) // _QUARTER * 15
    today_volume = sum(b.volume for b in quarters if b.start >= today.open)
    return _round(Decimal(today_volume) / minutes / past_rate)


def swing_levels(
    bars: Sequence[Bar], price: Decimal, range_start: datetime, config: IndicatorConfig
) -> tuple[tuple[PriceLevel, ...], tuple[PriceLevel, ...]]:
    """spec「前高前低」：范围里的前高前低，按现价分成上方和下方，各自从近到远。"""
    n = config.swing_bars
    levels: list[PriceLevel] = []
    for i in range(n, len(bars) - n):
        bar, before, after = bars[i], bars[i - n : i], bars[i + 1 : i + 1 + n]
        if bar.start < range_start:
            continue
        if all(bar.high > b.high for b in before) and all(bar.high >= b.high for b in after):
            levels.append(_level(LevelKind.HIGH, bar, bar.high, price))
        if all(bar.low < b.low for b in before) and all(bar.low <= b.low for b in after):
            levels.append(_level(LevelKind.LOW, bar, bar.low, price))
    count = config.levels_per_side
    above = sorted((x for x in levels if x.price > price), key=lambda x: x.price)
    below = sorted((x for x in levels if x.price <= price), key=lambda x: x.price, reverse=True)
    return tuple(above[:count]), tuple(below[:count])


def gaps(
    bars: Sequence[Bar], price: Decimal, calendar: TradingCalendar, config: IndicatorConfig
) -> tuple[tuple[PriceGap, ...], tuple[PriceGap, ...]]:
    """spec「跳空缺口」：最后一年日线里还剩下的缺口，按中点分成上方和下方，各自从近到远。"""
    above: list[PriceGap] = []
    below: list[PriceGap] = []
    for i in range(max(1, len(bars) - config.year_sessions), len(bars)):
        found = _gap(bars[i - 1], bars[i], bars[i + 1 :])
        if found is None:
            continue
        direction, low, high = found
        day = _day_of(calendar, bars[i])
        if (low + high) / 2 > price:
            distance = _change(low, price) if low > price else Decimal(0)
            above.append(_price_gap(day, direction, low, high, distance))
        else:
            distance = _change(high, price) if high < price else Decimal(0)
            below.append(_price_gap(day, direction, low, high, distance))
    count = config.levels_per_side
    above.sort(key=lambda g: g.distance)
    below.sort(key=lambda g: g.distance, reverse=True)
    return tuple(above[:count]), tuple(below[:count])


def year_extremes(
    bars: Sequence[Bar], price: Decimal, calendar: TradingCalendar, config: IndicatorConfig
) -> tuple[YearExtreme | None, YearExtreme | None]:
    """spec「52 周高低点」：最后一年日线里的最高价和最低价，一样时取后面那天；不满一年时没有。"""
    if len(bars) < config.year_sessions:
        return None, None
    latest_first = list(reversed(bars[-config.year_sessions :]))
    high = max(latest_first, key=lambda b: b.high)
    low = min(latest_first, key=lambda b: b.low)
    return (
        _extreme(_day_of(calendar, high), high.high, price),
        _extreme(_day_of(calendar, low), low.low, price),
    )


def event_reactions(
    events: Sequence[EventTime],
    symbol: str,
    sector: str | None,
    benchmark: str,
    quarters: Mapping[str, Sequence[Bar]],
    now: Mapping[str, Decimal],
    cutoff: datetime,
) -> tuple[EventReaction, ...]:
    """spec「事件反应」：每个事件以参照时刻以前最后一根 15 分钟线为参照，到现价的涨跌。"""
    reactions = []
    for event in events:
        at = min(event.occurred_at, cutoff)
        reference = _reference(quarters.get(symbol, ()), at)
        if reference is None:
            reactions.append(
                EventReaction(
                    event_id=event.event_id, reference_end=None, reference_price=None, change=None
                )
            )
            continue
        market = _reference(quarters[benchmark], at)
        assert market is not None  # 大盘每个交易时段都有成交
        sector_ref = None if sector is None else _reference(quarters.get(sector, ()), at)
        sector_change = None
        if sector is not None and sector_ref is not None:
            sector_change = _change(now[sector], sector_ref.close)
        reactions.append(
            EventReaction(
                event_id=event.event_id,
                reference_end=reference.end,
                reference_price=_round(reference.close),
                change=_comparison(
                    _change(now[symbol], reference.close),
                    sector_change,
                    _change(now[benchmark], market.close),
                ),
            )
        )
    return tuple(reactions)


def _without_price(
    request: IndicatorRequest, plan: BarPlan, config: IndicatorConfig
) -> QuantIndicators:
    """股票没有 K 线、也没有最新价：现价和全部数字都没有。"""
    return QuantIndicators(
        symbol=request.symbol,
        sector=request.sector,
        bars=request.window,
        cutoff=plan.cutoff,
        price=None,
        moving_averages=tuple(
            MovingAverage(length=length, value=None, deviation=None, slope=None)
            for length in config.ma_lengths
        ),
        range_change=None,
        atr=None,
        daily_atr=None,
        volume_ratio=None,
        levels_above=(),
        levels_below=(),
        gaps_above=(),
        gaps_below=(),
        year_high=None,
        year_low=None,
        events=tuple(
            EventReaction(
                event_id=e.event_id, reference_end=None, reference_price=None, change=None
            )
            for e in request.events
        ),
    )


def _round(value: Decimal) -> Decimal:
    return value.quantize(_STEP, ROUND_HALF_UP)


def _change(value: Decimal, base: Decimal) -> Decimal:
    """value 比 base 高低多少：value 除以 base 减 1。"""
    return value / base - 1


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def _comparison(stock: Decimal, sector: Decimal | None, benchmark: Decimal) -> Comparison:
    """三只的涨跌和相对强弱：1 加股票的涨跌除以 1 加对方的涨跌，再减 1。"""
    return Comparison(
        stock=_round(stock),
        sector=None if sector is None else _round(sector),
        benchmark=_round(benchmark),
        vs_sector=None if sector is None else _round(_change(1 + stock, 1 + sector)),
        vs_benchmark=_round(_change(1 + stock, 1 + benchmark)),
    )


def _atrs(bars: Sequence[Bar], length: int) -> list[Decimal | None]:
    """到每一根为止的 ATR：最后 length 根的真实波幅的平均；第一根没有真实波幅。"""
    ranges = [max(b.high, p.close) - min(b.low, p.close) for p, b in pairwise(bars)]
    return [None if i < length else _mean(ranges[i - length : i]) for i in range(len(bars))]


def _gap(
    previous: Bar, bar: Bar, later: Sequence[Bar]
) -> tuple[GapDirection, Decimal, Decimal] | None:
    """bar 相对前一天的缺口按之后的日线还剩下的部分：方向、下沿、上沿；没有或者补上了时为 None。"""
    if bar.low > previous.high:
        low, high = previous.high, bar.low
        if later:
            lowest = min(b.low for b in later)
            if lowest <= low:
                return None
            high = min(high, lowest)
        return GapDirection.UP, low, high
    if bar.high < previous.low:
        low, high = bar.high, previous.low
        if later:
            highest = max(b.high for b in later)
            if highest >= high:
                return None
            low = max(low, highest)
        return GapDirection.DOWN, low, high
    return None


def _price_gap(
    day: date, direction: GapDirection, low: Decimal, high: Decimal, distance: Decimal
) -> PriceGap:
    return PriceGap(
        day=day, direction=direction, low=_round(low), high=_round(high), distance=_round(distance)
    )


def _level(kind: LevelKind, bar: Bar, level: Decimal, price: Decimal) -> PriceLevel:
    return PriceLevel(
        kind=kind, start=bar.start, price=_round(level), distance=_round(_change(level, price))
    )


def _extreme(day: date, level: Decimal, price: Decimal) -> YearExtreme:
    return YearExtreme(day=day, price=_round(level), distance=_round(_change(level, price)))


def _reference(bars: Sequence[Bar], at: datetime) -> Bar | None:
    """结束时刻不晚于 at 的最后一根。"""
    return next((b for b in reversed(bars) if b.end <= at), None)


def _day_of(calendar: TradingCalendar, bar: Bar) -> date:
    """一根日线所在的交易日。"""
    session = calendar.session_at(bar.start)
    assert session is not None  # 日线从开盘开始
    return session.day


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _bars_in(session: TradingSession, period: BarPeriod) -> int:
    """一个交易日里这个周期的 K 线根数，见 spec「取数」的表；不用于周线。"""
    minutes = (session.close - session.open) // _MINUTE
    if period is BarPeriod.QUARTER_HOUR:
        return minutes // 15
    if period is BarPeriod.HOUR:
        return -(-minutes // 60)
    return 1
