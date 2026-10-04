"""订单记录和它的状态变化的测试，直接构造方案、再校验结果、订单和券商回报。

场景见 openspec 的 order-lifecycle spec「提交订单」「订单状态和券商回报」，
以及「到期撤单」「提前撤单」「券商调用失败时状态未知」「状态未知时暂停这只股票的新订单」用到的判断。
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from tests.modules.execution.builders import (
    BASELINE_CLOSE,
    BASELINE_NOW,
    BASELINE_SESSION,
    add_aaa,
    baseline_plan,
    close_aaa,
    decision,
    reduce_aaa,
    session,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrder,
    BrokerOrderStatus,
    Order,
    OrderSide,
    OrderStatus,
    new_order,
)

TERMINAL = (
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
)
DELAY = timedelta(seconds=30)
EARLY_DEADLINE = datetime(2026, 10, 14, 16, tzinfo=UTC)
SECOND = timedelta(seconds=1)


def _order(**changes: Any) -> Order:
    """基准方案按基准结果在基准时刻建立的订单，changes 覆盖其中的字段。"""
    order = new_order(baseline_plan(), decision(), BASELINE_SESSION, BASELINE_NOW)
    return replace(order, **changes)


def _report(
    status: BrokerOrderStatus,
    filled: int = 0,
    average_price: str | None = None,
    broker_order_id: str = "B1",
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        status=status,
        filled=filled,
        average_price=None if average_price is None else Decimal(average_price),
    )


# 提交订单：订单的内容


def test_buy_plan_becomes_a_pending_limit_buy_order() -> None:
    plan = baseline_plan()

    order = new_order(plan, decision(), BASELINE_SESSION, BASELINE_NOW)

    assert order == Order(
        plan_id=plan.plan_id,
        symbol="BBB",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(50),
        quantity=100,
        deadline=BASELINE_CLOSE,
        created_at=BASELINE_NOW,
        stop_price=Decimal(45),
    )
    assert order.status is OrderStatus.PENDING
    assert order.broker_order_id is None
    assert order.filled == 0
    assert order.average_price is None
    assert order.unknown_since is None
    assert not order.cancel_requested


@pytest.mark.parametrize(
    ("plan", "side"),
    [
        (baseline_plan(), OrderSide.BUY),
        (add_aaa(), OrderSide.BUY),
        (reduce_aaa(), OrderSide.SELL),
        (close_aaa(), OrderSide.SELL),
    ],
    ids=["buy", "add", "reduce", "close"],
)
def test_order_side_follows_the_action(plan: TradePlan, side: OrderSide) -> None:
    assert new_order(plan, decision(), BASELINE_SESSION, BASELINE_NOW).side is side


def test_reduce_plan_becomes_a_market_sell_order() -> None:
    order = new_order(reduce_aaa(60), decision(60), BASELINE_SESSION, BASELINE_NOW)

    assert (order.symbol, order.side, order.order_type) == ("AAA", OrderSide.SELL, OrderType.MARKET)
    assert order.limit_price is None
    assert order.quantity == 60


@pytest.mark.parametrize(
    ("plan", "stop_price"),
    [
        (baseline_plan(), Decimal(45)),
        (add_aaa(), Decimal(95)),
        (reduce_aaa(), None),
        (close_aaa(), None),
    ],
    ids=["buy", "add", "reduce", "close"],
)
def test_opening_orders_record_the_plan_stop_price(
    plan: TradePlan, stop_price: Decimal | None
) -> None:
    """order-lifecycle spec「提交订单」：买入、加仓取方案的止损价，减仓、清仓没有。"""
    assert new_order(plan, decision(), BASELINE_SESSION, BASELINE_NOW).stop_price == stop_price


def test_quantity_is_the_final_quantity_of_the_recheck() -> None:
    adjusted = new_order(baseline_plan(), decision(60), BASELINE_SESSION, BASELINE_NOW)
    close = new_order(close_aaa(), decision(100), BASELINE_SESSION, BASELINE_NOW)

    assert adjusted.quantity == 60
    assert close.quantity == 100


def test_unsent_orders_are_pending_or_never_accepted_unknown() -> None:
    """还没确认券商收到的订单：待提交，或者券商从没受理过的状态未知订单。"""
    assert _order().unsent
    assert _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW).unsent
    assert not _order(
        status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW, broker_order_id="B1"
    ).unsent
    assert not _order(status=OrderStatus.SUBMITTED, broker_order_id="B1").unsent


def test_client_order_id_is_the_plan_id() -> None:
    plan = baseline_plan()

    order = new_order(plan, decision(), BASELINE_SESSION, BASELINE_NOW)

    assert order.client_order_id == str(plan.plan_id)


def test_deadline_when_the_expiry_is_before_the_close() -> None:
    order = new_order(
        baseline_plan(), decision(expires_at=EARLY_DEADLINE), BASELINE_SESSION, BASELINE_NOW
    )

    assert order.deadline == EARLY_DEADLINE


def test_deadline_when_the_expiry_is_after_the_close() -> None:
    expires_at = datetime(2026, 10, 16, 20, tzinfo=UTC)

    order = new_order(
        baseline_plan(), decision(expires_at=expires_at), BASELINE_SESSION, BASELINE_NOW
    )

    assert order.deadline == BASELINE_CLOSE


def test_deadline_on_an_early_close_day() -> None:
    # 感恩节次日 13:00（纽约）提前收盘，即 18:00Z
    now = datetime(2026, 11, 27, 15, tzinfo=UTC)
    expires_at = datetime(2026, 11, 30, 21, tzinfo=UTC)

    order = new_order(
        baseline_plan(), decision(expires_at=expires_at), session(date(2026, 11, 27)), now
    )

    assert order.deadline == datetime(2026, 11, 27, 18, tzinfo=UTC)


# 订单状态和券商回报


@pytest.mark.parametrize(
    ("status", "filled", "now", "expected"),
    [
        (BrokerOrderStatus.WORKING, 0, BASELINE_NOW, OrderStatus.SUBMITTED),
        (BrokerOrderStatus.WORKING, 30, BASELINE_NOW, OrderStatus.PARTIALLY_FILLED),
        (BrokerOrderStatus.FILLED, 100, BASELINE_NOW, OrderStatus.FILLED),
        (BrokerOrderStatus.CANCELLED, 30, BASELINE_CLOSE - SECOND, OrderStatus.CANCELLED),
        (BrokerOrderStatus.CANCELLED, 30, BASELINE_CLOSE, OrderStatus.EXPIRED),
        (BrokerOrderStatus.REJECTED, 0, BASELINE_NOW, OrderStatus.REJECTED),
        (BrokerOrderStatus.UNKNOWN, 0, BASELINE_NOW, OrderStatus.UNKNOWN),
    ],
    ids=[
        "working-unfilled",
        "working-partly-filled",
        "filled",
        "cancelled-before-deadline",
        "cancelled-at-deadline",
        "rejected",
        "unknown",
    ],
)
def test_report_sets_the_status(
    status: BrokerOrderStatus, filled: int, now: datetime, expected: OrderStatus
) -> None:
    assert _order().apply(_report(status, filled), now).status is expected


def test_report_records_broker_order_id_fills_and_average_price() -> None:
    order = _order().apply(_report(BrokerOrderStatus.WORKING, 30, "49.90"), BASELINE_NOW)

    assert order.broker_order_id == "B1"
    assert order.filled == 30
    assert order.average_price == Decimal("49.90")


def test_cancelled_and_rejected_orders_keep_their_fills() -> None:
    partly = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=30)

    cancelled = partly.apply(_report(BrokerOrderStatus.CANCELLED, 30, "49.90"), BASELINE_NOW)
    rejected = partly.apply(_report(BrokerOrderStatus.REJECTED, 30, "49.90"), BASELINE_NOW)

    assert (cancelled.status, cancelled.filled) == (OrderStatus.CANCELLED, 30)
    assert (rejected.status, rejected.filled) == (OrderStatus.REJECTED, 30)


def test_unknown_report_records_the_time() -> None:
    now = datetime(2026, 10, 14, 15, 10, tzinfo=UTC)

    order = _order(status=OrderStatus.SUBMITTED).apply(_report(BrokerOrderStatus.UNKNOWN), now)

    assert order.status is OrderStatus.UNKNOWN
    assert order.unknown_since == now


def test_leaving_unknown_clears_the_time() -> None:
    unknown = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW)

    order = unknown.apply(_report(BrokerOrderStatus.WORKING), BASELINE_NOW + DELAY)

    assert order.status is OrderStatus.SUBMITTED
    assert order.unknown_since is None


def test_report_with_fewer_fills_is_ignored() -> None:
    partly = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=50)

    assert partly.apply(_report(BrokerOrderStatus.WORKING, 30), BASELINE_NOW) is partly


def test_report_with_the_same_fills_is_applied() -> None:
    partly = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=30)

    order = partly.apply(_report(BrokerOrderStatus.CANCELLED, 30), BASELINE_CLOSE)

    assert (order.status, order.filled) == (OrderStatus.EXPIRED, 30)


@pytest.mark.parametrize("status", TERMINAL)
def test_terminal_orders_ignore_reports(status: OrderStatus) -> None:
    order = _order(status=status, broker_order_id="B1")

    assert order.apply(_report(BrokerOrderStatus.WORKING, 100), BASELINE_NOW) is order


# 其他状态变化


def test_mark_unknown_keeps_broker_order_id_and_fills() -> None:
    partly = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=30)
    now = datetime(2026, 10, 14, 15, 10, tzinfo=UTC)

    order = partly.mark_unknown(now)

    assert order.status is OrderStatus.UNKNOWN
    assert order.unknown_since == now
    assert (order.broker_order_id, order.filled) == ("B1", 30)


def test_mark_unknown_again_restarts_the_timer() -> None:
    unknown = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW)

    assert unknown.mark_unknown(BASELINE_NOW + DELAY).unknown_since == BASELINE_NOW + DELAY


def test_reject() -> None:
    order = _order().reject()

    assert order.status is OrderStatus.REJECTED
    assert order.broker_order_id is None


def test_expire_unsent_order_from_unknown() -> None:
    order = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW).expire_unsent()

    assert order.status is OrderStatus.EXPIRED
    assert order.unknown_since is None
    assert order.filled == 0


def test_order_missing_at_the_broker_before_the_deadline_is_cancelled() -> None:
    """reconciliation spec「结束查不到的订单和止损单」：累计成交和成交均价保留已经记下的。"""
    order = _order(
        status=OrderStatus.UNKNOWN,
        broker_order_id="B1",
        filled=30,
        average_price=Decimal("49.90"),
        unknown_since=BASELINE_NOW,
    )

    ended = order.end_missing(BASELINE_CLOSE - SECOND)

    assert ended == replace(order, status=OrderStatus.CANCELLED, unknown_since=None)


@pytest.mark.parametrize("now", [BASELINE_CLOSE, BASELINE_CLOSE + SECOND])
def test_order_missing_at_the_broker_from_the_deadline_is_expired(now: datetime) -> None:
    order = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=30)

    ended = order.end_missing(now)

    assert ended.status is OrderStatus.EXPIRED
    assert ended.filled == 30


def test_request_cancel_keeps_the_status() -> None:
    order = _order(status=OrderStatus.SUBMITTED, broker_order_id="B1").request_cancel()

    assert order.status is OrderStatus.SUBMITTED
    assert order.cancel_requested


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED]
)
def test_want_cancel_records_the_cancel_request_only(status: OrderStatus) -> None:
    order = _order(status=status, broker_order_id="B1", filled=30)

    assert order.want_cancel() == replace(order, cancel_wanted=True)


def test_want_cancel_keeps_an_unknown_order_waiting() -> None:
    order = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW)

    assert order.want_cancel() == replace(order, cancel_wanted=True)


def test_unsent_order_with_a_cancel_request_is_cancelled() -> None:
    """有撤单要求，截止时刻之前确认券商没收到：已撤销，累计成交 0。"""
    order = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW, cancel_wanted=True)

    ended = order.cancel_unsent()

    assert ended.status is OrderStatus.CANCELLED
    assert ended.filled == 0
    assert ended.unknown_since is None


@pytest.mark.parametrize("status", TERMINAL)
def test_terminal_orders_do_not_change(status: OrderStatus) -> None:
    order = _order(status=status, broker_order_id="B1")

    assert order.mark_unknown(BASELINE_NOW) is order
    assert order.reject() is order
    assert order.expire_unsent() is order
    assert order.request_cancel() is order
    assert order.end_missing(BASELINE_NOW) is order
    assert order.want_cancel() is order
    assert order.cancel_unsent() is order


# 判断


def test_lookup_is_always_due_when_the_status_is_known() -> None:
    assert _order().lookup_due(BASELINE_NOW, DELAY)
    assert _order(status=OrderStatus.SUBMITTED).lookup_due(BASELINE_NOW, DELAY)


def test_lookup_of_an_unknown_order_waits_for_the_delay() -> None:
    unknown = _order(status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW)

    assert not unknown.lookup_due(BASELINE_NOW + DELAY - SECOND, DELAY)
    assert unknown.lookup_due(BASELINE_NOW + DELAY, DELAY)


def test_cancel_is_due_at_the_deadline() -> None:
    submitted = _order(status=OrderStatus.SUBMITTED, broker_order_id="B1")

    assert not submitted.cancel_due(BASELINE_CLOSE - SECOND)
    assert submitted.cancel_due(BASELINE_CLOSE)
    assert submitted.cancel_due(BASELINE_CLOSE + SECOND)


def test_cancel_is_due_for_a_partly_filled_order() -> None:
    partly = _order(status=OrderStatus.PARTIALLY_FILLED, broker_order_id="B1", filled=30)

    assert partly.cancel_due(BASELINE_CLOSE)


def test_cancel_is_requested_only_once() -> None:
    requested = _order(status=OrderStatus.SUBMITTED, broker_order_id="B1", cancel_requested=True)

    assert not requested.cancel_due(BASELINE_CLOSE)


@pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.UNKNOWN, *TERMINAL])
def test_cancel_is_not_due_for_other_statuses(status: OrderStatus) -> None:
    assert not _order(status=status, unknown_since=BASELINE_NOW).cancel_due(BASELINE_CLOSE)


@pytest.mark.parametrize("status", [OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED])
def test_cancel_request_makes_the_cancel_due_before_the_deadline(status: OrderStatus) -> None:
    order = _order(status=status, broker_order_id="B1", filled=30, cancel_wanted=True)

    assert order.cancel_due(BASELINE_NOW)


def test_cancel_request_does_not_repeat_a_requested_cancel() -> None:
    order = _order(
        status=OrderStatus.SUBMITTED,
        broker_order_id="B1",
        cancel_wanted=True,
        cancel_requested=True,
    )

    assert not order.cancel_due(BASELINE_NOW)


@pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.UNKNOWN, *TERMINAL])
def test_cancel_request_is_not_due_for_other_statuses(status: OrderStatus) -> None:
    order = _order(status=status, unknown_since=BASELINE_NOW, cancel_wanted=True)

    assert not order.cancel_due(BASELINE_NOW)


def test_without_a_cancel_request_the_cancel_waits_for_the_deadline() -> None:
    submitted = _order(status=OrderStatus.SUBMITTED, broker_order_id="B1")

    assert not submitted.cancel_due(BASELINE_NOW)


@pytest.mark.parametrize("status", list(OrderStatus))
def test_only_pending_and_unknown_orders_block_new_orders(status: OrderStatus) -> None:
    expected = status in (OrderStatus.PENDING, OrderStatus.UNKNOWN)

    assert _order(status=status).blocks_new_orders is expected


@pytest.mark.parametrize("status", list(OrderStatus))
def test_terminal_statuses(status: OrderStatus) -> None:
    assert status.is_terminal is (status in TERMINAL)
