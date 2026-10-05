"""执行模块的表：订单的版本表 execution_orders、止损保护的版本表 execution_protections，
以及 OrderStore、ProtectionStore 的数据库实现。

见 state-store spec 和 add-state-store 的 design D1。
"""

from uuid import UUID

from sqlalchemy import Boolean, Column, Engine, String, Uuid

from tradingagentspro.modules.execution.domain.order import Order
from tradingagentspro.modules.execution.domain.protection import Protection
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

ORDERS = versioned_table(
    "execution_orders",
    Column("plan_id", Uuid, nullable=False),
    Column("symbol", String, nullable=False),
    Column("active", Boolean, nullable=False),
)

PROTECTIONS = versioned_table(
    "execution_protections",
    Column("symbol", String, nullable=False),
    Column("active", Boolean, nullable=False),
)


class PostgresOrderStore:
    """满足 OrderStore：订单存在状态库的 execution_orders 表里，没有进入终态的订单 active 为真。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, ORDERS, Order)

    def get(self, plan_id: UUID) -> Order | None:
        return self._versions.get(plan_id)

    def open_orders(self) -> tuple[Order, ...]:
        return self._versions.latest(active=True)

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        return self._versions.latest(symbol=symbol)

    def save(self, order: Order) -> None:
        self._versions.save(
            order.plan_id, order, symbol=order.symbol, active=not order.status.is_terminal
        )


class PostgresProtectionStore:
    """满足 ProtectionStore：止损保护存在状态库的 execution_protections 表里。

    需要对齐的记录（Protection.is_active）active 为真。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, PROTECTIONS, Protection)

    def get(self, symbol: str) -> Protection | None:
        return self._versions.get(symbol)

    def active(self) -> tuple[Protection, ...]:
        return self._versions.latest(active=True)

    def save(self, protection: Protection) -> None:
        self._versions.save(protection.symbol, protection, active=protection.is_active)
