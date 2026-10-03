"""风控用例的测试：检查并预留、上报成交、释放。场景见 openspec 的 risk-reservations spec。"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.reservations import InMemoryReservationStore
from tests.modules.risk.builders import (
    baseline_account,
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
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk import RiskConfig, RiskService
from tradingagentspro.modules.risk.domain.exposure import ZERO, holding_risk
from tradingagentspro.modules.risk.domain.reservation import opening_reservation

PASSED, ADJUSTED, REJECTED = RiskOutcome.PASSED, RiskOutcome.ADJUSTED, RiskOutcome.REJECTED

# 已结算资金 10,050：正好够两个 100 股、限价 50 的买入方案（每个 5,025）
TIGHT = baseline_account(settled_cash=Decimal(10_050))


def _check(
    service: RiskService, plan: TradePlan, account: AccountSnapshot | None = None
) -> RiskDecision:
    """按基准账户（或给定的账户）检查方案，熔断没有触发。"""
    return service.check(plan, account or baseline_account(), daily_loss_breaker_tripped=False)


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
    taken = opening_reservation(reservation, baseline_account(), RiskConfig().cost_buffer_ratio)
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
    buffer = RiskConfig().cost_buffer_ratio
    reserved = sum(opening_reservation(r, TIGHT, buffer).cash for r in store.active())
    assert len(store.active()) == 2
    assert reserved <= TIGHT.settled_cash


# 合计不超限：后续方案按剩余额度检查（design D9）


def _assert_within_limits(store: InMemoryReservationStore, account: AccountSnapshot) -> None:
    """持仓加全部有效预留，满足已结算资金、组合合计风险、板块集中度、最多持仓数，
    并且每只股票的卖出预留之和不超过持仓股数。
    """
    config = RiskConfig()
    buffer_ratio = config.cost_buffer_ratio
    active = store.active()
    opening = [
        opening_reservation(r, account, buffer_ratio) for r in active if r.plan.action.is_opening
    ]
    selling = [r for r in active if not r.plan.action.is_opening]
    held = {p.symbol: p.quantity for p in account.positions}

    assert sum((r.cash for r in opening), ZERO) <= account.settled_cash
    total_risk = sum((holding_risk(p, buffer_ratio) for p in account.positions), ZERO)
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


_steps = st.lists(st.one_of(plans(), passable_opening_plans(), st.integers(0, 20)), max_size=12)


@given(accounts_within_limits(), _steps)
def test_holdings_plus_reservations_never_exceed_the_limits(
    account: AccountSnapshot, steps: list[TradePlan | int]
) -> None:
    """依次检查方案；整数表示释放已检查过的第几个方案。每一步之后都不超限。"""
    store = InMemoryReservationStore()
    service = risk_service(store)
    checked: list[TradePlan] = []

    for step in steps:
        if isinstance(step, int):
            if checked:
                service.release(checked[step % len(checked)].plan_id)
        else:
            _check(service, step, account)
            checked.append(step)
        _assert_within_limits(store, account)
