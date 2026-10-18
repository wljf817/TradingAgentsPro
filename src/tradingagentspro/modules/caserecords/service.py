"""案件记录的用例：在案件进行、结束时写入，按案件、按股票读回。见 case-records spec 和
add-case-records 的 design D3、D5、D6。
"""

from collections.abc import Sequence
from uuid import UUID

from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import Conclusion
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.review import CriticReview, PlanResponse
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.caserecords.domain.record import (
    CaseRecord,
    CaseTrigger,
    Finding,
    ProposalRecord,
    Response,
    Review,
    concluded,
    ended,
    left_queue,
    proposed,
    require_started,
    responded,
    reviewed,
    started,
    with_risk,
    without_critic,
)
from tradingagentspro.modules.caserecords.ports import CaseRecordStore
from tradingagentspro.shared.clock import Clock


class CaseRecordService:
    """案件记录的写入和读取。写入方是「案件工作流」（排队的案件不再运行由「排队运行」写）。

    每个写入读出这个案件现在的记录，按 domain 的规则得出新的记录再保存：每一步只写一次，同一步再写
    时记录不变（崩溃以后 DBOS 重新执行的步骤会再写一次）；前面的步骤没写时抛 ValueError，不保存。
    同一个案件的写入由调用方依次进行，不加锁。写案件记录不写程序日志（design D10）。
    """

    def __init__(self, store: CaseRecordStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def start(self, case_id: UUID, trigger: CaseTrigger, case_type: CaseType) -> None:
        """案件开始运行：开始时刻是现在。"""
        record = started(self._store.case(case_id), case_id, trigger, case_type, self._clock.now())
        self._store.save_case(record)

    def not_run(self, case_id: UUID, trigger: CaseTrigger) -> None:
        """排队的案件不再运行：结束时刻是现在，Task 数和调用次数是 0。"""
        record = left_queue(self._store.case(case_id), case_id, trigger, self._clock.now())
        self._store.save_case(record)

    def conclude(
        self, case_id: UUID, conclusion: Conclusion, model: ModelUse, plan: TradePlan | None
    ) -> None:
        """结论通过校验：结论时刻是现在；有交易方案时 plan 是它补上方案 ID、股票和板块以后的。

        先写方案记录、再写结论，两张表各自按"写过没有"判断，中间崩溃时重新执行补上没写的那张。
        """
        record = concluded(self._store.case(case_id), case_id, conclusion, model, self._clock.now())
        if plan is not None:
            self._store.save_proposal(proposed(self._store.proposal(case_id), case_id, plan))
        self._store.save_case(record)

    def review(self, case_id: UUID, review: CriticReview, model: ModelUse) -> None:
        """Critic 给出一次审核。"""
        proposal = reviewed(self._store.proposal(case_id), case_id, Review(review, model))
        self._store.save_proposal(proposal)

    def critic_unavailable(self, case_id: UUID) -> None:
        """Critic 不可用，方案没有经过审核。"""
        self._store.save_proposal(without_critic(self._store.proposal(case_id), case_id))

    def respond(self, case_id: UUID, response: PlanResponse, model: ModelUse) -> None:
        """PM 回应一轮质询。"""
        proposal = responded(self._store.proposal(case_id), case_id, Response(response, model))
        self._store.save_proposal(proposal)

    def risk_checked(self, case_id: UUID, decision: RiskDecision) -> None:
        """风控给出结果。"""
        self._store.save_proposal(with_risk(self._store.proposal(case_id), case_id, decision))

    def add_findings(self, case_id: UUID, findings: Sequence[Finding]) -> None:
        """结论里的股票池增删执行以后，按顺序写每一项；这个案件已经有发现时不写。"""
        require_started(self._store.case(case_id), case_id)
        self._store.add_findings(case_id, findings)

    def end(self, case_id: UUID, reason: CaseEndReason, tasks: int, calls: int) -> None:
        """案件结束：结束时刻是现在。"""
        record = ended(self._store.case(case_id), case_id, reason, tasks, calls, self._clock.now())
        self._store.save_case(record)

    def case(self, case_id: UUID) -> CaseRecord:
        """这个案件的记录；没有时抛 KeyError。"""
        record = self._store.case(case_id)
        if record is None:
            raise KeyError(case_id)
        return record

    def proposal(self, case_id: UUID) -> ProposalRecord | None:
        """这个案件的方案记录；没有交易方案的案件为 None。"""
        return self._store.proposal(case_id)

    def findings(self, case_id: UUID) -> tuple[Finding, ...]:
        """这个案件的发现，按写入的顺序。"""
        return self._store.findings(case_id)

    def recent_ended(self, symbol: str, limit: int) -> tuple[CaseRecord, ...]:
        """这只股票已结束的案件，按结束时刻从晚到早，最多 limit 个。"""
        return self._store.recent_ended(symbol, limit)

    def last_concluded(self, case_type: CaseType, symbol: str | None) -> CaseRecord | None:
        """这种案件类型（寻股的 symbol 为 None）结论时刻最晚的有结论的案件；没有时为 None。"""
        return self._store.last_concluded(case_type, symbol)
