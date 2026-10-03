"""预留记录和预留占用额度的测试。场景见 openspec 的 risk-reservations spec「预留占用的额度」。"""

from decimal import Decimal

from tests.modules.risk.builders import baseline_account, baseline_plan, holding, reduce_plan
from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import TradeAction, TradePlan
from tradingagentspro.modules.risk import (
    OpeningReservation,
    Reservation,
    RiskConfig,
    SellReservation,
)
from tradingagentspro.modules.risk.domain.reservation import opening_reservation, sell_reservation

BUFFER = RiskConfig().cost_buffer_ratio


def _reserved(plan: TradePlan, quantity: int) -> Reservation:
    """风控按 quantity 股通过这个方案后的预留，还没有成交。"""
    decision = RiskDecision(
        outcome=RiskOutcome.PASSED, quantity=quantity, emergency_threshold=None, violations=()
    )
    return Reservation(plan=plan, decision=decision)


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

    assert opening_reservation(reservation, baseline_account(), BUFFER) == OpeningReservation(
        symbol="BBB",
        sector="医疗",
        cash=Decimal(5025),
        market_value=Decimal(5000),
        risk=Decimal(525),
    )


def test_add_reservation_counts_the_increase_in_risk_against_current_holdings() -> None:
    reservation = _reserved(_add_ccc(), 50)

    before_fill = opening_reservation(reservation, _with_ccc(100, "50", 45), BUFFER)
    after_fill = opening_reservation(reservation.fill(25), _with_ccc(125, "50.4", 48), BUFFER)

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
