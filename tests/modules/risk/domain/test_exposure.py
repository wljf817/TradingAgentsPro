"""风控计算的测试。场景见 openspec 的 risk-rules spec「计划风险和市值的算法」。"""

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.modules.risk.builders import (
    AAA,
    NYSE,
    baseline_account,
    baseline_config,
    baseline_plan,
    holding,
)
from tradingagentspro.modules.risk import OpeningReservation, SellReservation
from tradingagentspro.modules.risk.domain.exposure import (
    effective_emergency_threshold,
    effective_expiry,
    effective_price_deviation,
    execution_day,
    holding_of,
    holding_risk,
    merged_risk,
    position_value,
    sector_value,
    sellable_quantity,
)

CONFIG = baseline_config()
WITH_COMMISSION = baseline_config(commission_per_order=Decimal(2))


def _utc(month: int, day: int, hour: int) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


# 计划风险和市值


def test_new_buy_planned_risk() -> None:
    assert merged_risk(None, 100, Decimal(50), Decimal(45), CONFIG) == Decimal(525)


def test_holding_planned_risk() -> None:
    assert holding_risk(AAA, CONFIG) == Decimal(1050)


def test_holding_risk_is_zero_when_stop_is_above_cost() -> None:
    position = holding("AAA", "科技", 100, 100, 110, 105)

    assert holding_risk(position, CONFIG) == 0


def test_add_is_merged_with_the_holding() -> None:
    assert merged_risk(AAA, 50, Decimal(110), Decimal(95), CONFIG) == Decimal("1327.5")
    assert position_value(AAA, 50, Decimal(110)) == Decimal(16_500)


def test_merged_risk_is_zero_when_negative() -> None:
    assert merged_risk(AAA, 50, Decimal(110), Decimal(105), CONFIG) == 0


def test_planned_risk_counts_commissions() -> None:
    """spec「计划风险计入佣金」：新买入和加仓计两笔，已有持仓计止损卖出的一笔。"""
    assert merged_risk(None, 100, Decimal(50), Decimal(45), WITH_COMMISSION) == Decimal(529)
    assert holding_risk(AAA, WITH_COMMISSION) == Decimal(1052)
    assert merged_risk(AAA, 50, Decimal(110), Decimal(95), WITH_COMMISSION) == Decimal("1331.5")


def test_new_buy_position_value_is_only_the_bought_part() -> None:
    assert position_value(None, 100, Decimal(50)) == Decimal(5000)


def test_holding_of_finds_the_position_by_symbol() -> None:
    account = baseline_account()

    assert holding_of(account, "AAA") == AAA
    assert holding_of(account, "BBB") is None


def test_sector_value_adds_holdings_and_opening_reservations() -> None:
    account = baseline_account()
    reservations = [
        OpeningReservation("EEE", "科技", Decimal(20_100), Decimal(20_000), Decimal(500)),
        OpeningReservation("CCC", "能源", Decimal(5025), Decimal(5000), Decimal(425)),
    ]

    assert sector_value(account, reservations, "科技") == Decimal(31_000)
    assert sector_value(account, reservations, "医疗") == 0


# 可卖数量


def test_sellable_quantity_subtracts_sell_reservations() -> None:
    account = baseline_account()
    reservations = [SellReservation("AAA", 40), SellReservation("CCC", 10)]

    assert sellable_quantity(account, reservations, "AAA") == 60
    assert sellable_quantity(account, (), "AAA") == 100


def test_sellable_quantity_is_zero_without_a_holding() -> None:
    assert sellable_quantity(baseline_account(), (), "BBB") == 0


# 执行交易日


def test_execution_day_in_session_is_today() -> None:
    assert execution_day(_utc(10, 14, 15), NYSE) == date(2026, 10, 14)


def test_execution_day_outside_session_is_the_next_open() -> None:
    assert execution_day(_utc(10, 14, 21), NYSE) == date(2026, 10, 15)  # 盘后
    assert execution_day(_utc(10, 14, 12), NYSE) == date(2026, 10, 14)  # 盘前
    assert execution_day(_utc(10, 17, 15), NYSE) == date(2026, 10, 19)  # 周六


# 紧急阈值


def test_effective_emergency_threshold_defaults_when_not_given() -> None:
    config = baseline_config()

    assert effective_emergency_threshold(baseline_plan(), config) == Decimal("0.08")
    plan = baseline_plan(emergency_threshold=Decimal("0.20"))
    assert effective_emergency_threshold(plan, config) == Decimal("0.20")


# 有效期和允许价格偏离


def test_effective_expiry_defaults_to_the_close_of_the_execution_day() -> None:
    assert effective_expiry(baseline_plan(), _utc(10, 14, 15), NYSE) == _utc(10, 14, 20)  # 盘中
    assert effective_expiry(baseline_plan(), _utc(10, 14, 21), NYSE) == _utc(10, 15, 20)  # 盘后


def test_effective_expiry_uses_the_plan_when_given() -> None:
    plan = baseline_plan(expires_at=_utc(10, 16, 20))

    assert effective_expiry(plan, _utc(10, 14, 15), NYSE) == _utc(10, 16, 20)


def test_effective_price_deviation_defaults_when_not_given() -> None:
    config = baseline_config()

    assert effective_price_deviation(baseline_plan(), config) == Decimal("0.03")
    plan = baseline_plan(max_price_deviation=Decimal("0.05"))
    assert effective_price_deviation(plan, config) == Decimal("0.05")
