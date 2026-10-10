"""测试用的 Alpaca：在内存里保存订单、持仓、账户和账户配置，由 httpx.MockTransport 把请求交给它。

只实现适配器用到的 9 个接口，行为按 Alpaca 文档，见 add-alpaca-paper-broker 的 design D1、D9。
"""

import json
from itertools import count
from typing import Any

import httpx

from tests.integrations.alpaca import payloads

# 已经结束的订单：不算挂着，不能撤单，也不能改单。
ENDED = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})
# 还没路由出去或者正在撤单、改单的订单，Alpaca 不允许改单。
NOT_REPLACEABLE = frozenset({"accepted", "pending_new", "pending_cancel", "pending_replace"})

type Planned = httpx.Response | type[httpx.TransportError]


class FakeAlpaca:
    """一个内存里的 Alpaca。

    - orders：按订单号保存的订单 JSON，测试可以直接改
    - positions：按股票保存的持仓 JSON；account：账户 JSON；configurations：账户配置
    - requests：收到的每个请求，按顺序；安排的失败也记录
    - 新订单的订单号先取 next_ids，用完以后依次是 N1、N2……；状态是 next_status，默认 accepted
    - 改单生成的新订单状态是 new，订单号的取法同上
    """

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.account = payloads.account()
        self.configurations = payloads.configurations()
        self.requests: list[httpx.Request] = []
        self.next_ids: list[str] = []
        self.next_status = "accepted"
        self._ids = count(1)
        self._planned: dict[tuple[str, str], list[Planned]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # 测试安排 Alpaca 那边的情况

    def add_order(self, **fields: Any) -> dict[str, Any]:
        """Alpaca 那边有这样一笔订单；没给的字段取 payloads.order 的默认值。"""
        order = payloads.order(**fields)
        self.orders[order["id"]] = order
        return order

    def hold(self, symbol: str, qty: str, avg_entry_price: str = "100.0") -> None:
        """Alpaca 那边持有这只股票。"""
        self.positions[symbol] = payloads.position(
            symbol=symbol, qty=qty, qty_available=qty, avg_entry_price=avg_entry_price
        )

    def respond_next(self, method: str, path: str, status: int, message: str = "") -> None:
        """下一次 method path 的请求直接返回这个状态码和 message，不做别的处理。"""
        response = httpx.Response(status, json={"code": int(f"{status}10000"), "message": message})
        self._planned.setdefault((method, path), []).append(response)

    def raise_next(self, method: str, path: str, error: type[httpx.TransportError]) -> None:
        """下一次 method path 的请求抛出这种连接错误，比如 httpx.ConnectTimeout。"""
        self._planned.setdefault((method, path), []).append(error)

    # 测试检查 Alpaca 收到的请求

    def last(self, method: str, path: str) -> httpx.Request:
        """最近一次 method path 的请求。"""
        return next(r for r in reversed(self.requests) if (r.method, r.url.path) == (method, path))

    def body(self, method: str, path: str) -> dict[str, Any]:
        """最近一次 method path 的请求的 JSON body。"""
        parsed: dict[str, Any] = json.loads(self.last(method, path).content)
        return parsed

    def count(self, method: str, path: str) -> int:
        """收到过几次 method path 的请求。"""
        return sum((r.method, r.url.path) == (method, path) for r in self.requests)

    # 处理请求

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        planned = self._planned.get((request.method, request.url.path))
        if planned:
            first = planned.pop(0)
            if isinstance(first, httpx.Response):
                return first
            raise first("模拟的连接失败", request=request)
        return self._route(request)

    def _route(self, request: httpx.Request) -> httpx.Response:
        match request.method, request.url.path.split("/")[2:]:
            case "GET", ["orders:by_client_order_id"]:
                client_order_id = request.url.params["client_order_id"]
                found = [o for o in self.orders.values() if o["client_order_id"] == client_order_id]
                return _ok(found[0]) if found else _error(404, "order not found")
            case "GET", ["orders"]:
                assert request.url.params["status"] == "open"
                assert request.url.params["limit"] == "500"
                return _ok([o for o in self.orders.values() if o["status"] not in ENDED])
            case "GET", ["orders", order_id]:
                return _ok(self.orders[order_id]) if order_id in self.orders else _not_found()
            case "POST", ["orders"]:
                return self._submit(json.loads(request.content))
            case "PATCH", ["orders", order_id]:
                return self._replace(order_id, json.loads(request.content))
            case "DELETE", ["orders", order_id]:
                return self._cancel(order_id)
            case "GET", ["positions"]:
                return _ok(list(self.positions.values()))
            case "GET", ["positions", symbol]:
                if symbol in self.positions:
                    return _ok(self.positions[symbol])
                return _error(404, "position does not exist")
            case "GET", ["account"]:
                return _ok(self.account)
            case "PATCH", ["account", "configurations"]:
                self.configurations |= json.loads(request.content)
                return _ok(self.configurations)
        raise AssertionError(f"FakeAlpaca 没有实现 {request.method} {request.url.path}")

    def _submit(self, body: dict[str, Any]) -> httpx.Response:
        if any(o["client_order_id"] == body["client_order_id"] for o in self.orders.values()):
            return _error(422, "client_order_id must be unique")
        order = self.add_order(
            id=self._next_id(),
            client_order_id=body["client_order_id"],
            symbol=body["symbol"],
            qty=body["qty"],
            side=body["side"],
            type=body["type"],
            order_type=body["type"],
            time_in_force=body["time_in_force"],
            limit_price=body.get("limit_price"),
            stop_price=body.get("stop_price"),
            status=self.next_status,
        )
        return _ok(order)

    def _replace(self, order_id: str, body: dict[str, Any]) -> httpx.Response:
        old = self.orders.get(order_id)
        if old is None:
            return _not_found()
        if old["status"] in ENDED | NOT_REPLACEABLE:
            return _error(422, f"order is {old['status']}, cannot be replaced")
        new_id = self._next_id()
        self.add_order(
            **old
            | body
            | {
                "id": new_id,
                "client_order_id": f"{new_id}-client",
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
                "replaces": order_id,
                "replaced_by": None,
            }
        )
        old |= {"status": "replaced", "replaced_by": new_id}
        return _ok(self.orders[new_id])

    def _cancel(self, order_id: str) -> httpx.Response:
        order = self.orders.get(order_id)
        if order is None:
            return _not_found()
        if order["status"] in ENDED or order["status"] == "pending_replace":
            # Alpaca 文档：正在改单（pending_replace）的订单拒绝撤单
            return _error(422, "order is not cancelable")
        order["status"] = "pending_cancel"
        return httpx.Response(204)

    def _next_id(self) -> str:
        return self.next_ids.pop(0) if self.next_ids else f"N{next(self._ids)}"


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json=data)


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"code": int(f"{status}10000"), "message": message})


def _not_found() -> httpx.Response:
    return _error(404, "order not found")
