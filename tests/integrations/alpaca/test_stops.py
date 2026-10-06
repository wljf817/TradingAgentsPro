"""止损单适配器 AlpacaStopBroker 的测试，场景见 alpaca-broker spec，Alpaca 由 FakeAlpaca 模拟。

基准数据见 spec「订单状态的映射」：每笔佣金 2；止损单 BBB-STOP-1 是 BBB 100 股，止损价 47。
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from tests.integrations.alpaca.server import FakeAlpaca
from tradingagentspro.integrations.alpaca import AlpacaClient, AlpacaConfig, AlpacaStopBroker
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    OrderRejectedError,
    StopBroker,
    StopOrder,
    StopReport,
    StopStatus,
    WorkingOrder,
)

WORKING = BrokerOrderStatus.WORKING
NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)
STOP_1 = StopOrder(
    symbol="BBB",
    client_order_id="BBB-STOP-1",
    quantity=100,
    stop_price=Decimal(47),
    created_at=NOW,
)


def _stops(server: FakeAlpaca) -> StopBroker:
    """接上 FakeAlpaca 的适配器；返回类型是端口，由 mypy 确认适配器满足 StopBroker。"""
    config = AlpacaConfig(key_id="K1", secret_key=SecretStr("S1"))
    return AlpacaStopBroker(AlpacaClient(config, transport=server.transport()), Decimal(2))


def _accepted(broker_order_id: str) -> StopOrder:
    """券商受理过、订单号是 broker_order_id 的 BBB-STOP-1。"""
    return replace(STOP_1, status=StopStatus.WORKING, broker_order_id=broker_order_id)


def _stop_at_alpaca(server: FakeAlpaca, order_id: str, **fields: Any) -> None:
    """Alpaca 有这张 BBB 的止损卖单：默认 100 股，止损价 47，状态 new。"""
    defaults = {
        "client_order_id": f"{order_id}-client",
        "symbol": "BBB",
        "side": "sell",
        "type": "stop",
        "qty": "100",
        "stop_price": "47",
        "limit_price": None,
        "time_in_force": "gtc",
        "status": "new",
    }
    server.add_order(id=order_id, **defaults | fields)


# 挂止损单


def test_place_a_stop() -> None:
    """spec「挂单」。"""
    server = FakeAlpaca()
    server.next_ids, server.next_status = ["O2"], "new"

    report = _stops(server).place(STOP_1)

    assert server.body("POST", "/v2/orders") == {
        "client_order_id": "BBB-STOP-1",
        "symbol": "BBB",
        "qty": "100",
        "side": "sell",
        "type": "stop",
        "stop_price": "47",
        "time_in_force": "gtc",
    }
    assert report == StopReport("O2", WORKING, 100, Decimal(47), 0, None, Decimal(0))


def test_place_rejected() -> None:
    """spec「挂单被拒」。"""
    server = FakeAlpaca()
    server.respond_next("POST", "/v2/orders", 403, "insufficient qty available for order")

    with pytest.raises(OrderRejectedError):
        _stops(server).place(STOP_1)


# 查询止损单


def test_find_by_broker_order_id() -> None:
    """spec「按订单号查询」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", client_order_id="BBB-STOP-1")

    report = _stops(server).find(_accepted("O2"))

    assert report == StopReport("O2", WORKING, 100, Decimal(47), 0, None, Decimal(0))
    assert server.count("GET", "/v2/orders/O2") == 1


def test_find_by_client_order_id_when_never_accepted() -> None:
    """spec「没受理过，按客户端订单 ID 查询」：挂单时超时，没有券商订单号。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", client_order_id="BBB-STOP-1", status="accepted")

    report = _stops(server).find(STOP_1)

    assert report is not None
    assert (report.broker_order_id, report.status, report.quantity) == ("O2", WORKING, 100)
    params = server.last("GET", "/v2/orders:by_client_order_id").url.params
    assert params["client_order_id"] == "BBB-STOP-1"


def test_find_a_filled_stop() -> None:
    """spec「止损单全部成交」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", status="filled", filled_qty="100", filled_avg_price="46.80")

    report = _stops(server).find(_accepted("O2"))

    assert report == StopReport(
        "O2", BrokerOrderStatus.FILLED, 0, Decimal(47), 100, Decimal("46.80"), Decimal(2)
    )


def test_find_nothing() -> None:
    """spec「查询止损单」的查不到：Alpaca 查这个订单号返回 404。"""
    assert _stops(FakeAlpaca()).find(_accepted("O2")) is None


# 改单产生的替换


def test_old_broker_order_id_leads_to_the_replacement() -> None:
    """spec「改过一次单，按旧订单号查到新单」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", status="replaced", replaced_by="O3")
    _stop_at_alpaca(server, "O3", qty="80", replaces="O2")

    report = _stops(server).find(_accepted("O2"))

    assert report == StopReport("O3", WORKING, 80, Decimal(47), 0, None, Decimal(0))


def test_fills_before_the_replacement_count() -> None:
    """spec「替换之前的成交计入累计成交」：两笔都有成交，也只收一笔佣金（spec「费用估算」）。"""
    server = FakeAlpaca()
    _stop_at_alpaca(
        server, "O2", status="replaced", filled_qty="10", filled_avg_price="47", replaced_by="O3"
    )
    _stop_at_alpaca(
        server,
        "O3",
        qty="90",
        status="partially_filled",
        filled_qty="30",
        filled_avg_price="46.50",
        replaces="O2",
    )

    report = _stops(server).find(_accepted("O3"))

    assert report == StopReport("O3", WORKING, 60, Decimal(47), 40, Decimal("46.625"), Decimal(2))


def test_replaced_twice() -> None:
    """spec「改了两次单」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", status="replaced", replaced_by="O3")
    _stop_at_alpaca(server, "O3", status="replaced", replaces="O2", replaced_by="O4")
    _stop_at_alpaca(server, "O4", qty="60", stop_price="46", replaces="O3")

    report = _stops(server).find(_accepted("O2"))

    assert report is not None
    assert (report.broker_order_id, report.quantity, report.stop_price) == (
        "O4",
        60,
        Decimal(46),
    )


def test_replacement_in_progress() -> None:
    """spec「改单产生的替换」的改单还在进行中：旧单还没指向新单。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", status="pending_replace")

    report = _stops(server).find(_accepted("O2"))

    assert report is not None
    assert (report.broker_order_id, report.status, report.quantity) == ("O2", WORKING, 100)


# 改单


def test_modify_accepted() -> None:
    """spec「改单请求被接受」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2")
    server.next_ids = ["O3"]

    _stops(server).modify(_accepted("O2"), 80, Decimal(47))

    assert server.body("PATCH", "/v2/orders/O2") == {"qty": "80", "stop_price": "47"}


def test_modify_rejected() -> None:
    """spec「改单被拒」：O2 正在改单，Alpaca 返回 422。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2", status="pending_replace")

    with pytest.raises(OrderRejectedError):
        _stops(server).modify(_accepted("O2"), 80, Decimal(47))


def test_modify_times_out() -> None:
    """spec「调用失败的分类」：改单超时报券商不可用。"""
    server = FakeAlpaca()
    server.raise_next("PATCH", "/v2/orders/O2", httpx.ReadTimeout)

    with pytest.raises(BrokerUnavailableError):
        _stops(server).modify(_accepted("O2"), 80, Decimal(47))


# 撤单


def test_cancel_a_stop() -> None:
    """spec「撤止损单」。"""
    server = FakeAlpaca()
    _stop_at_alpaca(server, "O2")

    _stops(server).cancel(_accepted("O2"))

    assert server.count("DELETE", "/v2/orders/O2") == 1


# 全部挂着的止损单


def test_working_stops_leave_out_other_orders() -> None:
    """spec「普通订单和止损单分开」：止损单这一半。"""
    server = FakeAlpaca()
    server.add_order(
        id="O1", client_order_id="P1", symbol="BBB", type="limit", status="partially_filled"
    )
    _stop_at_alpaca(server, "O2", client_order_id="BBB-STOP-1")
    server.add_order(id="O9", client_order_id="X1", symbol="CCC", type="limit", side="sell")
    _stop_at_alpaca(server, "O7", symbol="EEE", status="canceled")

    assert _stops(server).working_stops() == (WorkingOrder("BBB", "BBB-STOP-1", "O2"),)


def test_replacement_in_progress_counts_once() -> None:
    """spec「全部挂单」的改单还在进行中：只算替换它的新单。"""
    server = FakeAlpaca()
    _stop_at_alpaca(
        server, "O3", symbol="AAA", client_order_id="AAA-STOP-1", status="pending_replace"
    )
    _stop_at_alpaca(server, "O4", symbol="AAA", status="pending_new", replaces="O3")

    assert _stops(server).working_stops() == (WorkingOrder("AAA", "O4-client", "O4"),)


# Alpaca 的防自成交保护


def test_stop_rejected_while_a_buy_is_working() -> None:
    """spec「买单还挂着时止损单被拒」：报拒绝受理，买单不变。"""
    server = FakeAlpaca()
    server.add_order(
        id="O1",
        client_order_id="P1",
        symbol="BBB",
        type="limit",
        status="partially_filled",
        filled_qty="30",
    )
    server.respond_next(
        "POST", "/v2/orders", 403, "potential wash trade detected. use complex orders"
    )

    with pytest.raises(OrderRejectedError, match="wash trade"):
        _stops(server).place(replace(STOP_1, quantity=30))
    assert server.orders["O1"]["status"] == "partially_filled"
    assert [r.method for r in server.requests] == ["POST"]
