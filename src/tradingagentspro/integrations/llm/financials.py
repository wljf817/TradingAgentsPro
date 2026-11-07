"""Fundamentals 的读取工具 financials，见 financials-tool spec 和 add-financial-figures 的 design
D8。
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict

from tradingagentspro.contracts.agent_call import SubAgentRequest
from tradingagentspro.integrations.llm.client import ReadTool
from tradingagentspro.integrations.llm.sub_agent import STOCK_IS_SET, RecordEntries, ToolBuilder
from tradingagentspro.modules.companydata import (
    FinancialReport,
    FinancialsService,
    Item,
    QuarterFigures,
    ReleaseBeat,
    SurpriseHistory,
)

_DESCRIPTION: Final = (
    "列出这只股票最近几个财季的营收、利润、EPS、现金流和代码算好的利润率、同比增长，"
    f"以及一致预期和实际值。{STOCK_IS_SET}"
)
_MISSING: Final = "null 是公司没有报告、或者算不了的项，不要自己估算。"
_DERIVED: Final = "derived 里的项是同一起点的两个累计数相减得到的，比如第四季度是全年减前三季度。"
_PER_SHARE: Final = "每股数字是各期最近一次提交的口径，拆股前后的不能直接比较，看稀释加权股数。"
_FACTS_UNAVAILABLE: Final = "SEC 这次不可用，季度数字只来自以前存下的，可能不全。"
_ESTIMATE_SOURCE: Final = (
    "一致预期和实际值来自 Finnhub，EPS 可能是调整后的口径，和公司报告的稀释 EPS 不同。"
)
_ESTIMATES_UNAVAILABLE: Final = "一致预期这次取不到。"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Quarter(_Frozen):
    """一个季度：各项是公司报告的数，比率和增长是代码算的百分数。"""

    start: date
    end: date
    revenue: Decimal | None
    gross_profit: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    eps_diluted: Decimal | None
    diluted_shares: Decimal | None
    operating_cash_flow: Decimal | None
    capex: Decimal | None
    derived: tuple[str, ...]
    free_cash_flow: Decimal | None
    gross_margin: str | None
    operating_margin: str | None
    net_margin: str | None
    revenue_yoy: str | None
    operating_income_yoy: str | None
    net_income_yoy: str | None
    eps_diluted_yoy: str | None


class _Release(_Frozen):
    """一次财报发布：预期、实际和代码算的超预期幅度。"""

    fiscal_year: int
    fiscal_quarter: int
    day: date
    time: str | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    eps_beat: str | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None
    revenue_beat: str | None


class _Surprise(_Frozen):
    period: date
    fiscal_year: int
    fiscal_quarter: int
    eps_actual: Decimal | None
    eps_estimate: Decimal | None
    surprise: Decimal | None
    surprise_percent: Decimal | None


class _Report(_Frozen):
    """financials 的结果；entry_id 只在有内容可以引用时给出。"""

    entry_id: str | None
    filed_before: date
    quarters: tuple[_Quarter, ...]
    releases: tuple[_Release, ...]
    next_release: _Release | None
    surprises: tuple[_Surprise, ...]
    notices: tuple[str, ...]


def financials_tools(service: FinancialsService) -> ToolBuilder:
    """给出 Fundamentals 这次运行的 financials：股票是任务的股票，时刻是这版快照的生成时刻。"""

    def build(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
        symbol = request.task.symbol
        before = request.snapshot.taken_at

        def financials() -> str:
            report = service.report(symbol, before)
            entry_id = f"financials/{symbol}" if _citable(report) else None
            if entry_id is not None:
                record([entry_id])
            return _report(report, entry_id).model_dump_json()

        return (ReadTool("financials", _DESCRIPTION, financials),)

    return build


def _citable(report: FinancialReport) -> bool:
    """有至少一个季度、一次财报发布或者一个 surprise 的季度，见 spec「结果可以引用」。"""
    surprises = () if report.surprises is None else report.surprises.quarters
    return bool(report.quarters or report.releases or surprises)


def _report(report: FinancialReport, entry_id: str | None) -> _Report:
    surprises = report.surprises
    return _Report(
        entry_id=entry_id,
        filed_before=report.filed_before,
        quarters=tuple(_quarter(q) for q in report.quarters),
        releases=tuple(_release(r) for r in report.releases),
        next_release=None
        if report.next_release is None
        else _release(ReleaseBeat.of(report.next_release)),
        surprises=() if surprises is None else _surprises(surprises),
        notices=_notices(report),
    )


def _quarter(quarter: QuarterFigures) -> _Quarter:
    def value(item: Item) -> Decimal | None:
        figure = quarter.figures.get(item)
        return None if figure is None else figure.value

    return _Quarter(
        start=quarter.start,
        end=quarter.end,
        revenue=value(Item.REVENUE),
        gross_profit=value(Item.GROSS_PROFIT),
        operating_income=value(Item.OPERATING_INCOME),
        net_income=value(Item.NET_INCOME),
        eps_diluted=value(Item.EPS_DILUTED),
        diluted_shares=value(Item.DILUTED_SHARES),
        operating_cash_flow=value(Item.OPERATING_CASH_FLOW),
        capex=value(Item.CAPEX),
        derived=tuple(item.value for item, figure in quarter.figures.items() if figure.derived),
        free_cash_flow=quarter.free_cash_flow,
        gross_margin=_percent(quarter.gross_margin),
        operating_margin=_percent(quarter.operating_margin),
        net_margin=_percent(quarter.net_margin),
        revenue_yoy=_percent(quarter.growth[Item.REVENUE]),
        operating_income_yoy=_percent(quarter.growth[Item.OPERATING_INCOME]),
        net_income_yoy=_percent(quarter.growth[Item.NET_INCOME]),
        eps_diluted_yoy=_percent(quarter.growth[Item.EPS_DILUTED]),
    )


def _release(beat: ReleaseBeat) -> _Release:
    release = beat.release
    return _Release(
        fiscal_year=release.fiscal_year,
        fiscal_quarter=release.fiscal_quarter,
        day=release.day,
        time=None if release.time is None else release.time.value,
        eps_estimate=release.eps_estimate,
        eps_actual=release.eps_actual,
        eps_beat=_percent(beat.eps_beat),
        revenue_estimate=release.revenue_estimate,
        revenue_actual=release.revenue_actual,
        revenue_beat=_percent(beat.revenue_beat),
    )


def _surprises(history: SurpriseHistory) -> tuple[_Surprise, ...]:
    return tuple(
        _Surprise(
            period=s.period,
            fiscal_year=s.fiscal_year,
            fiscal_quarter=s.fiscal_quarter,
            eps_actual=s.eps_actual,
            eps_estimate=s.eps_estimate,
            surprise=s.surprise,
            surprise_percent=s.surprise_percent,
        )
        for s in history.quarters
    )


def _percent(value: Decimal | None) -> str | None:
    return None if value is None else f"{value}%"


def _notices(report: FinancialReport) -> tuple[str, ...]:
    day = report.filed_before.isoformat()
    notices = []
    if not report.covered:
        notices.append(f"SEC 没有 {report.symbol} 的季度财务数字，未覆盖。")
    if report.facts_unavailable:
        notices.append(_FACTS_UNAVAILABLE)
    if report.quarters:
        notices += [
            f"只用了 {day} 以前提交的数字；{day} 当天提交的文件用 read_filing 读。",
            _MISSING,
            _DERIVED,
            _PER_SHARE,
        ]
    if report.estimates_unavailable:
        notices.append(_ESTIMATES_UNAVAILABLE)
    else:
        notices.append(_ESTIMATE_SOURCE)
    if report.estimates_refreshed:
        notices.append(
            f"{report.symbol} 在快照时刻以前没有记下过一致预期，下面的一致预期是刷新时取的。"
        )
    return tuple(notices)
