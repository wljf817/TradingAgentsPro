"""一只股票的历史：把案件记录和交易记录组合成历史索引和 query_history 的结果，见 history-query spec
和 add-query-history 的 design D1、D4、D7、D8。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.history import (
    HistoryCase,
    HistoryIndex,
    HistoryReport,
    HistoryTrade,
)
from tradingagentspro.modules.caserecords import CaseRecord, CaseRecordService
from tradingagentspro.modules.execution import ExecutionService

_EASTERN: Final = ZoneInfo("America/New_York")


class CaseHistory:
    """满足 Agent 网关的 HistoryReader；只读，内容都截到给定的时刻以前。"""

    def __init__(self, records: CaseRecordService, execution: ExecutionService) -> None:
        self._records = records
        self._execution = execution

    def index(self, symbol: str, before: datetime) -> HistoryIndex:
        """案件上下文里的历史索引。"""
        return HistoryIndex(
            cases=len(self._ended(symbol, before)),
            trades=len(self._execution.trades_at(symbol, before)),
        )

    def report(
        self,
        symbol: str,
        before: datetime,
        since: date | None,
        case_type: CaseType | None,
        limit: int,
    ) -> HistoryReport:
        """一次查询的结果：案件和交易各最多 limit 条，都是最新的。"""
        cases = [r for r in self._ended(symbol, before) if _wanted(r, since, case_type)]
        trades = [
            t
            for t in self._execution.trades_at(symbol, before)
            if since is None or t.traded_on >= since
        ]
        return HistoryReport(
            symbol=symbol,
            total_cases=len(cases),
            cases=tuple(self._case(record) for record in cases[:limit]),
            total_trades=len(trades),
            trades=tuple(trades[:limit]),
            realized_pnl=_realized(trades),
        )

    def _ended(self, symbol: str, before: datetime) -> list[CaseRecord]:
        """结束时刻早于 before 的已结束案件，从晚到早。"""
        return [r for r in self._records.ended(symbol) if _ended_at(r) < before]

    def _case(self, record: CaseRecord) -> HistoryCase:
        assert record.case_type is not None and record.end_reason is not None  # 都是已结束的案件
        proposal = self._records.proposal(record.case_id)
        return HistoryCase(
            case_id=record.case_id,
            case_type=record.case_type,
            reason=record.trigger.reason,
            ended_at=_ended_at(record),
            end_reason=record.end_reason,
            conclusion=None if record.conclusion is None else record.conclusion.conclusion,
            plan=None if proposal is None else proposal.plan,
            reviews=() if proposal is None else tuple(r.review for r in proposal.reviews),
            risk=None if proposal is None else proposal.risk,
        )


def _ended_at(record: CaseRecord) -> datetime:
    assert record.ended_at is not None  # 都是已结束的案件
    return record.ended_at


def _wanted(record: CaseRecord, since: date | None, case_type: CaseType | None) -> bool:
    """起始日和案件类型都符合：起始日比较结束时刻的美东日期。"""
    if case_type is not None and record.case_type is not case_type:
        return False
    return since is None or _ended_at(record).astimezone(_EASTERN).date() >= since


def _realized(trades: list[HistoryTrade]) -> Decimal:
    """卖出和止损单的已实现盈亏之和。"""
    return sum((t.realized_pnl for t in trades if t.realized_pnl is not None), Decimal("0.00"))
