"""股票池的表：记录的版本表 stockpool_entries，以及 StockPoolStore 的数据库实现。

见 state-store spec、stock-pool spec「股票池的存储」和 update-stock-pool-storage 的 design D3。
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Engine, String

from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.modules.stockpool.domain.entry import PoolEntry
from tradingagentspro.shared.clock import Clock

ENTRIES = versioned_table(
    "stockpool_entries",
    Column("symbol", String, nullable=False),
    Column("in_pool", Boolean, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("removed_at", DateTime(timezone=True), nullable=True),
)


class PostgresStockPoolStore:
    """满足 StockPoolStore：记录存在状态库的 stockpool_entries 表里，键是股票代码。

    没有移出的记录 in_pool 为真；added_at、removed_at 是加入时刻和移出时刻，用来列出当天有增删的
    记录。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, ENTRIES, PoolEntry)

    def get(self, symbol: str) -> PoolEntry | None:
        return self._versions.get(symbol)

    def in_pool(self) -> tuple[PoolEntry, ...]:
        return self._versions.latest(in_pool=True)

    def changed_since(self, moment: datetime) -> tuple[PoolEntry, ...]:
        return self._versions.latest_since(moment, "added_at", "removed_at")

    def save(self, entry: PoolEntry) -> None:
        removal = entry.removal
        self._versions.save(
            entry.symbol,
            entry,
            in_pool=entry.in_pool,
            added_at=entry.added_at,
            removed_at=None if removal is None else removal.at,
        )
