"""估值：原始收盘价和拆股，TTM、拆股换算、PE、PS、市值、太旧和亏损，以及历史位置。

见 valuation-tool spec「TTM」「拆股换算」「PE、PS 和市值」「太旧的数字」「亏损」「历史位置」
「截止」，和 add-valuation-tool 的 design D2 到 D8。
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import prod
from statistics import median
from typing import Final

from tradingagentspro.modules.companydata.domain.financials import (
    ITEM_RULES,
    FinancialFact,
    Item,
    latest_versions,
)
from tradingagentspro.modules.companydata.domain.ratios import percent


@dataclass(frozen=True, slots=True)
class DailyClose:
    """一只股票一个交易日的原始收盘价，不按拆股和分红调整。数据来自数据源，不加约束。"""

    day: date  # 美东日期
    close: Decimal


@dataclass(frozen=True, slots=True)
class Split:
    """一次拆股：除权日和拆股比例（新股数除以旧股数，1 拆 2 是 2）。数据来自数据源，不加约束。"""

    ex_date: date
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class Dated:
    """一项数和它最近一期的结束日。"""

    value: Decimal
    through: date


@dataclass(frozen=True, slots=True)
class Position:
    """PE 或 PS 在窗口里的位置；days 是 0 时没有分位、最低、中位和最高。PS 的 loss_days 总是 0。"""

    days: int
    missing_days: int
    loss_days: int
    percentile: Decimal | None  # 值低于现在的天数占 days 的百分数
    low: Decimal | None
    median: Decimal | None
    high: Decimal | None


@dataclass(frozen=True, slots=True)
class Appraisal:
    """现在的估值和历史位置，都已经按 spec 舍入。

    - loss：TTM EPS 不大于 0，pe 为 None
    - pe_position、ps_position：现在没有 PE、PS 时为 None
    """

    eps: Dated | None
    revenue: Dated | None
    shares: Dated | None
    pe: Decimal | None
    loss: bool
    market_cap: Decimal | None
    ps: Decimal | None
    pe_position: Position | None
    ps_position: Position | None


@dataclass(frozen=True, slots=True)
class Price:
    """估值用的价格：快照的最新价带行情的更新时刻，收盘价带是哪一天的，两者只有一个。"""

    value: Decimal
    quoted_at: datetime | None
    close_day: date | None


@dataclass(frozen=True, slots=True)
class ValuationReport:
    """valuation 的结果，见 valuation-tool spec。

    - filed_before：现在的数字只用这一天以前提交的，时刻 T 的美东日期
    - covered：没有未覆盖；facts_unavailable：SEC 这次不可用，用的是存过的
    - prices_unavailable：Alpaca 这次不可用，没有价格和估值
    - window_start、window_end：历史位置的窗口；max_age_days：数字最旧多少天
    - price、appraisal：未覆盖或者 Alpaca 不可用时为 None
    """

    symbol: str
    filed_before: date
    covered: bool
    facts_unavailable: bool
    prices_unavailable: bool
    window_start: date
    window_end: date
    max_age_days: int
    price: Price | None
    appraisal: Appraisal | None


_EPS: Final = ITEM_RULES[Item.EPS_DILUTED]
_SHARES: Final = ITEM_RULES[Item.DILUTED_SHARES]
_REVENUE: Final = ITEM_RULES[Item.REVENUE]
_YEAR_DAYS: Final = range(350, 381)  # 全年
_YEAR_TO_DATE_DAYS: Final = range(80, 291)  # 今年累计
_PRIOR_SLACK_DAYS: Final = 10  # 去年同期累计和今年累计的时长最多相差这么多天
_ONE: Final = Decimal("0.1")
_TWO: Final = Decimal("0.01")
_WHOLE: Final = Decimal(1)

type _Convert = Callable[[FinancialFact], Decimal]


@dataclass(frozen=True, slots=True)
class _Known:
    """某一天能用的事实和拆股算出来的数，还没有看太旧：TTM EPS、按科目顺序的 TTM 营收、股数。"""

    eps: Dated | None
    revenues: tuple[Dated, ...]
    shares: Dated | None


@dataclass(frozen=True, slots=True)
class _Day:
    """某一天的 PE 和 PS，不舍入；loss 是 TTM EPS 不大于 0。"""

    eps: Dated | None
    revenue: Dated | None
    shares: Dated | None
    pe: Decimal | None
    loss: bool
    market_cap: Decimal | None
    ps: Decimal | None


def window(day: date, years: int) -> tuple[date, date]:
    """历史位置的窗口：day 往前 years 年的同一天（2 月 29 日取 2 月 28 日）到 day 的前一天。"""
    first = day.replace(day=28) if (day.month, day.day) == (2, 29) else day
    return first.replace(year=day.year - years), day - timedelta(days=1)


def appraise(
    facts: Sequence[FinancialFact],
    splits: Sequence[Split],
    closes: Sequence[DailyClose],
    day: date,
    price: Decimal,
    max_age_days: int,
) -> Appraisal:
    """day（时刻 T 的美东日期）的估值和窗口里每一天的位置。

    facts 只用提交日早于各自那一天的，splits 只用除权日不晚于各自那一天的；closes 是窗口里的
    原始收盘价。
    """
    known = _Cache(facts, splits)
    now = _on(known.on(day), day, price, max_age_days)
    history = [_on(known.on(c.day), c.day, c.close, max_age_days) for c in closes]
    return Appraisal(
        eps=_rounded(now.eps, _TWO),
        revenue=_rounded(now.revenue, _WHOLE),
        shares=_rounded(now.shares, _WHOLE),
        pe=_maybe(now.pe, _ONE),
        loss=now.loss,
        market_cap=_maybe(now.market_cap, _WHOLE),
        ps=_maybe(now.ps, _ONE),
        pe_position=None
        if now.pe is None
        else _position(now.pe, [d.pe for d in history], sum(d.loss for d in history)),
        ps_position=None if now.ps is None else _position(now.ps, [d.ps for d in history], 0),
    )


class _Cache:
    """按"能用哪些事实、哪些拆股"缓存算出来的数：一天能用的只在提交日和除权日变化。"""

    def __init__(self, facts: Sequence[FinancialFact], splits: Sequence[Split]) -> None:
        wanted = {_EPS.unit: {*_EPS.concepts}, _SHARES.unit: {*_SHARES.concepts}}
        wanted[_REVENUE.unit] = {*_REVENUE.concepts}
        self._facts = sorted(
            (f for f in facts if f.concept in wanted.get(f.unit, ())), key=lambda f: f.filed
        )
        self._filed = [f.filed for f in self._facts]
        self._splits = sorted(splits, key=lambda s: s.ex_date)
        self._ex_dates = [s.ex_date for s in self._splits]
        self._known: dict[tuple[int, int], _Known] = {}

    def on(self, day: date) -> _Known:
        """day 能用的：提交日早于 day 的事实，除权日不晚于 day 的拆股。"""
        key = (bisect_left(self._filed, day), bisect_right(self._ex_dates, day))
        if key not in self._known:
            facts, splits = self._facts[: key[0]], self._splits[: key[1]]
            self._known[key] = _known(facts, splits)
        return self._known[key]


def _known(facts: Sequence[FinancialFact], splits: Sequence[Split]) -> _Known:
    def factor(fact: FinancialFact) -> Decimal:
        """这条事实提交以后、这一天以前的拆股比例的乘积，见 spec「拆股换算」。"""
        return prod((s.ratio for s in splits if s.ex_date > fact.filed), start=Decimal(1))

    return _Known(
        eps=_ttm(_versions(facts, _EPS.concepts[0], _EPS.unit), lambda f: f.value / factor(f)),
        revenues=tuple(
            ttm
            for concept in _REVENUE.concepts
            if (ttm := _ttm(_versions(facts, concept, _REVENUE.unit), lambda f: f.value))
            is not None
        ),
        shares=_shares(_versions(facts, _SHARES.concepts[0], _SHARES.unit), factor),
    )


def _versions(facts: Iterable[FinancialFact], concept: str, unit: str) -> list[FinancialFact]:
    """这个科目每个时期最近一次提交的版本。"""
    matching = (f for f in facts if f.concept == concept and f.unit == unit)
    return list(latest_versions(matching).values())


def _ttm(versions: Sequence[FinancialFact], convert: _Convert) -> Dated | None:
    """全年加今年累计减去年同期累计，见 spec「TTM」；算不出时为 None。"""
    years = [f for f in versions if _days(f) in _YEAR_DAYS]
    if not years:
        return None
    year = max(years, key=lambda f: f.end)
    following = year.end + timedelta(days=1)
    to_date = [f for f in versions if f.start == following and _days(f) in _YEAR_TO_DATE_DAYS]
    if not to_date:
        return Dated(convert(year), year.end)
    current = max(to_date, key=lambda f: f.end)
    priors = [
        f
        for f in versions
        if f.start == year.start and abs(_days(f) - _days(current)) <= _PRIOR_SLACK_DAYS
    ]
    if not priors:
        return None
    prior = max(priors, key=lambda f: f.end)
    return Dated(convert(year) + convert(current) - convert(prior), current.end)


def _shares(
    versions: Sequence[FinancialFact], factor: Callable[[FinancialFact], Decimal]
) -> Dated | None:
    """结束日最晚、时长最短的稀释加权股数，换到这一天的口径；没有时为 None。"""
    if not versions:
        return None
    latest = max(f.end for f in versions)
    shares = min((f for f in versions if f.end == latest), key=_days)
    return Dated(shares.value * factor(shares), shares.end)


def _days(fact: FinancialFact) -> int:
    return (fact.end - fact.start).days


def _on(known: _Known, day: date, price: Decimal, max_age_days: int) -> _Day:
    """这一天的 PE、PS 和市值：太旧的项不用，见 spec「PE、PS 和市值」「太旧的数字」「亏损」。"""

    def fresh(item: Dated | None) -> Dated | None:
        return item if item is not None and (day - item.through).days <= max_age_days else None

    eps, shares = fresh(known.eps), fresh(known.shares)
    revenue = next((r for r in known.revenues if fresh(r) is not None), None)
    loss = eps is not None and eps.value <= 0
    market_cap = None if shares is None else price * shares.value
    return _Day(
        eps=eps,
        revenue=revenue,
        shares=shares,
        pe=None if eps is None or loss else price / eps.value,
        loss=loss,
        market_cap=market_cap,
        ps=None
        if market_cap is None or revenue is None or revenue.value <= 0
        else market_cap / revenue.value,
    )


def _position(current: Decimal, history: Sequence[Decimal | None], loss_days: int) -> Position:
    """current 在 history 里的位置；None 是没有数的一天，亏损的那几天另外计数。"""
    values = sorted(v for v in history if v is not None)
    missing = len(history) - len(values) - loss_days
    if not values:
        return Position(0, missing, loss_days, None, None, None, None)
    below = bisect_left(values, current)
    return Position(
        days=len(values),
        missing_days=missing,
        loss_days=loss_days,
        percentile=percent(Decimal(below), Decimal(len(values))),
        low=_round(values[0], _ONE),
        median=_round(median(values), _ONE),
        high=_round(values[-1], _ONE),
    )


def _round(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, ROUND_HALF_UP)


def _maybe(value: Decimal | None, places: Decimal) -> Decimal | None:
    return None if value is None else _round(value, places)


def _rounded(item: Dated | None, places: Decimal) -> Dated | None:
    return None if item is None else Dated(_round(item.value, places), item.through)
