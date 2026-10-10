"""执行模块的表：订单的版本表 execution_orders、止损保护的版本表 execution_protections、
交易记录的版本表 execution_trades、收盘对账记录的版本表 execution_close_reconciliations，以及
OrderStore、ProtectionStore、TradeStore、CloseReconciliationStore 的数据库实现。

见 state-store spec、add-state-store 的 design D1、add-trade-records 的 design D1 和
add-close-reconciliation 的 design D6、D7。
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Column, Date, Engine, String, Uuid

from tradingagentspro.modules.execution.domain.close import CloseReconciliation
from tradingagentspro.modules.execution.domain.order import Order
from tradingagentspro.modules.execution.domain.protection import Protection
from tradingagentspro.modules.execution.domain.trade import Trade
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

TRADES = versioned_table(
    "execution_trades",
    Column("client_order_id", String, nullable=False),
    Column("traded", Boolean, nullable=False),
)

CLOSE_RECONCILIATIONS = versioned_table(
    "execution_close_reconciliations",
    Column("day", Date, nullable=False),
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

    def all(self) -> tuple[Protection, ...]:
        return self._versions.latest()

    def history(self, since: datetime) -> tuple[Protection, ...]:
        return self._versions.history(since)

    def save(self, protection: Protection) -> None:
        self._versions.save(protection.symbol, protection, active=protection.is_active)


class PostgresTradeStore:
    """满足 TradeStore：交易记录存在 execution_trades 表里，累计成交大于 0 的 traded 为真。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, TRADES, Trade)

    def get(self, client_order_id: str) -> Trade | None:
        return self._versions.get(client_order_id)

    def traded(self) -> tuple[Trade, ...]:
        return self._versions.latest(traded=True)

    def save(self, trade: Trade) -> None:
        self._versions.save(trade.client_order_id, trade, traded=trade.filled > 0)


class PostgresCloseReconciliationStore:
    """满足 CloseReconciliationStore：记录存在 execution_close_reconciliations 表里，键是交易日。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, CLOSE_RECONCILIATIONS, CloseReconciliation)

    def get(self, day: date) -> CloseReconciliation | None:
        return self._versions.get(day)

    def save(self, record: CloseReconciliation) -> None:
        self._versions.save(record.day, record)
