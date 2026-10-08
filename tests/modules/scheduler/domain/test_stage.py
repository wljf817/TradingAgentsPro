"""案件状态的测试。

场景见 openspec 的 case-stages spec，每个场景一个测试（「Critic 审核和 PM 回应的格式」在
tests/contracts/test_review.py，「案件阶段的配置」的前两个场景在 test_config.py）。基准数据见
builders：开仓案件是 BBB（关注）的关注股事件，结论是买入；退出案件是 CCC（持仓）的持仓事件，
结论是减仓。场景里的"依次收到"对应 receive，状态推到某一步对应 after。
"""

from dataclasses import replace

import pytest

from tests.modules.scheduler.builders import (
    PLAN,
    CaseEvent,
    accepted,
    after,
    critic,
    exit_at,
    exit_case,
    opening_at,
    opening_case,
    pm,
    receive,
    rejected,
    risk,
)
from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import PlanDraft
from tradingagentspro.contracts.review import PlanResponse, ResponseDecision, Verdict
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
ENDED, RETROSPECTIVE = CaseStage.ENDED, CaseStage.RETROSPECTIVE
BUY, ADD, REDUCE, CLOSE = TradeAction.BUY, TradeAction.ADD, TradeAction.REDUCE, TradeAction.CLOSE
APPROVE, CHALLENGE, VETO = Verdict.APPROVE, Verdict.CHALLENGE, Verdict.VETO
MAINTAIN, MODIFY, WITHDRAW = (
    ResponseDecision.MAINTAIN,
    ResponseDecision.MODIFY,
    ResponseDecision.WITHDRAW,
)
APPROVED, DISPUTED, UNAVAILABLE = (
    CriticOutcome.APPROVED,
    CriticOutcome.DISPUTED,
    CriticOutcome.UNAVAILABLE,
)
PASSED, ADJUSTED, REJECTED = RiskOutcome.PASSED, RiskOutcome.ADJUSTED, RiskOutcome.REJECTED
ONE_ROUND = SchedulerConfig(max_challenge_rounds=1)
# 质询满两轮：PM 第 2 次回应以后回到 Critic 审核
TWO_ROUNDS = (critic(CHALLENGE), pm(MAINTAIN), critic(CHALLENGE), pm(MODIFY))
# 质询满两轮以后仍有分歧，进入风控
DISPUTED_OPENING = (*TWO_ROUNDS, critic(CHALLENGE))


def _ended(state: CaseState) -> tuple[CaseStage, CaseEndReason | None, bool]:
    """阶段、结束原因和有没有结论。"""
    return state.stage, state.end_reason, state.concluded


def _critic_outcome(state: CaseState) -> tuple[CaseStage, CriticOutcome | None, int]:
    """阶段、Critic 审核的结果和质询轮次。"""
    return state.stage, state.critic, state.rounds


# 案件的状态


def test_new_case_starts_by_waking_the_pm() -> None:
    """spec「新案件从唤醒 PM 开始」：开仓案件和寻股。"""
    fresh = CaseState(
        stage=CONCLUDING,
        action=None,
        concluded=False,
        resubmissions=0,
        rounds=0,
        critic=None,
        end_reason=None,
    )

    assert opening_case() == fresh
    assert new_case(CaseType.EXPLORATION) == fresh


def test_retrospective_starts_in_retrospective() -> None:
    """spec「复盘案件从复盘开始」：CCC 的卖出复盘和每周复盘。"""
    for case_type in (CaseType.SELL_RETROSPECTIVE, CaseType.WEEKLY_RETROSPECTIVE):
        state = new_case(case_type)

        assert (state.stage, state.action, state.concluded) == (RETROSPECTIVE, None, False)


# 案件的阶段


def test_concluding_and_retrospective_call_models_without_a_plan() -> None:
    """spec「唤醒 PM 和复盘调用模型、没有方案」。"""
    for state in (opening_case(), new_case(CaseType.SELL_RETROSPECTIVE)):
        assert state.calling_models
        assert state.plan_stage is None


def test_opening_case_goes_through_every_stage() -> None:
    """spec「开仓案件走完各阶段」。"""
    states = receive(
        opening_case(),
        accepted(BUY),
        critic(APPROVE),
        risk(PASSED),
        CaseState.confirm,
        CaseState.order_submitted,
    )

    assert [s.stage for s in states] == [
        CRITIC_REVIEW,
        RISK_CHECK,
        AWAITING_CONFIRMATION,
        AWAITING_ORDER,
        ENDED,
    ]
    assert [s.calling_models for s in states] == [True, False, False, False, False]
    assert [s.plan_stage for s in states] == [
        PlanStage.REVIEW,
        PlanStage.REVIEW,
        PlanStage.AWAITING_CONFIRMATION,
        PlanStage.AWAITING_ORDER,
        None,
    ]


def test_pm_response_calls_models() -> None:
    """spec「PM 回应也在调用模型」。"""
    state = critic(CHALLENGE)(opening_at(CRITIC_REVIEW))

    assert (state.stage, state.calling_models, state.plan_stage) == (
        PM_RESPONSE,
        True,
        PlanStage.REVIEW,
    )


# 唤醒 PM 和结论


def test_rejected_conclusion_goes_back_to_the_pm() -> None:
    """spec「校验不通过时退回 PM 重交」：两次研究不足，第三次通过（买入）。"""
    states = receive(opening_case(), rejected(), rejected(), accepted(BUY))

    assert [(s.stage, s.resubmissions, s.concluded) for s in states] == [
        (CONCLUDING, 1, False),
        (CONCLUDING, 2, False),
        (CRITIC_REVIEW, 2, True),
    ]


def test_resubmissions_run_out() -> None:
    """spec「重交次数用完」：已经重交 2 次，又不通过。"""
    state = after(opening_case(), rejected(), rejected())

    assert _ended(rejected()(state)) == (ENDED, CaseEndReason.INVALID_CONCLUSION, False)


def test_no_resubmission_allowed() -> None:
    """spec「不允许重交」：结论最多重交次数配置为 0。"""
    state = rejected(SchedulerConfig(max_resubmissions=0))(opening_case())

    assert _ended(state) == (ENDED, CaseEndReason.INVALID_CONCLUSION, False)


def test_conclusion_without_a_plan_ends_the_case() -> None:
    """spec「没有交易方案的结论直接结束」：继续关注、升级、加入 DDD。"""
    for case_type in (CaseType.WATCH_EVENT, CaseType.CANDIDATE_EVALUATION, CaseType.EXPLORATION):
        state = accepted(None)(new_case(case_type))

        assert _ended(state) == (ENDED, CaseEndReason.NO_PLAN, True)
        assert (state.action, state.critic) == (None, None)  # 没有经过 Critic


def test_conclusion_with_a_plan_goes_to_the_critic() -> None:
    """spec「有交易方案的结论进入 Critic 审核」：开仓案件买入，退出案件减仓。"""
    opening = accepted(BUY)(opening_case())
    exiting = accepted(REDUCE)(exit_case())

    assert (opening.stage, opening.concluded, opening.action) == (CRITIC_REVIEW, True, BUY)
    assert (exiting.stage, exiting.concluded, exiting.action) == (CRITIC_REVIEW, True, REDUCE)


# Critic 审核开仓类方案


def test_opening_plan_approved_at_the_first_review() -> None:
    """spec「第一次审核就通过」。"""
    state = critic(APPROVE)(opening_at(CRITIC_REVIEW))

    assert _critic_outcome(state) == (RISK_CHECK, APPROVED, 0)


def test_challenge_goes_to_the_pm() -> None:
    """spec「质询以后交给 PM 回应」。"""
    state = critic(CHALLENGE)(opening_at(CRITIC_REVIEW))

    assert _critic_outcome(state) == (PM_RESPONSE, None, 1)


def test_still_disputed_after_two_rounds() -> None:
    """spec「两轮以后仍有分歧」。"""
    states = receive(opening_at(CRITIC_REVIEW), *TWO_ROUNDS, critic(CHALLENGE))

    assert [_critic_outcome(s) for s in states] == [
        (PM_RESPONSE, None, 1),
        (CRITIC_REVIEW, None, 1),
        (PM_RESPONSE, None, 2),
        (CRITIC_REVIEW, None, 2),
        (RISK_CHECK, DISPUTED, 2),
    ]


def test_last_review_approves_or_vetoes() -> None:
    """spec「最后一次审核通过或否决」。"""
    last_review = after(opening_at(CRITIC_REVIEW), *TWO_ROUNDS)

    assert _critic_outcome(critic(APPROVE)(last_review)) == (RISK_CHECK, APPROVED, 2)
    assert _ended(critic(VETO)(last_review)) == (ENDED, CaseEndReason.VETOED, True)


def test_opening_plan_vetoed() -> None:
    """spec「否决开仓方案」。"""
    state = critic(VETO)(opening_at(CRITIC_REVIEW))

    assert _ended(state) == (ENDED, CaseEndReason.VETOED, True)


def test_adding_is_an_opening_plan() -> None:
    """spec「加仓也是开仓类方案」：CCC 的持仓事件结论通过（加仓）。"""
    in_review = accepted(ADD)(new_case(CaseType.HOLDING_EVENT))

    assert _critic_outcome(critic(CHALLENGE)(in_review)) == (PM_RESPONSE, None, 1)
    assert _ended(critic(VETO)(in_review)) == (ENDED, CaseEndReason.VETOED, True)


def test_supplementary_research_is_not_a_round() -> None:
    """spec「补充研究不算一轮」：补充研究不是事件，Critic 在这次审核里经 Agent 网关派发，
    结果回来以前案件的状态不变。
    """
    researching = opening_at(CRITIC_REVIEW)

    assert (researching.stage, researching.rounds, researching.calling_models) == (
        CRITIC_REVIEW,
        0,
        True,
    )
    assert _critic_outcome(critic(CHALLENGE)(researching)) == (PM_RESPONSE, None, 1)


def test_at_most_one_round() -> None:
    """spec「质询最多一轮」：质询最多轮数配置为 1。"""
    states = receive(
        opening_at(CRITIC_REVIEW),
        critic(CHALLENGE, ONE_ROUND),
        pm(MAINTAIN),
        critic(CHALLENGE, ONE_ROUND),
    )

    assert [_critic_outcome(s) for s in states] == [
        (PM_RESPONSE, None, 1),
        (CRITIC_REVIEW, None, 1),
        (RISK_CHECK, DISPUTED, 1),
    ]


# PM 回应


def test_pm_maintains() -> None:
    """spec「PM 维持」。"""
    state = pm(MAINTAIN)(opening_at(PM_RESPONSE))

    assert (state.stage, state.rounds, state.action) == (CRITIC_REVIEW, 1, BUY)


def test_pm_modifies() -> None:
    """spec「PM 修改」：股数从 100 改为 60。修改后的方案代替原来的方案由调用方完成（design D11），
    状态只记下回到 Critic 审核，方案的动作不变。
    """
    response = PlanResponse(
        decision=MODIFY,
        replies=("股数减为 60",),
        plan=PlanDraft.model_validate(PLAN.model_dump() | {"quantity": 60}),
    )

    state = opening_at(PM_RESPONSE).respond(response.decision)

    assert (state.stage, state.rounds, state.action) == (CRITIC_REVIEW, 1, BUY)
    assert response.plan is not None and response.plan.quantity == 60


def test_pm_withdraws() -> None:
    """spec「PM 撤回」。"""
    state = pm(WITHDRAW)(opening_at(PM_RESPONSE))

    assert _ended(state) == (ENDED, CaseEndReason.WITHDRAWN, True)


# Critic 审核退出类方案


def test_exit_plan_approved() -> None:
    """spec「退出方案通过」。"""
    state = critic(APPROVE)(exit_at(CRITIC_REVIEW))

    assert _critic_outcome(state) == (RISK_CHECK, APPROVED, 0)


def test_exit_plan_disputed() -> None:
    """spec「退出方案有异议」：没有经过 PM 回应，质询轮次 0。"""
    state = critic(CHALLENGE)(exit_at(CRITIC_REVIEW))

    assert _critic_outcome(state) == (RISK_CHECK, DISPUTED, 0)


def test_veto_of_an_exit_plan_counts_as_a_dispute() -> None:
    """spec「Critic 否决退出方案按异议处理」：退出案件减仓；CCC 的持仓复查清仓，去向移出。"""
    holding_review = accepted(CLOSE)(new_case(CaseType.HOLDING_REVIEW))

    for in_review in (exit_at(CRITIC_REVIEW), holding_review):
        state = critic(VETO)(in_review)

        assert _critic_outcome(state) == (RISK_CHECK, DISPUTED, 0)
        assert state.end_reason is None


# Critic 不可用


def test_exit_plan_skips_an_unavailable_critic() -> None:
    """spec「退出方案跳过 Critic」。"""
    state = exit_at(CRITIC_REVIEW).critic_unavailable()

    assert _critic_outcome(state) == (RISK_CHECK, UNAVAILABLE, 0)


def test_opening_case_fails_when_the_critic_is_unavailable() -> None:
    """spec「开仓方案的案件按系统故障结束」：第一次审核时，以及质询满两轮以后。"""
    for in_review in (opening_at(CRITIC_REVIEW), after(opening_at(CRITIC_REVIEW), *TWO_ROUNDS)):
        state = in_review.critic_unavailable()

        assert _ended(state) == (ENDED, CaseEndReason.FAILED, True)
        assert state.critic is None  # 不算 Critic 的判断


# 风控


def test_risk_passes() -> None:
    """spec「风控通过」。"""
    state = risk(PASSED)(opening_at(RISK_CHECK))

    assert (state.stage, state.critic) == (AWAITING_CONFIRMATION, APPROVED)


def test_risk_adjusts() -> None:
    """spec「风控调整」：股数从 100 调为 40；确认消息里标明已被风控调整由通知模块按风控结果完成。"""
    state = risk(ADJUSTED)(opening_at(RISK_CHECK))

    assert state.stage is AWAITING_CONFIRMATION


def test_risk_rejects() -> None:
    """spec「风控拒绝」：规则不通过和处于降级模式时，风控给出的都是拒绝。"""
    state = risk(REJECTED)(opening_at(RISK_CHECK))

    assert _ended(state) == (ENDED, CaseEndReason.RISK_REJECTED, True)


def test_disputed_plan_goes_through_risk() -> None:
    """spec「有分歧的方案照常过风控」。"""
    in_risk = after(opening_at(CRITIC_REVIEW), *DISPUTED_OPENING)

    state = risk(PASSED)(in_risk)

    assert (in_risk.stage, state.stage, state.critic) == (
        RISK_CHECK,
        AWAITING_CONFIRMATION,
        DISPUTED,
    )


# 人工确认


def test_confirmed() -> None:
    """spec「人工确认」：Critic 审核的结果是通过，确认消息不附双方意见。"""
    state = opening_at(AWAITING_CONFIRMATION).confirm()

    assert (state.stage, state.critic) == (AWAITING_ORDER, APPROVED)


def test_declined() -> None:
    """spec「人工拒绝」。"""
    state = opening_at(AWAITING_CONFIRMATION).decline()

    assert _ended(state) == (ENDED, CaseEndReason.HUMAN_REJECTED, True)


def test_confirmation_timed_out() -> None:
    """spec「确认超时」：已超时和确认请求发不出去，调用方都按确认超时交给状态。"""
    state = exit_at(AWAITING_CONFIRMATION).confirmation_timed_out()

    assert _ended(state) == (ENDED, CaseEndReason.CONFIRMATION_TIMED_OUT, True)


def test_disputed_plans_carry_both_opinions_to_the_human() -> None:
    """spec「有分歧的方案带着双方意见交给人」：Critic 审核的结果是有分歧，确认消息附上双方意见。"""
    opening = after(opening_at(CRITIC_REVIEW), *DISPUTED_OPENING, risk(PASSED))
    exiting = after(exit_at(CRITIC_REVIEW), critic(CHALLENGE), risk(PASSED))

    for awaiting in (opening, exiting):
        assert (awaiting.stage, awaiting.critic) == (AWAITING_CONFIRMATION, DISPUTED)
        assert awaiting.confirm().stage is AWAITING_ORDER


def test_exit_plan_without_the_critic_is_marked() -> None:
    """spec「Critic 不可用的退出方案注明」：Critic 审核的结果是 Critic 不可用，确认消息注明。"""
    state = after(exit_at(CRITIC_REVIEW), CaseState.critic_unavailable, risk(PASSED))

    assert (state.stage, state.critic) == (AWAITING_CONFIRMATION, UNAVAILABLE)


# 等下单


def test_order_submitted() -> None:
    """spec「订单已提交」：再校验通过，订单已提交。"""
    state = opening_at(AWAITING_ORDER).order_submitted()

    assert _ended(state) == (ENDED, CaseEndReason.ORDER_SUBMITTED, True)


def test_order_submitted_after_an_adjusted_recheck() -> None:
    """spec「再校验调整后照样提交」：再校验调整不是事件，按 40 股提交订单以后同样是订单已提交。"""
    states = receive(opening_at(AWAITING_ORDER), CaseState.order_submitted)

    assert [s.stage for s in states] == [ENDED]  # 没有重新进入等人工确认
    assert states[-1].end_reason is CaseEndReason.ORDER_SUBMITTED


def test_recheck_voids_the_plan() -> None:
    """spec「再校验作废」：价格偏离过大。"""
    state = opening_at(AWAITING_ORDER).recheck_voided()

    assert _ended(state) == (ENDED, CaseEndReason.RECHECK_VOIDED, True)


def test_waits_for_the_order_after_hours_and_while_disconnected() -> None:
    """spec「盘后确认、断线时留在等下单」：2026-10-14 21:00Z 盘后确认；开盘前、开盘以后券商断线时
    没有事件，案件留在等下单；恢复以后再校验因为超过有效期作废。
    """
    confirmed_after_hours = exit_at(AWAITING_ORDER)

    assert (
        confirmed_after_hours.stage,
        confirmed_after_hours.calling_models,
        confirmed_after_hours.plan_stage,
    ) == (AWAITING_ORDER, False, PlanStage.AWAITING_ORDER)
    assert confirmed_after_hours.recheck_voided().end_reason is CaseEndReason.RECHECK_VOIDED


# 方案被作废和紧急退出中止


def test_voided_while_awaiting_confirmation() -> None:
    """spec「等人工确认时被作废」：BBB 的新闻事件排队。"""
    state = opening_at(AWAITING_CONFIRMATION).void()

    assert _ended(state) == (ENDED, CaseEndReason.PLAN_VOIDED, True)


def test_voided_while_awaiting_the_order() -> None:
    """spec「等下单时被作废」。"""
    state = opening_at(AWAITING_ORDER).void()

    assert _ended(state) == (ENDED, CaseEndReason.PLAN_VOIDED, True)


def test_aborted_while_concluding() -> None:
    """spec「唤醒 PM 时被紧急退出中止」：CCC 的持仓复查。"""
    state = new_case(CaseType.HOLDING_REVIEW).abort()

    assert _ended(state) == (ENDED, CaseEndReason.EMERGENCY_ABORTED, False)


def test_aborted_after_the_conclusion() -> None:
    """spec「结论以后被紧急退出中止」：退出案件在等人工确认、在等下单；CCC 的持仓事件结论是加仓，
    在风控。
    """
    adding_in_risk = after(new_case(CaseType.HOLDING_EVENT), accepted(ADD), critic(APPROVE))

    for running in (exit_at(AWAITING_CONFIRMATION), exit_at(AWAITING_ORDER), adding_in_risk):
        assert _ended(running.abort()) == (ENDED, CaseEndReason.EMERGENCY_ABORTED, True)


# 系统故障


def test_pm_call_fails() -> None:
    """spec「PM 的调用失败」：CCC 的持仓复查；主备模型都超时和预算用完被拒绝，调用方都按系统故障
    交给状态。
    """
    state = new_case(CaseType.HOLDING_REVIEW).fail()

    assert _ended(state) == (ENDED, CaseEndReason.FAILED, False)


def test_pm_call_fails_while_responding() -> None:
    """spec「PM 回应时调用失败」。"""
    state = opening_at(PM_RESPONSE).fail()

    assert _ended(state) == (ENDED, CaseEndReason.FAILED, True)


def test_risk_check_or_order_flow_fails() -> None:
    """spec「风控和下单流程出错」：开仓案件在风控，退出案件在等下单。"""
    for running in (opening_at(RISK_CHECK), exit_at(AWAITING_ORDER)):
        assert _ended(running.fail()) == (ENDED, CaseEndReason.FAILED, True)


# 复盘案件


def test_retrospective_done() -> None:
    """spec「复盘完成」：CCC 的卖出复盘。"""
    state = new_case(CaseType.SELL_RETROSPECTIVE).finish_retrospective()

    assert _ended(state) == (ENDED, CaseEndReason.RETROSPECTIVE_DONE, False)


def test_retrospective_fails() -> None:
    """spec「复盘失败」：每周复盘，Reviewer 的调用失败。"""
    state = new_case(CaseType.WEEKLY_RETROSPECTIVE).fail()

    assert _ended(state) == (ENDED, CaseEndReason.FAILED, False)


# 结束原因


def test_ended_without_a_conclusion() -> None:
    """spec「没有结论就结束」：CCC 的持仓复查在唤醒 PM 时系统故障、紧急退出中止、重交用完。"""
    concluding = new_case(CaseType.HOLDING_REVIEW)
    run_out = after(concluding, rejected(), rejected(), rejected())

    for state in (concluding.fail(), concluding.abort(), run_out):
        assert state.stage is ENDED
        assert not state.concluded


def test_failure_after_the_conclusion_keeps_it() -> None:
    """spec「结论以后的系统故障不改变有结论」：CCC 的持仓复查结论通过（加仓，附论点更新）。"""
    state = after(new_case(CaseType.HOLDING_REVIEW), accepted(ADD), CaseState.critic_unavailable)

    assert _ended(state) == (ENDED, CaseEndReason.FAILED, True)


def test_reservation_released_when_the_case_ends() -> None:
    """spec「结束时释放预留」：没有预留时调用方的释放什么都不做，状态不区分有没有预留。"""
    timed_out = opening_at(AWAITING_CONFIRMATION).confirmation_timed_out()
    vetoed = critic(VETO)(opening_at(CRITIC_REVIEW))
    submitted = opening_at(AWAITING_ORDER).order_submitted()
    no_plan = accepted(None)(opening_case())
    failed_before_the_conclusion = opening_case().fail()
    still_in_review = opening_at(CRITIC_REVIEW)

    assert timed_out.releases_reservation
    assert vetoed.releases_reservation
    assert not submitted.releases_reservation
    assert not no_plan.releases_reservation
    assert not failed_before_the_conclusion.releases_reservation
    assert not still_in_review.releases_reservation


def _end_reason_rows() -> list[tuple[str, CaseState, CaseEndReason, bool]]:
    """spec「结束原因」的表：每种结束原因从每个能结束的阶段结束一次，以及有没有结论。"""
    after_conclusion = (
        CRITIC_REVIEW,
        PM_RESPONSE,
        RISK_CHECK,
        AWAITING_CONFIRMATION,
        AWAITING_ORDER,
    )
    weekly = new_case(CaseType.WEEKLY_RETROSPECTIVE)
    return [
        ("没有交易方案", accepted(None)(opening_case()), CaseEndReason.NO_PLAN, True),
        (
            "订单已提交",
            opening_at(AWAITING_ORDER).order_submitted(),
            CaseEndReason.ORDER_SUBMITTED,
            True,
        ),
        ("Critic 否决", critic(VETO)(opening_at(CRITIC_REVIEW)), CaseEndReason.VETOED, True),
        ("PM 撤回", pm(WITHDRAW)(opening_at(PM_RESPONSE)), CaseEndReason.WITHDRAWN, True),
        ("风控拒绝", risk(REJECTED)(opening_at(RISK_CHECK)), CaseEndReason.RISK_REJECTED, True),
        (
            "人工拒绝",
            opening_at(AWAITING_CONFIRMATION).decline(),
            CaseEndReason.HUMAN_REJECTED,
            True,
        ),
        (
            "确认超时",
            opening_at(AWAITING_CONFIRMATION).confirmation_timed_out(),
            CaseEndReason.CONFIRMATION_TIMED_OUT,
            True,
        ),
        (
            "再校验作废",
            opening_at(AWAITING_ORDER).recheck_voided(),
            CaseEndReason.RECHECK_VOIDED,
            True,
        ),
        *[
            (f"方案被作废（{stage}）", opening_at(stage).void(), CaseEndReason.PLAN_VOIDED, True)
            for stage in (AWAITING_CONFIRMATION, AWAITING_ORDER)
        ],
        ("紧急退出中止（唤醒 PM）", opening_case().abort(), CaseEndReason.EMERGENCY_ABORTED, False),
        *[
            (
                f"紧急退出中止（{stage}）",
                opening_at(stage).abort(),
                CaseEndReason.EMERGENCY_ABORTED,
                True,
            )
            for stage in after_conclusion
        ],
        (
            "结论无效",
            after(opening_case(), rejected(), rejected(), rejected()),
            CaseEndReason.INVALID_CONCLUSION,
            False,
        ),
        ("系统故障（唤醒 PM）", opening_case().fail(), CaseEndReason.FAILED, False),
        *[
            (f"系统故障（{stage}）", opening_at(stage).fail(), CaseEndReason.FAILED, True)
            for stage in after_conclusion
        ],
        ("系统故障（复盘）", weekly.fail(), CaseEndReason.FAILED, False),
        ("复盘完成", weekly.finish_retrospective(), CaseEndReason.RETROSPECTIVE_DONE, False),
    ]


def test_every_end_reason_and_whether_it_has_a_conclusion() -> None:
    """spec「结束原因」的表：13 种结束原因，每种从每个能结束的阶段结束时有没有结论。"""
    rows = _end_reason_rows()

    assert {reason for _, _, reason, _ in rows} == set(CaseEndReason)
    for name, state, reason, concluded in rows:
        assert _ended(state) == (ENDED, reason, concluded), name


# 已结束的案件和不适用的事件


def test_ended_case_does_not_change() -> None:
    """spec「已结束的案件不变」：开仓案件在等人工确认时被紧急退出中止，之后才到已确认、方案被作废、
    系统故障。
    """
    aborted = opening_at(AWAITING_CONFIRMATION).abort()

    for late in (CaseState.confirm, CaseState.void, CaseState.fail):
        assert late(aborted) == aborted
    assert aborted.end_reason is CaseEndReason.EMERGENCY_ABORTED


def _inapplicable_events() -> list[tuple[CaseState, CaseEvent]]:
    """spec「不适用的事件报错」：运行中的案件和它收到的不适用的事件。"""
    concluding = opening_case()
    awaiting_order = opening_at(AWAITING_ORDER)
    retrospective = new_case(CaseType.SELL_RETROSPECTIVE)
    return [
        (concluding, critic(APPROVE)),
        (concluding, pm(MAINTAIN)),
        (concluding, risk(PASSED)),
        (concluding, CaseState.confirm),
        (concluding, CaseState.order_submitted),
        (concluding, CaseState.void),
        (concluding, CaseState.finish_retrospective),
        (awaiting_order, accepted(BUY)),
        (awaiting_order, CaseState.critic_unavailable),
        (awaiting_order, CaseState.confirm),
        (opening_at(RISK_CHECK), CaseState.void),
        (retrospective, CaseState.abort),
        (retrospective, accepted(None)),
    ]


def test_inapplicable_event_is_an_error() -> None:
    """spec「不适用的事件报错」：都报错，状态不变。"""
    for state, inapplicable in _inapplicable_events():
        before = replace(state)

        with pytest.raises(ValueError, match="阶段不接受这个事件"):
            inapplicable(state)
        assert state == before


# 案件阶段的配置


def test_config_changes_the_transitions() -> None:
    """spec「改配置改变转换」：和默认配置比，不允许重交时第一次不通过就结论无效，质询最多一轮时
    第二次质询就有分歧。
    """
    once_challenged = after(opening_at(CRITIC_REVIEW), critic(CHALLENGE), pm(MAINTAIN))

    assert rejected()(opening_case()).stage is CONCLUDING
    assert rejected(SchedulerConfig(max_resubmissions=0))(opening_case()).end_reason is (
        CaseEndReason.INVALID_CONCLUSION
    )
    assert _critic_outcome(critic(CHALLENGE)(once_challenged)) == (PM_RESPONSE, None, 2)
    assert _critic_outcome(critic(CHALLENGE, ONE_ROUND)(once_challenged)) == (
        RISK_CHECK,
        DISPUTED,
        1,
    )
