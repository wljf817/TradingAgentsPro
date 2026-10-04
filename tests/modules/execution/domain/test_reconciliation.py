"""对账纯逻辑的测试，直接构造订单和券商的挂单。

场景见 openspec 的 reconciliation spec「不认得的挂单」「多出来的止损单」「来历不明的持仓」。
"""

from dataclasses import replace
from typing import Any

import pytest

from tests.modules.execution.builders import (
    BASELINE_NOW,
    BASELINE_SESSION,
    add_bbb,
    baseline_plan,
    decision,
    reduce_bbb,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    Order,
    OrderStatus,
    Reconciliation,
    WorkingOrder,
    extra_stops,
    new_order,
    position_explained,
    stray_orders,
)


def _order(plan: TradePlan, **changes: Any) -> Order:
    """方案按再校验结果在基准时刻建立的订单，changes 覆盖其中的字段。"""
    order = new_order(plan, decision(plan.quantity or 100), BASELINE_SESSION, BASELINE_NOW)
    return replace(order, **changes)


def _working(order: Order, broker_order_id: str = "B1") -> WorkingOrder:
    """券商那边挂着的这笔订单。"""
    return WorkingOrder(order.symbol, order.client_order_id, broker_order_id)


def _stop(client_order_id: str, symbol: str = "BBB") -> WorkingOrder:
    return WorkingOrder(symbol, client_order_id, f"S-{client_order_id}")


# 不认得的挂单


def test_working_order_with_the_same_broker_order_id_is_ours() -> None:
    order = _order(baseline_plan(), status=OrderStatus.SUBMITTED, broker_order_id="B1")

    assert stray_orders([_working(order, "B1")], [order]) == ()


def test_working_order_of_an_order_without_a_broker_order_id_is_ours() -> None:
    """提交之后、保存结果之前进程退出：订单还没记下券商订单号。"""
    pending = _order(baseline_plan())
    unknown = _order(baseline_plan(), status=OrderStatus.UNKNOWN, unknown_since=BASELINE_NOW)

    working = [_working(pending, "B1"), _working(unknown, "B2")]

    assert stray_orders(working, [pending, unknown]) == ()


def test_second_order_with_the_same_client_order_id_is_stray() -> None:
    order = _order(baseline_plan(), status=OrderStatus.SUBMITTED, broker_order_id="B1")
    second = _working(order, "B2")

    assert stray_orders([_working(order, "B1"), second], [order]) == (second,)


@pytest.mark.parametrize(
    "status",
    [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED],
)
def test_working_order_of_an_ended_order_is_stray(status: OrderStatus) -> None:
    order = _order(baseline_plan(), status=status)
    working = _working(order, "B1")

    assert stray_orders([working], [order]) == (working,)


def test_working_order_with_an_unknown_client_order_id_is_stray() -> None:
    order = _order(baseline_plan(), status=OrderStatus.SUBMITTED, broker_order_id="B1")
    unknown = WorkingOrder("CCC", "X1", "B9")

    assert stray_orders([_working(order, "B1"), unknown], [order]) == (unknown,)


def test_no_working_orders_no_stray_orders() -> None:
    order = _order(baseline_plan(), status=OrderStatus.SUBMITTED, broker_order_id="B1")

    assert stray_orders([], [order]) == ()


# 多出来的止损单


def test_one_working_stop_for_a_stop_in_use_is_consistent() -> None:
    assert extra_stops([_stop("BBB-STOP-1")], {"BBB"}) == ()


def test_one_working_stop_without_a_stop_in_use_is_extra() -> None:
    stop = _stop("X2")

    assert extra_stops([stop], set()) == (stop,)


def test_two_working_stops_are_extra_even_with_a_stop_in_use() -> None:
    first, second = _stop("BBB-STOP-1"), _stop("X2")

    assert extra_stops([first, second], {"BBB"}) == (first, second)


def test_extra_stops_only_list_the_inconsistent_symbols() -> None:
    bbb = _stop("BBB-STOP-1")
    ccc_first, ccc_second = _stop("CCC-STOP-1", "CCC"), _stop("X3", "CCC")
    ddd = _stop("X4", "DDD")

    result = extra_stops([bbb, ccc_first, ddd, ccc_second], {"BBB", "CCC"})

    assert result == (ccc_first, ddd, ccc_second)


def test_a_stop_in_use_without_working_stops_is_consistent() -> None:
    assert extra_stops([], {"BBB"}) == ()


# 来历不明的持仓


def test_position_is_explained_by_a_filled_buy() -> None:
    bought = _order(baseline_plan(), status=OrderStatus.FILLED, broker_order_id="B1", filled=100)

    assert position_explained([bought])


def test_position_is_explained_by_a_partly_filled_add_that_ended() -> None:
    added = _order(add_bbb(), status=OrderStatus.EXPIRED, broker_order_id="B1", filled=20)

    assert position_explained([added])


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.UNKNOWN]
)
def test_position_is_explained_by_an_open_buy_without_fills(status: OrderStatus) -> None:
    """还没查清的买单可能已经成交了。"""
    order = _order(baseline_plan(), status=status)

    assert position_explained([order])


def test_buy_that_ended_without_fills_does_not_explain_a_position() -> None:
    cancelled = _order(baseline_plan(), status=OrderStatus.CANCELLED, broker_order_id="B1")

    assert not position_explained([cancelled])


def test_sells_do_not_explain_a_position() -> None:
    sold = _order(reduce_bbb(), status=OrderStatus.PARTIALLY_FILLED, filled=20)

    assert not position_explained([sold])


def test_no_orders_do_not_explain_a_position() -> None:
    assert not position_explained([])


# 对账结果


def test_reconciliation_without_mismatches_is_clean() -> None:
    assert Reconciliation().clean


@pytest.mark.parametrize(
    "result",
    [
        Reconciliation(stray_orders=(WorkingOrder("CCC", "X1", "B9"),)),
        Reconciliation(extra_stops=(WorkingOrder("BBB", "X2", "S9"),)),
        Reconciliation(unexplained=(("CCC", 100),)),
    ],
)
def test_any_mismatch_makes_the_reconciliation_not_clean(result: Reconciliation) -> None:
    assert not result.clean
