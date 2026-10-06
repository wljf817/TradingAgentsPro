"""普通订单适配器 AlpacaBroker 的测试，场景见 alpaca-broker spec，Alpaca 由 FakeAlpaca 模拟。

基准数据见 spec「订单状态的映射」：每笔佣金 2；P1 买入 BBB 100 股，限价 50；P5 卖出 AAA 60 股，
市价单。
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from tests.integrations.alpaca.server import FakeAlpaca
from tradingagentspro.contracts.trade_plan import OrderType
from tradingagentspro.integrations.alpaca import AlpacaBroker, AlpacaClient, AlpacaConfig
from tradingagentspro.modules.execution import (
    Broker,
    BrokerAccount,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerUnavailableError,
    Order,
    OrderRejectedError,
    OrderSide,
    OrderStatus,
    WorkingOrder,
)

WORKING = BrokerOrderStatus.WORKING
NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)
CLOSE = datetime(2026, 10, 14, 20, tzinfo=UTC)

P1 = Order(
    plan_id=UUID(int=1),
    symbol="BBB",
    sector="半导体",
    side=OrderSide.BUY,
    order_type=OrderType.LIMIT,
    limit_price=Decimal(50),
    quantity=100,
    deadline=CLOSE,
    created_at=NOW,
)
P5 = Order(
    plan_id=UUID(int=5),
    symbol="AAA",
    sector="软件",
    side=OrderSide.SELL,
    order_type=OrderType.MARKET,
    limit_price=None,
    quantity=60,
    deadline=CLOSE,
    created_at=NOW,
)
P1_ID = P1.client_order_id
BY_CLIENT_ID = "/v2/orders:by_client_order_id"


def _broker(server: FakeAlpaca, commission: str = "2") -> Broker:
    """接上 FakeAlpaca 的适配器；返回类型是端口，由 mypy 确认适配器满足 Broker。"""
    config = AlpacaConfig(key_id="K1", secret_key=SecretStr("S1"))
    return AlpacaBroker(AlpacaClient(config, transport=server.transport()), Decimal(commission))


def _p1_at_alpaca(
    server: FakeAlpaca, status: str, filled: str = "0", price: str | None = None
) -> None:
    """Alpaca 有订单 O1：客户端订单 ID P1，BBB 100 股限价买单。"""
    server.add_order(
        id="O1",
        client_order_id=P1_ID,
        symbol="BBB",
        qty="100",
        side="buy",
        type="limit",
        limit_price="50",
        status=status,
        filled_qty=filled,
        filled_avg_price=price,
    )


def _submitted(order: Order) -> Order:
    """券商受理过、订单号 O1 的 P1。"""
    return replace(order, status=OrderStatus.SUBMITTED, broker_order_id="O1")


# 订单状态的映射


@pytest.mark.parametrize(
    "status",
    ["new", "accepted", "pending_new", "partially_filled", "pending_cancel", "done_for_day"],
)
def test_working_statuses(status: str) -> None:
    """spec「未结束的状态」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, status)

    report = _broker(server).find(P1)

    assert report is not None
    assert (report.broker_order_id, report.status) == ("O1", WORKING)


def test_filled() -> None:
    """spec「全部成交」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "filled", "100", "49.95")

    report = _broker(server).find(P1)

    assert report is not None
    assert report.status is BrokerOrderStatus.FILLED
    assert (report.filled, report.average_price) == (100, Decimal("49.95"))


@pytest.mark.parametrize("status", ["canceled", "expired"])
def test_cancelled_or_expired(status: str) -> None:
    """spec「撤销和失效」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, status, "30", "49.90")

    report = _broker(server).find(P1)

    assert report is not None
    assert report.status is BrokerOrderStatus.CANCELLED
    assert (report.filled, report.average_price) == (30, Decimal("49.90"))


def test_rejected() -> None:
    """spec「拒绝」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "rejected")

    report = _broker(server).find(P1)

    assert report is not None
    assert (report.status, report.filled) == (BrokerOrderStatus.REJECTED, 0)


def test_unrecognized_status() -> None:
    """spec「认不出的状态」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "some_new_status")

    report = _broker(server).find(P1)

    assert report is not None
    assert (report.broker_order_id, report.status) == ("O1", BrokerOrderStatus.UNKNOWN)


# 查询订单


def test_find_a_working_order_without_fills() -> None:
    """spec「已提交，没有成交」：按客户端订单 ID 查询。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "new")

    report = _broker(server).find(P1)

    assert report == BrokerOrder("O1", WORKING, 0, None, Decimal(0))
    assert server.last("GET", BY_CLIENT_ID).url.params["client_order_id"] == P1_ID


def test_find_a_partially_filled_order() -> None:
    """spec「部分成交」：成交均价按十进制精确读取。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "partially_filled", "30", "49.955")

    report = _broker(server).find(P1)

    assert report == BrokerOrder("O1", WORKING, 30, Decimal("49.955"), Decimal(2))


def test_find_nothing() -> None:
    """spec「查询订单」的查不到。"""
    assert _broker(FakeAlpaca()).find(P1) is None


# 提交当日有效单


def test_place_a_limit_buy() -> None:
    """spec「提交限价买单」。"""
    server = FakeAlpaca()
    server.next_ids, server.next_status = ["O1"], "pending_new"

    report = _broker(server).place(P1)

    assert server.body("POST", "/v2/orders") == {
        "client_order_id": P1_ID,
        "symbol": "BBB",
        "qty": "100",
        "side": "buy",
        "type": "limit",
        "limit_price": "50",
        "time_in_force": "day",
    }
    assert report == BrokerOrder("O1", WORKING, 0, None, Decimal(0))


def test_place_a_market_sell() -> None:
    """spec「提交市价卖单」。"""
    server = FakeAlpaca()
    server.next_ids, server.next_status = ["O5"], "accepted"

    report = _broker(server).place(P5)

    assert server.body("POST", "/v2/orders") == {
        "client_order_id": P5.client_order_id,
        "symbol": "AAA",
        "qty": "60",
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
    }
    assert (report.broker_order_id, report.status) == ("O5", WORKING)


def test_place_rejected_for_buying_power() -> None:
    """spec「购买力不足被拒」：带上 Alpaca 的说明。"""
    server = FakeAlpaca()
    server.respond_next("POST", "/v2/orders", 403, "insufficient buying power")

    with pytest.raises(OrderRejectedError, match="insufficient buying power"):
        _broker(server).place(P1)


# 撤单


def test_cancel_request_accepted() -> None:
    """spec「撤单请求被接受」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "new")

    _broker(server).cancel(_submitted(P1))

    assert server.count("DELETE", "/v2/orders/O1") == 1


def test_cancel_an_ended_order() -> None:
    """spec「订单已经结束」：Alpaca 返回 422，正常返回。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "filled", "100", "49.95")

    _broker(server).cancel(_submitted(P1))

    assert server.count("DELETE", "/v2/orders/O1") == 1


def test_cancel_an_order_alpaca_does_not_know() -> None:
    """spec「Alpaca 查不到订单号」：Alpaca 返回 404，正常返回。"""
    server = FakeAlpaca()

    _broker(server).cancel(_submitted(P1))

    assert server.count("DELETE", "/v2/orders/O1") == 1


# 持仓


def test_position_of_one_stock() -> None:
    """spec「查询一只股票的持仓」。"""
    server = FakeAlpaca()
    server.hold("BBB", "100")

    assert _broker(server).position("BBB") == 100


def test_no_position() -> None:
    """spec「持仓」的没有持仓：Alpaca 返回 404。"""
    assert _broker(FakeAlpaca()).position("CCC") == 0


def test_all_positions() -> None:
    """spec「全部持仓」。"""
    server = FakeAlpaca()
    server.hold("AAA", "60")
    server.hold("BBB", "100")

    assert _broker(server).all_positions() == {"AAA": 60, "BBB": 100}


# 账户


def test_account() -> None:
    """spec「读取账户」。"""
    server = FakeAlpaca()
    server.account |= {"equity": "100000", "cash": "40000", "last_equity": "101000"}
    server.hold("AAA", "60", avg_entry_price="98.5")
    server.hold("BBB", "100", avg_entry_price="49.95")

    assert _broker(server).account() == BrokerAccount(
        equity=Decimal(100000),
        cash=Decimal(40000),
        previous_close_equity=Decimal(101000),
        positions=(
            BrokerPosition("AAA", 60, Decimal("98.5")),
            BrokerPosition("BBB", 100, Decimal("49.95")),
        ),
    )


def test_account_without_positions() -> None:
    """spec「账户」的没有持仓。"""
    server = FakeAlpaca()
    server.account |= {"equity": "100000", "cash": "100000", "last_equity": "100000"}

    assert _broker(server).account().positions == ()


# 全部挂单


def test_working_orders_leave_out_stops() -> None:
    """spec「普通订单和止损单分开」：普通订单这一半。已经结束的订单不列出。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "partially_filled", "30", "49.90")
    server.add_order(
        id="O2", client_order_id="BBB-STOP-1", symbol="BBB", side="sell", type="stop", status="new"
    )
    server.add_order(
        id="O9",
        client_order_id="X1",
        symbol="CCC",
        side="sell",
        type="limit",
        status="pending_cancel",
    )
    server.add_order(id="O8", client_order_id="X2", symbol="DDD", type="limit", status="filled")

    assert _broker(server).working_orders() == (
        WorkingOrder("BBB", P1_ID, "O1"),
        WorkingOrder("CCC", "X1", "O9"),
    )


# 费用估算


def test_no_fees_without_fills() -> None:
    """spec「没有成交不收费」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "canceled")

    report = _broker(server).find(P1)

    assert report is not None
    assert report.fees == 0


def test_one_commission_with_fills() -> None:
    """spec「有成交收一笔」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "filled", "100", "49.95")

    report = _broker(server).find(P1)

    assert report is not None
    assert report.fees == Decimal(2)


def test_zero_commission() -> None:
    """spec「每笔佣金为 0」。"""
    server = FakeAlpaca()
    _p1_at_alpaca(server, "filled", "100", "49.95")

    report = _broker(server, commission="0").find(P1)

    assert report is not None
    assert report.fees == 0


# 调用失败的分类


def test_find_times_out() -> None:
    """spec「查询超时」。"""
    server = FakeAlpaca()
    server.raise_next("GET", BY_CLIENT_ID, httpx.ReadTimeout)

    with pytest.raises(BrokerUnavailableError):
        _broker(server).find(P1)


def test_working_orders_cannot_connect() -> None:
    """spec「连不上」。"""
    server = FakeAlpaca()
    server.raise_next("GET", "/v2/orders", httpx.ConnectError)

    with pytest.raises(BrokerUnavailableError):
        _broker(server).working_orders()


def test_place_rate_limited() -> None:
    """spec「限频」：报券商不可用，不报拒绝受理。"""
    server = FakeAlpaca()
    server.respond_next("POST", "/v2/orders", 429, "too many requests")

    with pytest.raises(BrokerUnavailableError):
        _broker(server).place(P1)


def test_account_server_error() -> None:
    """spec「服务端错误」。"""
    server = FakeAlpaca()
    server.respond_next("GET", "/v2/account", 503)

    with pytest.raises(BrokerUnavailableError):
        _broker(server).account()


def test_place_with_invalid_parameters() -> None:
    """spec「提交时参数不合规」。"""
    server = FakeAlpaca()
    server.respond_next("POST", "/v2/orders", 422, "invalid limit_price")

    with pytest.raises(OrderRejectedError):
        _broker(server).place(P1)


def test_wrong_api_key() -> None:
    """spec「API key 错误」：查询和提交都原样报错。"""
    server = FakeAlpaca()
    server.respond_next("GET", BY_CLIENT_ID, 401, "unauthorized")
    server.respond_next("POST", "/v2/orders", 401, "unauthorized")
    broker = _broker(server)

    with pytest.raises(httpx.HTTPStatusError):
        broker.find(P1)
    with pytest.raises(httpx.HTTPStatusError):
        broker.place(P1)


# Alpaca 的防自成交保护


def test_add_rejected_while_a_stop_is_working() -> None:
    """spec「持仓挂着止损单时加仓被拒」：报拒绝受理，止损单不变。"""
    server = FakeAlpaca()
    server.add_order(
        id="O2", client_order_id="BBB-STOP-1", symbol="BBB", side="sell", type="stop", status="new"
    )
    server.respond_next(
        "POST", "/v2/orders", 403, "potential wash trade detected. use complex orders"
    )

    with pytest.raises(OrderRejectedError, match="wash trade"):
        _broker(server).place(P1)
    assert server.orders["O2"]["status"] == "new"
    assert [r.method for r in server.requests] == ["POST"]
