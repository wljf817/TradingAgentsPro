"""案件的 DBOS 工作流：一个案件一个，以案件 ID 为工作流 ID，从开始运行到结束；以及给「排队运行」的
开始运行一个案件、读一个案件的进展。

见 case-workflow spec 和 add-case-workflow 的 design D1 到 D4、D8、D9。工作流函数只推进调度器的案件
状态（CaseState）、按 step 的结果分支、发布案件的进展；时间、I/O、日志和告警都在 step 里（step 调用
case_flow.py 的 CaseFlow）。案件状态只在这里：每次变化以后用 DBOS 的事件发布进展，别人只读。

- 「排队运行」：start_case 开始运行一个案件，running_cases 列出运行中的案件和它们的进展，组出队列
  快照里运行中的案件和还没提交订单的方案；case_progress 读一个案件的进展；部件在 DBOS launch 之前
  经 install 交进来
- 「方案接下单」：实现 plan_handoff.PlanHandoff；方案进入风控时案件在工作流函数里调用它，之后等下单
  流程用 plan_handoff.report_plan 送回结果
- 排队运行作废方案：交给下单流程，下单流程经 PLAN_TOPIC 送回方案被作废，案件在 _deliver 的等待里
  收到（add-queue-runner 的 design D6、D7）；作废的方案只在等人工确认、等下单，这两个阶段只在这里等

子 agent 的任务每个开一个子工作流 case_task 并行运行（design D8）。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tradingagentspro.contracts.agent_call import (
    CaseContext,
    Challenge,
    CriticReply,
    CriticTurn,
    DispatchTasks,
    PmReply,
    PmTurn,
    Returned,
    SubmitConclusion,
    SubmitReview,
    TaskOutcome,
    TaskResults,
)
from tradingagentspro.contracts.case import CaseEndReason, CaseType
from tradingagentspro.contracts.conclusion import Conclusion, HoldingConclusion, WatchConclusion
from tradingagentspro.contracts.model import ModelUse
from tradingagentspro.contracts.review import CriticReview, PlanResponse
from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.task import Dispatcher, TaskRequest
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.gateway import Answered, Dispatch, DispatchAction
from tradingagentspro.modules.scheduler import (
    CaseDefinition,
    CaseStage,
    CaseState,
    Trigger,
    new_case,
)
from tradingagentspro.workflows.case_entries import (
    RESEARCH_RETURNED,
    START_RESEARCH,
    TASKS_RETURNED,
    research_reason,
    respond_why,
    resubmit_why,
    review_why,
)
from tradingagentspro.workflows.case_flow import Accepted, CaseFlow, Concluded
from tradingagentspro.workflows.plan_handoff import PLAN_TOPIC, PlanEvent, PlanHandoff

PROGRESS = "progress"  # 工作流的事件：案件的进展

# 等下单那一侧送回结果时每次等这么久，等不到就接着等，不设时限（design D4）
_WAIT_SECONDS: Final = 24 * 60 * 60

# spec「交给下单」的第二张表：送回的结果和案件状态的转换
_PLAN_EVENTS: Final[dict[PlanEvent, Callable[[CaseState], CaseState]]] = {
    PlanEvent.CONFIRMED: CaseState.confirm,
    PlanEvent.DECLINED: CaseState.decline,
    PlanEvent.TIMED_OUT: CaseState.confirmation_timed_out,
    PlanEvent.SUBMITTED: CaseState.order_submitted,
    PlanEvent.RECHECK_VOIDED: CaseState.recheck_voided,
    PlanEvent.VOIDED: CaseState.void,
    PlanEvent.FAILED: CaseState.fail,
}


@dataclass(frozen=True, slots=True)
class CaseProgress:
    """一个运行中案件的进展，见 case-workflow spec「案件的进展」。

    队列用它组出 RunningCase（state.calling_models）和 PendingPlan（plan 和 state.plan_stage）。
    """

    case_type: CaseType
    symbol: str | None
    state: CaseState
    plan: TradePlan | None  # 现在的方案，PM 修改过的条款已经代入


@dataclass(frozen=True, slots=True)
class _Installed:
    flow: CaseFlow
    handoff: PlanHandoff


_installed: _Installed | None = None


def install(flow: CaseFlow, handoff: PlanHandoff) -> None:
    """DBOS launch 之前调用：之后的案件（包括恢复的）都用这些部件。"""
    global _installed
    _installed = _Installed(flow, handoff)


def _parts() -> _Installed:
    if _installed is None:
        raise RuntimeError("案件工作流还没有 install")
    return _installed


def start_case(
    case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
) -> None:
    """以案件 ID 为工作流 ID 开始运行；这个 ID 已经开始过时什么都不做。"""
    with SetWorkflowID(str(case_id)):
        DBOS.start_workflow(run_case, case_id, definition, trigger, triggered_at)


def running_cases() -> tuple[tuple[UUID, CaseProgress], ...]:
    """运行中的案件和它们的进展：PENDING 的 case 工作流，见 add-queue-runner 的 design D5。

    还没发布进展的（开始运行以后、工作流的线程跑到第一次发布以前）按输入里开始时确定的案件类型给出
    刚开始的进展：唤醒 PM 或者复盘，都在调用模型。进展是已结束的不算。
    """
    running: list[tuple[UUID, CaseProgress]] = []
    for workflow in DBOS.list_workflows(name="case", status="PENDING", load_output=False):
        progress: CaseProgress | None = DBOS.get_event(
            workflow.workflow_id, PROGRESS, timeout_seconds=0
        )
        if progress is None:
            assert workflow.input is not None  # 列出时读了输入
            definition: CaseDefinition = workflow.input["args"][1]
            state = new_case(definition.case_type)
            progress = CaseProgress(definition.case_type, definition.symbol, state, None)
        if progress.state.stage is not CaseStage.ENDED:
            running.append((UUID(workflow.workflow_id), progress))
    return tuple(running)


def case_progress(case_id: UUID) -> CaseProgress | None:
    """运行中案件的进展；没有这个案件、已经结束、出错停下时为 None。"""
    workflow = DBOS.get_workflow_status(str(case_id))
    if workflow is None or workflow.status != "PENDING":
        return None
    progress: CaseProgress | None = DBOS.get_event(str(case_id), PROGRESS, timeout_seconds=0)
    if progress is None or progress.state.stage is CaseStage.ENDED:
        return None
    return progress


class _Case:
    """工作流内存里的一个案件：重放时由 step 的输出一步步重建成同样的内容，不另存。"""

    def __init__(self, case_id: UUID, definition: CaseDefinition) -> None:
        self.id = case_id
        self.definition = definition
        self.state = new_case(definition.case_type)
        self.plan: TradePlan | None = None  # 现在的方案
        self.version = 1  # 最新一版快照
        self.context: CaseContext | None = None
        self.conclusion: HoldingConclusion | WatchConclusion | None = None  # 有方案的结论
        self.pm_turns: list[PmTurn] = []
        self.critic_turns: list[CriticTurn] = []
        # PM 上一次的回复：质询交给 PM 回应时，PM 的对话加一轮"上一次的回复和这次质询"
        self.last_pm_reply: SubmitConclusion | PlanResponse | None = None

    def publish(self) -> None:
        progress = CaseProgress(
            self.definition.case_type, self.definition.symbol, self.state, self.plan
        )
        DBOS.set_event(PROGRESS, progress)

    def advance(self, new: CaseState) -> None:
        """换上新的案件状态：阶段变了时写案件阶段变化；发布进展。"""
        if new.stage is not self.state.stage:
            _stage(self.id, self.state, new, self.plan)
        self.state = new
        self.publish()

    @property
    def ctx(self) -> CaseContext:
        assert self.context is not None  # 由 PM 处理的案件开始时组出
        return self.context


@DBOS.workflow(name="case")
def run_case(
    case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
) -> CaseEndReason:
    """一个案件，见 case-workflow spec。只按 step 的结果分支，不读时间、不写日志。"""
    case = _Case(case_id, definition)
    _start(case_id, definition, trigger, triggered_at)
    case.publish()
    version = _snapshot(case_id, definition, 1, None)
    if version is None:
        case.advance(case.state.fail())
    elif case.state.stage is CaseStage.RETROSPECTIVE:
        _retrospective(case)
    else:
        case.context = _context(definition, trigger)
        _research(case)
        _review(case)
        _deliver(case)
    reason = case.state.end_reason
    assert reason is not None  # 走完以后案件一定结束了
    _end(case_id, reason)
    return reason


def _research(case: _Case) -> None:
    """唤醒 PM：研究、派发任务、提交结论和检查，直到结论通过或者案件结束。"""
    config = _parts().flow.scheduler_config
    why = START_RESEARCH
    while case.state.stage is CaseStage.CONCLUDING:
        called = _call_pm(case.id, case.version, case.ctx, tuple(case.pm_turns), why)
        if called is None:
            case.advance(case.state.fail())
            return
        reply = called.output
        if isinstance(reply, DispatchTasks):
            outcomes = _run_tasks(case, Dispatcher.PM, reply.requests)
            case.pm_turns.append(PmTurn(reply=reply, feedback=TaskResults(outcomes=outcomes)))
            why = TASKS_RETURNED
            continue
        concluded = _conclude(
            case.id, case.definition, case.version, reply.conclusion, called.model_use
        )
        if isinstance(concluded, Returned):
            case.advance(case.state.reject_conclusion(config))
            case.pm_turns.append(PmTurn(reply=reply, feedback=concluded))
            why = resubmit_why(case.state.resubmissions, concluded.reason)
            continue
        assert isinstance(concluded, Accepted)
        case.plan = concluded.plan
        if concluded.plan is not None:
            assert isinstance(reply.conclusion, HoldingConclusion | WatchConclusion)
            case.conclusion = reply.conclusion
            case.last_pm_reply = reply
        case.advance(case.state.accept_conclusion(None if case.plan is None else case.plan.action))


def _review(case: _Case) -> None:
    """Critic 审核和 PM 回应，直到方案离开这两个阶段。"""
    config = _parts().flow.scheduler_config
    reviews = 0
    researches = 0
    why: str | None = None  # 下一次唤醒 Critic 的原因；None 是新的一次审核
    research: tuple[TaskOutcome, ...] = ()  # 这一次审核里补充研究的 Task 和 Result
    challenged: SubmitReview | None = None  # 交给 PM 回应的那次质询
    while case.state.stage in (CaseStage.CRITIC_REVIEW, CaseStage.PM_RESPONSE):
        if case.state.stage is CaseStage.PM_RESPONSE:
            assert challenged is not None
            _respond(case, challenged)
            continue
        if why is None:
            reviews += 1
            why = review_why(reviews)
        assert case.conclusion is not None
        called = _call_critic(
            case.id, case.version, case.ctx, case.conclusion, tuple(case.critic_turns), why
        )
        if called is None:
            _critic_unavailable(case.id)
            case.advance(case.state.critic_unavailable())
            continue
        reply = called.output
        if isinstance(reply, DispatchTasks):
            researches += 1
            version = _snapshot(
                case.id, case.definition, case.version + 1, research_reason(researches)
            )
            if version is None:
                case.advance(case.state.fail())
                continue
            case.version = version
            outcomes = _run_tasks(case, Dispatcher.CRITIC, reply.requests)
            case.critic_turns.append(
                CriticTurn(reply=reply, feedback=TaskResults(outcomes=outcomes))
            )
            research += outcomes
            why = RESEARCH_RETURNED
            continue
        _record_review(case.id, reply.review, called.model_use)
        reviewed = case.state.review(reply.review.verdict, config)
        case.advance(reviewed)
        if reviewed.stage is CaseStage.PM_RESPONSE:
            assert case.last_pm_reply is not None
            challenge = Challenge(review=reply.review, research=research)
            case.pm_turns.append(PmTurn(reply=case.last_pm_reply, feedback=challenge))
            challenged = reply
        research = ()
        why = None


def _respond(case: _Case, challenged: SubmitReview) -> None:
    """PM 回应一轮质询：维持或修改回到 Critic 审核，撤回时结束。"""
    called = _call_pm_respond(
        case.id, case.version, case.ctx, tuple(case.pm_turns), respond_why(case.state.rounds)
    )
    if called is None:
        case.advance(case.state.fail())
        return
    response = called.output
    _record_response(case.id, response, called.model_use)
    if response.plan is not None:
        assert case.plan is not None
        # 修改只改条款：方案 ID、股票、板块和动作不变，条款的字段和 PlanDraft 同名
        case.plan = TradePlan.model_validate(case.plan.model_dump() | response.plan.model_dump())
    case.advance(case.state.respond(response.decision))
    if case.state.stage is CaseStage.CRITIC_REVIEW:
        case.critic_turns.append(CriticTurn(reply=challenged, feedback=response))
        case.last_pm_reply = response


def _deliver(case: _Case) -> None:
    """交给下单：在工作流函数里调用交接（它会启动下单流程），再等送回的结果直到案件结束。"""
    if case.state.stage is not CaseStage.RISK_CHECK:
        return
    assert case.plan is not None and case.state.critic is not None
    handoff = _parts().handoff.submit(case.id, case.plan, case.state.critic)
    if handoff is None:
        case.advance(case.state.fail())
        return
    _record_risk(case.id, handoff.decision)
    outcome = handoff.decision.outcome
    case.advance(case.state.risk_checked(outcome, confirming=handoff.confirming))
    while case.state.stage in (CaseStage.AWAITING_CONFIRMATION, CaseStage.AWAITING_ORDER):
        message = DBOS.recv(PLAN_TOPIC, timeout_seconds=_WAIT_SECONDS)
        if message is not None:
            case.advance(_PLAN_EVENTS[PlanEvent(message)](case.state))


def _retrospective(case: _Case) -> None:
    """复盘案件：唤醒 Reviewer。"""
    done = _call_reviewer(case.id, case.version)
    case.advance(case.state.finish_retrospective() if done else case.state.fail())


def _run_tasks(
    case: _Case, dispatcher: Dispatcher, requests: Sequence[TaskRequest]
) -> tuple[TaskOutcome, ...]:
    """派发一次：派发出去的任务各开一个子工作流同时运行，都完成以后按请求的顺序读 Task 和
    Result。
    """
    dispatches = _dispatch(case.id, case.definition, dispatcher, tuple(requests))
    handles = [
        DBOS.start_workflow(_task, d.task.task_id, case.id, case.version)
        for d in dispatches
        if d.action is DispatchAction.DISPATCHED
    ]
    for handle in handles:
        handle.get_result()
    return _outcomes(tuple(d.task.task_id for d in dispatches))


@DBOS.workflow(name="case_task")
def _task(task_id: UUID, case_id: UUID, version: int) -> None:
    """一个派发出去的任务，读第 version 版快照。"""
    _run_task(task_id, case_id, version)


# 每个 step 只调用 CaseFlow 的同名方法，见 design D1 的表


@DBOS.step(name="case.start")
def _start(
    case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
) -> None:
    _parts().flow.start(case_id, definition, trigger, triggered_at)


@DBOS.step(name="case.snapshot")
def _snapshot(
    case_id: UUID, definition: CaseDefinition, version: int, reason: str | None
) -> int | None:
    return _parts().flow.snapshot(case_id, definition, version, reason)


@DBOS.step(name="case.context")
def _context(definition: CaseDefinition, trigger: Trigger) -> CaseContext:
    return _parts().flow.context(definition, trigger)


@DBOS.step(name="case.call_pm")
def _call_pm(
    case_id: UUID, version: int, context: CaseContext, turns: tuple[PmTurn, ...], why: str
) -> Answered[PmReply] | None:
    return _parts().flow.call_pm(case_id, version, context, turns, why)


@DBOS.step(name="case.call_pm_respond")
def _call_pm_respond(
    case_id: UUID, version: int, context: CaseContext, turns: tuple[PmTurn, ...], why: str
) -> Answered[PlanResponse] | None:
    return _parts().flow.call_pm_respond(case_id, version, context, turns, why)


@DBOS.step(name="case.call_critic")
def _call_critic(
    case_id: UUID,
    version: int,
    context: CaseContext,
    conclusion: HoldingConclusion | WatchConclusion,
    turns: tuple[CriticTurn, ...],
    why: str,
) -> Answered[CriticReply] | None:
    return _parts().flow.call_critic(case_id, version, context, conclusion, turns, why)


@DBOS.step(name="case.call_reviewer")
def _call_reviewer(case_id: UUID, version: int) -> bool:
    return _parts().flow.call_reviewer(case_id, version)


@DBOS.step(name="case.dispatch")
def _dispatch(
    case_id: UUID,
    definition: CaseDefinition,
    dispatcher: Dispatcher,
    requests: tuple[TaskRequest, ...],
) -> tuple[Dispatch, ...]:
    return _parts().flow.dispatch(case_id, definition, dispatcher, requests)


@DBOS.step(name="case_task.run")
def _run_task(task_id: UUID, case_id: UUID, version: int) -> None:
    _parts().flow.run_task(task_id, case_id, version)


@DBOS.step(name="case.outcomes")
def _outcomes(task_ids: tuple[UUID, ...]) -> tuple[TaskOutcome, ...]:
    return _parts().flow.outcomes(task_ids)


@DBOS.step(name="case.conclude")
def _conclude(
    case_id: UUID,
    definition: CaseDefinition,
    version: int,
    conclusion: Conclusion,
    model_use: ModelUse,
) -> Concluded:
    return _parts().flow.conclude(case_id, definition, version, conclusion, model_use)


@DBOS.step(name="case.record_review")
def _record_review(case_id: UUID, review: CriticReview, model_use: ModelUse) -> None:
    _parts().flow.record_review(case_id, review, model_use)


@DBOS.step(name="case.record_response")
def _record_response(case_id: UUID, response: PlanResponse, model_use: ModelUse) -> None:
    _parts().flow.record_response(case_id, response, model_use)


@DBOS.step(name="case.critic_unavailable")
def _critic_unavailable(case_id: UUID) -> None:
    _parts().flow.critic_unavailable(case_id)


@DBOS.step(name="case.record_risk")
def _record_risk(case_id: UUID, decision: RiskDecision) -> None:
    _parts().flow.record_risk(case_id, decision)


@DBOS.step(name="case.stage")
def _stage(case_id: UUID, before: CaseState, after: CaseState, plan: TradePlan | None) -> None:
    _parts().flow.stage(case_id, before, after, plan)


@DBOS.step(name="case.end")
def _end(case_id: UUID, reason: CaseEndReason) -> None:
    _parts().flow.end(case_id, reason)
