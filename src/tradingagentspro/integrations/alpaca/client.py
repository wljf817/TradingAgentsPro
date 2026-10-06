"""Alpaca Trading API 的客户端：发请求、给调用失败分类、解析订单，沿改单产生的替换找到现在的那一笔。

见 alpaca-broker spec，以及 add-alpaca-paper-broker 的 design D1 到 D4、D7。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    OrderRejectedError,
)

PAPER_URL = "https://paper-api.alpaca.markets"

# 订单状态的映射，见 alpaca-broker spec「订单状态的映射」。replaced 不在这里：沿替换找到的现在这一笔
# 不会是 replaced；表里没有的状态是结果未知。
_STATUS = {
    **dict.fromkeys(
        (
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
        ),
        BrokerOrderStatus.WORKING,
    ),
    "filled": BrokerOrderStatus.FILLED,
    "canceled": BrokerOrderStatus.CANCELLED,
    "expired": BrokerOrderStatus.CANCELLED,
    "rejected": BrokerOrderStatus.REJECTED,
}


def broker_status(status: str) -> BrokerOrderStatus:
    """Alpaca 的订单状态对应的券商回报。"""
    return _STATUS.get(status, BrokerOrderStatus.UNKNOWN)


class AlpacaConfig(BaseModel):
    """Alpaca 的配置，见 alpaca-broker spec「Alpaca 配置」。每笔佣金不在这里，由适配器另外接收。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = PAPER_URL
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class _Model(BaseModel):
    """Alpaca 的回报：字段很多，只取用到的；股数和价格是字符串，按 Decimal 精确读取。"""

    model_config = ConfigDict(frozen=True, extra="ignore")


class AlpacaOrder(_Model):
    """Alpaca 的一笔订单。qty 按 Alpaca 的定义可以为空（名义金额订单），本系统的订单都有股数。"""

    id: str
    client_order_id: str
    symbol: str
    status: str
    type: str
    side: str
    qty: Decimal | None
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    stop_price: Decimal | None
    replaced_by: str | None
    replaces: str | None


class AlpacaPosition(_Model):
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal


class AlpacaAccount(_Model):
    equity: Decimal
    cash: Decimal
    last_equity: Decimal  # 前一交易日收盘时的权益


_ORDERS = TypeAdapter(tuple[AlpacaOrder, ...])
_POSITIONS = TypeAdapter(tuple[AlpacaPosition, ...])


@dataclass(frozen=True, slots=True)
class Chain:
    """一笔订单连同改单产生的全部替换，见 design D4。

    - current：没有被替换的那一笔，也就是现在的这一笔
    - filled：整条链的已成交股数之和
    - average_price：按每一笔的已成交股数加权的成交均价；没有成交时为 None
    """

    current: AlpacaOrder
    filled: int
    average_price: Decimal | None

    @classmethod
    def of(cls, orders: Sequence[AlpacaOrder]) -> Self:
        """orders 按替换的先后排列，最后一笔是现在的这一笔。"""
        filled = sum((o.filled_qty for o in orders), Decimal(0))
        amount = sum(
            (o.filled_qty * o.filled_avg_price for o in orders if o.filled_avg_price is not None),
            Decimal(0),
        )
        return cls(orders[-1], int(filled), amount / filled if filled else None)

    def fees(self, commission: Decimal) -> Decimal:
        """估算的累计费用：整条链有成交时收一笔佣金，见 alpaca-broker spec「费用估算」。"""
        return commission if self.filled > 0 else Decimal(0)


class AlpacaClient:
    """Alpaca Trading API 的同步客户端，两个适配器共用一个。

    调用失败按 alpaca-broker spec「调用失败的分类」处理：超时、连不上、429 和 5xx 抛
    BrokerUnavailableError；提交和改单的其他 4xx（401 除外）抛 OrderRejectedError；查询的 404
    给出 None；撤单的 404 和 422 正常返回；其余的 4xx 原样抛 httpx.HTTPStatusError。
    """

    def __init__(self, config: AlpacaConfig, transport: httpx.BaseTransport | None = None) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Alpaca。"""
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={
                "APCA-API-KEY-ID": config.key_id,
                "APCA-API-SECRET-KEY": config.secret_key.get_secret_value(),
            },
            timeout=config.timeout.total_seconds(),
            transport=transport,
        )

    def order(self, order_id: str) -> AlpacaOrder | None:
        """按订单号查询；查不到时为 None。"""
        return self._find(self._send("GET", f"/v2/orders/{order_id}"))

    def order_by_client_id(self, client_order_id: str) -> AlpacaOrder | None:
        """按客户端订单 ID 查询；查不到时为 None。"""
        response = self._send(
            "GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}
        )
        return self._find(response)

    def submit(self, order: Mapping[str, str]) -> AlpacaOrder:
        """提交一笔订单，返回受理后的订单。"""
        response = self._send("POST", "/v2/orders", json=order)
        _reject_client_errors(response)
        return AlpacaOrder.model_validate_json(response.content)

    def replace(self, order_id: str, changes: Mapping[str, str]) -> None:
        """改单：Alpaca 生成一笔新订单替换它。"""
        _reject_client_errors(self._send("PATCH", f"/v2/orders/{order_id}", json=changes))

    def cancel(self, order_id: str) -> None:
        """请求撤单；订单已经结束（422）或者查不到（404）时也正常返回。"""
        response = self._send("DELETE", f"/v2/orders/{order_id}")
        if response.status_code not in (404, 422):
            response.raise_for_status()

    def open_orders(self) -> tuple[AlpacaOrder, ...]:
        """全部未结束的订单，包括部分成交、正在撤单和正在改单的；只读一页，最多 500 笔。"""
        response = self._send("GET", "/v2/orders", params={"status": "open", "limit": "500"})
        return _ORDERS.validate_json(response.raise_for_status().content)

    def position(self, symbol: str) -> AlpacaPosition | None:
        """这只股票的持仓；没有持仓时为 None。"""
        response = self._send("GET", f"/v2/positions/{symbol}")
        if response.status_code == 404:
            return None
        return AlpacaPosition.model_validate_json(response.raise_for_status().content)

    def positions(self) -> tuple[AlpacaPosition, ...]:
        response = self._send("GET", "/v2/positions")
        return _POSITIONS.validate_json(response.raise_for_status().content)

    def account(self) -> AlpacaAccount:
        response = self._send("GET", "/v2/account")
        return AlpacaAccount.model_validate_json(response.raise_for_status().content)

    def configure(self, settings: Mapping[str, Any]) -> None:
        """修改账户配置，只改给出的几项。"""
        self._send("PATCH", "/v2/account/configurations", json=settings).raise_for_status()

    def chain(self, found: AlpacaOrder) -> Chain:
        """从查到的这一笔往前沿 replaces、往后沿 replaced_by，找出整条替换链，每一跳查一次。"""
        orders = [found]
        while (earlier := orders[0].replaces) is not None:
            orders.insert(0, self._existing(earlier))
        while (later := orders[-1].replaced_by) is not None:
            orders.append(self._existing(later))
        return Chain.of(orders)

    def _existing(self, order_id: str) -> AlpacaOrder:
        order = self.order(order_id)
        assert order is not None, f"替换链上的订单 {order_id} 查不到"
        return order

    def _find(self, response: httpx.Response) -> AlpacaOrder | None:
        if response.status_code == 404:
            return None
        return AlpacaOrder.model_validate_json(response.raise_for_status().content)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """发出请求。超时、连不上、429 和 5xx 抛 BrokerUnavailableError，其余的交给调用方。"""
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as error:  # 超时也是 TransportError
            raise BrokerUnavailableError(f"{method} {path}：{error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise BrokerUnavailableError(f"{method} {path}：{response.status_code}")
        return response


def configure_account(client: AlpacaClient) -> None:
    """把账户设为不做空、最大保证金倍数 1，按现金账户使用；可以重复调用。

    见 alpaca-broker spec「账户设置」和 design D7。由装配在启动时、对账之前调用。
    """
    client.configure({"no_shorting": True, "max_margin_multiplier": "1"})


def _reject_client_errors(response: httpx.Response) -> None:
    """提交和改单：401 以外的 4xx 是拒绝受理，401 和其他错误原样抛出。"""
    if 400 <= response.status_code < 500 and response.status_code != 401:
        raise OrderRejectedError(f"{response.status_code} {response.text}")
    response.raise_for_status()
