"""股票池的用例：PM 和手动发起的加入、删除、升降级、保持和记录评分，以及代码发起的候选超期移出
和成交后的状态变化。

见 stock-pool spec。
"""

from datetime import datetime

from tradingagentspro.contracts.listing import Listing
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import WatchThesis
from tradingagentspro.modules.stockpool.config import StockPoolConfig
from tradingagentspro.modules.stockpool.domain.entry import PoolEntry, PoolSource, RemovalKind
from tradingagentspro.modules.stockpool.domain.rules import (
    Rejection,
    add_rejection,
    candidate_expiry,
    changes_since,
    promote_rejection,
    remove_rejection,
    status_rejection,
)
from tradingagentspro.modules.stockpool.ports import StockPoolStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar


class StockPoolService:
    """股票池的入口。

    PM 和手动发起的操作通过时返回新的记录，不通过时返回原因，不改变任何记录。同一个实例由调用方
    串行调用：容量和次数的检查是先读后写，做法 M4 再定（本变更 design D7）。
    """

    def __init__(
        self,
        store: StockPoolStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: StockPoolConfig,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._config = config

    def entry(self, symbol: str) -> PoolEntry | None:
        """这只股票在池子里的记录；不在池子里时为 None。"""
        entry = self._store.get(symbol)
        return entry if entry is not None and entry.in_pool else None

    def entries(self) -> tuple[PoolEntry, ...]:
        """池子里的全部股票。"""
        return self._store.in_pool()

    def add(
        self, symbol: str, source: PoolSource, reason: str, listing: Listing | None
    ) -> PoolEntry | Rejection:
        """PM 探索或手动添加一只股票，成为候选。listing 是调用方查到的上市信息，查不到时为 None。"""
        now = self._clock.now()
        rejection = add_rejection(
            reason,
            listing,
            self._store.get(symbol),
            self._store.in_pool(),
            self._changes_today(now),
            now,
            self._calendar,
            self._config,
        )
        if rejection is not None:
            return rejection
        assert listing is not None  # 检查保证查到了上市信息
        entry = PoolEntry(
            symbol=symbol,
            status=StockStatus.CANDIDATE,
            source=source,
            reason=reason,
            sector=listing.sector,
            added_at=now,
            expires_at=self._expiry(now),
        )
        self._store.save(entry)
        return entry

    def remove(self, symbol: str, reason: str) -> PoolEntry | Rejection:
        """PM 删除池子里的候选或关注，进入冷却期。"""
        now = self._clock.now()
        entry = self._store.get(symbol)
        rejection = remove_rejection(reason, entry, self._changes_today(now), self._config)
        if rejection is not None:
            return rejection
        assert entry is not None  # 检查保证在池子里
        return self._save(entry.removed(RemovalKind.DELETED, now, reason))

    def promote(self, symbol: str, thesis: WatchThesis) -> PoolEntry | Rejection:
        """候选升级为关注。关注论点是否齐全由它的格式保证。"""
        entry = self._store.get(symbol)
        rejection = promote_rejection(entry, self._store.in_pool(), self._config)
        if rejection is not None:
            return rejection
        assert entry is not None  # 检查保证在池子里
        return self._save(entry.promoted(thesis))

    def demote(self, symbol: str) -> PoolEntry | Rejection:
        """关注降为候选。"""
        entry = self._store.get(symbol)
        rejection = status_rejection(entry, StockStatus.WATCH)
        if rejection is not None:
            return rejection
        assert entry is not None  # 检查保证在池子里
        return self._save(entry.demoted(self._expiry(self._clock.now())))

    def keep(self, symbol: str) -> PoolEntry | Rejection:
        """候选保持，重设过期时间。"""
        entry = self._store.get(symbol)
        rejection = status_rejection(entry, StockStatus.CANDIDATE)
        if rejection is not None:
            return rejection
        assert entry is not None  # 检查保证在池子里
        return self._save(entry.kept(self._expiry(self._clock.now())))

    def record_score(self, symbol: str, score: int) -> PoolEntry | Rejection:
        """PM 对池子里的一只股票给出了结论：记下评分，上次复查时间改为现在。

        评分不是 -2 到 +2 的整数时抛 ValueError。
        """
        entry = self.entry(symbol)
        if entry is None:
            return Rejection.NOT_IN_POOL
        return self._save(entry.scored(score, self._clock.now()))

    def expire_candidates(self) -> tuple[PoolEntry, ...]:
        """移出过期时间不晚于现在的全部候选，原因是超期，返回这次移出的股票。

        调用方每个交易日收盘后调用一次；错过的话，下一次调用照样移出所有已经到期的候选。
        """
        now = self._clock.now()
        expired = [
            e
            for e in self._store.in_pool()
            if e.status is StockStatus.CANDIDATE
            and e.expires_at is not None
            and e.expires_at <= now
        ]
        return tuple(self._save(e.removed(RemovalKind.EXPIRED, now)) for e in expired)

    def bought(self, symbol: str) -> PoolEntry:
        """这只股票的买单有成交（部分成交也算）：候选和关注变为持仓，持仓不变。

        不在池子里时抛 ValueError。
        """
        return self._save(self._latest(symbol).bought())

    def closed_by_code(self, symbol: str) -> PoolEntry:
        """代码触发的清仓（止损单、紧急风险退出）完成：持仓转为候选。不是持仓时抛 ValueError。"""
        expires_at = self._expiry(self._clock.now())
        return self._save(self._latest(symbol).closed_to_candidate(expires_at))

    def closed_by_pm(self, symbol: str, thesis: WatchThesis | None) -> PoolEntry:
        """PM 发起的清仓完成：PM 写了关注论点时转为关注，没有写（None）时移出。

        不是持仓时抛 ValueError。
        """
        entry = self._latest(symbol)
        if thesis is None:
            return self._save(entry.removed(RemovalKind.CLOSED, self._clock.now()))
        return self._save(entry.closed_to_watch(thesis))

    def _latest(self, symbol: str) -> PoolEntry:
        """代码发起的操作用的记录：从来没有加入过时抛 ValueError，已经移出的由记录的方法报错。"""
        entry = self._store.get(symbol)
        if entry is None:
            raise ValueError(f"{symbol} 不在池子里")
        return entry

    def _save(self, entry: PoolEntry) -> PoolEntry:
        self._store.save(entry)
        return entry

    def _expiry(self, now: datetime) -> datetime:
        return candidate_expiry(now, self._calendar, self._config)

    def _changes_today(self, now: datetime) -> int:
        """now 归属的交易日里已经通过的增删次数。这个交易日从上一个交易日的收盘时刻开始。"""
        start = self._calendar.last_session_before(now).close
        return changes_since(self._store.changed_since(start), start)
