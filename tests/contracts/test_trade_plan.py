"""交易方案格式的测试。场景见 openspec 的 risk-rules spec「交易方案的格式」。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan

BUY, ADD, REDUCE, CLOSE = TradeAction.BUY, TradeAction.ADD, TradeAction.REDUCE, TradeAction.CLOSE
PLAN_ID = UUID(int=1)
EXPIRES_AT = datetime(2026, 10, 15, 20, tzinfo=UTC)


def _plan(action: TradeAction, **changes: Any) -> dict[str, Any]:
    """按动作给出一份合法方案的字段，changes 覆盖其中的字段；值为 None 表示不填。"""
    if action in (BUY, ADD):
        fields: dict[str, Any] = {
            "quantity": 100,
            "order_type": OrderType.LIMIT,
            "limit_price": Decimal(50),
            "stop_price": Decimal(45),
        }
    else:
        fields = {"quantity": 60 if action is REDUCE else None, "order_type": OrderType.MARKET}
    base = {"plan_id": PLAN_ID, "symbol": "BBB", "sector": "医疗", "action": action}
    return base | fields | changes


def _assert_format_error(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TradePlan.model_validate(fields)


def test_valid_opening_and_exit_plans() -> None:
    buy = TradePlan(
        plan_id=UUID(int=1),
        symbol="BBB",
        sector="医疗",
        action=BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(50),
        stop_price=Decimal(45),
    )
    add = TradePlan(
        plan_id=UUID(int=4),
        symbol="AAA",
        sector="科技",
        action=ADD,
        quantity=50,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(110),
        stop_price=Decimal(95),
        expires_at=EXPIRES_AT,
        max_price_deviation=Decimal("0.05"),
    )
    reduce = TradePlan(
        plan_id=UUID(int=2),
        symbol="AAA",
        sector="科技",
        action=REDUCE,
        quantity=60,
        order_type=OrderType.MARKET,
    )
    close = TradePlan(
        plan_id=UUID(int=3),
        symbol="AAA",
        sector="科技",
        action=CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(120),
        expires_at=EXPIRES_AT,
    )

    assert buy.emergency_threshold is None
    assert (buy.expires_at, buy.max_price_deviation) == (None, None)
    assert (add.expires_at, add.max_price_deviation) == (EXPIRES_AT, Decimal("0.05"))
    assert reduce.limit_price is None
    assert (close.quantity, close.expires_at) == (None, EXPIRES_AT)


@pytest.mark.parametrize("action", [BUY, CLOSE])
def test_plan_without_plan_id(action: TradeAction) -> None:
    fields = _plan(action)
    del fields["plan_id"]

    _assert_format_error(fields)


def test_plan_id_must_be_a_uuid() -> None:
    _assert_format_error(_plan(BUY, plan_id="plan-1"))


@pytest.mark.parametrize(
    ("action", "opening"), [(BUY, True), (ADD, True), (REDUCE, False), (CLOSE, False)]
)
def test_buy_and_add_are_opening_actions(action: TradeAction, opening: bool) -> None:
    assert action.is_opening is opening


@pytest.mark.parametrize("action", [BUY, REDUCE])
@pytest.mark.parametrize("quantity", [0, -10])
def test_quantity_must_be_a_positive_integer(action: TradeAction, quantity: int) -> None:
    _assert_format_error(_plan(action, quantity=quantity))


def test_reduce_without_quantity_and_close_with_quantity() -> None:
    _assert_format_error(_plan(REDUCE, quantity=None))
    _assert_format_error(_plan(CLOSE, quantity=100))


@pytest.mark.parametrize("action", [BUY, ADD])
def test_buy_and_add_must_be_limit_orders(action: TradeAction) -> None:
    _assert_format_error(_plan(action, order_type=OrderType.MARKET, limit_price=None))


@pytest.mark.parametrize("action", [BUY, REDUCE, CLOSE])
def test_limit_price_must_match_the_order_type(action: TradeAction) -> None:
    _assert_format_error(_plan(action, order_type=OrderType.LIMIT, limit_price=None))
    _assert_format_error(_plan(action, order_type=OrderType.LIMIT, limit_price=Decimal(0)))
    if not action.is_opening:
        _assert_format_error(_plan(action, order_type=OrderType.MARKET, limit_price=Decimal(120)))


def test_opening_plan_without_stop_price() -> None:
    _assert_format_error(_plan(BUY, stop_price=None))
    _assert_format_error(_plan(ADD, stop_price=Decimal(0)))


def test_exit_plan_with_stop_price_or_emergency_threshold() -> None:
    _assert_format_error(_plan(REDUCE, stop_price=Decimal(90)))
    _assert_format_error(_plan(CLOSE, emergency_threshold=Decimal("0.08")))


@pytest.mark.parametrize("threshold", [Decimal(0), Decimal(1)])
def test_emergency_threshold_format_bounds(threshold: Decimal) -> None:
    _assert_format_error(_plan(BUY, emergency_threshold=threshold))


def test_emergency_threshold_inside_the_bounds_is_valid() -> None:
    plan = TradePlan.model_validate(_plan(BUY, emergency_threshold=Decimal("0.08")))

    assert plan.emergency_threshold == Decimal("0.08")


def test_expiry_without_a_time_zone() -> None:
    _assert_format_error(_plan(BUY, expires_at=datetime(2026, 10, 15, 20)))  # noqa: DTZ001


@pytest.mark.parametrize("action", [REDUCE, CLOSE])
def test_exit_plan_with_max_price_deviation(action: TradeAction) -> None:
    _assert_format_error(_plan(action, max_price_deviation=Decimal("0.03")))


@pytest.mark.parametrize("deviation", [Decimal(0), Decimal(1)])
def test_max_price_deviation_format_bounds(deviation: Decimal) -> None:
    _assert_format_error(_plan(BUY, max_price_deviation=deviation))


def test_symbol_and_sector_must_not_be_empty() -> None:
    _assert_format_error(_plan(BUY, symbol=""))
    _assert_format_error(_plan(BUY, sector=""))


def test_fields_outside_the_table_are_rejected() -> None:
    _assert_format_error(_plan(BUY, note="理由"))
