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
)


class FakeBroker:
    """满足执行模块的 Broker。

    - calls：按顺序记录每次调用的（操作, 客户端订单 ID），操作是 find、place、cancel；
      失败的调用也记录
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
        self._accepted: list[str] = []
        self._lost: set[str] = set()
        self._failures: dict[str, list[bool]] = {"find": [], "place": [], "cancel": []}
        self._reject_next_place = False
        self._broker_ids = count(1)

    # 执行模块调用的三个操作

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
        self._accepted.append(order.client_order_id)
        return accepted

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
        """下一次 operation（find、place 或 cancel）调用抛 BrokerUnavailableError。

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

    def lose(self, client_order_id: str) -> None:
        """券商查不到这笔订单，即使它受理过。"""
        self._lost.add(client_order_id)

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
        self.calls.append((operation, order.client_order_id))
        failures = self._failures[operation]
        if not failures:
            return False
        after_effect = failures.pop(0)
        self.failures.append((operation, after_effect))
        if not after_effect:
            raise BrokerUnavailableError(f"{operation} 超时")
        return True
