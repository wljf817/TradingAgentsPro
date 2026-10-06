"""交易记录的测试：从订单和止损单推出交易记录，直接构造订单和止损单。

场景见 openspec 的 trade-records spec「交易记录的内容」「止损单交易记录的状态」「成交日和结算日」。
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tests.modules.execution.builders import (
    BASELINE_NOW,
    NYSE,
    baseline_plan,
    decision,
    reduce_aaa,
    session,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    Order,
    OrderStatus,
    StopOrder,
    StopStatus,
    Trade,
    TradeKind,
    new_order,
    trade_of_order,
    trade_of_stop,
)

P1 = UUID(int=101)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """2026 年 10 月某天的某个 UTC 时刻。"""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def _order(plan: TradePlan, created_at: datetime = BASELINE_NOW, **changes: Any) -> Order:
    """方案在 created_at 建立的订单，changes 覆盖其中的字段。"""
    order = new_order(plan, decision(plan.quantity or 100), session(created_at.date()), created_at)
    return replace(order, **changes)


def _stop(**changes: Any) -> StopOrder:
    """BBB-STOP-1：挂着，未成交 100 股，止损价 45，券商订单号 S1，2026-10-14 15:00Z 创建。"""
    stop = StopOrder(
        symbol="BBB",
        client_order_id="BBB-STOP-1",
        quantity=100,
        stop_price=Decimal(45),
        created_at=BASELINE_NOW,
        status=StopStatus.WORKING,
        broker_order_id="S1",
    )
    return replace(stop, **changes)


# 交易记录的内容


def test_trade_of_a_buy_order() -> None:
    """spec「买单的交易记录」。"""
    plan = baseline_plan()
    order = _order(plan, status=OrderStatus.SUBMITTED, broker_order_id="B1")

    assert trade_of_order(order, NYSE) == Trade(
        client_order_id=str(plan.plan_id),
        kind=TradeKind.BUY,
        symbol="BBB",
        plan_id=plan.plan_id,
        quantity=100,
        filled=0,
        average_price=None,
        fees=Decimal(0),
        status=OrderStatus.SUBMITTED,
        created_at=BASELINE_NOW,
        traded_on=None,
        settles_on=None,
    )


def test_trade_of_a_sell_order() -> None:
    """spec「卖单的交易记录」。"""
    plan = reduce_aaa(60)
    order = _order(
        plan,
        status=OrderStatus.FILLED,
        filled=60,
        average_price=Decimal(101),
        fees=Decimal(2),
    )

    trade = trade_of_order(order, NYSE)

    assert (trade.kind, trade.symbol, trade.plan_id) == (TradeKind.SELL, "AAA", plan.plan_id)
    assert (trade.quantity, trade.filled, trade.average_price) == (60, 60, Decimal(101))
    assert (trade.fees, trade.status) == (Decimal(2), OrderStatus.FILLED)
    assert (trade.traded_on, trade.settles_on) == (date(2026, 10, 14), date(2026, 10, 15))


def test_trade_of_a_stop_order() -> None:
    """spec「止损单的交易记录」：委托股数是未成交加累计成交，来源方案是传入的。"""
    stop = _stop(quantity=60, filled=40, average_price=Decimal("44.90"), fees=Decimal(2))

    trade = trade_of_stop(stop, P1, NYSE)

    assert (trade.client_order_id, trade.kind, trade.symbol) == (
        "BBB-STOP-1",
        TradeKind.STOP,
        "BBB",
    )
    assert (trade.plan_id, trade.quantity, trade.filled) == (P1, 100, 40)
    assert (trade.average_price, trade.fees) == (Decimal("44.90"), Decimal(2))
    assert trade.created_at == BASELINE_NOW


def test_trade_of_a_stop_order_without_a_source_plan() -> None:
    assert trade_of_stop(_stop(), None, NYSE).plan_id is None


# 止损单交易记录的状态


@pytest.mark.parametrize(
    ("stop", "status"),
    [
        (_stop(status=StopStatus.PENDING, broker_order_id=None), OrderStatus.PENDING),
        (_stop(), OrderStatus.SUBMITTED),
        (_stop(quantity=60, filled=40), OrderStatus.PARTIALLY_FILLED),
        (_stop(status=StopStatus.UNKNOWN, unknown_since=BASELINE_NOW), OrderStatus.UNKNOWN),
        (_stop(status=StopStatus.ENDED, quantity=0, filled=100), OrderStatus.FILLED),
        (_stop(status=StopStatus.ENDED), OrderStatus.CANCELLED),
        (_stop(status=StopStatus.ENDED, quantity=60, filled=40), OrderStatus.CANCELLED),
        (_stop(status=StopStatus.ENDED, broker_order_id=None), OrderStatus.REJECTED),
    ],
    ids=[
        "pending",
        "working",
        "working-with-fills",
        "unknown",
        "filled",
        "cancelled",
        "cancelled-after-fills",
        "never-accepted",
    ],
)
def test_stop_trade_status(stop: StopOrder, status: OrderStatus) -> None:
    """spec「止损单交易记录的状态」那张表的每一行。"""
    assert trade_of_stop(stop, P1, NYSE).status is status


# 成交日和结算日


def test_an_order_trades_on_the_day_it_was_created() -> None:
    """spec「收盘后才查到成交的订单」：成交日只看订单创建时所在的时段。"""
    order = _order(baseline_plan(), status=OrderStatus.FILLED, filled=100)

    trade = trade_of_order(order, NYSE)

    assert (trade.traded_on, trade.settles_on) == (date(2026, 10, 14), date(2026, 10, 15))


@pytest.mark.parametrize(
    ("created_at", "settles_on"),
    [
        (_at(16, 15), date(2026, 10, 19)),  # 周五
        (datetime(2026, 11, 25, 15, tzinfo=UTC), date(2026, 11, 27)),  # 感恩节前一天
    ],
    ids=["friday", "before-thanksgiving"],
)
def test_settlement_is_the_next_trading_day(created_at: datetime, settles_on: date) -> None:
    """spec「周五成交，周一结算」「结算日跳过休市日」。"""
    order = _order(baseline_plan(), created_at, status=OrderStatus.FILLED, filled=100)

    trade = trade_of_order(order, NYSE)

    assert (trade.traded_on, trade.settles_on) == (created_at.date(), settles_on)


def test_no_trade_day_without_fills() -> None:
    """spec「没有成交的交易记录没有成交日」。"""
    order = _order(baseline_plan(), status=OrderStatus.CANCELLED)

    trade = trade_of_order(order, NYSE)

    assert (trade.traded_on, trade.settles_on) == (None, None)
    assert trade_of_stop(_stop(), P1, NYSE).traded_on is None


@pytest.mark.parametrize(
    ("filled_at", "traded_on"),
    [
        (_at(14, 15, 30), date(2026, 10, 14)),  # 盘中
        (_at(14, 21), date(2026, 10, 14)),  # 当天收盘以后
        (_at(15, 13), date(2026, 10, 14)),  # 第二天开盘前
        (_at(17, 15), date(2026, 10, 16)),  # 周六
    ],
    ids=["in-session", "after-close", "before-next-open", "weekend"],
)
def test_a_stop_trades_on_the_session_when_its_fills_were_seen(
    filled_at: datetime, traded_on: date
) -> None:
    """spec「盘中发现成交的止损单」「第二天开盘前才发现成交的止损单」。"""
    stop = _stop(status=StopStatus.ENDED, quantity=0, filled=100, filled_at=filled_at)

    trade = trade_of_stop(stop, P1, NYSE)

    assert trade.traded_on == traded_on
    assert trade.settles_on == NYSE.next_session(traded_on).day


def test_settled_once_the_day_reaches_the_settlement_day() -> None:
    """spec「结算状态随执行交易日变化」。"""
    order = _order(reduce_aaa(60), status=OrderStatus.FILLED, filled=60)
    trade = trade_of_order(order, NYSE)
    unfilled = trade_of_order(_order(baseline_plan()), NYSE)

    assert not trade.settled(date(2026, 10, 14))
    assert trade.settled(date(2026, 10, 15))
    assert trade.settled(date(2026, 10, 16))
    assert not unfilled.settled(date(2026, 10, 15))


@pytest.mark.parametrize(
    ("kind", "is_sale"),
    [(TradeKind.BUY, False), (TradeKind.SELL, True), (TradeKind.STOP, True)],
)
def test_sells_and_stops_are_sales(kind: TradeKind, is_sale: bool) -> None:
    trade = replace(trade_of_order(_order(baseline_plan()), NYSE), kind=kind)

    assert trade.is_sale is is_sale
