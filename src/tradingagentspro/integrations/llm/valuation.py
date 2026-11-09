"""Fundamentals 的读取工具 valuation，见 valuation-tool spec 和 add-valuation-tool 的 design
D11。
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from tradingagentspro.contracts.agent_call import SubAgentRequest
from tradingagentspro.integrations.llm.client import ReadTool
from tradingagentspro.integrations.llm.sub_agent import STOCK_IS_SET, RecordEntries, ToolBuilder
from tradingagentspro.modules.companydata import (
    Appraisal,
    Dated,
    Position,
    Price,
    ValuationReport,
    ValuationService,
)

_DESCRIPTION: Final = (
    "列出这只股票的价格、TTM EPS 和营收、稀释加权股数、PE、PS、市值，"
    f"以及 PE、PS 在过去几年每个交易日里的位置。{STOCK_IS_SET}"
)
_TTM: Final = (
    "TTM 是上个财年全年加今年累计、减去年同期累计；每股数字和股数已经按拆股换到同一个口径。"
)
_NEW_QUARTER: Final = "财报当天的新一季要等 10-Q 提交以后才算进 TTM。"
_MISSING: Final = "null 是没有这个数：公司没有报告、算不出 TTM，或者最近一期太旧，不要自己估算。"
_LOSS: Final = "TTM EPS 不大于 0，PE 写亏损，不给 PE 的位置。"
_FACTS_UNAVAILABLE: Final = "SEC 这次不可用，数字只来自以前存下的，可能不全。"
_PRICES_UNAVAILABLE: Final = "Alpaca 这次不可用，估值这次给不出。"
_LOSS_TEXT: Final = "亏损"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Price(_Frozen):
    """价格：快照的最新价带行情的更新时刻，收盘价带是哪一天的。"""

    value: Decimal
    quoted_at: datetime | None
    close_day: date | None


class _Dated(_Frozen):
    """一项数和它最近一期的结束日。"""

    value: Decimal
    through: date


class _PePosition(_Frozen):
    """PE 的位置；百分数是文字，days 是 0 时没有分位和最低、中位、最高。"""

    days: int
    missing_days: int
    loss_days: int
    percentile: str | None
    low: Decimal | None
    median: Decimal | None
    high: Decimal | None


class _PsPosition(_Frozen):
    days: int
    missing_days: int
    percentile: str | None
    low: Decimal | None
    median: Decimal | None
    high: Decimal | None


class _Window(_Frozen):
    start: date
    end: date


class _Report(_Frozen):
    """valuation 的结果；entry_id 只在有价格时给出。"""

    entry_id: str | None
    filed_before: date
    price: _Price | None
    ttm_eps: _Dated | None
    ttm_revenue: _Dated | None
    diluted_shares: _Dated | None
    pe: Decimal | Literal["亏损"] | None
    ps: Decimal | None
    market_cap: Decimal | None
    pe_position: _PePosition | None
    ps_position: _PsPosition | None
    window: _Window
    notices: tuple[str, ...]


def valuation_tools(service: ValuationService) -> ToolBuilder:
    """给出 Fundamentals 这次运行的 valuation：股票是任务的股票，时刻是这版快照的生成时刻，价格
    先用这版快照里这只股票的最新价。
    """

    def build(request: SubAgentRequest, record: RecordEntries) -> Sequence[ReadTool]:
        symbol = request.task.symbol
        snapshot = request.snapshot
        quote = next((q for q in snapshot.quotes if q.symbol == symbol), None)

        def valuation() -> str:
            report = service.report(symbol, snapshot.taken_at, quote)
            entry_id = None if report.price is None else f"valuation/{symbol}"
            if entry_id is not None:
                record([entry_id])
            return _report(report, entry_id).model_dump_json()

        return (ReadTool("valuation", _DESCRIPTION, valuation),)

    return build


def _report(report: ValuationReport, entry_id: str | None) -> _Report:
    appraisal = report.appraisal
    return _Report(
        entry_id=entry_id,
        filed_before=report.filed_before,
        price=None if report.price is None else _price(report.price),
        ttm_eps=None if appraisal is None else _dated(appraisal.eps),
        ttm_revenue=None if appraisal is None else _dated(appraisal.revenue),
        diluted_shares=None if appraisal is None else _dated(appraisal.shares),
        pe=None if appraisal is None else _pe(appraisal),
        ps=None if appraisal is None else appraisal.ps,
        market_cap=None if appraisal is None else appraisal.market_cap,
        pe_position=None
        if appraisal is None or appraisal.pe_position is None
        else _pe_position(appraisal.pe_position),
        ps_position=None
        if appraisal is None or appraisal.ps_position is None
        else _ps_position(appraisal.ps_position),
        window=_Window(start=report.window_start, end=report.window_end),
        notices=_notices(report),
    )


def _price(price: Price) -> _Price:
    return _Price(value=price.value, quoted_at=price.quoted_at, close_day=price.close_day)


def _dated(item: Dated | None) -> _Dated | None:
    return None if item is None else _Dated(value=item.value, through=item.through)


def _pe(appraisal: Appraisal) -> Decimal | Literal["亏损"] | None:
    return _LOSS_TEXT if appraisal.loss else appraisal.pe


def _pe_position(position: Position) -> _PePosition:
    return _PePosition(
        days=position.days,
        missing_days=position.missing_days,
        loss_days=position.loss_days,
        percentile=_percent(position.percentile),
        low=position.low,
        median=position.median,
        high=position.high,
    )


def _ps_position(position: Position) -> _PsPosition:
    return _PsPosition(
        days=position.days,
        missing_days=position.missing_days,
        percentile=_percent(position.percentile),
        low=position.low,
        median=position.median,
        high=position.high,
    )


def _percent(value: Decimal | None) -> str | None:
    return None if value is None else f"{value}%"


def _notices(report: ValuationReport) -> tuple[str, ...]:
    """结果的说明，见 valuation-tool spec「结果的说明」和各条要求写明的内容。"""
    if not report.covered:
        return (f"SEC 没有 {report.symbol} 的季度财务数字，未覆盖。",)
    notices = [_FACTS_UNAVAILABLE] if report.facts_unavailable else []
    if report.price is None:
        return (*notices, _PRICES_UNAVAILABLE)
    price = report.price
    day = report.filed_before.isoformat()
    notices += [
        f"现在的数字只用了 {day} 以前提交的；历史上的每一天只用那一天以前提交的。",
        _TTM,
        _NEW_QUARTER,
        f"价格是快照的最新价，行情更新于 {price.quoted_at.isoformat()}。"
        if price.quoted_at is not None
        else f"价格是 {price.close_day} 的收盘价。",
        f"最近一期结束超过 {report.max_age_days} 天的数字不用。",
        f"位置按 {report.window_start} 到 {report.window_end} 每个交易日的原始收盘价和那一天以前"
        "提交的数字算；分位是值低于现在的天数占用了的天数的百分比。",
        _MISSING,
    ]
    if report.appraisal is not None and report.appraisal.loss:
        notices.append(_LOSS)
    return tuple(notices)
