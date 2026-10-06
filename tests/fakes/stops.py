"""测试用的止损单接口：按客户端订单 ID 保存券商那边的止损单，测试用它安排回报、拒绝和调用失败。"""

from dataclasses import replace
from decimal import Decimal
from itertools import count

from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    BrokerUnavailableError,
    OrderRejectedError,
    StopOrder,
    StopReport,
    WorkingOrder,
)


class FakeStopBroker:
    """满足执行模块的 StopBroker，用法和 FakeBroker 一致。

    - calls：按顺序记录每次调用的（操作, 客户端订单 ID），操作是 find、place、modify、cancel；
      读取全部挂着的止损单记为（working_stops, "*"）。失败和被拒的调用也记录
    - placed：券商受理的挂单，按顺序记录挂单时的止损单
    - modify_requests：被接受的改单请求（客户端订单 ID, 股数, 止损价）
    - cancel_requests：被接受的撤单请求的客户端订单 ID
    改单和撤单只记下请求，不改变止损单，由测试用 update 或 settle 决定什么时候生效
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.placed: list[StopOrder] = []
        self.modify_requests: list[tuple[str, int, Decimal]] = []
        self.cancel_requests: list[str] = []
        self._stops: dict[str, StopReport] = {}
        self._symbols: dict[str, str] = {}
        self._accepted: list[str] = []
        self._lost: set[str] = set()
        self._listed_while_lost: set[str] = set()
        self._extra: list[WorkingOrder] = []
        self._failures: dict[str, list[bool]] = {
            "find": [],
            "place": [],
            "modify": [],
            "cancel": [],
            "working_stops": [],
        }
        self._rejections: dict[str, int] = {"place": 0, "modify": 0}
        self._broker_ids = count(1)

    # 执行模块调用的操作

    def find(self, stop: StopOrder) -> StopReport | None:
        if self._call("find", stop):
            raise BrokerUnavailableError("查询超时")  # 查询没有副作用，生效前后失败一样
        if stop.client_order_id in self._lost:
            return None
        return self._stops.get(stop.client_order_id)

    def place(self, stop: StopOrder) -> StopReport:
        after_effect = self._call("place", stop)
        self._reject("place")
        accepted = self.accept(stop)
        self.placed.append(stop)
        if after_effect:
            raise BrokerUnavailableError("挂单的响应超时")
        return accepted

    def modify(self, stop: StopOrder, quantity: int, stop_price: Decimal) -> None:
        after_effect = self._call("modify", stop)
        self._reject("modify")
        self.modify_requests.append((stop.client_order_id, quantity, stop_price))
        if after_effect:
            raise BrokerUnavailableError("改单的响应超时")

    def cancel(self, stop: StopOrder) -> None:
        after_effect = self._call("cancel", stop)
        self.cancel_requests.append(stop.client_order_id)
        if after_effect:
            raise BrokerUnavailableError("撤单的响应超时")

    def working_stops(self) -> tuple[WorkingOrder, ...]:
        if self._record("working_stops", "*"):
            raise BrokerUnavailableError("读取全部止损单超时")
        ours = tuple(
            WorkingOrder(self._symbols[cid], cid, report.broker_order_id)
            for cid, report in self._stops.items()
            if report.status is BrokerOrderStatus.WORKING
            and (cid not in self._lost or cid in self._listed_while_lost)
        )
        return ours + tuple(self._extra)

    # 测试安排券商那边的情况

    def accept(self, stop: StopOrder) -> StopReport:
        """券商那边出现这张止损单：挂着，股数和止损价是挂单时的。不记为一次调用。"""
        accepted = StopReport(
            broker_order_id=f"S{next(self._broker_ids)}",
            status=BrokerOrderStatus.WORKING,
            quantity=stop.quantity,
            stop_price=stop.stop_price,
        )
        self._stops[stop.client_order_id] = accepted
        self._symbols[stop.client_order_id] = stop.symbol
        self._accepted.append(stop.client_order_id)
        return accepted

    def add_working(
        self, symbol: str, client_order_id: str, broker_order_id: str | None = None
    ) -> WorkingOrder:
        """券商另外挂着一张止损单：只出现在全部止损单里，按客户端订单 ID 查询时不会查到它。

        不记为一次调用。
        """
        working = WorkingOrder(
            symbol, client_order_id, broker_order_id or f"SX{len(self._extra) + 1}"
        )
        self._extra.append(working)
        return working

    def update(
        self,
        client_order_id: str,
        status: BrokerOrderStatus,
        quantity: int | None = None,
        stop_price: str | None = None,
        *,
        filled: int | None = None,
        average_price: str | None = None,
        fees: str | None = None,
    ) -> None:
        """券商回报变为……：之后查询时给出这个状态；没给的股数、止损价、累计成交、成交均价和
        累计费用不变。
        """
        current = self._stops[client_order_id]
        self._stops[client_order_id] = replace(
            current,
            status=status,
            quantity=current.quantity if quantity is None else quantity,
            stop_price=current.stop_price if stop_price is None else Decimal(stop_price),
            filled=current.filled if filled is None else filled,
            average_price=(
                current.average_price if average_price is None else Decimal(average_price)
            ),
            fees=current.fees if fees is None else Decimal(fees),
        )

    def settle(self) -> None:
        """让所有改单和撤单请求生效，按请求的顺序。"""
        for client_order_id, quantity, stop_price in self.modify_requests:
            if self._stops[client_order_id].status is BrokerOrderStatus.WORKING:
                self._stops[client_order_id] = replace(
                    self._stops[client_order_id], quantity=quantity, stop_price=stop_price
                )
        for client_order_id in self.cancel_requests:
            if self._stops[client_order_id].status is BrokerOrderStatus.WORKING:
                self.update(client_order_id, BrokerOrderStatus.CANCELLED)
        self.modify_requests.clear()
        self.cancel_requests.clear()

    def fail_next(self, operation: str, *, after_effect: bool = False) -> None:
        """下一次 operation 调用抛 BrokerUnavailableError。

        operation 是 find、place、modify、cancel 或 working_stops。
        after_effect 为真时，调用先生效再抛异常。同一种调用安排多次时按顺序排队。
        """
        self._failures[operation].append(after_effect)

    def clear_failures(self) -> None:
        """取消还没发生的失败和拒绝。"""
        for queued in self._failures.values():
            queued.clear()
        self._rejections = {"place": 0, "modify": 0}

    def reject_next(self, operation: str, times: int = 1) -> None:
        """接下来 times 次 operation（place 或 modify）被券商拒绝受理。"""
        self._rejections[operation] += times

    def lose(self, client_order_id: str, *, listed: bool = False) -> None:
        """券商按客户端订单 ID 查不到这张止损单，即使它受理过。

        listed 为真时，它仍挂着的话，全部止损单里还有它；默认全部止损单里也没有。
        """
        self._lost.add(client_order_id)
        if listed:
            self._listed_while_lost.add(client_order_id)

    # 测试检查券商那边的情况

    def stop(self, client_order_id: str) -> StopReport:
        return self._stops[client_order_id]

    def stop_count(self, client_order_id: str) -> int:
        """券商那边客户端订单 ID 是 client_order_id 的止损单有几张。"""
        return self._accepted.count(client_order_id)

    def working(self, symbol: str) -> dict[str, StopReport]:
        """这只股票挂着的止损单，按客户端订单 ID。"""
        return {
            client_order_id: report
            for client_order_id, report in self._stops.items()
            if self._symbols[client_order_id] == symbol
            and report.status is BrokerOrderStatus.WORKING
        }

    def calls_for(self, client_order_id: str) -> list[str]:
        """和这个客户端订单 ID 有关的调用，按顺序。"""
        return [op for op, cid in self.calls if cid == client_order_id]

    def _call(self, operation: str, stop: StopOrder) -> bool:
        """记下这次调用。安排了失败时：生效前失败直接抛异常；生效后失败返回 True，由调用方抛。"""
        return self._record(operation, stop.client_order_id)

    def _record(self, operation: str, key: str) -> bool:
        self.calls.append((operation, key))
        failures = self._failures[operation]
        if not failures:
            return False
        after_effect = failures.pop(0)
        if not after_effect:
            raise BrokerUnavailableError(f"{operation} 超时")
        return True

    def _reject(self, operation: str) -> None:
        if self._rejections[operation]:
            self._rejections[operation] -= 1
            raise OrderRejectedError(f"{operation} 被拒")
