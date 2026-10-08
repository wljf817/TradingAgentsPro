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


def _format_errors(fields: dict[str, Any]) -> list[str]:
    """构造方案报的全部格式错误的说明。"""
    with pytest.raises(ValidationError) as raised:
        TradePlan.model_validate(fields)
    return [str(error["msg"]) for error in raised.value.errors()]


@pytest.mark.parametrize("limit", ["180.50", "180.500", "1.00", "1"])
def test_prices_from_one_dollar_up_to_the_cent(limit: str) -> None:
    """spec「1 美元及以上的价格只能到分」：合规的价格。"""
    plan = TradePlan.model_validate(
        _plan(BUY, limit_price=Decimal(limit), stop_price=Decimal("0.50"))
    )

    assert plan.limit_price == Decimal(limit)


@pytest.mark.parametrize(
    ("field", "price", "name"),
    [
        ("limit_price", "180.505", "限价"),
        ("limit_price", "1.005", "限价"),
        ("stop_price", "171.001", "止损价"),
    ],
)
def test_prices_from_one_dollar_below_the_cent(field: str, price: str, name: str) -> None:
    """spec「1 美元及以上的价格只能到分」：报格式错误，写明是哪个价格和填的价格。"""
    fields = _plan(BUY, limit_price=Decimal(180), stop_price=Decimal(170))
    fields[field] = Decimal(price)

    [message] = _format_errors(fields)

    assert name in message
    assert price in message
    assert "最小变动单位" in message


@pytest.mark.parametrize("stop", ["0.5", "0.0001", "0.1235"])
def test_prices_below_one_dollar_up_to_four_places(stop: str) -> None:
    """spec「低于 1 美元的价格最多 4 位小数」：合规的价格。"""
    plan = TradePlan.model_validate(
        _plan(BUY, limit_price=Decimal("0.9999"), stop_price=Decimal(stop))
    )

    assert plan.stop_price == Decimal(stop)


@pytest.mark.parametrize(
    ("limit", "stop"), [("0.99995", "0.5"), ("0.9999", "0.00005")], ids=["限价", "止损价"]
)
def test_prices_below_one_dollar_beyond_four_places(limit: str, stop: str) -> None:
    """spec「低于 1 美元的价格最多 4 位小数」：报格式错误。"""
    _assert_format_error(_plan(BUY, limit_price=Decimal(limit), stop_price=Decimal(stop)))


def test_exactly_one_dollar_counts_as_two_places() -> None:
    """spec「正好 1 美元按 2 位小数算」。"""
    _assert_format_error(_plan(BUY, limit_price=Decimal("1.0001"), stop_price=Decimal("0.50")))
    plan = TradePlan.model_validate(
        _plan(BUY, limit_price=Decimal("0.9999"), stop_price=Decimal("0.50"))
    )

    assert plan.limit_price == Decimal("0.9999")


def test_exit_plan_limit_prices_are_checked_too() -> None:
    """spec「退出方案的限价也检查」：市价单没有价格要检查。"""
    _assert_format_error(_plan(CLOSE, order_type=OrderType.LIMIT, limit_price=Decimal("120.001")))
    _assert_format_error(_plan(REDUCE, order_type=OrderType.LIMIT, limit_price=Decimal("0.12345")))
    plan = TradePlan.model_validate(_plan(REDUCE))

    assert plan.order_type is OrderType.MARKET


def test_both_prices_off_the_tick_are_both_reported() -> None:
    """限价和止损价都不符合时两条都报，适配器一次列全（design D7）。"""
    messages = _format_errors(
        _plan(BUY, limit_price=Decimal("180.505"), stop_price=Decimal("171.001"))
    )

    assert len(messages) == 2
    assert any("限价" in m for m in messages)
    assert any("止损价" in m for m in messages)
