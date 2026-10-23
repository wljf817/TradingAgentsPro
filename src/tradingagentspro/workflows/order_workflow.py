"""下单流程的 DBOS 工作流：一个方案一个，从请求确认到订单交给盘中巡检；以及 /plan 的提交接口、
案件的方案的交接和确认按钮的回调。

见 order-flow spec 和 add-order-flow 的 design D1、D2、D3、D9。工作流函数只按 step 的结果分支，
时间、I/O 和日志都在 step 里（step 调用 order_flow.py 的 OrderFlow）。工作流和 step 在 import 时
按名字登记到 DBOS；它们用到的部件在 DBOS launch 之前经 install 交进来。

案件的方案经 CasePlanHandoff 交进来（order-flow spec「案件的方案」，update-order-flow-case-plans 的
design D1）：首次检查是案件工作流里的一个 step，下单流程是案件工作流的子工作流，带着来源
（CaseSource）。下单流程在每个结局用 plan_handoff.report_plan 把结果送回案件，意料之外的错误让它
停下以前送回出错（order-flow spec「结果送回案件」，design D3）。/plan 的方案来源为 None，不送回。

作废案件的方案用 void_plan，带着原因（VoidCause，order-flow spec「作废案件的方案」「案件中止时作废
它的方案」，add-queue-runner 的 design D6，update-emergency-abort-case 的 design D5）：排队运行要求
作废，或者案件被紧急退出中止、经 CasePlanHandoff.abandon 把方案交回。作废和按钮的结果走同一个主题
DECISION_TOPIC：按钮的结果是 Stage 的值，作废是 VoidCause 的值。等确认时收到的确认的进展改为
Stage.VOIDED；确认以后等开盘、等重试用 install 交进来的 wait，期间收到作废就结束。新案件排队的送回
案件 PlanEvent.VOIDED；案件交回的之后什么都不送回（_Reports），包括出错。
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.notification import ConfirmationOutcome
from tradingagentspro.modules.programlog import Action, Actor
from tradingagentspro.modules.scheduler import CriticOutcome
from tradingagentspro.workflows.entries import plan_text, release
from tradingagentspro.workflows.order_flow import (
    Attempt,
    AttemptKind,
    CaseCheck,
    CaseSource,
    OrderFlow,
    PlanEnd,
    Requested,
    Stage,
    Timing,
    TradeServices,
    VoidCause,
    check_plan,
)
from tradingagentspro.workflows.plan_handoff import Handoff, PlanEvent, report_plan

# 工作流的事件：确认的进展（Stage），按钮回调靠它判断方案是不是在等确认，见 design D3
CONFIRMATION = "confirmation"
# 按钮的结果经这个主题送给工作流
DECISION_TOPIC = "decision"

# 等这么多秒，期间收到作废就提前交回它，等满了交回 None
type Wait = Callable[[float], str | None]

# 确认的结果送回案件的哪个结果，见 order-flow spec「结果送回案件」
_EVENTS = {
    Stage.CONFIRMED: PlanEvent.CONFIRMED,
    Stage.REJECTED: PlanEvent.DECLINED,
    Stage.TIMED_OUT: PlanEvent.TIMED_OUT,
    Stage.UNSENT: PlanEvent.TIMED_OUT,
}


class NotAwaitingConfirmationError(RuntimeError):
    """按钮的结果没有送达：这个方案的下单流程没有在等确认。"""


def wait_decision(seconds: float) -> str | None:
    """满足 Wait：在工作流函数里等 DECISION_TOPIC 的消息，最多等 seconds 秒。

    DBOS 的 recv 带持久化的超时：重启以后只等剩下的时间，和 DBOS.sleep 一样（add-queue-runner 的
    design D6）。确认以后这个主题只会收到作废（VoidCause 的值）。
    """
    message: str | None = DBOS.recv(DECISION_TOPIC, timeout_seconds=seconds)
    return message


@dataclass(frozen=True, slots=True)
class _Installed:
    flow: OrderFlow
    wait: Wait


_installed: _Installed | None = None


def install(flow: OrderFlow, wait: Wait = wait_decision) -> None:
    """DBOS launch 之前调用：之后的下单流程（包括恢复的）都用这些部件。

    wait 是等开盘、等重试用的持久化等待：生产用 wait_decision，测试换成拨钟的替身。
    """
    global _installed
    _installed = _Installed(flow, wait)


def _parts() -> _Installed:
    if _installed is None:
        raise RuntimeError("下单流程还没有 install")
    return _installed


class OrderPlanIntake:
    """满足 PlanIntake，见 order-flow spec「查持仓的板块」「提交方案」和 design D1。"""

    def __init__(self, services: TradeServices) -> None:
        self._services = services

    def held_sector(self, symbol: str) -> str | None:
        """这只股票持仓的板块，取执行模块的账户数据；不拿锁。"""
        positions = self._services.execution.account().positions
        return next((p.sector for p in positions if p.symbol == symbol), None)

    def submit(self, plan: TradePlan) -> RiskDecision:
        """写程序日志的录入方案，检查并预留；通过或调整时以方案 ID 启动下单流程，启动失败时
        释放预留再抛出。见 program-log spec「人的动作」。
        """
        services = self._services
        services.log.write(Actor.HUMAN, Action.ENTER_PLAN, str(plan.plan_id), plan_text(plan))
        decision = check_plan(services, plan)
        if decision.outcome is RiskOutcome.REJECTED:
            return decision
        try:
            with SetWorkflowID(str(plan.plan_id)):
                DBOS.start_workflow(order_plan, plan, decision, None)
        except Exception:
            with services.lock:
                release(services.risk, services.log, plan.plan_id, "启动下单流程失败")
            raise
        return decision


class CasePlanHandoff:
    """满足 plan_handoff.PlanHandoff，见 order-flow spec「案件的方案」和 design D1。

    submit 只能在案件工作流的工作流函数里调用，不在 step 里：首次检查是一个 step，下单流程以方案 ID
    为 ID 作为案件工作流的子工作流启动，DBOS 不允许在 step 里启动工作流。案件工作流重放时 DBOS
    给出记下的检查结果和子工作流，不再检查、不再启动。用的部件是 install 交进来的 OrderFlow。
    """

    def submit(self, case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> Handoff | None:
        checked = _check_case_plan(case_id, plan, critic)
        if checked is None:
            return None
        if checked.source is None:  # 风控拒绝
            return Handoff(checked.decision, confirming=False)
        with SetWorkflowID(str(plan.plan_id)):
            DBOS.start_workflow(order_plan, plan, checked.decision, checked.source)
        return Handoff(checked.decision, confirming=checked.source.confirmation.required)

    def abandon(self, plan_id: UUID) -> None:
        """案件被紧急退出中止，把方案交回：这个方案的下单流程按中止作废，见 order-flow spec「案件
        中止时作废它的方案」。在案件工作流的工作流函数里调用，送出记成案件的一步，重放时不重送。
        """
        void_plan(plan_id, VoidCause.ABORTED)


def void_plan(plan_id: UUID, cause: VoidCause) -> None:
    """作废这个案件的方案：按原因送给它的下单流程，见 order-flow spec「作废案件的方案」「案件中止时
    作废它的方案」。

    下单流程在等的时候收下；已经结束的没人读，什么也不改。
    """
    DBOS.send(str(plan_id), cause.value, topic=DECISION_TOPIC)


def deliver_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：这个方案的下单流程在等确认时把结果送给它，否则抛错。"""
    workflow_id = str(plan_id)
    if DBOS.get_event(workflow_id, CONFIRMATION, timeout_seconds=0) != Stage.WAITING:
        raise NotAwaitingConfirmationError(f"方案 {plan_id} 没有在等确认")
    DBOS.send(workflow_id, outcome.value, topic=DECISION_TOPIC)


@dataclass(slots=True)
class _Reports:
    """这个下单流程把结果送回哪个案件，见 order-flow spec「结果送回案件」。

    /plan 的方案（source 为 None）不送。案件被中止、把方案交回以后（handed_back）什么都不送，包括
    出错：案件已经结束。handed_back 由收到的作废决定，重放时同样重建。
    """

    source: CaseSource | None
    handed_back: bool = False

    def voided(self, cause: VoidCause) -> None:
        """收到作废；案件交回的，之后不再送回。"""
        self.handed_back = cause is VoidCause.ABORTED

    def send(self, event: PlanEvent) -> None:
        """在工作流函数里调用，重放时不重送。"""
        if self.source is not None and not self.handed_back:
            report_plan(self.source.case_id, event)


@DBOS.workflow(name="order_plan")
def order_plan(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    """一个方案的下单流程，见 order-flow spec。只按 step 的结果分支，不读时间、不写日志。

    source 是案件的方案的来源，/plan 的为 None。案件的方案意料之外的错误让流程停下以前，把出错
    送回案件，再原样抛出（design D3）；案件已经把方案交回的不送。
    """
    reports = _Reports(source)
    try:
        return _run(plan, decision, source, reports)
    except Exception:
        reports.send(PlanEvent.FAILED)
        raise


def _run(
    plan: TradePlan, decision: RiskDecision, source: CaseSource | None, reports: _Reports
) -> PlanEnd:
    """要确认的请求确认、等按钮或作废；不要确认的（模型退出自动执行）直接执行。每个结局送回案件。"""
    if source is not None and not source.confirmation.required:
        return _executed(plan, decision, source, None, reports)
    DBOS.set_event(CONFIRMATION, Stage.WAITING)
    requested = _request_confirmation(plan, decision, source)
    if requested is None:
        stage = Stage.UNSENT
    else:
        choice = DBOS.recv(DECISION_TOPIC, timeout_seconds=requested.wait_seconds)
        if choice is not None and choice in VoidCause:
            return _voided_while_confirming(plan, requested, VoidCause(choice), reports)
        stage = Stage.TIMED_OUT if choice is None else Stage(choice)
    DBOS.set_event(CONFIRMATION, stage)
    _settle(plan, decision, stage, requested, source)
    reports.send(_EVENTS[stage])
    if stage is not Stage.CONFIRMED:
        return PlanEnd(stage.value)
    return _executed(plan, decision, source, requested, reports)


def _voided_while_confirming(
    plan: TradePlan, requested: Requested, cause: VoidCause, reports: _Reports
) -> PlanEnd:
    """等确认时收到作废：确认的进展改为已作废，报告确认消息已作废、释放，送回方案被作废。"""
    DBOS.set_event(CONFIRMATION, Stage.VOIDED)
    reports.voided(cause)
    _void_waiting(plan, requested, cause)
    reports.send(PlanEvent.VOIDED)
    return PlanEnd.VOIDED


def _executed(
    plan: TradePlan,
    decision: RiskDecision,
    source: CaseSource | None,
    requested: Requested | None,
    reports: _Reports,
) -> PlanEnd:
    """等开盘、再校验、提交，结局送回案件：订单交给巡检是订单已提交，作废是再校验作废，等的时候被
    作废是方案被作废。requested 是请求确认那一步的结果，不要确认的方案为 None。
    """
    end, event = _execute(plan, decision, source, requested, reports)
    reports.send(event)
    return end


def _execute(
    plan: TradePlan,
    decision: RiskDecision,
    source: CaseSource | None,
    requested: Requested | None,
    reports: _Reports,
) -> tuple[PlanEnd, PlanEvent]:
    """确认以后：等开盘、再校验、提交，直到订单交给巡检或者方案作废；给出怎样结束和送回案件的结果。"""
    failure: str | None = None  # 最近一次没能下单的原因
    alerted = False  # 这个方案出意外的错时已经告警过
    while True:
        timing = _timing(plan, decision)
        if timing.expired:
            return _recheck_end(_void_expired(plan, decision, failure))
        if timing.wait_seconds > 0:
            cause = _voided_while_waiting(timing.wait_seconds)
            if cause is not None:
                return _void_end(plan, decision, requested, cause, reports)
            continue
        attempt = _attempt(plan, decision, source, not alerted)
        if attempt.kind is AttemptKind.SUBMITTED:
            return PlanEnd.SUBMITTED, PlanEvent.SUBMITTED
        if attempt.kind is AttemptKind.VOIDED:
            return PlanEnd.VOIDED, PlanEvent.RECHECK_VOIDED
        failure = attempt.reason
        alerted = alerted or attempt.alerted
        if attempt.kind is AttemptKind.RETRY:
            cause = _voided_while_waiting(_parts().flow.config.retry_delay.total_seconds())
            if cause is not None:
                return _void_end(plan, decision, requested, cause, reports)


def _voided_while_waiting(seconds: float) -> VoidCause | None:
    """等开盘或者等重试；期间收到作废时交回它的原因。确认以后这个主题只会收到作废，收到别的值时
    VoidCause 抛 ValueError。
    """
    message = _parts().wait(seconds)
    return None if message is None else VoidCause(message)


def _recheck_end(end: PlanEnd) -> tuple[PlanEnd, PlanEvent]:
    """到有效期的结局：已经有订单的是订单已提交，否则是再校验作废。"""
    return end, PlanEvent.SUBMITTED if end is PlanEnd.SUBMITTED else PlanEvent.RECHECK_VOIDED


def _void_end(
    plan: TradePlan,
    decision: RiskDecision,
    requested: Requested | None,
    cause: VoidCause,
    reports: _Reports,
) -> tuple[PlanEnd, PlanEvent]:
    """等的时候被作废：已经有订单的是订单已提交，否则是方案被作废；案件交回的之后不送回。"""
    reports.voided(cause)
    end = _void_queued(plan, decision, requested, cause)
    return end, PlanEvent.SUBMITTED if end is PlanEnd.SUBMITTED else PlanEvent.VOIDED


# 每个 step 只调用 OrderFlow 的同名方法，见 design D2 的表


@DBOS.step(name="order_plan.check_case_plan")
def _check_case_plan(case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> CaseCheck | None:
    return _parts().flow.check_case_plan(case_id, plan, critic)


@DBOS.step(name="order_plan.request_confirmation")
def _request_confirmation(
    plan: TradePlan, decision: RiskDecision, source: CaseSource | None
) -> Requested | None:
    return _parts().flow.request_confirmation(plan, decision, source)


@DBOS.step(name="order_plan.settle")
def _settle(
    plan: TradePlan,
    decision: RiskDecision,
    stage: Stage,
    requested: Requested | None,
    source: CaseSource | None,
) -> None:
    _parts().flow.settle(plan, decision, stage, requested, source)


@DBOS.step(name="order_plan.void_waiting")
def _void_waiting(plan: TradePlan, requested: Requested, cause: VoidCause) -> None:
    _parts().flow.void_waiting(plan, requested, cause)


@DBOS.step(name="order_plan.timing")
def _timing(plan: TradePlan, decision: RiskDecision) -> Timing:
    return _parts().flow.timing(plan, decision)


@DBOS.step(name="order_plan.attempt")
def _attempt(
    plan: TradePlan, decision: RiskDecision, source: CaseSource | None, alert_error: bool
) -> Attempt:
    return _parts().flow.attempt(plan, decision, source, alert_error=alert_error)


@DBOS.step(name="order_plan.void_expired")
def _void_expired(plan: TradePlan, decision: RiskDecision, failure: str | None) -> PlanEnd:
    return _parts().flow.void_expired(plan, decision, failure)


@DBOS.step(name="order_plan.void_queued")
def _void_queued(
    plan: TradePlan, decision: RiskDecision, requested: Requested | None, cause: VoidCause
) -> PlanEnd:
    return _parts().flow.void_queued(plan, decision, requested, cause)
