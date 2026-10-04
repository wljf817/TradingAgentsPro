"""下单前再校验：作废的判断和用实时数据重跑规则，见 pre-order-recheck spec。"""

from decimal import Decimal

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.risk.domain.engine import decide
from tradingagentspro.modules.risk.domain.exposure import (
    effective_price_deviation,
    sellable_quantity,
)
from tradingagentspro.modules.risk.domain.request import RiskRequest
from tradingagentspro.modules.risk.domain.reservation import Reservation
from tradingagentspro.modules.risk.domain.rules import EXIT_RULES, OPENING_RULES


def recheck_plan(
    request: RiskRequest, reservation: Reservation, last_price: Decimal
) -> RiskDecision:
    """再校验一个有效预留的方案：有效期、价格偏离、跌破止损价任何一项不通过就作废；
    否则按实时数据重跑规则。

    request.plan 是 reservation.plan，request 里的预留已经去掉这个方案自己的。
    请求股数不超过预留股数，清仓除外：清仓卖出实时的全部可卖数量。
    """
    voids = _void_reasons(request, reservation, last_price)
    if voids:
        return _rejected(voids)
    plan = request.plan
    rules = OPENING_RULES if plan.action.is_opening else EXIT_RULES
    if plan.action is TradeAction.CLOSE:
        requested = sellable_quantity(request.account, request.sell_reservations, plan.symbol)
    else:
        requested = reservation.quantity
    decision = decide(request, rules, requested)
    if decision.quantity is None:
        return decision
    return decision.model_copy(update={"expires_at": reservation.decision.expires_at})


def plan_ended() -> RiskDecision:
    """没有有效预留的方案：拒绝，原因是"方案已结束"。"""
    return _rejected(
        (RiskViolation(rule="方案已结束", message="方案已被拒绝、释放或作废，没有有效预留"),)
    )


def _void_reasons(
    request: RiskRequest, reservation: Reservation, last_price: Decimal
) -> tuple[RiskViolation, ...]:
    """让方案作废的原因：超过有效期；开仓类方案的价格偏离过大或跌破止损价。"""
    reasons = []
    expires_at = reservation.decision.expires_at
    assert expires_at is not None  # 通过或调整的结果都有生效的有效期
    if request.now >= expires_at:
        reasons.append(RiskViolation(rule="有效期", message=f"方案的有效期到 {expires_at}"))
    plan = request.plan
    if plan.action.is_opening:
        assert plan.limit_price is not None  # 方案格式保证开仓方案有限价和止损价
        assert plan.stop_price is not None
        allowed = effective_price_deviation(plan, request.config)
        if abs(last_price - plan.limit_price) > plan.limit_price * allowed:
            reasons.append(
                RiskViolation(
                    rule="价格偏离",
                    message=(
                        f"实时价 {last_price} 偏离限价 {plan.limit_price} 超过允许的 {allowed}"
                    ),
                )
            )
        if last_price <= plan.stop_price:
            reasons.append(
                RiskViolation(
                    rule="跌破止损价",
                    message=f"实时价 {last_price} 不高于止损价 {plan.stop_price}",
                )
            )
    return tuple(reasons)


def _rejected(violations: tuple[RiskViolation, ...]) -> RiskDecision:
    return RiskDecision(
        outcome=RiskOutcome.REJECTED,
        quantity=None,
        emergency_threshold=None,
        expires_at=None,
        violations=violations,
    )
