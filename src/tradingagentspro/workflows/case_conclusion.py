"""PM 的结论：检查（case-workflow spec「结论的检查」）和结论生效（「结论生效」）。

检查的第 1 到 5 项是纯函数，输入由案件工作流的用例读好传进来，"现在"作为参数；第 6 项跟进审批
获批即保存，在用例里最后做。见 add-case-workflow 的 design D5、D6。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    AfterClose,
    CandidateConclusion,
    Conclusion,
    Decision,
    ExplorationConclusion,
    HoldingConclusion,
    PoolAddition,
    WatchConclusion,
)
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.snapshot import EarningsEntry, Snapshot
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import InvestmentThesis, ReferenceKind, WatchThesis
from tradingagentspro.modules.caserecords import CaseRecordService, Finding, FindingKind
from tradingagentspro.modules.followups import FollowUpService
from tradingagentspro.modules.gateway import (
    GatewayConfig,
    TaskRecord,
    blocked_decision,
    completed_results,
    conclusion_signal_rejection,
    dispatched_agents,
)
from tradingagentspro.modules.heartbeat import (
    HeartbeatConfig,
    ThesisUpdate,
    last_review_day_after,
    thesis_dates_rejection,
)
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    SchedulerConfig,
    conclusion_rejection,
)
from tradingagentspro.modules.stockpool import PoolEntry, PoolSource, Rejection, StockPoolService
from tradingagentspro.shared.trading_calendar import TradingCalendar
from tradingagentspro.workflows.case_entries import Blocked, Problem, Unscreened

# 复查类案件：附的论点更新是续期，其他案件里是平时调整（review-dates spec「检查论点里的日期」）
_REVIEW_CASES: Final = frozenset(
    {
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.WATCH_REVIEW,
        CaseType.WATCH_CATALYST_REVIEW,
    }
)


@dataclass(frozen=True, slots=True)
class CheckConfigs:
    """检查结论用到的配置：网关、调度器、心跳的，和装配这些模块时的是同一份。"""

    gateway: GatewayConfig
    scheduler: SchedulerConfig
    heartbeat: HeartbeatConfig


def conclusion_problem(
    definition: CaseDefinition,
    conclusion: Conclusion,
    model_use: ModelUse,
    records: Sequence[TaskRecord],
    entry: PoolEntry | None,
    snapshot: Snapshot,
    now: datetime,
    calendar: TradingCalendar,
    configs: CheckConfigs,
) -> Problem | None:
    """按 spec「结论的检查」第 1 到 5 项的顺序给出第一项不通过的原因；都通过时为 None。

    - model_use：提交结论的这次调用实际使用的模型
    - records：本案件的任务和 Result
    - entry：案件股票在股票池里的记录；寻股没有
    - snapshot：本案件最新的一版快照
    - now：收到结论的时刻；今天是它归属的交易日
    """
    signal = conclusion_signal_rejection(conclusion, now, configs.gateway)
    if signal is not None:
        return signal
    blocked = blocked_decision(conclusion, model_use, configs.gateway)
    if blocked is not None:
        return Blocked(blocked)
    rejection = conclusion_rejection(
        definition,
        conclusion,
        dispatched_agents(records),
        completed_results(records),
        configs.scheduler,
    )
    if rejection is not None:
        return rejection
    unscreened = _unscreened(conclusion, snapshot)
    if unscreened is not None:
        return Unscreened(unscreened)
    today = calendar.session_closing_after(now).day
    for update in thesis_updates(definition, conclusion, entry, snapshot):
        dates = thesis_dates_rejection(update, today, calendar, configs.heartbeat)
        if dates is not None:
            return dates
    return None


def _unscreened(conclusion: Conclusion, snapshot: Snapshot) -> str | None:
    """寻股加入的第一只不在快照粗筛名单里的股票；都在、或者不是寻股时为 None。"""
    if not isinstance(conclusion, ExplorationConclusion):
        return None
    assert snapshot.screen is not None  # 寻股的快照一定有粗筛结果
    listed = {c.symbol for c in snapshot.screen.candidates}
    return next((a.symbol for a in conclusion.additions if a.symbol not in listed), None)


def thesis_updates(
    definition: CaseDefinition,
    conclusion: Conclusion,
    entry: PoolEntry | None,
    snapshot: Snapshot,
) -> tuple[ThesisUpdate, ...]:
    """结论里每一份新写的论点和检查它的日期要用到的事实，见 spec「结论的检查」第二张表。

    结论的种类已经和案件类型对上（第 3 项）；论点更新的原来的论点和上次复查的那一天取股票池记录。
    """
    match conclusion:
        case WatchConclusion(decision=Decision.BUY, thesis=InvestmentThesis() as thesis):
            return (_new(thesis, snapshot),)
        case CandidateConclusion(decision=Decision.PROMOTE, watch_thesis=WatchThesis() as thesis):
            return (_new(thesis, snapshot),)
        case HoldingConclusion(after_close=AfterClose.WATCH, watch_thesis=WatchThesis() as thesis):
            return (_new(thesis, snapshot),)
        case HoldingConclusion(thesis=InvestmentThesis() as thesis):
            assert entry is not None
            return (_updated(thesis, entry.investment_thesis, definition, entry, snapshot),)
        case WatchConclusion(watch_thesis=WatchThesis() as thesis):
            assert entry is not None
            return (_updated(thesis, entry.watch_thesis, definition, entry, snapshot),)
    return ()


def _new(thesis: InvestmentThesis | WatchThesis, snapshot: Snapshot) -> ThesisUpdate:
    """新设：开仓方案、升级、清仓转为关注附的论点。"""
    return ThesisUpdate(thesis, schedule_day=schedule_day(thesis, snapshot))


def _updated(
    thesis: InvestmentThesis | WatchThesis,
    previous: InvestmentThesis | WatchThesis | None,
    definition: CaseDefinition,
    entry: PoolEntry,
    snapshot: Snapshot,
) -> ThesisUpdate:
    """论点更新：复查类案件里是续期，其他是平时调整；只有投资论点传浮动盈亏比例。"""
    pnl = pnl_ratio(snapshot, entry.symbol) if isinstance(thesis, InvestmentThesis) else None
    return ThesisUpdate(
        thesis,
        previous=previous,
        last_review_day=entry.last_review_day,
        renewal=definition.case_type in _REVIEW_CASES,
        pnl_ratio=pnl,
        schedule_day=schedule_day(thesis, snapshot),
    )


def pnl_ratio(snapshot: Snapshot, symbol: str) -> Decimal | None:
    """持仓的浮动盈亏比例 =(最新价 - 均价) / 均价；没有最新价时用前一交易日收盘价。

    快照里没有这只的行情、或者账户里没有这只持仓时为 None。
    """
    quote = next((q for q in snapshot.quotes if q.symbol == symbol), None)
    held = next((p for p in snapshot.account.positions if p.symbol == symbol), None)
    if quote is None or held is None:
        return None
    price = quote.previous_close if quote.last_price is None else quote.last_price
    return (price - held.average_cost) / held.average_cost


def schedule_day(thesis: InvestmentThesis | WatchThesis, snapshot: Snapshot) -> date | None:
    """催化剂引用日程项时，按日程项的 ID 在快照里查到的财报发布的发布日；查不到时为 None。"""
    reference = thesis.catalyst.reference
    if reference is None or reference.kind is not ReferenceKind.SCHEDULE:
        return None
    entry = snapshot.entry(reference.ref_id)
    return entry.day if isinstance(entry, EarningsEntry) else None


# 结论生效


class ConclusionEffects:
    """结论通过检查以后让它生效：记评分、论点更新、升降级和保持、股票池增删、跟进作废、写发现，见
    case-workflow spec「结论生效」和 add-case-workflow 的 design D6。

    股票池不通过的操作不改变结论：股票池写程序日志，增删写进发现。清仓的去向、开仓的投资论点在成交
    以后由下单那一侧交给股票池，这里不管。
    """

    def __init__(
        self, pool: StockPoolService, follow_ups: FollowUpService, records: CaseRecordService
    ) -> None:
        self._pool = pool
        self._follow_ups = follow_ups
        self._records = records

    def apply(
        self,
        case_id: UUID,
        definition: CaseDefinition,
        conclusion: Conclusion,
        entry: PoolEntry | None,
        snapshot: Snapshot,
        today: date,
    ) -> None:
        """entry 是检查时读到的案件股票的记录；today 是检查时的今天。"""
        findings: list[Finding] = []
        match conclusion:
            case HoldingConclusion(thesis=thesis):
                symbol = self._scored(case_id, definition, conclusion.signal.score)
                if thesis is not None:
                    day = self._last_review_day(definition, conclusion, entry, snapshot, today)
                    self._pool.update_investment_thesis(symbol, thesis, day, case_id=case_id)
            case WatchConclusion(decision=decision, watch_thesis=thesis):
                symbol = self._scored(case_id, definition, conclusion.signal.score)
                if thesis is not None:
                    day = self._last_review_day(definition, conclusion, entry, snapshot, today)
                    self._pool.update_watch_thesis(symbol, thesis, day, case_id=case_id)
                elif decision is Decision.DEMOTE:
                    demoted = self._pool.demote(symbol, case_id=case_id)
                    if isinstance(demoted, PoolEntry):
                        self._status_changed(symbol, StockStatus.WATCH, StockStatus.CANDIDATE)
                elif decision is Decision.REMOVE:
                    findings.append(self._remove(case_id, symbol, conclusion.rationale))
            case CandidateConclusion(decision=decision, watch_thesis=thesis):
                symbol = self._scored(case_id, definition, conclusion.signal.score)
                if thesis is not None:
                    self._pool.promote(symbol, thesis, today, case_id=case_id)
                elif decision is Decision.KEEP:
                    self._pool.keep(symbol, case_id=case_id)
                else:
                    findings.append(self._remove(case_id, symbol, conclusion.rationale))
            case ExplorationConclusion():
                findings.extend(self._add(case_id, a, snapshot) for a in conclusion.additions)
                findings.extend(
                    self._remove(case_id, r.symbol, r.reason) for r in conclusion.removals
                )
        self._records.add_findings(case_id, findings)

    def _scored(self, case_id: UUID, definition: CaseDefinition, score: int) -> str:
        """记下案件股票的评分（在改变状态以前），给出股票。"""
        assert definition.symbol is not None  # 持仓、关注、候选的案件针对单只股票
        self._pool.record_score(definition.symbol, score, case_id=case_id)
        return definition.symbol

    def _last_review_day(
        self,
        definition: CaseDefinition,
        conclusion: Conclusion,
        entry: PoolEntry | None,
        snapshot: Snapshot,
        today: date,
    ) -> date:
        """论点更新生效以后的上次复查的那一天：续期是今天，平时调整不变。"""
        (update,) = thesis_updates(definition, conclusion, entry, snapshot)
        return last_review_day_after(update, today)

    def _add(self, case_id: UUID, addition: PoolAddition, snapshot: Snapshot) -> Finding:
        """寻股加入一只候选：上市信息取快照粗筛名单里的（检查保证在名单里）；通过时记评分。"""
        assert snapshot.screen is not None
        listing = next(c.listing for c in snapshot.screen.candidates if c.symbol == addition.symbol)
        added = self._pool.add(
            addition.symbol, PoolSource.EXPLORATION, addition.reason, listing, case_id=case_id
        )
        if isinstance(added, PoolEntry):
            self._pool.record_score(addition.symbol, addition.signal.score, case_id=case_id)
        return Finding(
            FindingKind.ADD, addition.symbol, addition.reason, addition.signal, _rejected(added)
        )

    def _remove(self, case_id: UUID, symbol: str, reason: str) -> Finding:
        """删除一只股票；通过时作废它待执行的跟进。"""
        before = self._pool.entry(symbol)
        removed = self._pool.remove(symbol, reason, case_id=case_id)
        if isinstance(removed, PoolEntry):
            assert before is not None
            self._status_changed(symbol, before.status, None)
        return Finding(FindingKind.REMOVE, symbol, reason, None, _rejected(removed))

    def _status_changed(self, symbol: str, before: StockStatus, after: StockStatus | None) -> None:
        self._follow_ups.status_changed(symbol, before, after)


def _rejected(outcome: PoolEntry | Rejection) -> str | None:
    """股票池不通过的原因（Rejection 的值）；通过时为 None。"""
    return None if isinstance(outcome, PoolEntry) else outcome.value
