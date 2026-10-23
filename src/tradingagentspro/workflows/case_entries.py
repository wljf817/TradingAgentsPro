"""案件工作流的文字：调度器的触发换成案件记录的触发、退回 PM 的原因、程序日志的开案件、案件阶段
变化、唤醒 agent 和模型全部不可用的告警。

见 case-workflow spec、program-log spec「调度器的动作」、add-case-records 的 design D8 和
add-case-workflow 的 design D5、D12。「排队运行」写不再运行的案件时也用 case_trigger。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.agent import Agent
from tradingagentspro.contracts.agent_call import (
    DispatchTasks,
    RetrospectiveReport,
    SubmitConclusion,
    SubmitReview,
)
from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.conclusion import Decision, ExplorationConclusion
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.review import PlanResponse
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.caserecords import CaseTrigger, TriggerKind
from tradingagentspro.modules.followups import FollowUpRejection
from tradingagentspro.modules.gateway import SignalRejection
from tradingagentspro.modules.heartbeat import ThesisDatesRejection
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    CaseStage,
    CaseState,
    ConclusionRejection,
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    SellTrigger,
    Trigger,
)
from tradingagentspro.workflows.names import (
    ACTIONS,
    AGENTS,
    CASE_STAGES,
    CASE_TYPES,
    CRITIC_OUTCOMES,
    DECISIONS,
    END_REASONS,
    EVENT_CATEGORIES,
    RESPONSES,
    VERDICTS,
)

# case-types spec「触发」的检查项表：定时心跳开出的案件类型对应的检查项
_HEARTBEAT_ITEMS: Final = {
    CaseType.HOLDING_REVIEW: "持仓复查日到期",
    CaseType.HOLDING_CATALYST_REVIEW: "投资论点的催化剂截止日期到期",
    CaseType.WATCH_REVIEW: "关注复查日到期",
    CaseType.WATCH_CATALYST_REVIEW: "关注论点的催化剂截止日期到期",
    CaseType.CANDIDATE_EVALUATION: "有新候选",
    CaseType.EXPLORATION: "股票池候选不足",
    CaseType.WEEKLY_RETROSPECTIVE: "每周固定时间",
}


def case_trigger(trigger: Trigger, triggered_at: datetime) -> CaseTrigger:
    """调度器的触发换成案件记录的触发：种类、触发原因、股票和触发时刻（add-case-records D8）。

    事件的触发原因带事件 ID，卖出成交的带客户端订单 ID（add-event-detection 的 design D12）。
    """
    match trigger:
        case EventTrigger(symbol, category, holding_risk, event_id):
            reason = f"{symbol} 的{EVENT_CATEGORIES[category]}事件（{event_id}）"
            if holding_risk:
                reason += "，持仓风险级别"
            return CaseTrigger(TriggerKind.EVENT, reason, symbol, triggered_at)
        case HeartbeatTrigger(case_type, symbol):
            item = _HEARTBEAT_ITEMS[case_type]
            reason = item if symbol is None else f"{symbol} 的{item}"
            return CaseTrigger(TriggerKind.HEARTBEAT, reason, symbol, triggered_at)
        case FollowUpTrigger(symbol, question):
            reason = f"{symbol} 的跟进：{question}"
            return CaseTrigger(TriggerKind.FOLLOW_UP, reason, symbol, triggered_at)
        case SellTrigger(symbol, order_id):
            reason = f"{symbol} 卖出成交（{order_id}）"
            return CaseTrigger(TriggerKind.SELL, reason, symbol, triggered_at)


# 退回 PM 的原因


@dataclass(frozen=True, slots=True)
class Blocked:
    """PM 用备用模型时提了被限制的决定（model-fallback spec「备用模型的限制」）。"""

    decision: Decision


@dataclass(frozen=True, slots=True)
class Unscreened:
    """寻股加入的股票不在本案件快照的粗筛名单里。"""

    symbol: str


# 结论的检查不通过的原因，见 case-workflow spec「结论的检查」
type Problem = (
    SignalRejection
    | Blocked
    | ConclusionRejection
    | Unscreened
    | ThesisDatesRejection
    | FollowUpRejection
)

_SIGNAL: Final = {
    SignalRejection.HORIZON_NOT_OFFERED: "评估窗口不在配置的选项里",
    SignalRejection.DATA_AFTER_NOW: "数据截至时刻晚于现在",
}
_CONCLUSION: Final = {
    ConclusionRejection.WRONG_KIND: "结论不属于这个案件",
    ConclusionRejection.FOLLOW_UP_IN_FOLLOW_UP: "跟进案件不能再申请跟进",
    ConclusionRejection.MISSING_RENEWAL: "续期缺少论点更新",
    ConclusionRejection.MISSING_REQUIRED_AGENT: "缺少事件要求的子 agent",
    ConclusionRejection.INSUFFICIENT_RESEARCH: "研究不足",
}
_DATES: Final = {
    ThesisDatesRejection.REVIEW_TOO_SOON: "复查日太近",
    ThesisDatesRejection.POSTPONED_IN_LOSS: "亏损时不能推后复查日",
    ThesisDatesRejection.REVIEW_TOO_LATE: "复查日太远",
    ThesisDatesRejection.REVIEW_NOT_SESSION: "复查日不是交易日",
    ThesisDatesRejection.STALE_CATALYST: "没有写新的催化剂",
    ThesisDatesRejection.DEADLINE_TOO_SOON: "截止日期太近",
    ThesisDatesRejection.UNKNOWN_SCHEDULE: "引用的日程项不存在",
    ThesisDatesRejection.OFF_SCHEDULE: "截止日期和日程项不符",
    ThesisDatesRejection.DEADLINE_TOO_LATE: "截止日期太远",
    ThesisDatesRejection.DEADLINE_NOT_SESSION: "截止日期不是交易日",
}
_FOLLOW_UP: Final = {
    FollowUpRejection.NO_REFERENCE: "没有引用具体对象：去掉跟进申请，或者在论点更新里调整复查日",
    FollowUpRejection.REFERENCE_MISMATCH: "跟进申请的原因和引用的对象不符",
    FollowUpRejection.CATALYST_COVERED: "跟进申请引用的对象催化剂已经覆盖",
    FollowUpRejection.TRIGGER_PASSED: "跟进申请的触发时间已过",
    FollowUpRejection.TRIGGER_TOO_FAR: "跟进申请的触发时间太远",
    FollowUpRejection.STOCK_FULL: "这只股票待执行的跟进已满",
    FollowUpRejection.DAILY_LIMIT: "今天跟进申请的次数已用完",
}


def rejection_text(problem: Problem) -> str:
    """结论退回 PM 时交给它的原因。"""
    match problem:
        case SignalRejection():
            return f"结论里的信号不合法：{_SIGNAL[problem]}"
        case Blocked():
            return "用备用模型时不能提这个决定"
        case ConclusionRejection():
            return _CONCLUSION[problem]
        case Unscreened(symbol):
            return f"加入的 {symbol} 不在粗筛名单里"
        case ThesisDatesRejection():
            return _DATES[problem]
        case FollowUpRejection():
            return _FOLLOW_UP[problem]


# 唤醒 agent 的原因

START_RESEARCH: Final = "开始研究"
TASKS_RETURNED: Final = "交回任务的结果"
RESEARCH_RETURNED: Final = "交回补充研究的结果"
RETROSPECTIVE: Final = "复盘"


def resubmit_why(count: int, reason: str) -> str:
    """退回重交以后唤醒 PM：第几次退回和原因。"""
    return f"第 {count} 次退回重交：{reason}"


def respond_why(round_: int) -> str:
    return f"回应第 {round_} 轮质询"


def review_why(count: int) -> str:
    return f"第 {count} 次审核"


def research_reason(count: int) -> str:
    """Critic 补充研究时新一版快照的原因。"""
    return f"Critic 第 {count} 次补充研究"


# 程序日志的说明


def open_case_text(definition: CaseDefinition, trigger: CaseTrigger) -> str:
    """开案件：案件类型、股票和触发原因。"""
    stock = definition.symbol or "不针对单只股票"
    return f"{CASE_TYPES[definition.case_type]}，{stock}；触发：{trigger.reason}"


def stage_text(before: CaseState, after: CaseState, plan: TradePlan | None) -> str:
    """案件阶段变化：之前和之后的阶段，以及方案、质询轮次、Critic 审核的结果、结束原因。"""
    details = []
    if plan is not None:
        details.append(f"{ACTIONS[plan.action]}，方案 {plan.plan_id}")
    if after.stage is CaseStage.PM_RESPONSE:
        details.append(f"第 {after.rounds} 轮质询")
    if after.stage is CaseStage.RISK_CHECK:
        assert after.critic is not None  # 进入风控时一定有 Critic 审核的结果
        details.append(CRITIC_OUTCOMES[after.critic])
    if after.end_reason is not None:
        details.append(END_REASONS[after.end_reason])
        details.append("有结论" if after.concluded else "没有结论")
    text = f"{CASE_STAGES[before.stage]} → {CASE_STAGES[after.stage]}"
    return f"{text}（{'，'.join(details)}）" if details else text


# agent 交回的输出：PM 的派发、结论和回应，Critic 的派发和审核，Reviewer 的复盘报告
type Reply = DispatchTasks | SubmitConclusion | PlanResponse | SubmitReview | RetrospectiveReport


def _returned(output: Reply) -> str:
    """agent 交回了什么。"""
    match output:
        case DispatchTasks(requests=requests):
            return f"派发 {len(requests)} 个任务"
        case SubmitConclusion(conclusion=conclusion):
            if isinstance(conclusion, ExplorationConclusion):
                added = "、".join(a.symbol for a in conclusion.additions) or "没有"
                removed = "、".join(r.symbol for r in conclusion.removals) or "没有"
                return f"提交寻股的结论：加入 {added}，删除 {removed}"
            return f"提交结论：{DECISIONS[conclusion.decision]}"
        case PlanResponse(decision=decision):
            return f"回应：{RESPONSES[decision]}"
        case SubmitReview(review=review):
            return f"审核：{VERDICTS[review.verdict]}"
        case RetrospectiveReport(summary=summary):
            return f"复盘报告：{summary}"


def wake_text(agent: Agent, why: str, output: Reply, model: ModelUse) -> str:
    """唤醒 agent 并且调用成功：哪个 agent、为什么唤醒、交回了什么、实际使用的模型。"""
    fallback = "是备用模型" if model.fallback else "不是备用模型"
    used = f"模型 {model.model.vendor}/{model.model.name}，{fallback}"
    return f"{AGENTS[agent]}：{why}；{_returned(output)}；{used}"


def wake_failed_text(agent: Agent, why: str, detail: str) -> str:
    """唤醒 agent 但调用失败：网关给出的说明。"""
    return f"{AGENTS[agent]}：{why}；调用失败：{detail}"


def unavailable_alert(
    case_id: UUID, case_type: CaseType, symbol: str | None, agent: Agent, detail: str
) -> str:
    """PM、Critic 或 Reviewer 的模型全部不可用时的告警。"""
    stock = symbol or "不针对单只股票"
    return (
        f"案件 {case_id}（{CASE_TYPES[case_type]}，{stock}）：{AGENTS[agent]} 的模型全部不可用，"
        f"{detail}"
    )
