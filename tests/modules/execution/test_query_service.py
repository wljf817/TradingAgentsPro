"""执行用例的只读查询：未结束的订单、一个方案的订单、需要对齐止损单的股票。

盘中巡检用它们（add-session-patrol 的 design D5、D6）。基准数据见 order-lifecycle spec，
不调用券商的查询用假券商的调用记录检查。
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.modules.execution.builders import (
    BASELINE_NOW,
    add_aaa,
    baseline_plan,
    decision,
    execution_service,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    ExecutionService,
    Protection,
    StopOrder,
    StopStatus,
)


def _service(
    store: InMemoryOrderStore | None = None,
    broker: FakeBroker | None = None,
    protections: InMemoryProtectionStore | None = None,
    clock: FakeClock | None = None,
    *,
    reconciled: bool = True,
) -> tuple[ExecutionService, FakeBroker]:
    broker = broker or FakeBroker()
    service = execution_service(
        store or InMemoryOrderStore(),
        broker,
        clock or FakeClock(BASELINE_NOW),
        stops=FakeStopBroker(),
        protections=protections,
        reconciled=reconciled,
    )
    return service, broker


def _submit(service: ExecutionService, plan: TradePlan) -> None:
    service.submit(plan, decision(quantity=plan.quantity or 100))


# 未结束的订单


def test_open_orders_lists_unfinished_orders_in_creation_order() -> None:
    clock = FakeClock(BASELINE_NOW)
    service, broker = _service(clock=clock)
    first, second, done = baseline_plan(), add_aaa(), baseline_plan(symbol="CCC")
    _submit(service, done)
    broker.update(str(done.plan_id), BrokerOrderStatus.FILLED, 100)
    service.track(done.plan_id)
    clock.advance(timedelta(minutes=1))
    _submit(service, second)
    clock.advance(timedelta(minutes=1))
    _submit(service, first)
    broker.calls.clear()

    orders = service.open_orders()

    assert [o.plan_id for o in orders] == [second.plan_id, first.plan_id]
    assert broker.calls == []


def test_open_orders_when_there_are_none() -> None:
    service, _ = _service()

    assert service.open_orders() == ()


# 一个方案的订单


def test_order_returns_finished_orders_too() -> None:
    service, broker = _service()
    plan = baseline_plan()
    _submit(service, plan)
    broker.update(str(plan.plan_id), BrokerOrderStatus.FILLED, 100, "49.95")
    service.track(plan.plan_id)
    broker.calls.clear()

    order = service.order(plan.plan_id)

    assert order is not None and order.filled == 100 and order.status.is_terminal
    assert broker.calls == []


def test_order_of_a_plan_without_an_order() -> None:
    service, _ = _service()

    assert service.order(baseline_plan().plan_id) is None


# 一只股票的止损保护


def test_protection_reads_the_record_without_calling_the_broker() -> None:
    protections = InMemoryProtectionStore()
    saved = Protection("WWW", used=1, stop=_stop("WWW", StopStatus.WORKING))
    protections.save(saved)
    service, broker = _service(protections=protections, reconciled=False)
    stops_calls = len(broker.calls)

    assert service.protection("WWW") == saved
    assert service.protection("ZZZ") is None
    assert len(broker.calls) == stops_calls


# 需要对齐止损单的股票


def _stop(symbol: str, status: StopStatus) -> StopOrder:
    return StopOrder(
        symbol=symbol,
        client_order_id=f"{symbol}-STOP-1",
        quantity=10,
        stop_price=Decimal(9),
        created_at=BASELINE_NOW,
        status=status,
    )


def test_symbols_to_protect_joins_positions_and_active_protections() -> None:
    protections = InMemoryProtectionStore()
    protections.save(Protection("WWW", used=1, stop=_stop("WWW", StopStatus.WORKING)))
    protections.save(Protection("UUU", used=1, stop=_stop("UUU", StopStatus.UNKNOWN)))
    protections.save(
        Protection(
            "NNN", used=1, stop=_stop("NNN", StopStatus.ENDED), unprotected_since=BASELINE_NOW
        )
    )
    protections.save(Protection("EEE", used=1, stop=_stop("EEE", StopStatus.ENDED)))
    broker = FakeBroker()
    broker.positions = {"ZZZ": 5, "WWW": 10, "AAA": 0}
    service, _ = _service(broker=broker, protections=protections, reconciled=False)

    symbols = service.symbols_to_protect()

    assert symbols == ("NNN", "UUU", "WWW", "ZZZ")
    assert broker.calls == [("all_positions", "*")]


def test_symbols_to_protect_when_reading_positions_fails() -> None:
    service, broker = _service()
    broker.fail_next("all_positions")

    with pytest.raises(BrokerUnavailableError):
        service.symbols_to_protect()
