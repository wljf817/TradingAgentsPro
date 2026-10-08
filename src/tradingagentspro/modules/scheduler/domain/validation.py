"""结论的校验：按案件类型检查 PM 提交的结论，见 case-types spec「结论的校验」。

哪个决定附什么和案件类型无关，由结论模型保证；这里只做和案件类型有关的检查。拒绝是 PM 提交的正常
结果，所以作为返回值，不抛异常，由「案件阶段」退回 PM。
"""

from collections.abc import Collection, Mapping
from enum import StrEnum
from typing import Final

from tradingagentspro.contracts.agent import SubAgent
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import (
    CandidateConclusion,
    Conclusion,
    Decision,
    ExplorationConclusion,
    FollowUpRequest,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.thesis import InvestmentThesis, WatchThesis
from tradingagentspro.modules.scheduler.config import SchedulerConfig
from tradingagentspro.modules.scheduler.domain.case_type import CaseDefinition, conclusion_model


class ConclusionRejection(StrEnum):
    """PM 的结论不通过的原因。"""

    WRONG_KIND = "wrong_kind"  # 结论不属于这个案件
    FOLLOW_UP_IN_FOLLOW_UP = "follow_up_in_follow_up"  # 跟进案件不能再申请跟进
    MISSING_RENEWAL = "missing_renewal"  # 续期缺少论点更新
    MISSING_REQUIRED_AGENT = "missing_required_agent"  # 缺少事件要求的子 agent
    INSUFFICIENT_RESEARCH = "insufficient_research"  # 研究不足


_FOLLOW_UP_CASES: Final = frozenset({CaseType.HOLDING_FOLLOW_UP, CaseType.WATCH_FOLLOW_UP})
_REVIEW_CASES: Final = frozenset(
    {
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.WATCH_REVIEW,
        CaseType.WATCH_CATALYST_REVIEW,
    }
)
# 结论之后股票还是这个状态的决定：复查类案件里选了就是续期
_RENEWING: Final = frozenset({Decision.HOLD, Decision.ADD, Decision.REDUCE, Decision.KEEP_WATCHING})
# 有研究要求的决定
_RESEARCHED: Final = frozenset({Decision.BUY, Decision.ADD, Decision.PROMOTE})


def _parts(
    conclusion: Conclusion,
) -> tuple[Decision | None, FollowUpRequest | None, InvestmentThesis | WatchThesis | None]:
    """结论的决定、跟进申请和论点更新；没有的为 None。"""
    match conclusion:
        case HoldingConclusion():
            return conclusion.decision, conclusion.follow_up, conclusion.thesis
        case WatchConclusion():
            return conclusion.decision, conclusion.follow_up, conclusion.watch_thesis
        case CandidateConclusion():
            return conclusion.decision, None, None
        case ExplorationConclusion():
            return None, None, None


def conclusion_rejection(
    case: CaseDefinition,
    conclusion: Conclusion,
    dispatched: Collection[SubAgent],
    completed: Mapping[str, SubAgent],
    config: SchedulerConfig,
) -> ConclusionRejection | None:
    """按 spec「结论的校验」的顺序给出第一项不通过的原因，全部通过时为 None。

    - dispatched：本案件派过任务的子 agent，不论 Result 的状态
    - completed：本案件状态是完成的 Result 出自哪个子 agent，键是 Result ID；没传入的 Result 不计数

    复盘案件不唤醒 PM，没有结论，抛 ValueError。
    """
    model = conclusion_model(case.case_type)
    if model is None:
        raise ValueError(f"复盘案件 {case.case_type} 不唤醒 PM，没有结论")
    # 种类放在第一项：后面的检查都要按种类读字段
    if type(conclusion) is not model:
        return ConclusionRejection.WRONG_KIND
    decision, follow_up, update = _parts(conclusion)
    if case.case_type in _FOLLOW_UP_CASES and follow_up is not None:
        return ConclusionRejection.FOLLOW_UP_IN_FOLLOW_UP
    if case.case_type in _REVIEW_CASES and decision in _RENEWING and update is None:
        return ConclusionRejection.MISSING_RENEWAL
    if case.required_agent is not None and case.required_agent not in dispatched:
        return ConclusionRejection.MISSING_REQUIRED_AGENT
    if decision in _RESEARCHED:
        agents = {completed[r] for r in conclusion.cited_results if r in completed}
        if len(agents) < config.min_cited_agents:
            return ConclusionRejection.INSUFFICIENT_RESEARCH
    return None
