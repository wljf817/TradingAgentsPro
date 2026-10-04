"""测试用的订单存储：存在内存里，按方案 ID 保存每笔订单的最新状态。"""

from uuid import UUID

from tradingagentspro.modules.execution import Order


class InMemoryOrderStore:
    """满足执行模块的 OrderStore。用同一个实例重新创建执行用例，相当于进程重启。"""

    def __init__(self) -> None:
        self._orders: dict[UUID, Order] = {}

    def get(self, plan_id: UUID) -> Order | None:
        return self._orders.get(plan_id)

    def open_orders(self) -> tuple[Order, ...]:
        return tuple(o for o in self._orders.values() if not o.status.is_terminal)

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        return tuple(o for o in self._orders.values() if o.symbol == symbol)

    def save(self, order: Order) -> None:
        self._orders[order.plan_id] = order
