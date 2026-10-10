"""订单存储、止损保护存储、交易记录存储和收盘对账记录存储的测试：state-store spec「读取最新版本」
里订单表和止损保护表那两行，「读取一段时间里的版本」，以及「记录原样读回」和「重启后读到同样的
状态」；trade-records spec「保存交易记录」；close-reconciliation spec「收盘对账记录」。

列出记录的场景同时测内存实现和数据库实现，原样读回和重启只测数据库实现。
数据库实现的用例需要开着 Docker，内存实现的不需要。
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from tests.fakes.clock import FakeClock
from tests.fakes.closes import InMemoryCloseReconciliationStore
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.trades import InMemoryTradeStore
from tests.modules.execution.builders import (
    BASELINE_CLOSE,
    BASELINE_NOW,
    BASELINE_SESSION,
    baseline_plan,
    decision,
)
from tradingagentspro.modules.execution import (
    CloseReconciliation,
    CloseReconciliationStore,
    LeftoverOrder,
    NewStop,
    Order,
    OrderSide,
    OrderStatus,
    OrderStore,
    PostgresCloseReconciliationStore,
    PostgresOrderStore,
    PostgresProtectionStore,
    PostgresTradeStore,
    Protection,
    ProtectionStore,
    Reconciliation,
    StopOrder,
    StopStatus,
    Trade,
    TradeKind,
    TradeStore,
    UnprotectedSpell,
    WorkingOrder,
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
        created_at=BASELINE_NOW,
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


def _trade(client_order_id: str, status: OrderStatus, filled: int = 0, **changes: Any) -> Trade:
    """一条买入 BBB 100 股的交易记录，累计成交 filled 股；有成交时成交日 2026-10-14。"""
    trade = Trade(
        client_order_id=client_order_id,
        kind=TradeKind.BUY,
        symbol="BBB",
        plan_id=UUID(int=1),
        quantity=100,
        filled=filled,
        average_price=Decimal(50) if filled else None,
        fees=Decimal(0),
        status=status,
        created_at=BASELINE_NOW,
        traded_on=date(2026, 10, 14) if filled else None,
        settles_on=date(2026, 10, 15) if filled else None,
    )
    return replace(trade, **changes)


@pytest.fixture(params=["memory", "postgres"])
def trades(request: pytest.FixtureRequest) -> TradeStore:
    """同一组场景同时测内存实现和数据库实现。"""
    if request.param == "memory":
        return InMemoryTradeStore()
    database: Engine = request.getfixturevalue("database")
    return PostgresTradeStore(database, FakeClock(BASELINE_NOW))


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
        created_at=BASELINE_NOW,
        status=StopStatus.WORKING,
        broker_order_id="S2",
        requested=100,
        filled=70,
        average_price=Decimal("45.0533333333"),
        fees=Decimal("2.15"),
        filled_at=BASELINE_NOW,
    )
    protection = Protection("BBB", used=2, stop=stop, unprotected_since=BASELINE_NOW)
    store = PostgresProtectionStore(database, FakeClock(BASELINE_NOW))
    store.save(protection)

    read = store.get("BBB")
    assert read == protection
    assert read is not None
    assert read.stop is not None
    assert str(read.stop.stop_price) == "45.10"


def test_staggered_protection_reads_back_unchanged(database: Engine) -> None:
    """stop-protection spec「保存止损保护」：记录是否错开中。"""
    protection = Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.ENDED), staggered=True)
    store = PostgresProtectionStore(database, FakeClock(BASELINE_NOW))
    store.save(protection)

    assert store.get("BBB") == protection


def test_an_old_protection_record_is_not_staggered(database: Engine) -> None:
    """update-stagger-buys-and-stops 的 design D4：之前写入的记录没有 staggered，读成不是错开中。"""
    protection = Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.WORKING))
    store = PostgresProtectionStore(database, FakeClock(BASELINE_NOW))
    store.save(protection)
    with database.begin() as connection:
        connection.execute(text("update execution_protections set data = data - 'staggered'"))

    assert store.get("BBB") == protection


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


# 交易记录表


def test_get_reads_the_latest_trade(trades: TradeStore) -> None:
    """state-store spec「读到最新版本」，用交易记录。"""
    trades.save(_trade("P1", OrderStatus.PENDING))
    trades.save(_trade("P1", OrderStatus.SUBMITTED))

    assert trades.get("P1") == _trade("P1", OrderStatus.SUBMITTED)
    assert trades.get("P9") is None


def test_listing_an_empty_trade_table(trades: TradeStore) -> None:
    assert trades.traded() == ()


def test_only_trades_with_fills_are_listed(trades: TradeStore) -> None:
    """trade-records spec「列出有成交的交易记录」：都是最新版本。"""
    trades.save(_trade("P1", OrderStatus.SUBMITTED))
    trades.save(_trade("P1", OrderStatus.FILLED, 100))
    trades.save(_trade("P5", OrderStatus.CANCELLED, kind=TradeKind.SELL, symbol="AAA"))
    stop = _trade("BBB-STOP-1", OrderStatus.PARTIALLY_FILLED, 40, kind=TradeKind.STOP)
    trades.save(stop)

    listed = trades.traded()
    assert len(listed) == 2
    assert set(listed) == {_trade("P1", OrderStatus.FILLED, 100), stop}


def test_a_trade_is_listed_once_its_latest_version_has_fills(trades: TradeStore) -> None:
    trades.save(_trade("P1", OrderStatus.SUBMITTED))
    assert trades.traded() == ()

    trades.save(_trade("P1", OrderStatus.PARTIALLY_FILLED, 30))
    assert trades.traded() == (_trade("P1", OrderStatus.PARTIALLY_FILLED, 30),)


def test_trade_reads_back_unchanged(database: Engine) -> None:
    """trade-records spec「交易记录原样读回」。"""
    trade = _trade(
        "P1",
        OrderStatus.PARTIALLY_FILLED,
        30,
        average_price=Decimal("49.9533333333"),
        fees=Decimal("2.15"),
    )
    store = PostgresTradeStore(database, FakeClock(BASELINE_NOW))
    store.save(trade)

    read = store.get("P1")
    assert read == trade
    assert read is not None
    assert (str(read.average_price), str(read.fees)) == ("49.9533333333", "2.15")
    assert (read.created_at, read.traded_on, read.settles_on) == (
        BASELINE_NOW,
        date(2026, 10, 14),
        date(2026, 10, 15),
    )


def test_a_new_trade_store_reads_the_same_trades_after_a_restart(database: Engine) -> None:
    """trade-records spec「保存交易记录」：重启后从存储读出交易记录。"""
    clock = FakeClock(BASELINE_NOW)
    PostgresTradeStore(database, clock).save(_trade("P1", OrderStatus.FILLED, 100))

    restarted = connect(database.url.render_as_string(hide_password=False))
    try:
        store = PostgresTradeStore(restarted, clock)
        assert store.traded() == (_trade("P1", OrderStatus.FILLED, 100),)
    finally:
        restarted.dispose()


# 止损保护表：全部记录和历史，收盘对账用，见 add-close-reconciliation 的 design D7


def _day(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.fixture(params=["memory", "postgres"])
def timed_protections(request: pytest.FixtureRequest) -> tuple[ProtectionStore, FakeClock]:
    """带测试时钟的止损保护存储：保存的时刻由时钟决定，内存实现和数据库实现都测。"""
    clock = FakeClock(BASELINE_NOW)
    if request.param == "memory":
        return InMemoryProtectionStore(clock), clock
    database: Engine = request.getfixturevalue("database")
    return PostgresProtectionStore(database, clock), clock


def test_all_protections(protections: ProtectionStore) -> None:
    """全部股票的最新记录，包括不需要对齐的，按代码排序。"""
    ended = Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.ENDED))
    aaa = Protection("AAA", used=1, stop=_stop("AAA", 1, StopStatus.WORKING))
    protections.save(Protection("BBB", used=1, stop=_stop("BBB", 1, StopStatus.WORKING)))
    protections.save(ended)
    protections.save(aaa)

    assert protections.all() == (aaa, ended)


def test_protection_history(timed_protections: tuple[ProtectionStore, FakeClock]) -> None:
    """state-store spec「按时刻读历史」，用止损保护表。"""
    store, clock = timed_protections
    versions = [
        (_day(13, 15), Protection("BBB", used=1)),
        (_day(13, 19), Protection("BBB", used=2)),
        (_day(14, 15), Protection("BBB", used=3, unprotected_since=_day(14, 15))),
        (_day(14, 16), Protection("CCC", used=1)),
        (_day(12, 15), Protection("DDD", used=1)),
    ]
    for at, protection in versions:
        clock.set(at)
        store.save(protection)

    assert store.history(_day(13, 20, 10)) == (
        Protection("BBB", used=2),
        Protection("BBB", used=3, unprotected_since=_day(14, 15)),
        Protection("CCC", used=1),
        Protection("DDD", used=1),
    )


def test_protection_history_skips_unchanged_saves(
    timed_protections: tuple[ProtectionStore, FakeClock],
) -> None:
    """内容没变的保存不写新版本（state-store spec「内容没变时不写入」），历史里也没有它。"""
    store, clock = timed_protections
    store.save(Protection("BBB", used=1))
    clock.set(_day(14, 20, 30))
    store.save(Protection("BBB", used=1))

    assert store.history(_day(14, 20, 10)) == (Protection("BBB", used=1),)


def test_protection_history_of_an_empty_table(
    timed_protections: tuple[ProtectionStore, FakeClock],
) -> None:
    """state-store spec「空表」。"""
    store, _ = timed_protections

    assert store.history(_day(14, 20, 10)) == ()


# 收盘对账表


def _close(day: date = date(2026, 10, 14), **changes: Any) -> CloseReconciliation:
    """一条没有差异的收盘对账记录：1 只持仓，连续天数 1。"""
    record = CloseReconciliation(
        day=day,
        finished_at=datetime.combine(day, datetime.min.time(), UTC).replace(hour=20, minute=10),
        result=Reconciliation(),
        leftover_orders=(),
        new_stops=(),
        unprotected=(),
        holdings=1,
        resumed=False,
        streak=1,
    )
    return replace(record, **changes)


@pytest.fixture(params=["memory", "postgres"])
def closes(request: pytest.FixtureRequest) -> CloseReconciliationStore:
    """同一组场景同时测内存实现和数据库实现。"""
    if request.param == "memory":
        return InMemoryCloseReconciliationStore()
    database: Engine = request.getfixturevalue("database")
    return PostgresCloseReconciliationStore(database, FakeClock(BASELINE_NOW))


def test_close_records_by_day(closes: CloseReconciliationStore) -> None:
    """close-reconciliation spec「收盘对账记录」：按交易日保存和读取；没有记录的交易日为空。"""
    closes.save(_close(date(2026, 10, 13)))
    closes.save(_close(date(2026, 10, 14), streak=2))

    assert closes.get(date(2026, 10, 14)) == _close(date(2026, 10, 14), streak=2)
    assert closes.get(date(2026, 10, 13)) == _close(date(2026, 10, 13))
    assert closes.get(date(2026, 10, 15)) is None


def test_close_record_reads_back_unchanged(database: Engine) -> None:
    """close-reconciliation spec「记录原样读回」。"""
    record = _close(
        result=Reconciliation(
            stray_orders=(WorkingOrder("DDD", "X1", "B9"),),
            extra_stops=(WorkingOrder("EEE", "X2", "S9"),),
            unexplained=(("CCC", 100),),
        ),
        leftover_orders=(LeftoverOrder(UUID(int=1), "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),),
        new_stops=(NewStop("BBB", "BBB-STOP-2"),),
        unprotected=(UnprotectedSpell("BBB", _day(14, 15)),),
        holdings=2,
        resumed=True,
        streak=0,
    )
    store = PostgresCloseReconciliationStore(database, FakeClock(BASELINE_NOW))
    store.save(record)

    assert store.get(date(2026, 10, 14)) == record


def test_a_new_close_store_reads_the_same_records_after_a_restart(database: Engine) -> None:
    """close-reconciliation spec「收盘对账记录」：重启以后读到之前保存的记录。"""
    clock = FakeClock(BASELINE_NOW)
    PostgresCloseReconciliationStore(database, clock).save(_close())

    restarted = connect(database.url.render_as_string(hide_password=False))
    try:
        assert PostgresCloseReconciliationStore(restarted, clock).get(date(2026, 10, 14)) == (
            _close()
        )
    finally:
        restarted.dispose()
