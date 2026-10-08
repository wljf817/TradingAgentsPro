"""股票池：保存每只股票的状态（候选、关注、持仓）和记录，由代码强制执行加入和删除的校验、
冷却期、容量和关注名额、每天增删次数、升降级、候选超期移出和成交后的状态变化。

规则见 openspec 的 stock-pool spec。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.stockpool.config import StockPoolConfig
from tradingagentspro.modules.stockpool.domain.entry import (
    PoolEntry,
    PoolSource,
    Removal,
    RemovalKind,
)
from tradingagentspro.modules.stockpool.domain.rules import (
    Rejection,
    add_rejection,
    candidate_expiry,
    changes_since,
    cooling_down,
    promote_rejection,
    remove_rejection,
    status_rejection,
)
from tradingagentspro.modules.stockpool.ports import StockPoolStore
from tradingagentspro.modules.stockpool.service import StockPoolService

__all__ = [
    "PoolEntry",
    "PoolSource",
    "Rejection",
    "Removal",
    "RemovalKind",
    "StockPoolConfig",
    "StockPoolService",
    "StockPoolStore",
    "add_rejection",
    "candidate_expiry",
    "changes_since",
    "cooling_down",
    "promote_rejection",
    "remove_rejection",
    "status_rejection",
]
