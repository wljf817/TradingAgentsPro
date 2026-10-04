"""风控用例的测试：检查并预留、上报成交、释放、下单前再校验。

场景见 openspec 的 risk-reservations spec 和 pre-order-recheck spec。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from tests.fakes.clock import FakeClock
from tests.fakes.reservations import InMemoryReservationStore
from tests.modules.risk.builders import (
    BASELINE_NOW,
    baseline_account,
    baseline_config,
    baseline_plan,
    close_plan,
    holding,
    reduce_plan,
    risk_service,
)
from tests.modules.risk.domain.strategies import (
    SECTORS,
    SYMBOLS,
    accounts_within_limits,
    passable_opening_plans,
    plans,
)
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.risk import RiskConfig, RiskService
from tradingagentspro.modules.risk.domain.exposure import ZERO, holding_risk
from tradingagentspro.modules.risk.domain.reservation import opening_reservation
from tradingagentspro.shared.trading_calendar import OutsideSessionError

PASSED, ADJUSTED, REJECTED = RiskOutcome.PASSED, RiskOutcome.ADJUSTED, RiskOutcome.REJECTED

# 已结算资金 10,050：正好够两个 100 股、限价 50 的买入方案（每个 5,025）
TIGHT = baseline_account(settled_cash=Decimal(10_050))
AFTER_HOURS = datetime(2026, 10, 14, 21, tzinfo=UTC)
BEFORE_OPEN = datetime(2026, 10, 15, 13, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
NEXT_DAY = datetime(2026, 10, 15, 14, tzinfo=UTC)


def _check(
    service: RiskService,
    plan: TradePlan,
    account: AccountSnapshot | None = None,
    *,
    degraded: bool = False,
) -> RiskDecision:
    """按基准账户（或给定的账户）检查方案，熔断没有触发，默认不处于降级模式。"""
    return service.check(
        plan,
        account or baseline_account(),
        daily_loss_breaker_tripped=False,
        degraded=degraded,
    )


def _recheck(
    service: RiskService,
    plan: TradePlan,
    account: AccountSnapshot | None = None,
    last_price: str | None = None,
    daily_loss_breaker_tripped: bool = False,
    degraded: bool = False,
) -> RiskDecision:
    """再校验：默认实时账户是基准账户，实时价等于限价（市价单用 1），熔断没有触发，
    不处于降级模式。
    """
    price = Decimal(last_price) if last_price else (plan.limit_price or Decimal(1))
    return service.recheck(
        plan.plan_id,
        account or baseline_account(),
        price,
        daily_loss_breaker_tripped=daily_loss_breaker_tripped,
        degraded=degraded,
    )


def _clocked(
    store: InMemoryReservationStore, now: datetime = BASELINE_NOW
) -> tuple[RiskService, FakeClock]:
    """从 now 开始的风控用例和它的时钟。"""
    clock = FakeClock(now)
    return risk_service(store, clock=clock), clock


def _rules(decision: RiskDecision) -> list[str]:
    return [v.rule for v in decision.violations]


def _buy(symbol: str, quantity: int, stop_price: int) -> TradePlan:
    """买入（能源），限价 50。"""
    return baseline_plan(
        symbol=symbol, sector="能源", quantity=quantity, stop_price=Decimal(stop_price)
    )


def _reserved_shares(store: InMemoryReservationStore, plan: TradePlan) -> int | None:
    """这个方案有效预留的剩余股数；没有有效预留时为 None。"""
    reservation = store.get(plan.plan_id)
    return reservation.remaining if reservation and reservation.is_active else None


def _holding_aaa(quantity: int) -> AccountSnapshot:
    """基准账户，AAA 的持仓股数改为 quantity。"""
    return baseline_account(positions=(holding("AAA", "科技", quantity, 100, 110, 90),))


# 通过后立即预留


def test_reserves_after_passing_and_later_plans_use_what_is_left() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb, ccc, ddd = baseline_plan(), _buy("CCC", 150, 48), _buy("DDD", 10, 45)

    first = _check(service, bbb, TIGHT)
    second = _check(service, ccc, TIGHT)
    third = _check(service, ddd, TIGHT)

    assert (first.outcome, first.quantity) == (PASSED, 100)
    assert _reserved_shares(store, bbb) == 100
    assert (second.outcome, second.quantity) == (ADJUSTED, 100)
    assert _rules(second) == ["已结算资金"]
    assert _reserved_shares(store, ccc) == 100
    assert third.outcome is REJECTED
    assert _rules(third) == ["已结算资金"]
    assert store.get(ddd.plan_id) is None


def test_exit_plan_reserves_the_shares_to_sell() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    reduce = reduce_plan("AAA", "科技", 60)

    reduced = _check(service, reduce)
    closed = _check(service, close_plan("AAA", "科技"))

    assert (reduced.outcome, reduced.quantity) == (PASSED, 60)
    assert _reserved_shares(store, reduce) == 60
    assert (closed.outcome, closed.quantity) == (PASSED, 40)


# 预留占用的额度：卖出预留随成交缩小


def test_sell_reservation_shrinks_with_fills() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    reduce = reduce_plan("AAA", "科技", 60)
    _check(service, reduce)

    service.record_fill(reduce.plan_id, 30)
    closed = _check(service, close_plan("AAA", "科技"), _holding_aaa(70))

    assert _reserved_shares(store, reduce) == 30
    assert (closed.outcome, closed.quantity) == (PASSED, 40)


# 成交时缩小预留


def test_after_a_partial_fill_the_rest_stays_reserved() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    service.record_fill(bbb.plan_id, 30)
    after_first_report = _reserved_shares(store, bbb)
    service.record_fill(bbb.plan_id, 30)

    assert after_first_report == 70
    assert _reserved_shares(store, bbb) == 70
    reservation = store.get(bbb.plan_id)
    assert reservation is not None
    taken = opening_reservation(reservation, baseline_account(), baseline_config())
    assert (taken.cash, taken.market_value) == (Decimal("3517.5"), Decimal(3500))


def test_reservation_ends_when_fully_filled() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    service.record_fill(bbb.plan_id, 30)
    service.record_fill(bbb.plan_id, 100)

    assert _reserved_shares(store, bbb) is None
    assert store.active() == ()


# 方案或订单结束时释放预留


def test_plan_ending_before_the_order_releases_everything() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb, TIGHT)

    service.release(bbb.plan_id)  # 人工拒绝
    ccc = _check(service, _buy("CCC", 150, 48), TIGHT)

    assert _reserved_shares(store, bbb) is None
    assert (ccc.outcome, ccc.quantity) == (PASSED, 150)


def test_order_ending_releases_the_unfilled_part() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    service.record_fill(bbb.plan_id, 30)
    service.release(bbb.plan_id)  # 订单撤销

    assert _reserved_shares(store, bbb) is None
    assert store.active() == ()


def test_order_expiring_without_any_fill_releases_everything() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    reduce = reduce_plan("AAA", "科技", 60)
    _check(service, reduce)

    service.release(reduce.plan_id)  # 收盘时过期
    closed = _check(service, close_plan("AAA", "科技"))

    assert (closed.outcome, closed.quantity) == (PASSED, 100)


def test_ended_reservation_does_not_change() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb, TIGHT)
    service.release(bbb.plan_id)

    service.release(bbb.plan_id)
    service.record_fill(bbb.plan_id, 30)
    ccc = _check(service, _buy("CCC", 150, 48), TIGHT)

    assert _reserved_shares(store, bbb) is None
    assert (ccc.outcome, ccc.quantity) == (PASSED, 150)


def test_plan_without_a_reservation() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    rejected = _check(service, bbb, baseline_account(settled_cash=Decimal(50)))

    service.release(bbb.plan_id)
    service.record_fill(bbb.plan_id, 30)

    assert rejected.outcome is REJECTED
    assert store.get(bbb.plan_id) is None


# 同一方案重复检查


def test_checking_the_same_plan_again_returns_the_first_result() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()

    first = _check(service, bbb, TIGHT)
    again = _check(service, bbb, TIGHT)
    ccc = _check(service, _buy("CCC", 150, 48), TIGHT)

    # 没有因为"未完成的开仓方案"被自己的预留挡住
    assert again == first
    assert (again.outcome, again.quantity) == (PASSED, 100)
    # BBB 仍然只有一份预留
    assert (ccc.outcome, ccc.quantity) == (ADJUSTED, 100)


def test_rejected_plan_is_checked_again() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()

    rejected = _check(service, bbb, baseline_account(settled_cash=Decimal(50)))
    passed = _check(service, bbb, baseline_account())

    assert rejected.outcome is REJECTED
    assert _rules(rejected) == ["已结算资金"]
    assert (passed.outcome, passed.quantity) == (PASSED, 100)
    assert _reserved_shares(store, bbb) == 100


# 预留保存在存储里


def test_reservations_still_count_after_a_restart() -> None:
    store = InMemoryReservationStore()
    bbb = baseline_plan()
    _check(risk_service(store), bbb, TIGHT)

    restarted = risk_service(store)
    ccc = _check(restarted, _buy("CCC", 150, 48), TIGHT)
    restarted.release(bbb.plan_id)
    ddd = _check(restarted, _buy("DDD", 10, 45), TIGHT)

    assert (ccc.outcome, ccc.quantity) == (ADJUSTED, 100)
    assert _rules(ccc) == ["已结算资金"]
    assert (ddd.outcome, ddd.quantity) == (PASSED, 10)


# 一次只检查一个方案：M1 只有同步调用，按两种先后顺序各检查一遍（design D5）


@pytest.mark.parametrize("bbb_first", [True, False])
def test_two_plans_submitted_together(bbb_first: bool) -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb, ccc = baseline_plan(), _buy("CCC", 150, 48)
    first, second = (bbb, ccc) if bbb_first else (ccc, bbb)

    first_decision = _check(service, first, TIGHT)
    _check(service, second, TIGHT)

    # 先检查的方案按完整的额度检查
    assert (first_decision.outcome, first_decision.quantity) == (PASSED, first.quantity)
    # 后检查的方案按剩余额度检查，两份预留的资金合计不超过已结算资金
    config = baseline_config()
    reserved = sum(opening_reservation(r, TIGHT, config).cash for r in store.active())
    assert len(store.active()) == 2
    assert reserved <= TIGHT.settled_cash


# 下单前再校验（pre-order-recheck spec）：时机


def test_recheck_before_the_open_raises() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store, AFTER_HOURS)
    bbb = baseline_plan()
    _check(service, bbb)

    clock.set(BEFORE_OPEN)
    with pytest.raises(OutsideSessionError):
        _recheck(service, bbb)

    assert _reserved_shares(store, bbb) == 100


# 再校验：有效期


def test_recheck_voids_an_in_session_plan_after_the_close() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store)
    bbb = baseline_plan()
    first = _check(service, bbb)

    clock.set(NEXT_DAY)
    decision = _recheck(service, bbb)

    assert first.expires_at == datetime(2026, 10, 14, 20, tzinfo=UTC)
    assert decision.outcome is REJECTED
    assert _rules(decision) == ["有效期"]
    assert _reserved_shares(store, bbb) is None


def test_recheck_an_after_hours_plan_at_the_next_open() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store, AFTER_HOURS)
    bbb = baseline_plan()
    first = _check(service, bbb)

    clock.set(NEXT_OPEN)
    decision = _recheck(service, bbb)

    assert (first.outcome, first.expires_at) == (PASSED, datetime(2026, 10, 15, 20, tzinfo=UTC))
    assert (decision.outcome, decision.quantity) == (PASSED, 100)
    assert decision.emergency_threshold == Decimal("0.08")
    assert decision.expires_at == datetime(2026, 10, 15, 20, tzinfo=UTC)


def test_recheck_voids_the_plan_at_its_own_expiry() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store)
    bbb = baseline_plan(expires_at=datetime(2026, 10, 14, 16, tzinfo=UTC))
    _check(service, bbb)

    clock.set(datetime(2026, 10, 14, 15, 59, tzinfo=UTC))
    before = _recheck(service, bbb)
    clock.set(datetime(2026, 10, 14, 16, tzinfo=UTC))
    at_expiry = _recheck(service, bbb)

    assert (before.outcome, before.quantity) == (PASSED, 100)
    assert at_expiry.outcome is REJECTED
    assert _rules(at_expiry) == ["有效期"]
    assert _reserved_shares(store, bbb) is None


def test_recheck_voids_an_expired_exit_plan() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store)
    reduce = reduce_plan("AAA", "科技", 60)
    _check(service, reduce)

    clock.set(NEXT_DAY)
    decision = _recheck(service, reduce)
    closed = _check(service, close_plan("AAA", "科技"))

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["有效期"]
    assert (closed.outcome, closed.quantity) == (PASSED, 100)


# 再校验：价格偏离


@pytest.mark.parametrize("last_price", ["51.5", "48.5"])
def test_recheck_passes_when_the_deviation_equals_the_allowed_value(last_price: str) -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan()
    _check(service, bbb)

    decision = _recheck(service, bbb, last_price=last_price)

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


@pytest.mark.parametrize("last_price", ["51.55", "48.45"])
def test_recheck_voids_the_plan_when_the_deviation_is_too_large(last_price: str) -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    decision = _recheck(service, bbb, last_price=last_price)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["价格偏离"]
    assert _reserved_shares(store, bbb) is None


def test_recheck_uses_the_plan_s_own_allowed_deviation() -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan(max_price_deviation=Decimal("0.05"))
    _check(service, bbb)

    within = _recheck(service, bbb, last_price="52.5")
    over = _recheck(service, bbb, last_price="52.55")

    assert (within.outcome, within.quantity) == (PASSED, 100)
    assert over.outcome is REJECTED
    assert _rules(over) == ["价格偏离"]


def test_recheck_voids_an_after_hours_plan_that_gaps_at_the_open() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store, AFTER_HOURS)
    bbb = baseline_plan()
    _check(service, bbb)

    clock.set(NEXT_OPEN)
    decision = _recheck(service, bbb, last_price="53")

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["价格偏离"]
    assert _reserved_shares(store, bbb) is None


def test_recheck_does_not_check_the_deviation_of_exit_plans() -> None:
    service = risk_service(InMemoryReservationStore())
    reduce = reduce_plan("AAA", "科技", 60).model_copy(
        update={"order_type": OrderType.LIMIT, "limit_price": Decimal(120)}
    )
    _check(service, reduce)

    decision = _recheck(service, reduce, last_price="100")

    assert (decision.outcome, decision.quantity) == (PASSED, 60)


# 再校验：跌破止损价


def test_recheck_voids_the_plan_when_the_price_reaches_the_stop() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan(stop_price=Decimal(49))
    _check(service, bbb)

    decision = _recheck(service, bbb, last_price="49")

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["跌破止损价"]
    assert _reserved_shares(store, bbb) is None


def test_recheck_passes_when_the_price_is_above_the_stop() -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan(stop_price=Decimal(49))
    _check(service, bbb)

    decision = _recheck(service, bbb, last_price="49.01")

    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_recheck_lists_both_the_deviation_and_the_stop() -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan(stop_price=Decimal(49))
    _check(service, bbb)

    decision = _recheck(service, bbb, last_price="48")

    assert decision.outcome is REJECTED
    assert set(_rules(decision)) == {"价格偏离", "跌破止损价"}


# 再校验：按实时数据重跑风控


def test_recheck_does_not_count_the_plan_s_own_reservation() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb, TIGHT)
    ccc = _check(service, _buy("CCC", 150, 48), TIGHT)

    decision = _recheck(service, bbb, TIGHT)

    assert (ccc.outcome, ccc.quantity) == (ADJUSTED, 100)
    assert (decision.outcome, decision.quantity) == (PASSED, 100)
    assert decision.violations == ()


def test_recheck_adjusts_when_live_cash_is_short() -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan()
    _check(service, bbb)

    decision = _recheck(service, bbb, baseline_account(settled_cash=Decimal(2010)))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 40)
    assert _rules(decision) == ["已结算资金"]


def test_recheck_does_not_go_above_the_confirmed_quantity() -> None:
    service = risk_service(InMemoryReservationStore())
    plan = baseline_plan(quantity=300)
    first = _check(service, plan, baseline_account(settled_cash=Decimal(5025)))

    decision = _recheck(service, plan, baseline_account())

    assert (first.outcome, first.quantity) == (ADJUSTED, 100)
    assert (decision.outcome, decision.quantity) == (PASSED, 100)


def test_recheck_voids_the_plan_when_the_breaker_has_tripped() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    decision = _recheck(service, bbb, daily_loss_breaker_tripped=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["单日亏损熔断"]
    assert _reserved_shares(store, bbb) is None


def test_no_reservation_when_rejected_in_degraded_mode() -> None:
    """risk-rules spec「降级模式下禁止开仓」：被拒绝的方案不预留。"""
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()

    decision = _check(service, bbb, degraded=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["降级模式"]
    assert store.get(bbb.plan_id) is None


def test_recheck_voids_the_plan_in_degraded_mode() -> None:
    """pre-order-recheck spec「再校验时处于降级模式」。"""
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    decision = _recheck(service, bbb, degraded=True)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["降级模式"]
    assert _reserved_shares(store, bbb) is None


def test_exit_recheck_passes_in_degraded_mode() -> None:
    """pre-order-recheck spec「降级模式下退出照常再校验」。"""
    store = InMemoryReservationStore()
    service = risk_service(store)
    close = close_plan("AAA", "科技")
    _check(service, close)

    decision = _recheck(service, close, degraded=True)

    assert (decision.outcome, decision.quantity) == (PASSED, 100)
    assert _reserved_shares(store, close) == 100


def test_recheck_of_a_close_sells_the_live_sellable_quantity() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    close = close_plan("AAA", "科技")
    _check(service, close)

    partly_stopped = _recheck(service, close, _holding_aaa(60))
    shares_after_partly = _reserved_shares(store, close)
    fully_stopped = _recheck(service, close, baseline_account(positions=()))

    assert (partly_stopped.outcome, partly_stopped.quantity) == (PASSED, 60)
    assert shares_after_partly == 60
    assert fully_stopped.outcome is REJECTED
    assert _rules(fully_stopped) == ["可卖数量"]
    assert _reserved_shares(store, close) is None


def test_recheck_adjusts_a_reduce_over_the_live_holding() -> None:
    service = risk_service(InMemoryReservationStore())
    reduce = reduce_plan("AAA", "科技", 60)
    _check(service, reduce)

    decision = _recheck(service, reduce, _holding_aaa(40))

    assert (decision.outcome, decision.quantity) == (ADJUSTED, 40)
    assert _rules(decision) == ["可卖数量"]


# 再校验后的预留


def test_recheck_shrinks_the_reservation_and_never_grows_it_back() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb)

    adjusted = _recheck(service, bbb, baseline_account(settled_cash=Decimal(2010)))
    reservation = store.get(bbb.plan_id)
    again = _recheck(service, bbb, baseline_account())

    assert (adjusted.outcome, adjusted.quantity) == (ADJUSTED, 40)
    assert reservation is not None
    taken = opening_reservation(reservation, baseline_account(), baseline_config())
    assert (taken.cash, taken.market_value, taken.risk) == (
        Decimal(2010),
        Decimal(2000),
        Decimal(210),
    )
    assert (again.outcome, again.quantity) == (PASSED, 40)


def test_recheck_voids_the_plan_when_the_commission_ratio_gets_too_high() -> None:
    """spec「再校验调小股数后佣金占比过高时作废」：每笔佣金 2。"""
    store = InMemoryReservationStore()
    service = risk_service(store, config=baseline_config(commission_per_order=Decimal(2)))
    bbb = baseline_plan()
    _check(service, bbb)

    adjusted = _recheck(service, bbb, baseline_account(settled_cash=Decimal(404)))
    shares = _reserved_shares(store, bbb)
    voided = _recheck(service, bbb, baseline_account(settled_cash=Decimal(403)))

    assert (adjusted.outcome, adjusted.quantity, shares) == (ADJUSTED, 8, 8)
    assert voided.outcome is REJECTED
    assert [v.rule for v in voided.violations] == ["已结算资金", "佣金占比"]
    assert _reserved_shares(store, bbb) is None


def test_checking_again_after_a_recheck_returns_the_first_result() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    first = _check(service, bbb)
    _recheck(service, bbb, baseline_account(settled_cash=Decimal(2010)))

    again = _check(service, bbb)

    assert again == first
    assert (again.outcome, again.quantity) == (PASSED, 100)
    assert _reserved_shares(store, bbb) == 40


# 再校验：已经结束的方案


def test_recheck_of_a_plan_voided_while_waiting_for_the_open() -> None:
    store = InMemoryReservationStore()
    service, clock = _clocked(store, AFTER_HOURS)
    bbb = baseline_plan()
    _check(service, bbb)
    service.release(bbb.plan_id)  # 同一股票有新案件排队，方案作废

    clock.set(NEXT_OPEN)
    decision = _recheck(service, bbb)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["方案已结束"]


def test_recheck_of_an_already_voided_plan() -> None:
    service = risk_service(InMemoryReservationStore())
    bbb = baseline_plan()
    _check(service, bbb)
    _recheck(service, bbb, last_price="53")

    decision = _recheck(service, bbb, last_price="50")

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["方案已结束"]


def test_recheck_of_a_plan_rejected_by_the_risk_check() -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb = baseline_plan()
    _check(service, bbb, baseline_account(settled_cash=Decimal(50)))

    decision = _recheck(service, bbb)

    assert decision.outcome is REJECTED
    assert _rules(decision) == ["方案已结束"]
    assert store.get(bbb.plan_id) is None


# 一次只处理一个方案：再校验和检查按两种先后顺序各跑一遍（design D7）


@pytest.mark.parametrize("recheck_first", [True, False])
def test_recheck_and_check_together(recheck_first: bool) -> None:
    store = InMemoryReservationStore()
    service = risk_service(store)
    bbb, ccc = baseline_plan(), _buy("CCC", 150, 48)
    _check(service, bbb, TIGHT)

    if recheck_first:
        rechecked = _recheck(service, bbb, TIGHT)
        checked = _check(service, ccc, TIGHT)
    else:
        checked = _check(service, ccc, TIGHT)
        rechecked = _recheck(service, bbb, TIGHT)

    assert (rechecked.outcome, rechecked.quantity) == (PASSED, 100)
    assert (checked.outcome, checked.quantity) == (ADJUSTED, 100)
    config = baseline_config()
    reserved = sum(opening_reservation(r, TIGHT, config).cash for r in store.active())
    assert reserved <= TIGHT.settled_cash


# 合计不超限：后续方案按剩余额度检查（design D9）。用默认配置，佣金也计入（本变更 design D5）

DEFAULT_CONFIG = RiskConfig()


def _assert_within_limits(store: InMemoryReservationStore, account: AccountSnapshot) -> None:
    """持仓加全部有效预留，满足已结算资金、组合合计风险、板块集中度、最多持仓数，
    并且每只股票的卖出预留之和不超过持仓股数。
    """
    config = DEFAULT_CONFIG
    active = store.active()
    opening = [opening_reservation(r, account, config) for r in active if r.plan.action.is_opening]
    selling = [r for r in active if not r.plan.action.is_opening]
    held = {p.symbol: p.quantity for p in account.positions}

    assert sum((r.cash for r in opening), ZERO) <= account.settled_cash
    total_risk = sum((holding_risk(p, config) for p in account.positions), ZERO)
    total_risk += sum((r.risk for r in opening), ZERO)
    assert total_risk <= account.equity * config.max_total_risk_ratio
    for sector in SECTORS:
        value = sum(
            (p.quantity * p.last_price for p in account.positions if p.sector == sector), ZERO
        )
        value += sum((r.market_value for r in opening if r.sector == sector), ZERO)
        assert value <= account.equity * config.max_sector_ratio
    assert len(held.keys() | {r.symbol for r in opening}) <= config.max_positions
    for symbol in SYMBOLS:
        assert sum(r.remaining for r in selling if r.plan.symbol == symbol) <= held.get(symbol, 0)


@dataclass(frozen=True)
class _Recheck:
    """再校验已检查过的第几个方案。"""

    index: int


_steps = st.lists(
    st.one_of(
        plans(),
        passable_opening_plans(),
        st.integers(0, 20),
        st.builds(_Recheck, st.integers(0, 20)),
    ),
    max_size=12,
)


@given(accounts_within_limits(), _steps)
def test_holdings_plus_reservations_never_exceed_the_limits(
    account: AccountSnapshot, steps: list[TradePlan | int | _Recheck]
) -> None:
    """依次检查方案；整数表示释放已检查过的第几个方案，_Recheck 表示再校验它，实时数据
    和检查时相同。每一步之后都不超限；除清仓外，再校验的股数不超过再校验前的预留股数。
    """
    store = InMemoryReservationStore()
    service = risk_service(store, config=DEFAULT_CONFIG)
    checked: list[TradePlan] = []

    for step in steps:
        if isinstance(step, TradePlan):
            _check(service, step, account)
            checked.append(step)
        elif checked:
            index = step.index if isinstance(step, _Recheck) else step
            plan = checked[index % len(checked)]
            if isinstance(step, int):
                service.release(plan.plan_id)
            else:
                _assert_recheck_does_not_grow(store, service, plan, account)
        _assert_within_limits(store, account)


def _assert_recheck_does_not_grow(
    store: InMemoryReservationStore, service: RiskService, plan: TradePlan, account: AccountSnapshot
) -> None:
    before = _reserved_shares(store, plan)
    decision = _recheck(service, plan, account)
    event(f"再校验：{'有效预留' if before else '没有有效预留'}，{decision.outcome}")
    if before is not None and plan.action is not TradeAction.CLOSE:
        assert decision.quantity is None or decision.quantity <= before
