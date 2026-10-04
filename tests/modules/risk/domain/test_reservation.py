"""预留记录和预留占用额度的测试。场景见 openspec 的 risk-reservations spec「预留占用的额度」。"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.modules.risk.builders import (
    baseline_account,
    baseline_config,
    baseline_plan,
    holding,
    reduce_plan,
)
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.modules.risk import (
    OpeningReservation,
    Reservation,
    SellReservation,
)
from tradingagentspro.modules.risk.domain.reservation import opening_reservation, sell_reservation

CONFIG = baseline_config()
WITH_COMMISSION = baseline_config(commission_per_order=Decimal(2))
EXPIRES_AT = datetime(2026, 10, 14, 20, tzinfo=UTC)


def _decision(quantity: int, outcome: RiskOutcome = RiskOutcome.PASSED) -> RiskDecision:
    return RiskDecision(
        outcome=outcome,
        quantity=quantity,
        emergency_threshold=None,
        expires_at=EXPIRES_AT,
        violations=(),
    )


def _reserved(plan: TradePlan, quantity: int) -> Reservation:
    """风控按 quantity 股通过这个方案后的预留，还没有成交。"""
    return Reservation(plan=plan, decision=_decision(quantity))


def _add_ccc() -> TradePlan:
    """加仓 CCC（能源）50 股，限价 52，止损价 48。"""
    return baseline_plan(
        symbol="CCC",
        sector="能源",
        action=TradeAction.ADD,
        quantity=50,
        limit_price=Decimal(52),
        stop_price=Decimal(48),
    )


def _with_ccc(quantity: int, average_cost: str, stop_price: int) -> AccountSnapshot:
    return baseline_account(
        positions=(
            *baseline_account().positions,
            holding("CCC", "能源", quantity, average_cost, 50, stop_price),
        )
    )


# 预留占用的额度


def test_new_position_reservation() -> None:
    reservation = _reserved(baseline_plan(), 100)

    assert opening_reservation(reservation, baseline_account(), CONFIG) == OpeningReservation(
        symbol="BBB",
        sector="医疗",
        cash=Decimal(5025),
        market_value=Decimal(5000),
        risk=Decimal(525),
    )


def test_add_reservation_counts_the_increase_in_risk_against_current_holdings() -> None:
    reservation = _reserved(_add_ccc(), 50)

    before_fill = opening_reservation(reservation, _with_ccc(100, "50", 45), CONFIG)
    after_fill = opening_reservation(reservation.fill(25), _with_ccc(125, "50.4", 48), CONFIG)

    # 合并后的计划风险 438 低于已有持仓的 525，增加的部分记 0
    assert before_fill == OpeningReservation(
        symbol="CCC", sector="能源", cash=Decimal(2613), market_value=Decimal(2600), risk=Decimal(0)
    )
    # 剩余 25 股：合并后 438，减去已有持仓的 331.5
    assert after_fill == OpeningReservation(
        symbol="CCC",
        sector="能源",
        cash=Decimal("1306.5"),
        market_value=Decimal(1300),
        risk=Decimal("106.5"),
    )


def test_new_position_reservation_counts_commissions() -> None:
    """spec「新建仓的预留计入佣金」：资金加一笔买入佣金，风险加买卖两笔。"""
    reservation = _reserved(baseline_plan(), 100)

    assert opening_reservation(reservation, baseline_account(), WITH_COMMISSION) == (
        OpeningReservation(
            symbol="BBB",
            sector="医疗",
            cash=Decimal(5027),
            market_value=Decimal(5000),
            risk=Decimal(529),
        )
    )


def test_add_reservation_counts_the_add_buy_commission() -> None:
    """spec「加仓的预留计入加仓买入的佣金」：合并后 442，减去已有持仓的 333.5。"""
    reservation = _reserved(_add_ccc(), 50).fill(25)

    taken = opening_reservation(reservation, _with_ccc(125, "50.4", 48), WITH_COMMISSION)

    assert taken == OpeningReservation(
        symbol="CCC",
        sector="能源",
        cash=Decimal("1308.5"),
        market_value=Decimal(1300),
        risk=Decimal("108.5"),
    )


def test_sell_reservation_shrinks_with_fills() -> None:
    reservation = _reserved(reduce_plan("AAA", "科技", 60), 60)

    assert sell_reservation(reservation) == SellReservation(symbol="AAA", quantity=60)
    assert sell_reservation(reservation.fill(30)) == SellReservation(symbol="AAA", quantity=30)


# 记录的状态变化


def test_partial_fill_leaves_the_rest_reserved() -> None:
    reservation = _reserved(baseline_plan(), 100).fill(30)

    assert reservation.remaining == 70
    assert reservation.is_active


def test_reporting_the_same_cumulative_fill_twice_changes_nothing() -> None:
    once = _reserved(baseline_plan(), 100).fill(30)

    assert once.fill(30) == once


def test_reservation_ends_when_fully_filled() -> None:
    reservation = _reserved(baseline_plan(), 100).fill(30).fill(100)

    assert reservation.remaining == 0
    assert not reservation.is_active


def test_reservation_ends_when_released() -> None:
    reservation = _reserved(baseline_plan(), 100).fill(30).release()

    assert not reservation.is_active


def test_ended_reservation_does_not_change() -> None:
    released = _reserved(baseline_plan(), 100).release()
    filled = _reserved(baseline_plan(), 100).fill(100)

    assert released.release() == released
    assert released.fill(30) == released
    assert filled.release() == filled
    assert filled.fill(30) == filled


# 再校验后的预留（pre-order-recheck spec「再校验后的预留」）


def test_reserved_quantity_is_the_first_decision_before_any_recheck() -> None:
    reservation = _reserved(baseline_plan(), 100)

    assert reservation.rechecked is None
    assert reservation.quantity == 100


def test_recheck_changes_the_reserved_quantity_and_keeps_the_first_decision() -> None:
    first = _reserved(baseline_plan(), 100)
    rechecked = first.recheck(_decision(40, RiskOutcome.ADJUSTED))

    assert rechecked.decision == first.decision
    assert rechecked.quantity == 40
    assert rechecked.remaining == 40
    assert rechecked.fill(10).remaining == 30
    assert opening_reservation(rechecked, baseline_account(), CONFIG).cash == Decimal(2010)


def test_ended_reservation_ignores_a_recheck() -> None:
    released = _reserved(baseline_plan(), 100).release()

    assert released.recheck(_decision(40, RiskOutcome.ADJUSTED)) == released
