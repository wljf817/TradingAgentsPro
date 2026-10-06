"""连真实 Alpaca 模拟盘的冒烟测试：验证 Alpaca 文档没写清、FakeAlpaca 只能照文档假设的行为。

见 add-alpaca-paper-broker 的 design D9。默认不运行，用 uv run pytest -m alpaca_paper 运行；
没有 APCA_API_KEY_ID、APCA_API_SECRET_KEY 时跳过。会在模拟盘上真的下单：开盘时买 1 股 SPY，
最后卖掉；休市时只跑不需要成交的部分。
"""

import os
import secrets
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from tradingagentspro.contracts.trade_plan import OrderType
from tradingagentspro.integrations.alpaca import (
    PAPER_URL,
    AlpacaBroker,
    AlpacaClient,
    AlpacaConfig,
    AlpacaStopBroker,
    configure_account,
)
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    Order,
    OrderRejectedError,
    OrderSide,
    OrderStatus,
    StopOrder,
    StopStatus,
)

KEY_ID = os.environ.get("APCA_API_KEY_ID", "")
SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY", "")

pytestmark = [
    pytest.mark.alpaca_paper,
    pytest.mark.skipif(not (KEY_ID and SECRET_KEY), reason="没有 Alpaca 模拟盘的 API key"),
]

SYMBOL = "SPY"
# 订单的截止时刻和创建时刻适配器用不到，随便给一个固定值。
SOME_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[AlpacaClient]:
    yield AlpacaClient(AlpacaConfig(key_id=KEY_ID, secret_key=SecretStr(SECRET_KEY)))


def _raw(path: str) -> httpx.Response:
    """不经过适配器直接读 Alpaca，用来核对。"""
    headers = {"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET_KEY}
    return httpx.get(f"{PAPER_URL}{path}", headers=headers, timeout=10).raise_for_status()


def _order(side: OrderSide, order_type: OrderType, limit_price: str | None = None) -> Order:
    """一笔 1 股 SPY 的订单，客户端订单 ID 每次都不同。"""
    return Order(
        plan_id=UUID(hex=secrets.token_hex(16)),
        symbol=SYMBOL,
        sector="指数",
        side=side,
        order_type=order_type,
        limit_price=None if limit_price is None else Decimal(limit_price),
        quantity=1,
        deadline=SOME_TIME,
        created_at=SOME_TIME,
    )


def _until(check: Callable[[], bool], seconds: int = 60) -> None:
    """每秒检查一次，直到 check 为真；超过 seconds 秒就失败。"""
    pause = threading.Event()
    for _ in range(seconds):
        if check():
            return
        pause.wait(1)
    pytest.fail(f"等了 {seconds} 秒仍然没有达到")


def test_reads_and_account_settings(client: AlpacaClient) -> None:
    """账户设置生效；读取账户、持仓、挂单不出错；查不到的客户端订单 ID 得到查不到。"""
    broker, stops = AlpacaBroker(client, Decimal(2)), AlpacaStopBroker(client, Decimal(2))

    configure_account(client)

    settings = _raw("/v2/account/configurations").json()
    assert (settings["no_shorting"], settings["max_margin_multiplier"]) == (True, "1")
    broker.account()
    broker.all_positions()
    broker.working_orders()
    stops.working_stops()
    assert broker.find(_order(OrderSide.BUY, OrderType.LIMIT, "1")) is None


def test_limit_buy_placed_found_and_cancelled(client: AlpacaClient) -> None:
    """远低于市价的限价买单：提交、按客户端订单 ID 查到、撤单后查到撤销。休市时也能跑。"""
    broker = AlpacaBroker(client, Decimal(2))
    order = _order(OrderSide.BUY, OrderType.LIMIT, "1")

    placed = broker.place(order)
    found = broker.find(order)
    broker.cancel(
        replace(order, status=OrderStatus.SUBMITTED, broker_order_id=placed.broker_order_id)
    )

    assert placed.status is BrokerOrderStatus.WORKING
    assert found is not None and found.broker_order_id == placed.broker_order_id

    def cancelled() -> bool:
        report = broker.find(order)
        return report is not None and report.status is BrokerOrderStatus.CANCELLED

    _until(cancelled)


def test_stop_lifecycle_while_market_is_open(client: AlpacaClient) -> None:
    """开盘时：买 1 股；挂止损单；挂着止损单时买单被拒（防自成交）；改单后查询跟着替换找到新单，
    全部挂着的止损单只有一张；撤止损单；卖掉这 1 股。
    """
    if not _raw("/v2/clock").json()["is_open"]:
        pytest.skip("休市，跳过需要成交的部分")
    broker, stops = AlpacaBroker(client, Decimal(2)), AlpacaStopBroker(client, Decimal(2))
    buy = _order(OrderSide.BUY, OrderType.MARKET)
    broker.place(buy)
    _until(lambda: (r := broker.find(buy)) is not None and r.status is BrokerOrderStatus.FILLED)
    bought = broker.find(buy)
    assert bought is not None and bought.average_price is not None
    stop = StopOrder(
        symbol=SYMBOL,
        client_order_id=f"{SYMBOL}-STOP-{secrets.token_hex(4)}",
        quantity=1,
        stop_price=(bought.average_price / 2).quantize(Decimal("0.01")),
        created_at=SOME_TIME,
    )
    try:
        placed = stops.place(stop)
        stop = replace(stop, status=StopStatus.WORKING, broker_order_id=placed.broker_order_id)
        _until(
            lambda: (o := client.order(placed.broker_order_id)) is not None and o.status == "new"
        )

        with pytest.raises(OrderRejectedError):
            broker.place(_order(OrderSide.BUY, OrderType.LIMIT, "1"))

        lower = (bought.average_price * Decimal("0.4")).quantize(Decimal("0.01"))
        stops.modify(stop, 1, lower)

        def replaced() -> bool:
            report = stops.find(stop)
            return report is not None and report.broker_order_id != placed.broker_order_id

        _until(replaced)
        report = stops.find(stop)
        assert report is not None
        assert (report.status, report.quantity, report.stop_price) == (
            BrokerOrderStatus.WORKING,
            1,
            lower,
        )
        assert [s.symbol for s in stops.working_stops()].count(SYMBOL) == 1
        stop = replace(stop, broker_order_id=report.broker_order_id)
    finally:
        if stop.broker_order_id is not None:  # 挂单失败时没有止损单要撤
            stops.cancel(stop)
            _until(
                lambda: (
                    (r := stops.find(stop)) is not None
                    and r.status is not BrokerOrderStatus.WORKING
                )
            )
        sell = _order(OrderSide.SELL, OrderType.MARKET)
        broker.place(sell)
        _until(
            lambda: (r := broker.find(sell)) is not None and r.status is BrokerOrderStatus.FILLED
        )
