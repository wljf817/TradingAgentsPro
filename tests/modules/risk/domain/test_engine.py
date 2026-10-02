"""判定的测试。场景见 openspec 的 risk-rules spec「风控的输入和判定」。

前面的测试用规则替身检查判定方法；最后的性质测试用已登记的真实规则。
真实规则的具体场景在 test_rules.py。
"""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from tests.modules.risk.builders import baseline_plan, baseline_request, close_plan
from tests.modules.risk.domain.strategies import risk_requests
from tradingagentspro.contracts.risk import RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import TradeAction
from tradingagentspro.modules.risk import RiskRequest
from tradingagentspro.modules.risk.domain.engine import decide, evaluate_plan
from tradingagentspro.modules.risk.domain.exposure import sellable_quantity
from tradingagentspro.modules.risk.domain.rules import EXIT_RULES, OPENING_RULES, RiskRule

REQUEST = baseline_request()


def _at_most(limit: int, name: str = "上限") -> RiskRule:
    """股数不超过 limit 时通过。"""

    def rule(request: RiskRequest, quantity: int) -> RiskViolation | None:
        return None if quantity <= limit else RiskViolation(rule=name, message=f"超过 {limit} 股")

    return rule


def _always_fails(name: str) -> RiskRule:
    def rule(request: RiskRequest, quantity: int) -> RiskViolation | None:
        return RiskViolation(rule=name, message="总是不通过")

    return rule


def _names(violations: tuple[RiskViolation, ...]) -> list[str]:
    return [v.rule for v in violations]


def test_passes_when_the_requested_quantity_passes() -> None:
    decision = decide(REQUEST, [_at_most(200)], 100)

    assert decision.outcome is RiskOutcome.PASSED
    assert decision.quantity == 100
    assert decision.violations == ()


def test_adjusts_to_the_largest_passing_quantity() -> None:
    decision = decide(REQUEST, [_at_most(40, "甲"), _at_most(70, "乙")], 100)

    assert decision.outcome is RiskOutcome.ADJUSTED
    assert decision.quantity == 40
    assert _names(decision.violations) == ["甲", "乙"]  # 请求股数下不通过的规则


def test_rejects_when_one_share_fails() -> None:
    decision = decide(REQUEST, [_always_fails("甲"), _at_most(40), _always_fails("乙")], 100)

    assert decision.outcome is RiskOutcome.REJECTED
    assert decision.quantity is None
    assert decision.emergency_threshold is None
    assert _names(decision.violations) == ["甲", "乙"]  # 1 股时不通过的全部规则


def test_requested_quantity_is_the_plan_quantity() -> None:
    decision = evaluate_plan(REQUEST)

    assert decision.outcome is RiskOutcome.PASSED
    assert decision.quantity == 100


def test_close_requests_the_sellable_quantity() -> None:
    decision = evaluate_plan(baseline_request(plan=close_plan("AAA", "科技")))

    assert decision.quantity == 100


def test_emergency_threshold_only_for_opening_plans_that_are_not_rejected() -> None:
    passed = decide(REQUEST, [], 100)
    adjusted = decide(
        baseline_request(plan=baseline_plan(emergency_threshold=Decimal("0.1"))),
        [_at_most(40)],
        100,
    )
    rejected = decide(REQUEST, [_always_fails("甲")], 100)
    exit_plan = decide(baseline_request(plan=close_plan("AAA", "科技")), [], 100)

    assert passed.emergency_threshold == Decimal("0.08")
    assert adjusted.emergency_threshold == Decimal("0.1")
    assert rejected.emergency_threshold is None
    assert exit_plan.emergency_threshold is None


@given(st.lists(st.integers(0, 300), max_size=5), st.integers(1, 300))
def test_decision_matches_the_tightest_limit(limits: list[int], requested: int) -> None:
    decision = decide(REQUEST, [_at_most(limit) for limit in limits], requested)

    tightest = min(limits, default=requested)
    if tightest < 1:
        assert decision.outcome is RiskOutcome.REJECTED
    elif requested <= tightest:
        assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, requested)
    else:
        assert (decision.outcome, decision.quantity) == (RiskOutcome.ADJUSTED, tightest)


@given(risk_requests())
def test_decision_is_consistent_with_the_registered_rules(request: RiskRequest) -> None:
    """spec「调整后的股数是能通过的最大股数」，以及通过和拒绝的含义。"""
    plan = request.plan
    rules = OPENING_RULES if plan.action.is_opening else EXIT_RULES
    if plan.action is TradeAction.CLOSE:
        requested = sellable_quantity(request.account, request.sell_reservations, plan.symbol)
    else:
        assert plan.quantity is not None
        requested = plan.quantity

    def passes(quantity: int) -> bool:
        return all(rule(request, quantity) is None for rule in rules)

    decision = evaluate_plan(request)

    if decision.outcome is RiskOutcome.PASSED:
        assert decision.quantity == requested
        assert passes(requested)
    elif decision.outcome is RiskOutcome.ADJUSTED:
        assert decision.quantity is not None
        assert decision.quantity < requested
        assert passes(decision.quantity)
        assert not passes(decision.quantity + 1)
    else:
        assert not passes(1)
