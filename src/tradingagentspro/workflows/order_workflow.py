"""下单流程的 DBOS 工作流：一个方案一个，从请求确认到订单交给盘中巡检；以及 /plan 的提交接口和
确认按钮的回调。

见 order-flow spec 和 add-order-flow 的 design D1、D2、D3、D9。工作流函数只按 step 的结果分支，
时间、I/O 和日志都在 step 里（step 调用 order_flow.py 的 OrderFlow）。工作流和 step 在 import 时
按名字登记到 DBOS；它们用到的部件在 DBOS launch 之前经 install 交进来。
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.notification import ConfirmationOutcome
from tradingagentspro.modules.programlog import Action, Actor
from tradingagentspro.workflows.entries import plan_text, release
from tradingagentspro.workflows.order_flow import (
    Attempt,
    AttemptKind,
    OrderFlow,
    PlanEnd,
    Requested,
    Stage,
    Timing,
    TradeServices,
    check_plan,
)

# 工作流的事件：确认的进展（Stage），按钮回调靠它判断方案是不是在等确认，见 design D3
CONFIRMATION = "confirmation"
# 按钮的结果经这个主题送给工作流
DECISION_TOPIC = "decision"

type Sleep = Callable[[float], None]


class NotAwaitingConfirmationError(RuntimeError):
    """按钮的结果没有送达：这个方案的下单流程没有在等确认。"""


@dataclass(frozen=True, slots=True)
class _Installed:
    flow: OrderFlow
    sleep: Sleep


_installed: _Installed | None = None


def install(flow: OrderFlow, sleep: Sleep = DBOS.sleep) -> None:
    """DBOS launch 之前调用：之后的下单流程（包括恢复的）都用这些部件。

    sleep 是持久化休眠，见 design D9：生产用 DBOS.sleep，测试换成拨钟的替身。
    """
    global _installed
    _installed = _Installed(flow, sleep)


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
                DBOS.start_workflow(order_plan, plan, decision)
        except Exception:
            with services.lock:
                release(services.risk, services.log, plan.plan_id, "启动下单流程失败")
            raise
        return decision


def deliver_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：这个方案的下单流程在等确认时把结果送给它，否则抛错。"""
    workflow_id = str(plan_id)
    if DBOS.get_event(workflow_id, CONFIRMATION, timeout_seconds=0) != Stage.WAITING:
        raise NotAwaitingConfirmationError(f"方案 {plan_id} 没有在等确认")
    DBOS.send(workflow_id, outcome.value, topic=DECISION_TOPIC)


@DBOS.workflow(name="order_plan")
def order_plan(plan: TradePlan, decision: RiskDecision) -> PlanEnd:
    """一个方案的下单流程，见 order-flow spec。只按 step 的结果分支，不读时间、不写日志。"""
    DBOS.set_event(CONFIRMATION, Stage.WAITING)
    requested = _request_confirmation(plan, decision)
    if requested is None:
        DBOS.set_event(CONFIRMATION, Stage.UNSENT)
        _settle(plan, decision, Stage.UNSENT, None)
        return PlanEnd.UNSENT
    choice = DBOS.recv(DECISION_TOPIC, timeout_seconds=requested.wait_seconds)
    stage = Stage.TIMED_OUT if choice is None else Stage(choice)
    DBOS.set_event(CONFIRMATION, stage)
    _settle(plan, decision, stage, requested)
    if stage is not Stage.CONFIRMED:
        return PlanEnd(stage.value)
    return _execute(plan, decision)


def _execute(plan: TradePlan, decision: RiskDecision) -> PlanEnd:
    """确认以后：等开盘、再校验、提交，直到订单交给巡检或者方案作废。"""
    failure: str | None = None  # 最近一次没能下单的原因
    alerted = False  # 这个方案出意外的错时已经告警过
    while True:
        timing = _timing(plan, decision)
        if timing.expired:
            return _void_expired(plan, decision, failure)
        if timing.wait_seconds > 0:
            _parts().sleep(timing.wait_seconds)
            continue
        attempt = _attempt(plan, decision, not alerted)
        if attempt.kind is AttemptKind.SUBMITTED:
            return PlanEnd.SUBMITTED
        if attempt.kind is AttemptKind.VOIDED:
            return PlanEnd.VOIDED
        failure = attempt.reason
        alerted = alerted or attempt.alerted
        if attempt.kind is AttemptKind.RETRY:
            _parts().sleep(_parts().flow.config.retry_delay.total_seconds())


# 每个 step 只调用 OrderFlow 的同名方法，见 design D2 的表


@DBOS.step(name="order_plan.request_confirmation")
def _request_confirmation(plan: TradePlan, decision: RiskDecision) -> Requested | None:
    return _parts().flow.request_confirmation(plan, decision)


@DBOS.step(name="order_plan.settle")
def _settle(
    plan: TradePlan, decision: RiskDecision, stage: Stage, requested: Requested | None
) -> None:
    _parts().flow.settle(plan, decision, stage, requested)


@DBOS.step(name="order_plan.timing")
def _timing(plan: TradePlan, decision: RiskDecision) -> Timing:
    return _parts().flow.timing(plan, decision)


@DBOS.step(name="order_plan.attempt")
def _attempt(plan: TradePlan, decision: RiskDecision, alert_error: bool) -> Attempt:
    return _parts().flow.attempt(plan, decision, alert_error=alert_error)


@DBOS.step(name="order_plan.void_expired")
def _void_expired(plan: TradePlan, decision: RiskDecision, failure: str | None) -> PlanEnd:
    return _parts().flow.void_expired(plan, decision, failure)
