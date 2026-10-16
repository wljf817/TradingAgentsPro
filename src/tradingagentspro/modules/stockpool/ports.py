"""股票池模块需要的外部接口。"""

from datetime import datetime
from typing import Protocol

from tradingagentspro.modules.stockpool.domain.entry import PoolEntry


class StockPoolStore(Protocol):
    """股票池的存储：按股票代码保存每只股票最新的记录，包括已经移出的。

    数据库实现在 repo.py，只追加不覆盖：内容有变化时写入一个新版本，读取时取每只股票的最新版本，
    见 state-store spec。数据库的错误原样抛出，不当作没有记录。
    """

    def get(self, symbol: str) -> PoolEntry | None:
        """这只股票最新的记录，包括已经移出的；从来没有加入过时为 None。"""
        ...

    def in_pool(self) -> tuple[PoolEntry, ...]:
        """没有移出的全部记录。"""
        ...

    def changed_since(self, moment: datetime) -> tuple[PoolEntry, ...]:
        """加入时刻或移出时刻不早于 moment 的记录，用来数当天的增删次数。"""
        ...

    def save(self, entry: PoolEntry) -> None:
        """保存这只股票最新的记录。"""
        ...
