"""再校验纯逻辑的测试。场景见 openspec 的 pre-order-recheck spec。

请求里的预留已经去掉方案自己的，这一步由 service 负责，见 test_service.py。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.modules.risk.builders import (
    BASELINE_NOW,
    baseline_account,
    baseline_plan,
    baseline_request,
    close_plan,
    holding,
    reduce_plan,
)
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.modules.risk import Reservation, evaluate_plan
from tradingagentspro.modules.risk.domain.recheck import plan_ended, recheck_plan

PASSED, ADJUSTED, REJECTED = RiskOutcome.PASSED, RiskOutcome.ADJUSTED, RiskOutcome.REJECTED
AFTER_HOURS = datetime(2026, 10, 14, 21, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
NEXT_DAY = datetime(2026, 10, 15, 14, tzinfo=UTC)


def _utc(hour: int, minute: int = 0) -> datetime:
    """2026-10-14 当天的 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, tzinfo=UTC)


def _reserved(
    plan: TradePlan, now: datetime = BASELINE_NOW, account: AccountSnapshot | None = None
) -> Reservation:
    """在 now 用基准账户（或给定的账户）检查这个方案，按结果预留。"""
    decision = evaluate_plan(
        baseline_request(plan=plan, now=now, account=account or baseline_account())
    )
    assert decision.quantity is not None
    return Reservation(plan=plan, decision=decision)


def _recheck(
    reservation: Reservation, last_price: str | None = None, **changes: Any
) -> RiskDecision:
    """再校验：默认基准账户、基准时刻、熔断没有触发，实时价等于限价（市价单用 1）。"""
    plan = reservation.plan
    price = Decimal(last_price) if last_price else (plan.limit_price or Decimal(1))
    return recheck_plan(baseline_request(plan=plan, **changes), reservation, price)


def _rules(decision: RiskDecision) -> list[str]:
    return [v.rule for v in decision.violations]


def _holding_aaa(quantity: int) -> AccountSnapshot:
    """基准账户，AAA 的持仓股数改为 quantity；0 表示没有持仓。"""
    positions = (holding("AAA", "科技", quantity, 100, 110, 90),) if quantity else ()
    return baseline_account(positions=positions)


# 有效期


def test_in_session_plan_expires_at_the_close() -> None:
    decision = _recheck(_reserved(baseline_plan()), now=NEXT_DAY)

    assert (decision.outcome, decision.quantity) == (REJECTED, None)
    assert _rules(decision) == ["有效期"]


def test_after_hours_plan_is_rechecked_at_the_next_open() -> None:
    reservation = _reserved(baseline_plan(), now=AFTER_HOURS)

    decision = _recheck(reservation, now=NEXT_OPEN)

    assert reservation.decision.expires_at == datetime(2026, 10, 15, 20, tzinfo=UTC)
    assert (decision.outcome, decision.quantity) == (PASSED, 100)
    assert decision.emergency_threshold == Decimal("0.08")
    assert decision.expires_at == datetime(2026, 10, 15, 20, tzinfo=UTC)


def test_plan_expires_when_its_own_expiry_is_reached() -> None:
    reservation = _reserved(baseline_plan(expires_at=_utc(16)))

    before = _recheck(reservation, now=_utc(15, 59))
    at_expiry = _recheck(reservation, now=_utc(16))

    assert (before.outcome, before.quantity) == (PASSED, 100)
    assert at_expiry.outcome is REJECTED
    assert _rules(at_expiry) == ["有效期"]


def test_exit_plan_expires_too() -> None:
    decision = _recheck(_reserved(reduce_plan("AAA", "科技", 60)), now=NEXT_DAY)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["有效期"]


# 价格偏离


@pytest.mark.parametrize("last_price", ["51.5", "48.5"])
def test_deviation_equal_to_the_allowed_value_passes(last_price: str) -> None:
    decision = _recheck(_reserved(baseline_plan()), last_price)

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


@pytest.mark.parametrize("last_price", ["51.55", "48.45"])
def test_deviation_over_the_allowed_value_voids_the_plan(last_price: str) -> None:
    decision = _recheck(_reserved(baseline_plan()), last_price)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["价格偏离"]


def test_plan_with_its_own_allowed_deviation() -> None:
    reservation = _reserved(baseline_plan(max_price_deviation=Decimal("0.05")))

    assert _recheck(reservation, "52.5").outcome is PASSED
    assert _rules(_recheck(reservation, "52.55")) == ["价格偏离"]


def test_after_hours_plan_gaps_at_the_open() -> None:
    reservation = _reserved(baseline_plan(), now=AFTER_HOURS)

    decision = _recheck(reservation, "53", now=NEXT_OPEN)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["价格偏离"]


def test_exit_plans_do_not_check_the_deviation() -> None:
    plan = reduce_plan("AAA", "科技", 60).model_copy(
        update={"order_type": OrderType.LIMIT, "limit_price": Decimal(120)}
    )

    decision = _recheck(_reserved(plan), "100")

    assert (decision.outcome, decision.quantity) == (PASSED, 60)


# 跌破止损价


def test_price_at_the_stop_voids_the_plan() -> None:
    decision = _recheck(_reserved(baseline_plan(stop_price=Decimal(49))), "49")

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["跌破止损价"]


def test_price_above_the_stop_passes() -> None:
    decision = _recheck(_reserved(baseline_plan(stop_price=Decimal(49))), "49.01")

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_deviation_and_stop_are_both_listed() -> None:
    decision = _recheck(_reserved(baseline_plan(stop_price=Decimal(49))), "48")

    assert decision.outcome is REJECTED
    assert set(_rules(decision)) == {"价格偏离", "跌破止损价"}


def test_rules_are_not_rerun_once_the_plan_is_void() -> None:
    decision = _recheck(_reserved(baseline_plan()), "53", daily_loss_breaker_tripped=True)

    assert _rules(decision) == ["价格偏离"]


# 按实时数据重跑风控


def test_requested_quantity_is_the_reserved_quantity() -> None:
    plan = baseline_plan(quantity=300)
    reservation = _reserved(plan, account=baseline_account(settled_cash=Decimal(5025)))

    decision = _recheck(reservation)

    assert reservation.quantity == 100
    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_requested_quantity_follows_an_earlier_recheck() -> None:
    reservation = _reserved(baseline_plan())
    adjusted = _recheck(reservation, account=baseline_account(settled_cash=Decimal(2010)))

    decision = _recheck(reservation.recheck(adjusted))

    assert (adjusted.outcome, adjusted.quantity) == (ADJUSTED, 40)
    assert (decision.outcome, decision.quantity) == (PASSED, 40)


def test_rejected_by_the_rules_on_recheck() -> None:
    decision = _recheck(_reserved(baseline_plan()), daily_loss_breaker_tripped=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["单日亏损熔断"]


def test_rejected_in_degraded_mode_on_recheck() -> None:
    """pre-order-recheck spec「再校验时处于降级模式」。"""
    decision = _recheck(_reserved(baseline_plan()), degraded=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["降级模式"]


def test_exit_passes_recheck_in_degraded_mode() -> None:
    """pre-order-recheck spec「降级模式下退出照常再校验」。"""
    decision = _recheck(_reserved(close_plan("AAA", "科技")), degraded=True)

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_close_uses_the_live_sellable_quantity() -> None:
    reservation = _reserved(close_plan("AAA", "科技"))

    partly_stopped = _recheck(reservation, account=_holding_aaa(60))
    fully_stopped = _recheck(reservation, account=_holding_aaa(0))

    assert reservation.quantity == 100
    assert (partly_stopped.outcome, partly_stopped.quantity) == (PASSED, 60)
    assert (fully_stopped.outcome, fully_stopped.quantity) == (REJECTED, None)
    assert _rules(fully_stopped) == ["可卖数量"]


def test_reduce_over_the_live_holding_is_adjusted() -> None:
    decision = _recheck(_reserved(reduce_plan("AAA", "科技", 60)), account=_holding_aaa(40))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 40)
    assert _rules(decision) == ["可卖数量"]


# 已经结束的方案


def test_plan_ended() -> None:
    decision = plan_ended()

    assert (decision.outcome, decision.quantity) == (REJECTED, None)
    assert (decision.emergency_threshold, decision.expires_at) == (None, None)
    assert _rules(decision) == ["方案已结束"]
