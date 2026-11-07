"""财务事实和季度的财务数字。

见 company-data spec「财务事实的记录」，financials-tool spec「列出的季度」「每个季度的数字」
「代码算的数」「截止」，以及 add-financial-figures 的 design D3 到 D6。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Final

from tradingagentspro.modules.companydata.domain.earnings import (
    EarningsRelease,
    ReleaseBeat,
    SurpriseHistory,
)
from tradingagentspro.modules.companydata.domain.ratios import percent


@dataclass(frozen=True, slots=True)
class FinancialFact:
    """公司在一份提交给 SEC 的文件里报告的一个时期的数。数据来自数据源，不加约束。

    - concept：us-gaap 的科目名；unit：单位，比如 USD、USD/shares、shares
    - start、end：这个数所属时期的第一天和最后一天
    - filed：那份文件的提交日，数据源给出的美东日期
    """

    symbol: str
    concept: str
    unit: str
    start: date
    end: date
    value: Decimal
    accession_number: str
    form: str
    filed: date


class Item(StrEnum):
    """一个季度列出的项，按 spec「每个季度的数字」的表。"""

    REVENUE = "revenue"  # 营收
    GROSS_PROFIT = "gross_profit"  # 毛利
    OPERATING_INCOME = "operating_income"  # 营业利润
    NET_INCOME = "net_income"  # 净利润
    EPS_DILUTED = "eps_diluted"  # 稀释 EPS
    DILUTED_SHARES = "diluted_shares"  # 稀释加权股数
    OPERATING_CASH_FLOW = "operating_cash_flow"  # 经营现金流
    CAPEX = "capex"  # 资本开支


@dataclass(frozen=True, slots=True)
class _ItemRule:
    concepts: tuple[str, ...]  # 按顺序用第一个有数的科目
    unit: str
    subtract: bool  # 能不能用两个累计数相减


_USD: Final = "USD"
ITEM_RULES: Final[Mapping[Item, _ItemRule]] = {
    Item.REVENUE: _ItemRule(
        (
            "Revenues",
            "RevenuesNetOfInterestExpense",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ),
        _USD,
        True,
    ),
    Item.GROSS_PROFIT: _ItemRule(("GrossProfit",), _USD, True),
    Item.OPERATING_INCOME: _ItemRule(("OperatingIncomeLoss",), _USD, True),
    Item.NET_INCOME: _ItemRule(("NetIncomeLoss",), _USD, True),
    Item.EPS_DILUTED: _ItemRule(("EarningsPerShareDiluted",), "USD/shares", False),
    Item.DILUTED_SHARES: _ItemRule(
        ("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares", False
    ),
    Item.OPERATING_CASH_FLOW: _ItemRule(
        ("NetCashProvidedByUsedInOperatingActivities",), _USD, True
    ),
    Item.CAPEX: _ItemRule(
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
        _USD,
        True,
    ),
}

# 公司数据模块要取的科目，见 company-data spec「财务事实的记录」
CONCEPTS: Final = tuple(concept for rule in ITEM_RULES.values() for concept in rule.concepts)

# 季度的结束日：时长约 3 个月或约一年的事实的结束日
_QUARTER_DAYS: Final = range(80, 101)
_YEAR_DAYS: Final = range(350, 381)
# 有同比增长的项
GROWTH_ITEMS: Final = (Item.REVENUE, Item.OPERATING_INCOME, Item.NET_INCOME, Item.EPS_DILUTED)
_YEAR_AGO: Final = 4  # 上年同期是列表里往后数第 4 个季度

type _Key = tuple[str, str, date, date]  # 科目、单位、起始日、结束日


@dataclass(frozen=True, slots=True)
class Figure:
    """一项的数；derived 是两个累计数相减得到的。"""

    value: Decimal
    derived: bool


@dataclass(frozen=True, slots=True)
class QuarterFigures:
    """一个季度的各项和代码算的数。

    figures 里没有的项是缺失；比率和增长是百分数，算不了时为 None。
    """

    start: date
    end: date
    figures: Mapping[Item, Figure]
    free_cash_flow: Decimal | None
    gross_margin: Decimal | None
    operating_margin: Decimal | None
    net_margin: Decimal | None
    growth: Mapping[Item, Decimal | None]  # GROWTH_ITEMS 的同比增长


@dataclass(frozen=True, slots=True)
class FinancialReport:
    """financials 的结果，见 financials-tool spec。

    - filed_before：只用这一天以前提交的数字，时刻 T 的美东日期
    - covered：SEC 有这只股票的季度数字；facts_unavailable：SEC 这次不可用，用的是存过的
    - releases：按发布日从晚到早；next_release：发布日不早于 filed_before 的最早一次
    - estimates_refreshed：时刻 T 以前没有记下过、是刷新时取的
    - estimates_unavailable：要刷新时取不到
    """

    symbol: str
    filed_before: date
    covered: bool
    facts_unavailable: bool
    quarters: tuple[QuarterFigures, ...]
    releases: tuple[ReleaseBeat, ...]
    next_release: EarningsRelease | None
    surprises: SurpriseHistory | None
    estimates_refreshed: bool
    estimates_unavailable: bool


def latest_versions(facts: Iterable[FinancialFact]) -> dict[_Key, FinancialFact]:
    """每个科目、单位、起始日、结束日最近一次提交的版本：提交日最晚，相同时 accession number 大。"""
    versions: dict[_Key, FinancialFact] = {}
    for fact in facts:
        key = (fact.concept, fact.unit, fact.start, fact.end)
        known = versions.get(key)
        if known is None or (fact.filed, fact.accession_number) > (
            known.filed,
            known.accession_number,
        ):
            versions[key] = fact
    return versions


def quarters(facts: Iterable[FinancialFact], count: int) -> tuple[QuarterFigures, ...]:
    """最近 count 个季度，最新的在前。facts 是已经按提交日截止的事实。"""
    versions = latest_versions(facts)
    ends = _quarter_ends(versions)
    periods = [(previous + timedelta(days=1), end) for end, previous in pairwise(ends)][:count]
    listed = [_figures(versions, start, end) for start, end in periods]
    return tuple(
        _with_computed(start, end, listed[i], _year_ago(listed, i))
        for i, (start, end) in enumerate(periods)
    )


def _year_ago(listed: Sequence[Mapping[Item, Figure]], index: int) -> Mapping[Item, Figure] | None:
    """上年同期：列表里往后数第 4 个季度；不在列表里时为 None。"""
    later = index + _YEAR_AGO
    return listed[later] if later < len(listed) else None


def _quarter_ends(versions: Mapping[_Key, FinancialFact]) -> list[date]:
    """各项科目里时长约 3 个月或约一年的事实的结束日，从晚到早。"""
    units = {(c, rule.unit) for rule in ITEM_RULES.values() for c in rule.concepts}
    ends = {
        end
        for concept, unit, start, end in versions
        if (concept, unit) in units
        and ((end - start).days in _QUARTER_DAYS or (end - start).days in _YEAR_DAYS)
    }
    return sorted(ends, reverse=True)


def _figures(versions: Mapping[_Key, FinancialFact], start: date, end: date) -> dict[Item, Figure]:
    figures: dict[Item, Figure] = {}
    for item, rule in ITEM_RULES.items():
        for concept in rule.concepts:
            figure = _figure(versions, concept, rule, start, end)
            if figure is not None:
                figures[item] = figure
                break
    return figures


def _figure(
    versions: Mapping[_Key, FinancialFact], concept: str, rule: _ItemRule, start: date, end: date
) -> Figure | None:
    """这个科目在这个季度的数：正好是这个季度的事实，否则同一起点的两个累计数相减。"""
    direct = versions.get((concept, rule.unit, start, end))
    if direct is not None:
        return Figure(direct.value, derived=False)
    if not rule.subtract:
        return None
    previous_end = start - timedelta(days=1)
    origins = [
        origin
        for c, u, origin, e in versions
        if c == concept
        and u == rule.unit
        and e == end
        and origin < start
        and (concept, rule.unit, origin, previous_end) in versions
    ]
    if not origins:
        return None
    origin = max(origins)
    later = versions[(concept, rule.unit, origin, end)].value
    earlier = versions[(concept, rule.unit, origin, previous_end)].value
    return Figure(later - earlier, derived=True)


def _with_computed(
    start: date,
    end: date,
    figures: Mapping[Item, Figure],
    year_ago: Mapping[Item, Figure] | None,
) -> QuarterFigures:
    revenue = _value(figures, Item.REVENUE)
    cash_flow, capex = _value(figures, Item.OPERATING_CASH_FLOW), _value(figures, Item.CAPEX)
    return QuarterFigures(
        start=start,
        end=end,
        figures=figures,
        free_cash_flow=None if cash_flow is None or capex is None else cash_flow - capex,
        gross_margin=_margin(_value(figures, Item.GROSS_PROFIT), revenue),
        operating_margin=_margin(_value(figures, Item.OPERATING_INCOME), revenue),
        net_margin=_margin(_value(figures, Item.NET_INCOME), revenue),
        growth={
            item: _growth(
                _value(figures, item), None if year_ago is None else _value(year_ago, item)
            )
            for item in GROWTH_ITEMS
        },
    )


def _value(figures: Mapping[Item, Figure], item: Item) -> Decimal | None:
    figure = figures.get(item)
    return None if figure is None else figure.value


def _margin(numerator: Decimal | None, revenue: Decimal | None) -> Decimal | None:
    if numerator is None or revenue is None or revenue <= 0:
        return None
    return percent(numerator, revenue)


def _growth(value: Decimal | None, year_ago: Decimal | None) -> Decimal | None:
    if value is None or year_ago is None or year_ago <= 0:
        return None
    return percent(value - year_ago, year_ago)
