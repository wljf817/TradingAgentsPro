"""Alpaca 客户端的测试：配置、调用失败的分类、状态映射、订单解析和沿替换找订单。

见 add-alpaca-paper-broker 的 design D2 到 D4，以及 alpaca-broker spec「Alpaca 配置」
「订单状态的映射」「改单产生的替换」。Alpaca 由 FakeAlpaca 模拟。
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from tests.integrations.alpaca import payloads
from tests.integrations.alpaca.server import FakeAlpaca
from tradingagentspro.integrations.alpaca.client import (
    PAPER_URL,
    AlpacaClient,
    AlpacaConfig,
    AlpacaOrder,
    broker_status,
)
from tradingagentspro.modules.execution import BrokerOrderStatus, BrokerUnavailableError

WORKING = BrokerOrderStatus.WORKING


def _client(server: FakeAlpaca, base_url: str = PAPER_URL) -> AlpacaClient:
    settings = AlpacaConfig(key_id="K1", secret_key=SecretStr("S1"), base_url=base_url)
    return AlpacaClient(settings, transport=server.transport())


# 配置


def test_default_address_and_timeout() -> None:
    """spec「Alpaca 配置」的默认值：请求发往模拟盘地址，超时时长 10 秒。"""
    server = FakeAlpaca()

    _client(server).account()

    request = server.last("GET", "/v2/account")
    assert PAPER_URL == "https://paper-api.alpaca.markets"
    assert str(request.url) == f"{PAPER_URL}/v2/account"
    assert request.extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }


def test_configured_address() -> None:
    server = FakeAlpaca()

    _client(server, base_url="https://api.alpaca.markets").account()

    assert str(server.last("GET", "/v2/account").url) == "https://api.alpaca.markets/v2/account"


def test_requests_carry_the_keys() -> None:
    """spec「请求带上认证」。"""
    server = FakeAlpaca()

    _client(server).account()

    headers = server.last("GET", "/v2/account").headers
    assert headers["APCA-API-KEY-ID"] == "K1"
    assert headers["APCA-API-SECRET-KEY"] == "S1"


def test_timeout_must_be_positive() -> None:
    """spec「超时时长不合法」。"""
    with pytest.raises(ValidationError):
        AlpacaConfig(key_id="K1", secret_key=SecretStr("S1"), timeout=timedelta(0))


def test_key_id_is_required() -> None:
    with pytest.raises(ValidationError):
        AlpacaConfig(key_id="", secret_key=SecretStr("S1"))


# 调用失败的分类


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_connection_failures_mean_the_broker_is_unavailable(
    error: type[httpx.TransportError],
) -> None:
    server = FakeAlpaca()
    server.raise_next("GET", "/v2/account", error)

    with pytest.raises(BrokerUnavailableError):
        _client(server).account()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limits_and_server_errors_mean_the_broker_is_unavailable(status: int) -> None:
    server = FakeAlpaca()
    server.respond_next("GET", "/v2/account", status)

    with pytest.raises(BrokerUnavailableError):
        _client(server).account()


def test_other_client_errors_are_raised_as_is() -> None:
    server = FakeAlpaca()
    server.respond_next("GET", "/v2/account", 403, "forbidden")

    with pytest.raises(httpx.HTTPStatusError):
        _client(server).account()


# 状态映射


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        *(
            (status, WORKING)
            for status in [
                "new",
                "accepted",
                "pending_new",
                "accepted_for_bidding",
                "partially_filled",
                "done_for_day",
                "pending_cancel",
                "pending_replace",
                "stopped",
                "suspended",
                "calculated",
                "held",
            ]
        ),
        ("filled", BrokerOrderStatus.FILLED),
        ("canceled", BrokerOrderStatus.CANCELLED),
        ("expired", BrokerOrderStatus.CANCELLED),
        ("rejected", BrokerOrderStatus.REJECTED),
        ("some_new_status", BrokerOrderStatus.UNKNOWN),
    ],
)
def test_status_mapping(status: str, expected: BrokerOrderStatus) -> None:
    """spec「订单状态的映射」的表，加上表里没有的状态。"""
    assert broker_status(status) is expected


# 订单解析


def test_numbers_are_read_exactly() -> None:
    order = AlpacaOrder.model_validate(
        payloads.order(qty="100", filled_qty="30", filled_avg_price="49.955")
    )

    assert order.qty == 100
    assert order.filled_qty == 30
    assert order.filled_avg_price == Decimal("49.955")
    assert str(order.filled_avg_price) == "49.955"


def test_no_average_price_without_fills() -> None:
    order = AlpacaOrder.model_validate(payloads.order(filled_qty="0", filled_avg_price=None))

    assert order.filled_avg_price is None


# 沿替换找订单


def _stop(server: FakeAlpaca, order_id: str, **fields: str | None) -> AlpacaOrder:
    """Alpaca 那边有这张 BBB 的止损卖单，返回解析后的订单。"""
    defaults: dict[str, str | None] = {
        "symbol": "BBB",
        "side": "sell",
        "type": "stop",
        "qty": "100",
        "stop_price": "47",
        "limit_price": None,
        "status": "new",
    }
    return AlpacaOrder.model_validate(server.add_order(id=order_id, **defaults | fields))


def test_an_order_never_replaced_needs_no_more_requests() -> None:
    server = FakeAlpaca()
    o2 = _stop(server, "O2")

    chain = _client(server).chain(o2)

    assert chain.current == o2
    assert (chain.filled, chain.average_price) == (0, None)
    assert server.requests == []


def test_follows_replacements_to_the_current_order() -> None:
    """spec「改了两次单」。"""
    server = FakeAlpaca()
    o2 = _stop(server, "O2", status="replaced", replaced_by="O3")
    _stop(server, "O3", status="replaced", replaces="O2", replaced_by="O4")
    _stop(server, "O4", qty="60", stop_price="46", replaces="O3")

    chain = _client(server).chain(o2)

    assert chain.current.id == "O4"
    assert (chain.current.qty, chain.current.stop_price) == (Decimal(60), Decimal(46))


@pytest.mark.parametrize("start", ["O2", "O3"])
def test_fills_add_up_across_the_whole_chain(start: str) -> None:
    """spec「替换之前的成交计入累计成交」：从哪一笔查起都一样。"""
    server = FakeAlpaca()
    _stop(
        server,
        "O2",
        status="replaced",
        filled_qty="10",
        filled_avg_price="47",
        replaced_by="O3",
    )
    _stop(
        server,
        "O3",
        qty="90",
        status="partially_filled",
        filled_qty="30",
        filled_avg_price="46.50",
        replaces="O2",
    )
    found = AlpacaOrder.model_validate(server.orders[start])

    chain = _client(server).chain(found)

    assert chain.current.id == "O3"
    assert chain.filled == 40
    assert chain.average_price == Decimal("46.625")


def test_a_replacement_in_progress_keeps_the_old_order() -> None:
    """spec「改单还在进行中」：旧单还没指向新单。"""
    server = FakeAlpaca()
    o2 = _stop(server, "O2", status="pending_replace")

    chain = _client(server).chain(o2)

    assert chain.current == o2
