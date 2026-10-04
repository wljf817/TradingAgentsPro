"""判定的测试。场景见 openspec 的 risk-rules spec「风控的输入和判定」。

前面的测试用规则替身检查判定方法；最后的性质测试用已登记的真实规则。
真实规则的具体场景在 test_rules.py。
"""

from bisect import bisect_left
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hypothesis import event, given
from hypothesis import strategies as st

from tests.modules.risk.builders import (
    baseline_account,
    baseline_config,
    baseline_plan,
    baseline_request,
    close_plan,
)
from tests.modules.risk.domain.strategies import (
    accounts_within_limits,
    commission_configs,
    passable_opening_plans,
    risk_requests,
)
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome, RiskViolation
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.modules.risk import RiskConfig, RiskRequest
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


def test_expiry_for_every_plan_that_is_not_rejected() -> None:
    close = datetime(2026, 10, 14, 20, tzinfo=UTC)

    assert decide(REQUEST, [], 100).expires_at == close
    assert decide(REQUEST, [_at_most(40)], 100).expires_at == close
    assert decide(REQUEST, [_always_fails("甲")], 100).expires_at is None
    exit_request = baseline_request(plan=close_plan("AAA", "科技"))
    assert decide(exit_request, [], 100).expires_at == close


# 佣金占比：spec 的场景，用真实规则，每笔佣金 2

WITH_COMMISSION = baseline_config(commission_per_order=Decimal(2))


def _check_with_commission(**changes: Any) -> RiskDecision:
    return evaluate_plan(baseline_request(config=WITH_COMMISSION, **changes))


def test_commission_ratio_equal_to_the_cap_keeps_the_adjustment() -> None:
    """spec「调整后佣金占比等于上限时仍是调整」：往返佣金 4 恰好是成交额 400 的 1%。"""
    decision = _check_with_commission(account=baseline_account(settled_cash=Decimal(404)))

    assert (decision.outcome, decision.quantity) == (RiskOutcome.ADJUSTED, 8)
    assert _names(decision.violations) == ["已结算资金"]


def test_commission_ratio_over_the_cap_rejects_the_adjustment() -> None:
    """spec「调整后佣金占比超过上限时拒绝」：最多 7 股，往返佣金 4 超过成交额 350 的 1%。"""
    decision = _check_with_commission(account=baseline_account(settled_cash=Decimal(403)))

    assert decision.outcome is RiskOutcome.REJECTED
    assert decision.quantity is None
    assert decision.emergency_threshold is None
    assert decision.expires_at is None
    assert _names(decision.violations) == ["已结算资金", "佣金占比"]


def test_requested_quantity_too_small_for_the_commission_is_rejected() -> None:
    """spec「请求的股数本身太小时拒绝」。"""
    too_small = _check_with_commission(plan=baseline_plan(quantity=7))
    enough = _check_with_commission(plan=baseline_plan(quantity=8))

    assert too_small.outcome is RiskOutcome.REJECTED
    assert _names(too_small.violations) == ["佣金占比"]
    assert (enough.outcome, enough.quantity) == (RiskOutcome.PASSED, 8)


def test_commission_ratio_is_not_checked_when_one_share_fails() -> None:
    """spec「1 股都通不过规则时不检查佣金占比」。"""
    decision = _check_with_commission(account=baseline_account(settled_cash=Decimal(50)))

    assert decision.outcome is RiskOutcome.REJECTED
    assert _names(decision.violations) == ["已结算资金"]


def test_exit_plans_skip_the_commission_ratio() -> None:
    """spec「退出方案不检查佣金占比」：减仓 AAA 1 股，限价 110。"""
    plan = baseline_plan(
        symbol="AAA",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=1,
        limit_price=Decimal(110),
        stop_price=None,
    )

    decision = _check_with_commission(plan=plan)

    assert (decision.outcome, decision.quantity) == (RiskOutcome.PASSED, 1)


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
    _assert_consistent_with_the_registered_rules(request)


@given(accounts_within_limits(), passable_opening_plans(), commission_configs)
def test_commission_ratio_is_judged_at_the_largest_passing_quantity(
    account: AccountSnapshot, plan: TradePlan, config: RiskConfig
) -> None:
    """spec「佣金占比按能通过规则的最大股数判断」：方案多半能通过规则，佣金随机。"""
    _assert_consistent_with_the_registered_rules(
        baseline_request(plan=plan, account=account, config=config)
    )


def _assert_consistent_with_the_registered_rules(request: RiskRequest) -> None:
    """结论和已登记的规则、佣金占比一致。"""
    plan = request.plan
    rules = OPENING_RULES if plan.action.is_opening else EXIT_RULES
    if plan.action is TradeAction.CLOSE:
        requested = sellable_quantity(request.account, request.sell_reservations, plan.symbol)
    else:
        assert plan.quantity is not None
        requested = plan.quantity

    def passes(quantity: int) -> bool:
        return all(rule(request, quantity) is None for rule in rules)

    def worth_the_commission(quantity: int) -> bool:
        """佣金占比：减仓类方案不检查；开仓方案的往返佣金不超过成交额乘以上限。"""
        if not plan.action.is_opening:
            return True
        assert plan.limit_price is not None
        cap = quantity * plan.limit_price * request.config.max_round_trip_commission_ratio
        return 2 * request.config.commission_per_order <= cap

    decision = evaluate_plan(request)
    reasons = [v.rule for v in decision.violations]

    if decision.outcome is RiskOutcome.PASSED:
        assert decision.quantity == requested
        assert passes(requested)
        assert worth_the_commission(requested)
    elif decision.outcome is RiskOutcome.ADJUSTED:
        assert decision.quantity is not None
        assert decision.quantity < requested
        assert passes(decision.quantity)
        assert not passes(decision.quantity + 1)
        assert worth_the_commission(decision.quantity)
    elif passes(1):
        largest = bisect_left(range(1, requested + 1), True, key=lambda q: not passes(q))
        event("佣金占比拒绝")
        assert "佣金占比" in reasons
        assert not worth_the_commission(largest)
    else:
        assert "佣金占比" not in reasons
