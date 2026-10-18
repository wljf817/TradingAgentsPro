"""案件阶段：一个案件从唤醒 PM 到提交订单走到了哪一步、什么时候结束、以什么原因结束。

规则见 case-stages spec。状态不可变，每个事件一个方法，返回新的状态；不读写存储。
状态由案件工作流（workflows/case_workflow.py）在 DBOS 工作流里推进；事件从哪里来、结束以后
写案件记录由它负责，释放预留由下单那一侧负责。
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, Self

from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.review import ResponseDecision, Verdict
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.scheduler.config import SchedulerConfig
from tradingagentspro.modules.scheduler.domain.case_type import conclusion_model
from tradingagentspro.modules.scheduler.domain.queue import PlanStage


class CaseStage(StrEnum):
    """案件所处的阶段，含义见 case-stages spec「案件的阶段」。"""

    CONCLUDING = "concluding"  # 唤醒 PM：PM 研究并提交结论，校验不通过时修改再提交
    CRITIC_REVIEW = "critic_review"  # Critic 审核方案，包括它要求的补充研究
    PM_RESPONSE = "pm_response"  # PM 逐条回应质询
    RISK_CHECK = "risk_check"  # 等风控检查的结果
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 等人工确认
    AWAITING_ORDER = "awaiting_order"  # 已经确认，等开盘和下单前再校验
    RETROSPECTIVE = "retrospective"  # 复盘：Reviewer 处理
    ENDED = "ended"


class CriticOutcome(StrEnum):
    """方案离开 Critic 审核时的结果，调用方据此在确认消息里附上说明。"""

    APPROVED = "approved"  # 通过
    DISPUTED = "disputed"  # 有分歧：开仓类方案质询满轮数，或者退出类方案有异议
    UNAVAILABLE = "unavailable"  # Critic 不可用，退出类方案跳过了审核


# spec「案件的阶段」的表：还在调用模型的阶段，以及每个阶段里方案所处的阶段
_CALLING_MODELS: Final = frozenset(
    {
        CaseStage.CONCLUDING,
        CaseStage.CRITIC_REVIEW,
        CaseStage.PM_RESPONSE,
        CaseStage.RETROSPECTIVE,
    }
)
_PLAN_STAGES: Final = {
    CaseStage.CRITIC_REVIEW: PlanStage.REVIEW,
    CaseStage.PM_RESPONSE: PlanStage.REVIEW,
    CaseStage.RISK_CHECK: PlanStage.REVIEW,
    CaseStage.AWAITING_CONFIRMATION: PlanStage.AWAITING_CONFIRMATION,
    CaseStage.AWAITING_ORDER: PlanStage.AWAITING_ORDER,
}
# spec「已结束的案件和不适用的事件」的表：方案被作废、紧急退出中止和系统故障适用的阶段
_CONFIRMING: Final = (CaseStage.AWAITING_CONFIRMATION, CaseStage.AWAITING_ORDER)
_RUNNING: Final = tuple(s for s in CaseStage if s is not CaseStage.ENDED)
_ABORTABLE: Final = tuple(s for s in _RUNNING if s is not CaseStage.RETROSPECTIVE)


@dataclass(frozen=True, slots=True)
class CaseState:
    """一个案件的状态。事件方法都返回新的状态；已结束的案件原样返回。

    运行中的案件收到不适用于当前阶段的事件时抛 ValueError，见 spec「已结束的案件和不适用的事件」。
    """

    stage: CaseStage
    action: TradeAction | None = None  # 方案的动作；没有方案时为 None
    concluded: bool = False  # 已经有通过校验的结论
    resubmissions: int = 0  # 结论校验不通过、退回 PM 重交的次数
    rounds: int = 0  # 开仓类方案被质询、交给 PM 回应的次数
    critic: CriticOutcome | None = None  # 方案离开 Critic 审核时的结果
    end_reason: CaseEndReason | None = None  # 结束以后才有

    @property
    def calling_models(self) -> bool:
        """还在调用模型的阶段，给队列的 RunningCase.calling_models。"""
        return self.stage in _CALLING_MODELS

    @property
    def plan_stage(self) -> PlanStage | None:
        """方案所处的阶段，给队列的 PendingPlan.stage；没有方案或已结束时为 None。"""
        return _PLAN_STAGES.get(self.stage)

    @property
    def releases_reservation(self) -> bool:
        """已结束、有方案、没有提交订单：调用方要释放方案的预留。"""
        return (
            self.stage is CaseStage.ENDED
            and self.action is not None
            and self.end_reason is not CaseEndReason.ORDER_SUBMITTED
        )

    # 唤醒 PM

    def reject_conclusion(self, config: SchedulerConfig) -> Self:
        """结论校验不通过：重交次数没用完就退回 PM，用完以后结论无效。"""
        if self._ignores(CaseStage.CONCLUDING):
            return self
        if self.resubmissions >= config.max_resubmissions:
            return self._end(CaseEndReason.INVALID_CONCLUSION)
        return replace(self, resubmissions=self.resubmissions + 1)

    def accept_conclusion(self, action: TradeAction | None) -> Self:
        """结论通过校验；action 是结论里交易方案的动作，没有方案时为 None。"""
        if self._ignores(CaseStage.CONCLUDING):
            return self
        concluded = replace(self, concluded=True)
        if action is None:
            return concluded._end(CaseEndReason.NO_PLAN)
        return replace(concluded, stage=CaseStage.CRITIC_REVIEW, action=action)

    # Critic 审核和 PM 回应

    def review(self, verdict: Verdict, config: SchedulerConfig) -> Self:
        """Critic 给出结论。退出类方案只审一次、不能否决；开仓类方案按质询轮次转换。"""
        if self._ignores(CaseStage.CRITIC_REVIEW):
            return self
        assert self.action is not None  # 有方案才会进入 Critic 审核
        if verdict is Verdict.APPROVE:
            return self._to_risk(CriticOutcome.APPROVED)
        if not self.action.is_opening:
            return self._to_risk(CriticOutcome.DISPUTED)  # 否决也按异议处理
        if verdict is Verdict.VETO:
            return self._end(CaseEndReason.VETOED)
        if self.rounds >= config.max_challenge_rounds:
            return self._to_risk(CriticOutcome.DISPUTED)
        return replace(self, stage=CaseStage.PM_RESPONSE, rounds=self.rounds + 1)

    def critic_unavailable(self) -> Self:
        """Critic 的主模型和备用模型全部不可用：退出类跳过审核，开仓类按系统故障结束。"""
        if self._ignores(CaseStage.CRITIC_REVIEW):
            return self
        assert self.action is not None
        if self.action.is_opening:
            return self._end(CaseEndReason.FAILED)
        return self._to_risk(CriticOutcome.UNAVAILABLE)

    def respond(self, decision: ResponseDecision) -> Self:
        """PM 回应质询：维持或修改回到 Critic 审核，撤回时结束。"""
        if self._ignores(CaseStage.PM_RESPONSE):
            return self
        if decision is ResponseDecision.WITHDRAW:
            return self._end(CaseEndReason.WITHDRAWN)
        return replace(self, stage=CaseStage.CRITIC_REVIEW)

    # 风控、人工确认和等下单

    def risk_checked(self, outcome: RiskOutcome, *, confirming: bool) -> Self:
        """风控的结果：拒绝时结束；通过和调整时，要人工确认的进入等人工确认，不要确认的（模型退出
        自动执行）直接进入等下单。拒绝时不看 confirming。
        """
        if self._ignores(CaseStage.RISK_CHECK):
            return self
        if outcome is RiskOutcome.REJECTED:
            return self._end(CaseEndReason.RISK_REJECTED)
        stage = CaseStage.AWAITING_CONFIRMATION if confirming else CaseStage.AWAITING_ORDER
        return replace(self, stage=stage)

    def confirm(self) -> Self:
        """人工确认：进入等下单。"""
        if self._ignores(CaseStage.AWAITING_CONFIRMATION):
            return self
        return replace(self, stage=CaseStage.AWAITING_ORDER)

    def decline(self) -> Self:
        """人工拒绝。"""
        return self._end_in(CaseEndReason.HUMAN_REJECTED, CaseStage.AWAITING_CONFIRMATION)

    def confirmation_timed_out(self) -> Self:
        """确认超时；确认请求发不出去也按确认超时处理。"""
        return self._end_in(CaseEndReason.CONFIRMATION_TIMED_OUT, CaseStage.AWAITING_CONFIRMATION)

    def order_submitted(self) -> Self:
        """再校验通过或调整，执行模块已经建立订单。"""
        return self._end_in(CaseEndReason.ORDER_SUBMITTED, CaseStage.AWAITING_ORDER)

    def recheck_voided(self) -> Self:
        """下单前再校验的结论是拒绝。"""
        return self._end_in(CaseEndReason.RECHECK_VOIDED, CaseStage.AWAITING_ORDER)

    # 作废、中止、系统故障和复盘

    def void(self) -> Self:
        """队列作废了方案：这只股票有新案件排队，只作废已经交给人工确认的开仓类方案。"""
        return self._end_in(CaseEndReason.PLAN_VOIDED, *_CONFIRMING)

    def abort(self) -> Self:
        """这只股票触发紧急风险退出，中止复盘以外的案件；调用方不再对它的方案调用 void。"""
        return self._end_in(CaseEndReason.EMERGENCY_ABORTED, *_ABORTABLE)

    def fail(self) -> Self:
        """系统故障：调用失败，风控检查或下单流程出错。不改变有没有结论。"""
        return self._end_in(CaseEndReason.FAILED, *_RUNNING)

    def finish_retrospective(self) -> Self:
        """Reviewer 完成复盘。"""
        return self._end_in(CaseEndReason.RETROSPECTIVE_DONE, CaseStage.RETROSPECTIVE)

    def _ignores(self, *stages: CaseStage) -> bool:
        """已结束的案件忽略任何事件；运行中的案件收到不适用的事件是调用方的错误。"""
        if self.stage is CaseStage.ENDED:
            return True
        if self.stage not in stages:
            raise ValueError(f"{self.stage} 阶段不接受这个事件")
        return False

    def _to_risk(self, critic: CriticOutcome) -> Self:
        return replace(self, stage=CaseStage.RISK_CHECK, critic=critic)

    def _end(self, reason: CaseEndReason) -> Self:
        return replace(self, stage=CaseStage.ENDED, end_reason=reason)

    def _end_in(self, reason: CaseEndReason, *stages: CaseStage) -> Self:
        """只适用于 stages 阶段、收到就结束的事件。"""
        if self._ignores(*stages):
            return self
        return self._end(reason)


def new_case(case_type: CaseType) -> CaseState:
    """开始一个案件：复盘案件不唤醒 PM，从复盘开始；其他从唤醒 PM 开始。"""
    if conclusion_model(case_type) is None:
        return CaseState(CaseStage.RETROSPECTIVE)
    return CaseState(CaseStage.CONCLUDING)
