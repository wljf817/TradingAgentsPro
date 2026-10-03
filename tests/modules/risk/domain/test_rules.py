"""风控规则的测试。场景见 openspec 的 risk-rules spec：开仓类规则、减仓类规则、
其他方案的预留计入额度、规则的单调性，以及依赖真实规则的判定场景。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.calendar import sample_nyse_calendar
from tests.modules.risk.builders import (
    AAA,
    add_aaa,
    baseline_account,
    baseline_plan,
    baseline_request,
    close_plan,
    holding,
    reduce_plan,
)
from tests.modules.risk.domain.strategies import risk_requests
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.modules.risk import (
    OpeningReservation,
    RiskConfig,
    RiskRequest,
    SellReservation,
    evaluate_plan,
)
from tradingagentspro.modules.risk.domain.rules import EXIT_RULES, OPENING_RULES
from tradingagentspro.shared.trading_calendar import CalendarRangeError

PASSED, ADJUSTED, REJECTED = RiskOutcome.PASSED, RiskOutcome.ADJUSTED, RiskOutcome.REJECTED


def _check(**changes: Any) -> RiskDecision:
    """在基准数据上做 changes 里的修改，然后检查方案。"""
    return evaluate_plan(baseline_request(**changes))


def _rules(decision: RiskDecision) -> list[str]:
    return [v.rule for v in decision.violations]


def _last_trade_bbb(day: date) -> AccountSnapshot:
    """基准账户，另外记下 BBB 的最近成交日。"""
    return baseline_account(last_trade_days={"AAA": date(2026, 10, 5), "BBB": day})


def _tech_buy(quantity: int) -> TradePlan:
    """买入 BBB，板块改为"科技"，限价 50，止损价 49。"""
    return baseline_plan(sector="科技", quantity=quantity, stop_price=Decimal(49))


# 规则的单调性


@given(risk_requests(), st.integers(2, 2000))
def test_every_registered_rule_is_monotone(request: RiskRequest, quantity: int) -> None:
    rules = OPENING_RULES if request.plan.action.is_opening else EXIT_RULES
    for rule in rules:
        if rule(request, quantity) is None:
            assert rule(request, quantity - 1) is None


# 判定（依赖真实规则的场景）


def test_passes() -> None:
    decision = _check()

    assert decision.outcome is PASSED
    assert decision.quantity == 100
    assert decision.emergency_threshold == Decimal("0.08")
    assert decision.violations == ()


def test_the_tightest_rule_limits_the_quantity() -> None:
    decision = _check(
        account=baseline_account(settled_cash=Decimal(5025)), plan=baseline_plan(quantity=300)
    )

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 100)
    assert set(_rules(decision)) == {"已结算资金", "单笔计划风险"}


def test_rejects_when_one_share_fails() -> None:
    decision = _check(account=baseline_account(settled_cash=Decimal(50)))

    assert (decision.outcome, decision.quantity) == (REJECTED, None)
    assert _rules(decision) == ["已结算资金"]


# 开仓类规则：前五行，不通过时调整


def test_adjusts_when_settled_cash_is_short() -> None:
    decision = _check(account=baseline_account(settled_cash=Decimal(2010)))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 40)
    assert _rules(decision) == ["已结算资金"]


def test_adjusts_when_over_the_position_limit() -> None:
    decision = _check(plan=add_aaa(100, 110, 105))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 81)
    assert _rules(decision) == ["单只仓位上限"]


def test_adjusts_when_over_the_trade_risk_limit() -> None:
    decision = _check(plan=baseline_plan(quantity=300))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 190)
    assert _rules(decision) == ["单笔计划风险"]


def test_cannot_add_without_raising_the_stop_when_holding_risk_is_over_the_limit() -> None:
    decision = _check(plan=add_aaa(10, 110, 90))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["单笔计划风险"]


def test_adjusts_when_over_the_total_risk_limit() -> None:
    ccc = holding("CCC", "能源", 400, 50, 50, 42)

    decision = _check(
        account=baseline_account(positions=(AAA, ccc)), plan=baseline_plan(quantity=150)
    )

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 123)
    assert _rules(decision) == ["组合合计风险"]


def test_adjusts_when_over_the_sector_limit() -> None:
    ddd = holding("DDD", "科技", 200, 100, 100, 95)

    decision = _check(account=baseline_account(positions=(AAA, ddd)), plan=_tech_buy(200))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 180)
    assert _rules(decision) == ["板块集中度"]


# 其他方案的预留计入额度


def test_reserved_cash_is_taken_from_settled_cash() -> None:
    ccc = OpeningReservation("CCC", "能源", Decimal(37_990), Decimal(37_800), Decimal(900))

    decision = _check(opening_reservations=(ccc,))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 40)
    assert _rules(decision) == ["已结算资金"]


def test_reserved_risk_counts_toward_total_risk() -> None:
    ccc = OpeningReservation("CCC", "能源", Decimal(5025), Decimal(5000), Decimal(425))

    decision = _check(
        config=RiskConfig(max_total_risk_ratio=Decimal("0.02")),
        opening_reservations=(ccc,),
        plan=baseline_plan(quantity=150),
    )

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 100)
    assert _rules(decision) == ["组合合计风险"]


def test_reserved_market_value_counts_toward_its_sector() -> None:
    eee = OpeningReservation("EEE", "科技", Decimal(20_100), Decimal(20_000), Decimal(500))

    decision = _check(opening_reservations=(eee,), plan=_tech_buy(200))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 180)
    assert _rules(decision) == ["板块集中度"]


# 风控阈值配置


def test_changing_a_threshold_changes_the_result() -> None:
    decision = _check(
        config=RiskConfig(max_trade_risk_ratio=Decimal("0.02")), plan=baseline_plan(quantity=300)
    )

    assert (decision.outcome, decision.quantity) == (PASSED, 300)


# 开仓类规则：后六行，不通过时拒绝


def test_rejects_a_new_position_when_positions_are_full() -> None:
    decision = _check(config=RiskConfig(max_positions=1))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["最多持仓数"]


def test_rejects_while_the_stock_has_an_unfinished_opening_plan() -> None:
    bbb = OpeningReservation("BBB", "医疗", Decimal(5025), Decimal(5000), Decimal(525))

    decision = _check(opening_reservations=(bbb,))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["未完成的开仓方案"]


def test_cannot_open_again_on_the_day_of_the_last_trade() -> None:
    decision = _check(account=_last_trade_bbb(date(2026, 10, 14)))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["最短交易间隔"]


def test_can_open_after_a_trade_on_the_previous_session() -> None:
    decision = _check(account=_last_trade_bbb(date(2026, 10, 13)))

    assert decision.outcome is PASSED


def test_after_hours_plans_count_the_interval_to_the_next_open() -> None:
    decision = _check(
        account=_last_trade_bbb(date(2026, 10, 14)),
        now=datetime(2026, 10, 14, 21, tzinfo=UTC),
    )

    assert decision.outcome is PASSED


def test_now_outside_the_calendar_range_raises() -> None:
    calendar = sample_nyse_calendar(date(2026, 9, 1), date(2026, 10, 13))

    with pytest.raises(CalendarRangeError):
        _check(account=_last_trade_bbb(date(2026, 10, 13)), calendar=calendar)


@pytest.mark.parametrize("stop_price", [50, 51])
def test_rejects_when_stop_is_not_below_the_limit(stop_price: int) -> None:
    decision = _check(plan=baseline_plan(stop_price=Decimal(stop_price)))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["止损价"]


def test_default_emergency_threshold_when_not_given() -> None:
    decision = _check()

    assert decision.outcome is PASSED
    assert decision.emergency_threshold == Decimal("0.08")


@pytest.mark.parametrize("threshold", ["0.03", "0.20"])
def test_emergency_threshold_at_the_bounds_passes(threshold: str) -> None:
    decision = _check(plan=baseline_plan(emergency_threshold=Decimal(threshold)))

    assert decision.outcome is PASSED
    assert decision.emergency_threshold == Decimal(threshold)


@pytest.mark.parametrize("threshold", ["0.02", "0.25"])
def test_emergency_threshold_out_of_range_is_rejected(threshold: str) -> None:
    decision = _check(plan=baseline_plan(emergency_threshold=Decimal(threshold)))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["紧急阈值"]


@pytest.mark.parametrize("plan", [baseline_plan(), add_aaa(10, 110, 105)])
def test_no_opening_after_the_daily_loss_breaker_trips(plan: TradePlan) -> None:
    decision = _check(plan=plan, daily_loss_breaker_tripped=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["单日亏损熔断"]


def test_stocks_with_opening_reservations_take_position_slots() -> None:
    ccc = OpeningReservation("CCC", "能源", Decimal(5025), Decimal(5000), Decimal(425))

    decision = _check(config=RiskConfig(max_positions=2), opening_reservations=(ccc,))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["最多持仓数"]


# 判定（依赖拒绝类规则的场景）


def test_rejection_lists_every_failing_rule() -> None:
    decision = _check(plan=baseline_plan(stop_price=Decimal(50)), daily_loss_breaker_tripped=True)

    assert decision.outcome is REJECTED
    assert set(_rules(decision)) == {"单日亏损熔断", "止损价"}


def test_actual_holdings_decide_between_new_position_and_add() -> None:
    config = RiskConfig(max_positions=1)
    buy_aaa = baseline_plan(
        symbol="AAA",
        sector="科技",
        quantity=10,
        limit_price=Decimal(110),
        stop_price=Decimal(105),
    )
    add_bbb = baseline_plan(action=TradeAction.ADD)

    bought = _check(config=config, plan=buy_aaa)
    added = _check(config=config, plan=add_bbb)

    assert (bought.outcome, bought.quantity) == (PASSED, 10)
    assert added.outcome is REJECTED
    assert _rules(added) == ["最多持仓数"]


# 减仓类规则


def test_reduce_within_the_sellable_quantity() -> None:
    decision = _check(plan=reduce_plan("AAA", "科技", 60))

    assert (decision.outcome, decision.quantity) == (PASSED, 60)
    assert decision.emergency_threshold is None


def test_reduce_over_the_sellable_quantity_is_adjusted() -> None:
    decision = _check(plan=reduce_plan("AAA", "科技", 150))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 100)
    assert _rules(decision) == ["可卖数量"]


def test_close_sells_the_whole_sellable_quantity() -> None:
    decision = _check(plan=close_plan("AAA", "科技"))

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_sell_reservations_of_other_plans_are_subtracted() -> None:
    reserved = (SellReservation("AAA", 40),)

    closed = _check(plan=close_plan("AAA", "科技"), sell_reservations=reserved)
    reduced = _check(plan=reduce_plan("AAA", "科技", 80), sell_reservations=reserved)

    assert (closed.outcome, closed.quantity) == (PASSED, 60)
    assert (reduced.outcome, reduced.quantity) == (ADJUSTED, 60)


def test_rejects_when_nothing_is_sellable() -> None:
    no_holding = _check(plan=close_plan("BBB", "医疗"))
    all_reserved = _check(
        plan=reduce_plan("AAA", "科技", 10), sell_reservations=(SellReservation("AAA", 100),)
    )

    assert no_holding.outcome is REJECTED
    assert _rules(no_holding) == ["可卖数量"]
    assert all_reserved.outcome is REJECTED
    assert _rules(all_reserved) == ["可卖数量"]


def test_exits_are_not_limited_by_opening_rules() -> None:
    ccc = holding("CCC", "能源", 400, 50, 50, 30)
    account = baseline_account(
        settled_cash=Decimal(0),
        positions=(AAA, ccc),
        last_trade_days={"AAA": date(2026, 10, 14)},
    )

    decision = _check(
        plan=reduce_plan("AAA", "科技", 50), account=account, daily_loss_breaker_tripped=True
    )

    assert (decision.outcome, decision.quantity) == (PASSED, 50)
