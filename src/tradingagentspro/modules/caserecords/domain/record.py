"""案件记录、方案记录和发现，以及写入它们的规则。

每个写入规则拿这个案件现在的记录（还没有时为 None），给出要保存的记录：这一步已经写过时原样返回
（崩溃以后重新执行的步骤会再写一次）；前面的步骤没写时抛 ValueError。见 case-records spec
「重复写入和写入的顺序」和 add-case-records 的 design D2、D5。
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import Conclusion
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.review import CriticReview, PlanResponse
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.signal import Signal
from tradingagentspro.contracts.trade_plan import TradePlan


class TriggerKind(StrEnum):
    """触发的种类，就是 case-types spec「触发」的四种。"""

    EVENT = "event"  # 事件
    HEARTBEAT = "heartbeat"  # 定时心跳的检查项
    FOLLOW_UP = "follow_up"  # 跟进
    SELL = "sell"  # 卖出成交


class CaseStatus(StrEnum):
    RUNNING = "running"  # 进行中
    ENDED = "ended"  # 已结束
    NOT_RUN = "not_run"  # 不再运行


@dataclass(frozen=True, slots=True)
class CaseTrigger:
    """案件的触发，开始运行和不再运行都带。"""

    kind: TriggerKind
    reason: str  # 触发原因：调用方写的中文说明
    symbol: str | None  # 寻股和每周复盘没有
    at: datetime  # 触发时刻


@dataclass(frozen=True, slots=True)
class CaseConclusion:
    """通过校验的结论、给出它的模型和结论时刻。"""

    conclusion: Conclusion
    model: ModelUse
    at: datetime


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """一个案件的记录，各项见 case-records spec「案件记录的内容」。

    状态和耗时由记录推出：开始时刻为空就是不再运行，结束时刻为空就是进行中。case_type 和
    started_at 不给默认值，存着的记录缺了它们时读回报错。
    """

    case_id: UUID
    trigger: CaseTrigger
    case_type: CaseType | None  # 不再运行的没有
    started_at: datetime | None  # 不再运行的没有
    conclusion: CaseConclusion | None = None
    end_reason: CaseEndReason | None = None  # 只有已结束的有
    ended_at: datetime | None = None  # 已结束和不再运行的有
    tasks: int | None = None  # Task 数：结束时写；不再运行的是 0
    calls: int | None = None  # 调用次数：同上

    @property
    def status(self) -> CaseStatus:
        if self.started_at is None:
            return CaseStatus.NOT_RUN
        return CaseStatus.RUNNING if self.ended_at is None else CaseStatus.ENDED

    @property
    def duration(self) -> timedelta | None:
        """耗时：只有已结束的有。"""
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass(frozen=True, slots=True)
class Review:
    review: CriticReview
    model: ModelUse  # Critic 这次用的模型


@dataclass(frozen=True, slots=True)
class Response:
    response: PlanResponse
    model: ModelUse  # PM 这次用的模型


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """一个案件的方案记录，各项见 case-records spec「方案记录」。"""

    case_id: UUID
    plan: TradePlan  # 结论通过时的交易方案；修改的条款在那一轮回应里
    reviews: tuple[Review, ...] = ()
    responses: tuple[Response, ...] = ()
    critic_unavailable: bool = False
    risk: RiskDecision | None = None


class FindingKind(StrEnum):
    ADD = "add"  # 加入候选
    REMOVE = "remove"  # 删除


@dataclass(frozen=True, slots=True)
class Finding:
    """一项股票池增删和股票池的结果，见 case-records spec「股票池增删的发现」。"""

    kind: FindingKind
    symbol: str
    reason: str  # 加入的理由与论点草稿；删除的理由
    signal: Signal | None  # 加入的候选的信号；删除没有
    rejection: str | None  # 股票池不通过的原因：stockpool 的 Rejection 的值；通过为 None


# 案件记录


def started(
    record: CaseRecord | None,
    case_id: UUID,
    trigger: CaseTrigger,
    case_type: CaseType,
    now: datetime,
) -> CaseRecord:
    """开始运行，开始时刻是 now。"""
    if record is None:
        return CaseRecord(case_id, trigger, case_type, now)
    if record.status is CaseStatus.NOT_RUN:
        raise ValueError(f"案件 {case_id} 已经不再运行，不能开始运行")
    return record


def left_queue(
    record: CaseRecord | None, case_id: UUID, trigger: CaseTrigger, now: datetime
) -> CaseRecord:
    """排队的案件不再运行，结束时刻是 now，Task 数和调用次数是 0。"""
    if record is None:
        return CaseRecord(case_id, trigger, None, None, ended_at=now, tasks=0, calls=0)
    if record.status is not CaseStatus.NOT_RUN:
        raise ValueError(f"案件 {case_id} 已经开始运行，不能写不再运行")
    return record


def concluded(
    record: CaseRecord | None,
    case_id: UUID,
    conclusion: Conclusion,
    model: ModelUse,
    now: datetime,
) -> CaseRecord:
    """结论通过校验，结论时刻是 now。"""
    running = require_started(record, case_id)
    if running.conclusion is not None:
        return running
    return replace(running, conclusion=CaseConclusion(conclusion, model, now))


def ended(
    record: CaseRecord | None,
    case_id: UUID,
    reason: CaseEndReason,
    tasks: int,
    calls: int,
    now: datetime,
) -> CaseRecord:
    """案件结束，结束时刻是 now。"""
    running = require_started(record, case_id)
    if running.ended_at is not None:
        return running
    return replace(running, end_reason=reason, ended_at=now, tasks=tasks, calls=calls)


def require_started(record: CaseRecord | None, case_id: UUID) -> CaseRecord:
    """写了开始运行的记录；没有记录或者不再运行时抛 ValueError。"""
    if record is None or record.status is CaseStatus.NOT_RUN:
        raise ValueError(f"案件 {case_id} 还没有开始运行")
    return record


# 方案记录


def proposed(proposal: ProposalRecord | None, case_id: UUID, plan: TradePlan) -> ProposalRecord:
    """结论有交易方案：新建方案记录。"""
    return ProposalRecord(case_id, plan) if proposal is None else proposal


def reviewed(proposal: ProposalRecord | None, case_id: UUID, review: Review) -> ProposalRecord:
    """Critic 给出一次审核。上一次审核还没有回应时是重复写入，原样返回。"""
    current = _proposed(proposal, case_id)
    if len(current.reviews) > len(current.responses):
        return current
    return replace(current, reviews=(*current.reviews, review))


def responded(proposal: ProposalRecord | None, case_id: UUID, response: Response) -> ProposalRecord:
    """PM 回应一轮质询。上一轮回应以后还没有新的审核时是重复写入，原样返回。"""
    current = _proposed(proposal, case_id)
    if not current.reviews:
        raise ValueError(f"案件 {case_id} 的方案还没有审核，不能写回应")
    if len(current.reviews) == len(current.responses):
        return current
    return replace(current, responses=(*current.responses, response))


def without_critic(proposal: ProposalRecord | None, case_id: UUID) -> ProposalRecord:
    """Critic 不可用，方案没有经过审核。"""
    return replace(_proposed(proposal, case_id), critic_unavailable=True)


def with_risk(
    proposal: ProposalRecord | None, case_id: UUID, decision: RiskDecision
) -> ProposalRecord:
    """风控给出结果。"""
    current = _proposed(proposal, case_id)
    return current if current.risk is not None else replace(current, risk=decision)


def _proposed(proposal: ProposalRecord | None, case_id: UUID) -> ProposalRecord:
    if proposal is None:
        raise ValueError(f"案件 {case_id} 没有方案记录")
    return proposal
