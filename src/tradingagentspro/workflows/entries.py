"""几个流程写程序日志共用的部分：订单变化、调用前后的订单变化、释放预留，以及方案、风控结果和
提交订单的说明。

见 program-log spec「风控引擎的动作」「执行模块的动作」「人的动作」和 add-program-log 的
design D5、D6。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import ExecutionService, Order
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.modules.risk import RiskService
from tradingagentspro.workflows.names import ACTIONS, ORDER_STATUSES, ORDER_TYPES, SIDES

_OUTCOMES = {
    RiskOutcome.PASSED: "通过",
    RiskOutcome.ADJUSTED: "调整",
    RiskOutcome.REJECTED: "拒绝",
}


def log_order_change(log: ProgramLog, before: Order, after: Order) -> None:
    """跟进前后状态或累计成交变了时写一条订单变化；没变时不写。"""
    if (before.status, before.filled) == (after.status, after.filled):
        return
    text = (
        f"{after.symbol} {SIDES[after.side]}：{ORDER_STATUSES[before.status]} → "
        f"{ORDER_STATUSES[after.status]}，累计成交 {after.filled} / {after.quantity}"
    )
    if after.filled > 0:
        text += f"，成交均价 {after.average_price}，累计费用 {after.fees}"
    log.write(Actor.EXECUTION, Action.ORDER_CHANGE, str(after.plan_id), text)


@contextmanager
def order_changes(execution: ExecutionService, log: ProgramLog) -> Iterator[None]:
    """块里执行模块跟进过的订单：进入时记下全部未结束的订单，退出时按方案 ID 读回，有变化的
    写订单变化。块里抛错时不写。

    给对账和撤单用：它们跟进过的订单可能已经结束，巡检之后的轮次不会再看到，见 design D5。
    """
    before = execution.open_orders()
    yield
    for order in before:
        after = execution.order(order.plan_id)
        assert after is not None  # 订单只追加，不会消失
        log_order_change(log, order, after)


def release(risk: RiskService, log: ProgramLog, plan_id: UUID, reason: str) -> None:
    """释放这个方案剩下的预留；原来有有效预留时写一条释放预留，原因是 reason，见 design D6。

    调用方照旧负责拿锁。
    """
    active = plan_id in risk.active_plan_ids()
    risk.release(plan_id)
    if active:
        log.write(Actor.RISK, Action.RELEASE, str(plan_id), reason)


def trade_text(plan: TradePlan, quantity: int | None) -> str:
    """动作、股票和股数，比如「买入 BBB 20 股」；没有股数时不写。"""
    text = f"{ACTIONS[plan.action]} {plan.symbol}"
    return text if quantity is None else f"{text} {quantity} 股"


def plan_text(plan: TradePlan) -> str:
    """录入方案的说明：动作、股票、板块，以及填了的各项。"""
    parts = [f"{ACTIONS[plan.action]} {plan.symbol}", f"板块 {plan.sector}"]
    if plan.quantity is not None:
        parts.append(f"{plan.quantity} 股")
    parts.append(ORDER_TYPES[plan.order_type])
    if plan.limit_price is not None:
        parts.append(f"限价 {plan.limit_price}")
    if plan.stop_price is not None:
        parts.append(f"止损价 {plan.stop_price}")
    if plan.emergency_threshold is not None:
        parts.append(f"紧急阈值 {plan.emergency_threshold}")
    if plan.expires_at is not None:
        parts.append(f"有效期 {plan.expires_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC")
    if plan.max_price_deviation is not None:
        parts.append(f"允许价格偏离 {plan.max_price_deviation}")
    return "，".join(parts)


def check_text(plan: TradePlan, decision: RiskDecision) -> str:
    """风控检查的说明：动作和股票、结果和最终股数、不通过的规则；通过或调整时写按几股预留。"""
    parts = [f"{ACTIONS[plan.action]} {plan.symbol}：{outcome_text(decision)}"]
    parts += _violations(decision)
    if decision.quantity is not None:
        parts.append(f"按 {decision.quantity} 股预留")
    return "；".join(parts)


def recheck_text(decision: RiskDecision) -> str:
    """下单前再校验的说明：结果和最终股数、不通过的规则；预留改为几股，或者方案作废。"""
    parts = [outcome_text(decision), *_violations(decision)]
    if decision.quantity is None:
        parts.append("方案作废，预留已经释放")
    else:
        parts.append(f"预留改为 {decision.quantity} 股")
    return "；".join(parts)


def outcome_text(decision: RiskDecision) -> str:
    """风控的结果和最终股数，比如「通过，20 股」；拒绝时只有「拒绝」。"""
    name = _OUTCOMES[decision.outcome]
    return name if decision.quantity is None else f"{name}，{decision.quantity} 股"


def submit_text(plan: TradePlan, order: Order) -> str:
    """提交订单的说明：动作、股票、委托股数、订单类型和限价、返回时订单的状态。"""
    kind = ORDER_TYPES[order.order_type]
    if order.limit_price is not None:
        kind += f"，限价 {order.limit_price}"
    return f"{trade_text(plan, order.quantity)}，{kind}：{ORDER_STATUSES[order.status]}"


def log_submit(log: ProgramLog, plan: TradePlan, order: Order) -> None:
    """写一条提交订单：对象是方案 ID，输出是券商订单号（券商受理过才有）。"""
    log.write(
        Actor.EXECUTION,
        Action.SUBMIT,
        str(plan.plan_id),
        submit_text(plan, order),
        outputs=[] if order.broker_order_id is None else [order.broker_order_id],
    )


def _violations(decision: RiskDecision) -> list[str]:
    return [f"不通过：{v.rule}（{v.message}）" for v in decision.violations]
