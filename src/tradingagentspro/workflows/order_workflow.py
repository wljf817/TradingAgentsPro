"""下单流程的 DBOS 工作流：一个方案一个，从请求确认到订单交给盘中巡检；以及 /plan 的提交接口、
案件的方案的交接和确认按钮的回调。

见 order-flow spec 和 add-order-flow 的 design D1、D2、D3、D9。工作流函数只按 step 的结果分支，
时间、I/O 和日志都在 step 里（step 调用 order_flow.py 的 OrderFlow）。工作流和 step 在 import 时
按名字登记到 DBOS；它们用到的部件在 DBOS launch 之前经 install 交进来。

案件的方案经 CasePlanHandoff 交进来（order-flow spec「案件的方案」，update-order-flow-case-plans 的
design D1）：首次检查是案件工作流里的一个 step，下单流程是案件工作流的子工作流，带着来源
（CaseSource）。下单流程在每个结局用 plan_handoff.report_plan 把结果送回案件，意料之外的错误让它
停下以前送回出错（order-flow spec「结果送回案件」，design D3）。/plan 的方案来源为 None，不送回。

排队运行用 void_plan 作废案件的方案（order-flow spec「作废案件的方案」，add-queue-runner 的
design D6）：作废和按钮的结果走同一个主题 DECISION_TOPIC。等确认时收到的按 Stage.VOIDED 处理；确认
以后等开盘、等重试用 install 交进来的 wait，期间收到作废就结束。都送回案件 PlanEvent.VOIDED。
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
    Stage.VOIDED: PlanEvent.VOIDED,
}


class NotAwaitingConfirmationError(RuntimeError):
    """按钮的结果没有送达：这个方案的下单流程没有在等确认。"""


def wait_decision(seconds: float) -> str | None:
    """满足 Wait：在工作流函数里等 DECISION_TOPIC 的消息，最多等 seconds 秒。

    DBOS 的 recv 带持久化的超时：重启以后只等剩下的时间，和 DBOS.sleep 一样（add-queue-runner 的
    design D6）。确认以后这个主题只会收到作废。
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


def void_plan(plan_id: UUID) -> None:
    """排队运行要求作废这个案件的方案：送给它的下单流程，见 order-flow spec「作废案件的方案」。

    下单流程在等的时候收下；已经结束的没人读，什么也不改。
    """
    DBOS.send(str(plan_id), Stage.VOIDED.value, topic=DECISION_TOPIC)


def deliver_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：这个方案的下单流程在等确认时把结果送给它，否则抛错。"""
    workflow_id = str(plan_id)
    if DBOS.get_event(workflow_id, CONFIRMATION, timeout_seconds=0) != Stage.WAITING:
        raise NotAwaitingConfirmationError(f"方案 {plan_id} 没有在等确认")
    DBOS.send(workflow_id, outcome.value, topic=DECISION_TOPIC)


@DBOS.workflow(name="order_plan")
def order_plan(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    """一个方案的下单流程，见 order-flow spec。只按 step 的结果分支，不读时间、不写日志。

    source 是案件的方案的来源，/plan 的为 None。案件的方案意料之外的错误让流程停下以前，把出错
    送回案件，再原样抛出（design D3）。
    """
    try:
        return _run(plan, decision, source)
    except Exception:
        _report(source, PlanEvent.FAILED)
        raise


def _run(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    """要确认的请求确认、等按钮或作废；不要确认的（模型退出自动执行）直接执行。每个结局送回案件。"""
    if source is not None and not source.confirmation.required:
        return _executed(plan, decision, source, None)
    DBOS.set_event(CONFIRMATION, Stage.WAITING)
    requested = _request_confirmation(plan, decision, source)
    if requested is None:
        stage = Stage.UNSENT
    else:
        choice = DBOS.recv(DECISION_TOPIC, timeout_seconds=requested.wait_seconds)
        stage = Stage.TIMED_OUT if choice is None else Stage(choice)
    DBOS.set_event(CONFIRMATION, stage)
    _settle(plan, decision, stage, requested, source)
    _report(source, _EVENTS[stage])
    if stage is not Stage.CONFIRMED:
        return PlanEnd(stage.value)
    return _executed(plan, decision, source, requested)


def _executed(
    plan: TradePlan,
    decision: RiskDecision,
    source: CaseSource | None,
    requested: Requested | None,
) -> PlanEnd:
    """等开盘、再校验、提交，结局送回案件：订单交给巡检是订单已提交，作废是再校验作废，等的时候被
    排队运行作废是方案被作废。requested 是请求确认那一步的结果，不要确认的方案为 None。
    """
    end, event = _execute(plan, decision, source, requested)
    _report(source, event)
    return end


def _report(source: CaseSource | None, event: PlanEvent) -> None:
    """案件的方案把结果送回案件；/plan 的不送。在工作流函数里调用，重放时不重送。"""
    if source is not None:
        report_plan(source.case_id, event)


def _execute(
    plan: TradePlan,
    decision: RiskDecision,
    source: CaseSource | None,
    requested: Requested | None,
) -> tuple[PlanEnd, PlanEvent]:
    """确认以后：等开盘、再校验、提交，直到订单交给巡检或者方案作废；给出怎样结束和送回案件的结果。"""
    failure: str | None = None  # 最近一次没能下单的原因
    alerted = False  # 这个方案出意外的错时已经告警过
    while True:
        timing = _timing(plan, decision)
        if timing.expired:
            return _recheck_end(_void_expired(plan, decision, failure))
        if timing.wait_seconds > 0:
            if _voided_while_waiting(timing.wait_seconds):
                return _queued_end(_void_queued(plan, decision, requested))
            continue
        attempt = _attempt(plan, decision, source, not alerted)
        if attempt.kind is AttemptKind.SUBMITTED:
            return PlanEnd.SUBMITTED, PlanEvent.SUBMITTED
        if attempt.kind is AttemptKind.VOIDED:
            return PlanEnd.VOIDED, PlanEvent.RECHECK_VOIDED
        failure = attempt.reason
        alerted = alerted or attempt.alerted
        if attempt.kind is AttemptKind.RETRY:
            if _voided_while_waiting(_parts().flow.config.retry_delay.total_seconds()):
                return _queued_end(_void_queued(plan, decision, requested))


def _voided_while_waiting(seconds: float) -> bool:
    """等开盘或者等重试；期间收到作废时为 True。确认以后这个主题只会收到作废，收到别的是错误。"""
    message = _parts().wait(seconds)
    if message is None:
        return False
    if Stage(message) is not Stage.VOIDED:
        raise ValueError(f"确认以后收到了 {message}")
    return True


def _recheck_end(end: PlanEnd) -> tuple[PlanEnd, PlanEvent]:
    """到有效期的结局：已经有订单的是订单已提交，否则是再校验作废。"""
    return end, PlanEvent.SUBMITTED if end is PlanEnd.SUBMITTED else PlanEvent.RECHECK_VOIDED


def _queued_end(end: PlanEnd) -> tuple[PlanEnd, PlanEvent]:
    """被排队运行作废的结局：已经有订单的是订单已提交，否则是方案被作废。"""
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
def _void_queued(plan: TradePlan, decision: RiskDecision, requested: Requested | None) -> PlanEnd:
    return _parts().flow.void_queued(plan, decision, requested)
