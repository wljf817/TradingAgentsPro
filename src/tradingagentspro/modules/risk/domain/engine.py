"""判定：按规则组给出通过、调整或拒绝，见 risk-rules spec「风控的输入和判定」。"""

from collections.abc import Sequence

from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.risk.domain.exposure import (
    effective_emergency_threshold,
    effective_expiry,
    sellable_quantity,
)
from tradingagentspro.modules.risk.domain.request import RiskRequest
from tradingagentspro.modules.risk.domain.rules import EXIT_RULES, OPENING_RULES, RiskRule


def evaluate_plan(request: RiskRequest) -> RiskDecision:
    """检查一个方案：开仓类用开仓类规则，减仓类用减仓类规则；清仓的请求股数是可卖数量。"""
    plan = request.plan
    rules = OPENING_RULES if plan.action.is_opening else EXIT_RULES
    if plan.action is TradeAction.CLOSE:
        requested = sellable_quantity(request.account, request.sell_reservations, plan.symbol)
    else:
        assert plan.quantity is not None  # 方案格式保证买入、加仓、减仓都有股数
        requested = plan.quantity
    return decide(request, rules, requested)


def decide(request: RiskRequest, rules: Sequence[RiskRule], requested: int) -> RiskDecision:
    """1 股都通不过就拒绝；请求股数能通过就通过；否则调整为能通过的最大股数。

    依赖每条规则的单调性，用二分查找找最大股数。开仓方案得出最终股数之后，再检查佣金占比，
    不通过就改为拒绝：股数越小越通不过，所以它不是规则，不参与二分查找。
    """

    def violations_at(quantity: int) -> tuple[RiskViolation, ...]:
        return tuple(v for rule in rules if (v := rule(request, quantity)) is not None)

    at_one = violations_at(1)
    if at_one:
        return _rejected(at_one)
    at_requested = violations_at(requested)
    if not at_requested:
        outcome, quantity = RiskOutcome.PASSED, requested
    else:
        passing, failing = 1, requested
        while failing - passing > 1:
            middle = (passing + failing) // 2
            if violations_at(middle):
                failing = middle
            else:
                passing = middle
        outcome, quantity = RiskOutcome.ADJUSTED, passing
    if (violation := _commission_ratio(request, quantity)) is not None:
        return _rejected((*at_requested, violation))
    return _approved(request, outcome, quantity, at_requested)


def _commission_ratio(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """开仓方案：买卖两笔佣金不超过成交额乘以往返佣金占比上限。减仓类方案不检查。"""
    plan, config = request.plan, request.config
    if not plan.action.is_opening:
        return None
    assert plan.limit_price is not None  # 方案格式保证开仓方案有限价
    round_trip = 2 * config.commission_per_order
    value = quantity * plan.limit_price
    cap = value * config.max_round_trip_commission_ratio
    if round_trip <= cap:
        return None
    return RiskViolation(
        rule="佣金占比",
        message=f"往返佣金 {round_trip:,.2f}，超过 {quantity} 股成交额对应的上限 {cap:,.2f}",
    )


def _rejected(violations: tuple[RiskViolation, ...]) -> RiskDecision:
    """拒绝的结果：没有最终股数、生效的紧急阈值和有效期。"""
    return RiskDecision(
        outcome=RiskOutcome.REJECTED,
        quantity=None,
        emergency_threshold=None,
        expires_at=None,
        violations=violations,
    )


def _approved(
    request: RiskRequest,
    outcome: RiskOutcome,
    quantity: int,
    violations: tuple[RiskViolation, ...],
) -> RiskDecision:
    """通过或调整的结果：带上生效的有效期；开仓类方案还带上生效的紧急阈值。"""
    plan = request.plan
    threshold = (
        effective_emergency_threshold(plan, request.config) if plan.action.is_opening else None
    )
    return RiskDecision(
        outcome=outcome,
        quantity=quantity,
        emergency_threshold=threshold,
        expires_at=effective_expiry(plan, request.now, request.calendar),
        violations=violations,
    )
