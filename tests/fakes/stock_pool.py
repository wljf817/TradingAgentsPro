"""测试用的股票池存储：存在内存里，按股票代码保存每只股票最新的记录，包括已经移出的。"""

from datetime import datetime

from tradingagentspro.modules.stockpool import PoolEntry


class InMemoryStockPoolStore:
    """满足股票池模块的 StockPoolStore。"""

    def __init__(self) -> None:
        self._entries: dict[str, PoolEntry] = {}

    def get(self, symbol: str) -> PoolEntry | None:
        return self._entries.get(symbol)

    def in_pool(self) -> tuple[PoolEntry, ...]:
        return tuple(e for e in self._entries.values() if e.in_pool)

    def changed_since(self, moment: datetime) -> tuple[PoolEntry, ...]:
        return tuple(
            e
            for e in self._entries.values()
            if e.added_at >= moment or (e.removal is not None and e.removal.at >= moment)
        )

    def save(self, entry: PoolEntry) -> None:
        self._entries[entry.symbol] = entry
