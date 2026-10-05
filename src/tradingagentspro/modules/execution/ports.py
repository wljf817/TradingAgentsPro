"""执行模块需要的外部接口：券商、券商的止损单、订单存储和止损保护存储。"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from tradingagentspro.modules.execution.domain.order import BrokerOrder, Order
from tradingagentspro.modules.execution.domain.protection import Protection, StopOrder, StopReport
from tradingagentspro.modules.execution.domain.reconciliation import WorkingOrder


class BrokerUnavailableError(RuntimeError):
    """券商调用超时或断线：不知道这次调用有没有生效。"""


class OrderRejectedError(RuntimeError):
    """券商拒绝受理这次请求：提交时没有生成订单，改单时订单保持原样。"""


class Broker(Protocol):
    """券商的下单接口。订单都是当日有效单；客户端订单 ID 随订单交给券商，之后可以按它查到这笔订单。

    每个操作超时或断线时都抛 BrokerUnavailableError。
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

    def position(self, symbol: str) -> int:
        """券商那边这只股票的持仓股数；没有持仓时为 0。"""
        ...

    def working_orders(self) -> tuple[WorkingOrder, ...]:
        """券商那边全部挂着（已受理、未结束）的订单，不含止损单；包括部分成交、正在撤单的。"""
        ...

    def all_positions(self) -> Mapping[str, int]:
        """券商那边的全部持仓：股票到股数，只含股数大于 0 的。"""
        ...


class OrderStore(Protocol):
    """订单的存储：按方案 ID 保存每笔订单的最新状态。

    数据库实现只追加不覆盖：内容有变化时写入一个新版本，读取时取每笔订单的最新版本，
    见 state-store spec。
    """

    def get(self, plan_id: UUID) -> Order | None:
        """这个方案的订单，包括已经结束的；没有订单时为 None。"""
        ...

    def open_orders(self) -> tuple[Order, ...]:
        """全部没有进入终态的订单。"""
        ...

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        """这只股票的全部订单，包括已经结束的。"""
        ...

    def save(self, order: Order) -> None:
        """保存这笔订单的最新状态。"""
        ...


class StopBroker(Protocol):
    """券商的止损单接口：长期有效的止损市价卖单，只在常规交易时段触发。

    超时或断线时抛 BrokerUnavailableError；挂单、改单被券商拒绝受理时抛 OrderRejectedError。
    """

    def find(self, stop: StopOrder) -> StopReport | None:
        """这张止损单现在的情况；查不到时为 None。

        受理过的按券商订单号查，并且跟着改单产生的替换找到现在的那一笔；
        没受理过的按客户端订单 ID 查。
        """
        ...

    def place(self, stop: StopOrder) -> StopReport:
        """挂单，返回受理后的情况。"""
        ...

    def modify(self, stop: StopOrder, quantity: int, stop_price: Decimal) -> None:
        """请求改单为这个股数和止损价；结果由之后的查询得知。"""
        ...

    def cancel(self, stop: StopOrder) -> None:
        """请求撤单，结果由之后的查询得知；已经结束、撤不了时也正常返回。"""
        ...

    def working_stops(self) -> tuple[WorkingOrder, ...]:
        """券商那边全部挂着的止损单。改单还在替换过程中时，新旧两笔只算一张。"""
        ...


class ProtectionStore(Protocol):
    """止损保护的存储：按股票保存最新的记录。

    数据库实现只追加不覆盖：内容有变化时写入一个新版本，读取时取每只股票的最新版本，
    见 state-store spec。
    """

    def get(self, symbol: str) -> Protection | None:
        """这只股票的止损保护；没有记录时为 None。"""
        ...

    def active(self) -> tuple[Protection, ...]:
        """需要对齐的记录：Protection.is_active 为真的那些。"""
        ...

    def save(self, protection: Protection) -> None:
        """保存这只股票的最新记录。"""
        ...
