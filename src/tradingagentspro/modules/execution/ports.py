"""执行模块需要的外部接口：券商和订单存储。"""

from typing import Protocol
from uuid import UUID

from tradingagentspro.modules.execution.domain.order import BrokerOrder, Order


class BrokerUnavailableError(RuntimeError):
    """券商调用超时或断线：不知道这次调用有没有生效。"""


class OrderRejectedError(RuntimeError):
    """券商拒绝受理，没有生成订单。"""


class Broker(Protocol):
    """券商的下单接口。订单都是当日有效单，客户端订单 ID 写进订单的备注。

    三个操作超时或断线时都抛 BrokerUnavailableError。
    """

    def find(self, order: Order) -> BrokerOrder | None:
        """按客户端订单 ID 查这笔订单，范围覆盖订单创建的那个交易日；没有时为 None。"""
        ...

    def place(self, order: Order) -> BrokerOrder:
        """提交当日有效单，返回受理后的状态；券商拒绝受理时抛 OrderRejectedError。"""
        ...

    def cancel(self, order: Order) -> None:
        """按券商订单号请求撤单，结果由之后的查询得知。

        请求被接受就返回；订单已经结束、撤不了时也正常返回。
        """
        ...


class OrderStore(Protocol):
    """订单的存储：按方案 ID 保存每笔订单的最新状态。

    数据库实现只追加不覆盖：每次保存写入一个新版本，读取时取每笔订单的最新版本。
    """

    def get(self, plan_id: UUID) -> Order | None:
        """这个方案的订单，包括已经结束的；没有订单时为 None。"""
        ...

    def open_orders(self) -> tuple[Order, ...]:
        """全部没有进入终态的订单。"""
        ...

    def save(self, order: Order) -> None:
        """保存这笔订单的最新状态。"""
        ...
