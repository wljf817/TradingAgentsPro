"""案件状态的性质测试（add-case-stages 的 design D13）。

一个接一个地跑案件：每个案件开始时随机取案件类型、结论最多重交次数、质询最多轮数和外来事件的阶段。
每一步收到一个适用于当前阶段的事件（参数也随机）；每一步以后对当前状态试遍全部事件和参数，按
case-stages spec「已结束的案件和不适用的事件」的表检查已结束时不变、不适用时报错，再检查不变量。
参考检查只用 spec 的规则，不读状态的实现。
"""

from typing import Final

import pytest
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from tests.modules.scheduler.builders import CaseEvent, accepted, critic, pm, rejected, risk
from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.review import ResponseDecision, Verdict
from tradingagentspro.contracts.risk import RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.scheduler import (
    CaseStage,
    CaseState,
    CriticOutcome,
    PlanStage,
    SchedulerConfig,
    new_case,
)

CONCLUDING, CRITIC_REVIEW, PM_RESPONSE, RISK_CHECK = (
    CaseStage.CONCLUDING,
    CaseStage.CRITIC_REVIEW,
    CaseStage.PM_RESPONSE,
    CaseStage.RISK_CHECK,
)
AWAITING_CONFIRMATION, AWAITING_ORDER = CaseStage.AWAITING_CONFIRMATION, CaseStage.AWAITING_ORDER
RETROSPECTIVE, ENDED = CaseStage.RETROSPECTIVE, CaseStage.ENDED
RETROSPECTIVES: Final = frozenset({CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE})

# spec「案件的阶段」的表：调用模型的阶段；方案的阶段只按这个顺序往前走
CALLING_MODELS: Final = frozenset({CONCLUDING, CRITIC_REVIEW, PM_RESPONSE, RETROSPECTIVE})
PLAN_ORDER: Final = (PlanStage.REVIEW, PlanStage.AWAITING_CONFIRMATION, PlanStage.AWAITING_ORDER)

# spec「已结束的案件和不适用的事件」的表：每个事件适用的阶段
RUNNING: Final = frozenset(CaseStage) - {ENDED}
APPLICABLE: Final = {
    "reject_conclusion": frozenset({CONCLUDING}),
    "accept_conclusion": frozenset({CONCLUDING}),
    "review": frozenset({CRITIC_REVIEW}),
    "critic_unavailable": frozenset({CRITIC_REVIEW}),
    "respond": frozenset({PM_RESPONSE}),
    "risk_checked": frozenset({RISK_CHECK}),
    "confirm": frozenset({AWAITING_CONFIRMATION}),
    "decline": frozenset({AWAITING_CONFIRMATION}),
    "confirmation_timed_out": frozenset({AWAITING_CONFIRMATION}),
    "order_submitted": frozenset({AWAITING_ORDER}),
    "recheck_voided": frozenset({AWAITING_ORDER}),
    "void": frozenset({AWAITING_CONFIRMATION, AWAITING_ORDER}),
    "abort": RUNNING - {RETROSPECTIVE},
    "fail": RUNNING,
    "finish_retrospective": frozenset({RETROSPECTIVE}),
}
# 来自案件流程之外、几乎每个阶段都适用的事件：每个案件只在取到的一个阶段收到，
# 否则案件大多在前一两步就因为它们结束，很少走到人工确认以后
OUTSIDE: Final = frozenset({"critic_unavailable", "void", "abort", "fail"})

# spec「结束原因」的表：一定有结论的、一定没有结论的；紧急退出中止和系统故障两种都可能
CONCLUDED_REASONS: Final = frozenset(
    {
        CaseEndReason.NO_PLAN,
        CaseEndReason.ORDER_SUBMITTED,
        CaseEndReason.VETOED,
        CaseEndReason.WITHDRAWN,
        CaseEndReason.RISK_REJECTED,
        CaseEndReason.HUMAN_REJECTED,
        CaseEndReason.CONFIRMATION_TIMED_OUT,
        CaseEndReason.RECHECK_VOIDED,
        CaseEndReason.PLAN_VOIDED,
    }
)
UNCONCLUDED_REASONS: Final = frozenset(
    {CaseEndReason.INVALID_CONCLUSION, CaseEndReason.RETROSPECTIVE_DONE}
)


def _every_event(config: SchedulerConfig) -> dict[str, dict[str, CaseEvent]]:
    """全部事件：事件名 → 参数 → 事件；不带参数的事件只有一个参数 ""。"""
    return {
        "reject_conclusion": {"": rejected(config)},
        "accept_conclusion": {str(a): accepted(a) for a in (None, *TradeAction)},
        "review": {v: critic(v, config) for v in Verdict},
        "critic_unavailable": {"": CaseState.critic_unavailable},
        "respond": {d: pm(d) for d in ResponseDecision},
        "risk_checked": {o: risk(o) for o in RiskOutcome},
        "confirm": {"": CaseState.confirm},
        "decline": {"": CaseState.decline},
        "confirmation_timed_out": {"": CaseState.confirmation_timed_out},
        "order_submitted": {"": CaseState.order_submitted},
        "recheck_voided": {"": CaseState.recheck_voided},
        "void": {"": CaseState.void},
        "abort": {"": CaseState.abort},
        "fail": {"": CaseState.fail},
        "finish_retrospective": {"": CaseState.finish_retrospective},
    }


# 一个案件：案件类型、配置，以及外来事件的阶段
type _Case = tuple[CaseType, SchedulerConfig, CaseStage | None]
_cases: st.SearchStrategy[_Case] = st.tuples(
    st.sampled_from(CaseType),
    st.builds(
        SchedulerConfig,
        max_resubmissions=st.integers(0, 3),
        max_challenge_rounds=st.integers(1, 3),
    ),
    st.sampled_from(sorted(RUNNING)),
)


class CaseStageMachine(RuleBasedStateMachine):
    """一个接一个的案件。"""

    def __init__(self) -> None:
        super().__init__()
        self._start((CaseType.WATCH_EVENT, SchedulerConfig(), None))

    def _start(self, case: _Case) -> None:
        self.case_type, self.config, self.outside_stage = case
        self.events = _every_event(self.config)
        self.state = new_case(self.case_type)
        self.applied = 0  # 这个案件适用并执行了的事件数
        self.left_models = False  # 复盘以外的案件已经离开调用模型的阶段
        self.last_plan_stage: PlanStage | None = None
        expected = RETROSPECTIVE if self.case_type in RETROSPECTIVES else CONCLUDING
        assert self.state == CaseState(expected)

    def _receivable(self) -> list[str]:
        """这一步能收到的事件：适用于当前阶段；外来事件只在取到的阶段。"""
        stage = self.state.stage
        return [
            name
            for name in APPLICABLE
            if stage in APPLICABLE[name] and (name not in OUTSIDE or stage is self.outside_stage)
        ]

    @initialize(case=_cases)
    def start(self, case: _Case) -> None:
        self._start(case)

    @rule(data=st.data())
    def step(self, data: st.DataObject) -> None:
        """案件结束了就开始下一个；否则收到一个适用于当前阶段的事件，执行并计数。"""
        if self.state.stage is ENDED:
            self._start(data.draw(_cases, label="下一个案件"))
            return
        name = data.draw(st.sampled_from(self._receivable()), label="事件")
        argument = data.draw(st.sampled_from(sorted(self.events[name])), label="参数")
        before = self.state
        after = self.events[name][argument](before)
        self.state = after
        self.applied += 1
        assert after != before
        self._record(before, after)

    def _record(self, before: CaseState, after: CaseState) -> None:
        """检查和前一个状态有关的性质，并记下样例里出现过的情况。"""
        if before.concluded:
            assert after.concluded
        if self.case_type not in RETROSPECTIVES and not after.calling_models:
            self.left_models = True
        if after.plan_stage is not None:
            if self.last_plan_stage is not None:
                assert PLAN_ORDER.index(after.plan_stage) >= PLAN_ORDER.index(self.last_plan_stage)
            self.last_plan_stage = after.plan_stage
        elif self.last_plan_stage is not None:
            assert after.stage is ENDED
        if before.critic is None and after.critic is not None:
            event(f"Critic 审核的结果：{after.critic}")
            assert after.action is not None
            if after.critic is CriticOutcome.DISPUTED and after.action.is_opening:
                event("质询满轮数")
        if after.end_reason is not None:
            event(f"结束原因：{after.end_reason}")
            if after.end_reason is CaseEndReason.INVALID_CONCLUSION:
                assert before.resubmissions == self.config.max_resubmissions
                event("重交用完")

    # 每一步以后的检查

    @invariant()
    def follows_the_event_table(self) -> None:
        """试遍全部事件和参数：已结束时状态不变，运行中收到不适用的事件报错。"""
        state = self.state
        for name, arguments in self.events.items():
            for case_event in arguments.values():
                if state.stage is ENDED:
                    assert case_event(state) == state
                elif state.stage not in APPLICABLE[name]:
                    with pytest.raises(ValueError):
                        case_event(state)

    @invariant()
    def calling_models_follows_the_stage(self) -> None:
        """调用模型当且仅当阶段在表里；复盘以外的案件离开调用模型的阶段以后不再回去。"""
        assert self.state.calling_models is (self.state.stage in CALLING_MODELS)
        if self.left_models:
            assert not self.state.calling_models

    @invariant()
    def counters_stay_within_the_config(self) -> None:
        """重交次数和质询轮次不超过配置；退出类方案不被质询，从不进入 PM 回应。"""
        state = self.state
        assert 0 <= state.resubmissions <= self.config.max_resubmissions
        assert 0 <= state.rounds <= self.config.max_challenge_rounds
        if state.action is not None and not state.action.is_opening:
            assert state.rounds == 0
            assert state.stage is not PM_RESPONSE

    @invariant()
    def conclusion_matches_the_end_reason(self) -> None:
        """有方案就有结论；结束原因和有没有结论按 spec「结束原因」的表。"""
        state = self.state
        if state.action is not None:
            assert state.concluded
        if state.end_reason in CONCLUDED_REASONS:
            assert state.concluded
        if state.end_reason in UNCONCLUDED_REASONS:
            assert not state.concluded

    @invariant()
    def critic_outcomes_fit_the_action(self) -> None:
        """否决只结束开仓类方案；Critic 不可用只出现在退出类方案；开仓类有分歧时轮次等于上限。"""
        state = self.state
        if state.end_reason is CaseEndReason.VETOED:
            assert state.action is not None and state.action.is_opening
        if state.critic is not None:
            assert state.action is not None
            if state.critic is CriticOutcome.UNAVAILABLE:
                assert not state.action.is_opening
            if state.critic is CriticOutcome.DISPUTED and state.action.is_opening:
                assert state.rounds == self.config.max_challenge_rounds

    @invariant()
    def end_reason_and_reservation(self) -> None:
        """结束原因当且仅当已结束时才有；要释放预留当且仅当已结束、有方案、没有提交订单。"""
        state = self.state
        assert (state.end_reason is not None) is (state.stage is ENDED)
        assert state.releases_reservation is (
            state.stage is ENDED
            and state.action is not None
            and state.end_reason is not CaseEndReason.ORDER_SUBMITTED
        )

    @invariant()
    def case_ends_in_bounded_steps(self) -> None:
        """适用的事件数有上限，所以每个案件在有限步内结束。"""
        config = self.config
        assert self.applied <= config.max_resubmissions + 2 * config.max_challenge_rounds + 5


TestCaseStageMachine = CaseStageMachine.TestCase
