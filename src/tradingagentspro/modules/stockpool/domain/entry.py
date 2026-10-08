"""股票池的记录：一只股票一条，以及按 stock-pool spec「状态变化」表改变状态的方法。

移出以后记录保留，带着移出的时刻和原因，用来判断冷却期；重新加入时换成一条新记录。
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import WatchThesis

_MIN_SCORE, _MAX_SCORE = -2, 2


class PoolSource(StrEnum):
    """股票是怎么加入股票池的。"""

    EXPLORATION = "exploration"  # PM 探索
    MANUAL = "manual"  # 手动添加


class RemovalKind(StrEnum):
    """移出的原因。三种都进入冷却期，只有删除计入每天的增删次数。"""

    DELETED = "deleted"  # PM 删除
    EXPIRED = "expired"  # 候选超期，代码移出
    CLOSED = "closed"  # PM 发起的清仓后选择移出


@dataclass(frozen=True, slots=True)
class Removal:
    """一次移出。reason 是删除的理由；超期和清仓后移出为 None。"""

    kind: RemovalKind
    at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class PoolEntry:
    """一只股票在股票池里的记录。

    - reason：加入时的理由与论点草稿
    - sector：加入时由调用方从数据源传入，之后不变
    - expires_at：过期时间，是某个交易日的收盘时刻；只有候选有
    - thesis：关注论点；只有关注有
    - score、reviewed_at：PM 最近一次对这只股票给出结论时的评分和时刻；还没有时为 None
    - removal：移出以后才有，这时状态停在移出前的状态

    状态变化的方法都返回新的记录，并维持不变量：候选有过期时间、没有关注论点；关注有关注论点、
    没有过期时间；持仓两者都没有。PM 发起的变化先由 rules 里的检查给出拒绝原因，方法本身不再
    检查；代码发起的变化（买入成交、清仓）在方法里检查状态，不对就抛 ValueError。
    """

    symbol: str
    status: StockStatus
    source: PoolSource
    reason: str
    sector: str
    added_at: datetime
    expires_at: datetime | None
    thesis: WatchThesis | None = None
    score: int | None = None
    reviewed_at: datetime | None = None
    removal: Removal | None = None

    @property
    def in_pool(self) -> bool:
        return self.removal is None

    def promoted(self, thesis: WatchThesis) -> Self:
        """候选升级为关注。"""
        return replace(self, status=StockStatus.WATCH, expires_at=None, thesis=thesis)

    def kept(self, expires_at: datetime) -> Self:
        """候选保持，重设过期时间。"""
        return replace(self, expires_at=expires_at)

    def demoted(self, expires_at: datetime) -> Self:
        """关注降为候选：去掉关注论点，设过期时间。"""
        return replace(self, status=StockStatus.CANDIDATE, expires_at=expires_at, thesis=None)

    def bought(self) -> Self:
        """买入成交（部分成交也算）：候选和关注变为持仓，持仓加仓不变。"""
        if not self.in_pool:
            raise ValueError(f"{self.symbol} 不在池子里，不能买入成交")
        if self.status is StockStatus.HOLDING:
            return self
        return replace(self, status=StockStatus.HOLDING, expires_at=None, thesis=None)

    def closed_to_candidate(self, expires_at: datetime) -> Self:
        """代码触发的清仓（止损单、紧急风险退出）：持仓转为候选，设过期时间。"""
        self._check_holding()
        return replace(self, status=StockStatus.CANDIDATE, expires_at=expires_at)

    def closed_to_watch(self, thesis: WatchThesis) -> Self:
        """PM 发起的清仓，PM 选择转为关注：持仓转为关注。"""
        self._check_holding()
        return replace(self, status=StockStatus.WATCH, thesis=thesis)

    def scored(self, score: int, at: datetime) -> Self:
        """PM 在 at 给出了一次结论，评分是 score：更新最新评分和上次复查时间。"""
        if not _MIN_SCORE <= score <= _MAX_SCORE:
            raise ValueError(f"评分 {score} 必须是 -2 到 +2 的整数")
        return replace(self, score=score, reviewed_at=at)

    def removed(self, kind: RemovalKind, at: datetime, reason: str | None = None) -> Self:
        """在 at 移出。清仓后移出要求持仓，和另外两种清仓一样检查。"""
        if kind is RemovalKind.CLOSED:
            self._check_holding()
        return replace(self, removal=Removal(kind, at, reason))

    def _check_holding(self) -> None:
        if not (self.in_pool and self.status is StockStatus.HOLDING):
            raise ValueError(f"{self.symbol} 不是池子里的持仓，不能清仓")
