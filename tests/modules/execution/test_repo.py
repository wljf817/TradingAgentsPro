"""订单存储和止损保护存储的测试：state-store spec「读取最新版本」里订单表和止损保护表那两行，
以及「记录原样读回」和「重启后读到同样的状态」。

列出记录的场景同时测内存实现和数据库实现，原样读回和重启只测数据库实现。
数据库实现的用例需要开着 Docker，内存实现的不需要。
"""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.modules.execution.builders import (
    BASELINE_CLOSE,
    BASELINE_NOW,
    BASELINE_SESSION,
    baseline_plan,
    decision,
)
from tradingagentspro.modules.execution import (
    Order,
    OrderStatus,
    OrderStore,
    PostgresOrderStore,
    PostgresProtectionStore,
    Protection,
    ProtectionStore,
    StopOrder,
    StopStatus,
    new_order,
)
from tradingagentspro.modules.statestore import connect


def _order(symbol: str = "BBB") -> Order:
    """一笔待提交的买单：100 股，限价 50，止损价 45；方案 ID 每次都是新的。"""
    return new_order(baseline_plan(symbol=symbol), decision(), BASELINE_SESSION, BASELINE_NOW)


def _with(order: Order, status: OrderStatus, filled: int = 0) -> Order:
    """券商受理过的这笔订单，状态和累计成交改为给定的值。"""
    return replace(order, status=status, broker_order_id="B1", filled=filled)


def _stop(symbol: str, sequence: int, status: StopStatus) -> StopOrder:
    """这只股票第 sequence 张止损单：100 股，止损价 45。"""
    return StopOrder(
        symbol=symbol,
        client_order_id=f"{symbol}-STOP-{sequence}",
        quantity=100,
        stop_price=Decimal(45),
        status=status,
        broker_order_id=None if status is StopStatus.PENDING else f"S{sequence}",
    )


@pytest.fixture(params=["memory", "postgres"])
def orders(request: pytest.FixtureRequest) -> OrderStore:
    """同一组场景同时测内存实现和数据库实现。"""
    if request.param == "memory":
        return InMemoryOrderStore()
    database: Engine = request.getfixturevalue("database")
    return PostgresOrderStore(database, FakeClock(BASELINE_NOW))


@pytest.fixture(params=["memory", "postgres"])
def protections(request: pytest.FixtureRequest) -> ProtectionStore:
    """同一组场景同时测内存实现和数据库实现。"""
    if request.param == "memory":
        return InMemoryProtectionStore()
    database: Engine = request.getfixturevalue("database")
    return PostgresProtectionStore(database, FakeClock(BASELINE_NOW))


# 订单表


def test_get_reads_the_latest_order(orders: OrderStore) -> None:
    """spec「读到最新版本」。"""
    p1 = _order()
    orders.save(p1)
    orders.save(_with(p1, OrderStatus.SUBMITTED))

    assert orders.get(p1.plan_id) == _with(p1, OrderStatus.SUBMITTED)


def test_get_without_the_plan_is_none(orders: OrderStore) -> None:
    """spec「没有这个键」。"""
    orders.save(_order())

    assert orders.get(UUID(int=9)) is None


def test_listing_an_empty_order_table(orders: OrderStore) -> None:
    """spec「表里没有记录时列出」：未结束的订单、BBB 的订单都为空。"""
    assert orders.open_orders() == ()
    assert orders.of_symbol("BBB") == ()


def test_ended_order_is_no_longer_open(orders: OrderStore) -> None:
    """spec「结束的订单不再列为未结束」。另一笔已提交的订单仍然列出。"""
    p1, p2 = _order(), _order()
    orders.save(_with(p1, OrderStatus.SUBMITTED))
    orders.save(_with(p1, OrderStatus.FILLED, 100))
    orders.save(_with(p2, OrderStatus.SUBMITTED))

    assert orders.open_orders() == (_with(p2, OrderStatus.SUBMITTED),)
    assert _with(p1, OrderStatus.FILLED, 100) in orders.of_symbol("BBB")


def test_orders_of_one_symbol(orders: OrderStore) -> None:
    """spec「一只股票的订单」：每笔订单一个最新版本，不含别的股票。"""
    p1, p2, p3 = _order(), _order(), _order("CCC")
    orders.save(_with(p1, OrderStatus.SUBMITTED))
    orders.save(_with(p1, OrderStatus.FILLED, 100))
    orders.save(_with(p2, OrderStatus.SUBMITTED))
    orders.save(_with(p3, OrderStatus.SUBMITTED))

    bbb = orders.of_symbol("BBB")
    assert len(bbb) == 2
    assert set(bbb) == {_with(p1, OrderStatus.FILLED, 100), _with(p2, OrderStatus.SUBMITTED)}


# 止损保护表


def test_listing_an_empty_protection_table(protections: ProtectionStore) -> None:
    """spec「表里没有记录时列出」：需要对齐的止损保护为空。"""
    assert protections.active() == ()


def test_ended_stop_no_longer_needs_alignment(protections: ProtectionStore) -> None:
    """spec「止损单结束后不再需要对齐」。另一只挂着止损单的股票仍然列出。"""
    aaa = Protection("AAA", used=1, stop=_stop("AAA", 1, StopStatus.WORKING))
    protections.save(Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.WORKING)))
    protections.save(Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.ENDED)))
    protections.save(aaa)

    assert protections.active() == (aaa,)


def test_unprotected_position_needs_alignment(protections: ProtectionStore) -> None:
    """spec「无保护仓位需要对齐」：止损单挂单被拒，已经结束。"""
    ccc = Protection(
        "CCC", used=1, stop=_stop("CCC", 1, StopStatus.ENDED), unprotected_since=BASELINE_NOW
    )
    protections.save(ccc)

    assert protections.active() == (ccc,)


def test_new_stop_after_an_ended_one_needs_alignment(protections: ProtectionStore) -> None:
    """spec「结束以后又挂新的止损单」。"""
    protections.save(Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.ENDED)))
    renewed = Protection("BBB", used=2, stop=_stop("BBB", 2, StopStatus.PENDING))
    protections.save(renewed)

    assert protections.active() == (renewed,)


# 只有数据库实现才有的行为


def test_order_reads_back_unchanged(database: Engine) -> None:
    """spec「订单原样读回」。"""
    plan = baseline_plan(limit_price=Decimal("50.01"))
    order = replace(
        new_order(plan, decision(), BASELINE_SESSION, BASELINE_NOW),
        status=OrderStatus.PARTIALLY_FILLED,
        broker_order_id="B1",
        filled=30,
        average_price=Decimal("50.0066666667"),
    )
    store = PostgresOrderStore(database, FakeClock(BASELINE_NOW))
    store.save(order)

    read = store.get(order.plan_id)
    assert read == order
    assert read is not None
    assert str(read.average_price) == "50.0066666667"
    assert (read.deadline, read.created_at) == (BASELINE_CLOSE, BASELINE_NOW)
    assert read.unknown_since is None


def test_protection_reads_back_unchanged(database: Engine) -> None:
    """spec「止损保护原样读回」。"""
    stop = StopOrder(
        symbol="BBB",
        client_order_id="BBB-STOP-2",
        quantity=30,
        stop_price=Decimal("45.10"),
        status=StopStatus.WORKING,
        broker_order_id="S2",
        requested=100,
    )
    protection = Protection("BBB", used=2, stop=stop, unprotected_since=BASELINE_NOW)
    store = PostgresProtectionStore(database, FakeClock(BASELINE_NOW))
    store.save(protection)

    read = store.get("BBB")
    assert read == protection
    assert read is not None
    assert read.stop is not None
    assert str(read.stop.stop_price) == "45.10"


def test_a_new_order_store_continues_after_a_restart(database: Engine) -> None:
    """spec「重启后继续」：用同一个数据库新建 Engine 和订单存储，读到之前的订单，再往后写。"""
    clock = FakeClock(BASELINE_NOW)
    p1 = _order()
    PostgresOrderStore(database, clock).save(p1)
    PostgresOrderStore(database, clock).save(_with(p1, OrderStatus.SUBMITTED))

    restarted = connect(database.url.render_as_string(hide_password=False))
    try:
        store = PostgresOrderStore(restarted, clock)
        assert store.get(p1.plan_id) == _with(p1, OrderStatus.SUBMITTED)
        store.save(_with(p1, OrderStatus.FILLED, 100))
    finally:
        restarted.dispose()

    with database.connect() as connection:
        versions = connection.execute(
            text(
                "select version, data ->> 'status' from execution_orders "
                "where plan_id = :plan_id order by version"
            ),
            {"plan_id": p1.plan_id},
        ).all()
    assert [tuple(row) for row in versions] == [(1, "pending"), (2, "submitted"), (3, "filled")]
