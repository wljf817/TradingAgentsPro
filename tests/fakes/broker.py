"""测试用的券商：按客户端订单 ID 保存券商那边的订单，测试用它安排回报、拒绝和调用失败。"""

from dataclasses import replace
from decimal import Decimal
from itertools import count

from tradingagentspro.modules.execution import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerUnavailableError,
    Order,
    OrderRejectedError,
    WorkingOrder,
)


class FakeBroker:
    """满足执行模块的 Broker。

    - calls：按顺序记录每次调用的（操作, 客户端订单 ID），操作是 find、place、cancel；
      查询持仓记为（position, 股票），读取全部挂单、全部持仓记为（working_orders, "*"）、
      （all_positions, "*"）。失败的调用也记录
    - positions：券商那边每只股票的持仓股数，由测试设定；没有设定的是 0
    - failures：安排的失败真正发生时，按顺序记录（操作, 是否生效后才失败）
    - placed：券商收到并受理的提交，按顺序记录提交时的订单
    - cancel_requests：被接受的撤单请求，按顺序记录客户端订单 ID。撤单只记下请求，不改变订单，
      由测试用 update 决定撤单什么时候生效
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failures: list[tuple[str, bool]] = []
        self.placed: list[Order] = []
        self.cancel_requests: list[str] = []
        self._orders: dict[str, BrokerOrder] = {}
        self._symbols: dict[str, str] = {}
        self._accepted: list[str] = []
        self._lost: set[str] = set()
        self._listed_while_lost: set[str] = set()
        self._extra: list[WorkingOrder] = []
        self.positions: dict[str, int] = {}
        self._failures: dict[str, list[bool]] = {
            "find": [],
            "place": [],
            "cancel": [],
            "position": [],
            "working_orders": [],
            "all_positions": [],
        }
        self._reject_next_place = False
        self._broker_ids = count(1)

    # 执行模块调用的操作

    def find(self, order: Order) -> BrokerOrder | None:
        if self._call("find", order):
            raise BrokerUnavailableError("查询超时")  # 查询没有副作用，生效前后失败一样
        if order.client_order_id in self._lost:
            return None
        return self._orders.get(order.client_order_id)

    def place(self, order: Order) -> BrokerOrder:
        after_effect = self._call("place", order)
        if self._reject_next_place:
            self._reject_next_place = False
            raise OrderRejectedError("购买力不足")
        accepted = self.accept(order)
        self.placed.append(order)
        if after_effect:
            raise BrokerUnavailableError("提交的响应超时")
        return accepted

    def cancel(self, order: Order) -> None:
        after_effect = self._call("cancel", order)
        self.cancel_requests.append(order.client_order_id)
        if after_effect:
            raise BrokerUnavailableError("撤单的响应超时")

    def position(self, symbol: str) -> int:
        if self._record("position", symbol):
            raise BrokerUnavailableError("查询持仓超时")  # 查询没有副作用，生效前后失败一样
        return self.positions.get(symbol, 0)

    def working_orders(self) -> tuple[WorkingOrder, ...]:
        if self._record("working_orders", "*"):
            raise BrokerUnavailableError("读取全部挂单超时")
        ours = tuple(
            WorkingOrder(self._symbols[cid], cid, report.broker_order_id)
            for cid, report in self._orders.items()
            if report.status is BrokerOrderStatus.WORKING
            and (cid not in self._lost or cid in self._listed_while_lost)
        )
        return ours + tuple(self._extra)

    def all_positions(self) -> dict[str, int]:
        if self._record("all_positions", "*"):
            raise BrokerUnavailableError("读取全部持仓超时")
        return {symbol: shares for symbol, shares in self.positions.items() if shares > 0}

    # 测试安排券商那边的情况

    def accept(self, order: Order) -> BrokerOrder:
        """券商那边出现这笔订单：已受理、没有成交。不记为一次调用。"""
        accepted = BrokerOrder(
            broker_order_id=f"B{next(self._broker_ids)}",
            status=BrokerOrderStatus.WORKING,
            filled=0,
            average_price=None,
        )
        self._orders[order.client_order_id] = accepted
        self._symbols[order.client_order_id] = order.symbol
        self._accepted.append(order.client_order_id)
        return accepted

    def add_working(
        self, symbol: str, client_order_id: str, broker_order_id: str | None = None
    ) -> WorkingOrder:
        """券商另外挂着一笔订单：只出现在全部挂单里，按客户端订单 ID 查询时不会查到它。

        不记为一次调用。
        """
        working = WorkingOrder(
            symbol, client_order_id, broker_order_id or f"X{len(self._extra) + 1}"
        )
        self._extra.append(working)
        return working

    def update(
        self,
        client_order_id: str,
        status: BrokerOrderStatus,
        filled: int,
        average_price: str | None = None,
    ) -> None:
        """券商回报变为……：之后查询时给出这个状态、累计成交和成交均价。"""
        self._orders[client_order_id] = replace(
            self._orders[client_order_id],
            status=status,
            filled=filled,
            average_price=None if average_price is None else Decimal(average_price),
        )

    def fail_next(self, operation: str, *, after_effect: bool = False) -> None:
        """下一次 operation 调用抛 BrokerUnavailableError。

        operation 是 find、place、cancel、position、working_orders 或 all_positions。

        after_effect 为真时，调用先生效再抛异常。同一种调用安排多次时按顺序排队，每次调用用掉一个。
        """
        self._failures[operation].append(after_effect)

    def clear_failures(self) -> None:
        """取消还没发生的失败。"""
        for queued in self._failures.values():
            queued.clear()

    def reject_next_place(self) -> None:
        """下一次提交被券商拒绝受理。"""
        self._reject_next_place = True

    def lose(self, client_order_id: str, *, listed: bool = False) -> None:
        """券商按客户端订单 ID 查不到这笔订单，即使它受理过。

        listed 为真时，它仍挂着的话，全部挂单里还有它；默认全部挂单里也没有。
        """
        self._lost.add(client_order_id)
        if listed:
            self._listed_while_lost.add(client_order_id)

    # 测试检查券商那边的情况

    def order_count(self, client_order_id: str) -> int:
        """券商那边客户端订单 ID 是 client_order_id 的订单有几笔。"""
        return self._accepted.count(client_order_id)

    def order(self, client_order_id: str) -> BrokerOrder:
        return self._orders[client_order_id]

    def calls_for(self, client_order_id: str) -> list[str]:
        """和这个客户端订单 ID 有关的调用，按顺序。"""
        return [op for op, cid in self.calls if cid == client_order_id]

    def _call(self, operation: str, order: Order) -> bool:
        """记下这次调用。安排了失败时：生效前失败直接抛异常；生效后失败返回 True，由调用方抛。"""
        return self._record(operation, order.client_order_id)

    def _record(self, operation: str, key: str) -> bool:
        self.calls.append((operation, key))
        failures = self._failures[operation]
        if not failures:
            return False
        after_effect = failures.pop(0)
        self.failures.append((operation, after_effect))
        if not after_effect:
            raise BrokerUnavailableError(f"{operation} 超时")
        return True
