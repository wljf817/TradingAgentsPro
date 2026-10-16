"""股票池的用例：PM 和手动发起的加入、删除、升降级、保持、记录评分和论点更新，以及代码发起的候选
超期移出和成交后的状态变化；每个动作写进程序日志。

见 stock-pool spec 和 program-log spec「股票池的动作」。
"""

import threading
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

from tradingagentspro.contracts.listing import Listing
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import InvestmentThesis, WatchThesis
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
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

_SOURCES: Final = {PoolSource.EXPLORATION: "PM 探索", PoolSource.MANUAL: "手动添加"}
_STATUSES: Final = {
    StockStatus.CANDIDATE: "候选",
    StockStatus.WATCH: "关注",
    StockStatus.HOLDING: "持仓",
}
_REJECTIONS: Final = {
    Rejection.MISSING_REASON: "缺少理由",
    Rejection.ALREADY_IN_POOL: "已在池子里",
    Rejection.COOLING_DOWN: "冷却期内",
    Rejection.UNKNOWN_SYMBOL: "股票代码不存在",
    Rejection.NOT_ALLOWED: "不在允许交易的范围内",
    Rejection.ILLIQUID: "流动性不达标",
    Rejection.POOL_FULL: "池子已满",
    Rejection.WATCH_FULL: "关注名额已满",
    Rejection.DAILY_LIMIT: "增删次数已用完",
    Rejection.NOT_IN_POOL: "不在池子里",
    Rejection.HOLDING: "持仓不能删除",
    Rejection.WRONG_STATUS: "状态不允许",
}


class StockPoolService:
    """股票池的入口。

    PM 和手动发起的操作通过时返回新的记录，不通过时返回原因，不改变任何记录；它们带上所属的案件 ID，
    手动添加没有。论点和上次复查的那一天由调用方按 review-dates spec 检查、算好，这里只保存。

    每个动作在记录保存以后写一条程序日志，被拒绝的请求只写条目；写条目出错时原样抛出，已经保存的
    记录不撤回。

    容量、关注名额和次数的检查是先读后写，所以改变记录的操作和被拒绝的请求从读时钟到写条目都拿着
    一把锁，同一个进程里一个接一个进行；读取（entry、entries）不拿锁。见 update-stock-pool-storage
    的 design D4。
    """

    def __init__(
        self,
        store: StockPoolStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: StockPoolConfig,
        log: ProgramLog,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._log = log
        self._lock = threading.Lock()

    def entry(self, symbol: str) -> PoolEntry | None:
        """这只股票在池子里的记录；不在池子里时为 None。"""
        return self._in_pool(symbol)

    def entries(self) -> tuple[PoolEntry, ...]:
        """池子里的全部股票。"""
        return self._store.in_pool()

    def add(
        self,
        symbol: str,
        source: PoolSource,
        reason: str,
        listing: Listing | None,
        *,
        case_id: UUID | None,
    ) -> PoolEntry | Rejection:
        """PM 探索或手动添加一只股票，成为候选。listing 是调用方查到的上市信息，查不到时为 None；
        case_id 是寻股的案件，手动添加为 None。
        """
        with self._lock:
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
            request = f"{_SOURCES[source]}，理由：{reason}"
            if rejection is not None:
                self._write(Action.POOL_ADD, symbol, f"{_rejected(rejection)}；{request}", case_id)
                return rejection
            assert listing is not None  # 检查保证查到了上市信息
            expires_at = self._expiry(now)
            entry = self._save(
                PoolEntry(
                    symbol=symbol,
                    status=StockStatus.CANDIDATE,
                    source=source,
                    reason=reason,
                    sector=listing.sector,
                    added_at=now,
                    expires_at=expires_at,
                    candidate_since=now,
                )
            )
            detail = f"通过：{request}；板块 {listing.sector}，过期时间 {_utc(expires_at)}"
            self._write(Action.POOL_ADD, symbol, detail, case_id)
            return entry

    def remove(self, symbol: str, reason: str, *, case_id: UUID) -> PoolEntry | Rejection:
        """PM 删除池子里的候选或关注，进入冷却期。"""
        with self._lock:
            now = self._clock.now()
            entry = self._store.get(symbol)
            rejection = remove_rejection(reason, entry, self._changes_today(now), self._config)
            if rejection is not None:
                detail = f"{_rejected(rejection)}；理由：{reason}"
                self._write(Action.POOL_REMOVE, symbol, detail, case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            removed = self._save(entry.removed(RemovalKind.DELETED, now, reason))
            detail = f"通过：{_STATUSES[entry.status]}移出，理由：{reason}"
            self._write(Action.POOL_REMOVE, symbol, detail, case_id)
            return removed

    def promote(
        self, symbol: str, thesis: WatchThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection:
        """候选升级为关注。关注论点是否齐全由它的格式保证。"""
        with self._lock:
            entry = self._store.get(symbol)
            rejection = promote_rejection(entry, self._store.in_pool(), self._config)
            if rejection is not None:
                self._write(Action.POOL_PROMOTE, symbol, _rejected(rejection), case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            promoted = self._save(entry.promoted(thesis, last_review_day))
            detail = f"通过：{_thesis_dates(thesis, last_review_day)}"
            self._write(Action.POOL_PROMOTE, symbol, detail, case_id)
            return promoted

    def demote(self, symbol: str, *, case_id: UUID) -> PoolEntry | Rejection:
        """关注降为候选。"""
        with self._lock:
            entry = self._store.get(symbol)
            rejection = status_rejection(entry, StockStatus.WATCH)
            if rejection is not None:
                self._write(Action.POOL_DEMOTE, symbol, _rejected(rejection), case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            expires_at = self._expiry(self._clock.now())
            demoted = self._save(entry.demoted(expires_at))
            self._write(Action.POOL_DEMOTE, symbol, f"通过：过期时间 {_utc(expires_at)}", case_id)
            return demoted

    def keep(self, symbol: str, *, case_id: UUID) -> PoolEntry | Rejection:
        """候选保持，重设过期时间。"""
        with self._lock:
            entry = self._store.get(symbol)
            rejection = status_rejection(entry, StockStatus.CANDIDATE)
            if rejection is not None:
                self._write(Action.POOL_KEEP, symbol, _rejected(rejection), case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            expires_at = self._expiry(self._clock.now())
            kept = self._save(entry.kept(expires_at))
            self._write(Action.POOL_KEEP, symbol, f"通过：过期时间 {_utc(expires_at)}", case_id)
            return kept

    def record_score(self, symbol: str, score: int, *, case_id: UUID) -> PoolEntry | Rejection:
        """PM 对池子里的一只股票给出了结论：记下评分，上次复查时间改为现在。

        评分不是 -2 到 +2 的整数时抛 ValueError。
        """
        with self._lock:
            entry = self._in_pool(symbol)
            if entry is None:
                detail = f"{_rejected(Rejection.NOT_IN_POOL)}；评分 {score}"
                self._write(Action.POOL_SCORE, symbol, detail, case_id)
                return Rejection.NOT_IN_POOL
            scored = self._save(entry.scored(score, self._clock.now()))
            self._write(Action.POOL_SCORE, symbol, f"评分 {score}", case_id)
            return scored

    def update_watch_thesis(
        self, symbol: str, thesis: WatchThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection:
        """关注股的论点更新：关注论点和上次复查的那一天换成给出的，要求关注。"""
        with self._lock:
            entry = self._store.get(symbol)
            rejection = status_rejection(entry, StockStatus.WATCH)
            if rejection is not None:
                detail = f"关注论点，{_rejected(rejection)}"
                self._write(Action.POOL_THESIS, symbol, detail, case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            updated = self._save(entry.watch_thesis_updated(thesis, last_review_day))
            detail = f"关注论点，通过：{_thesis_dates(thesis, last_review_day)}"
            self._write(Action.POOL_THESIS, symbol, detail, case_id)
            return updated

    def update_investment_thesis(
        self, symbol: str, thesis: InvestmentThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection:
        """持仓的论点更新：投资论点和上次复查的那一天换成给出的，要求持仓。"""
        with self._lock:
            entry = self._store.get(symbol)
            rejection = status_rejection(entry, StockStatus.HOLDING)
            if rejection is not None:
                detail = f"投资论点，{_rejected(rejection)}"
                self._write(Action.POOL_THESIS, symbol, detail, case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            updated = self._save(entry.investment_thesis_updated(thesis, last_review_day))
            detail = f"投资论点，通过：{_thesis_dates(thesis, last_review_day)}"
            self._write(Action.POOL_THESIS, symbol, detail, case_id)
            return updated

    def expire_candidates(self) -> tuple[PoolEntry, ...]:
        """移出过期时间不晚于现在的全部候选，原因是超期，按股票代码一只一只移出、写条目，返回这次
        移出的股票。

        调用方每个交易日收盘后调用一次；错过的话，下一次调用照样移出所有已经到期的候选。
        """
        with self._lock:
            now = self._clock.now()
            due = sorted(
                (
                    e
                    for e in self._store.in_pool()
                    if e.status is StockStatus.CANDIDATE
                    and e.expires_at is not None
                    and e.expires_at <= now
                ),
                key=lambda e: e.symbol,
            )
            expired: list[PoolEntry] = []
            for entry in due:
                assert entry.expires_at is not None  # 上面只选了有过期时间的
                expired.append(self._save(entry.removed(RemovalKind.EXPIRED, now)))
                detail = f"过期时间 {_utc(entry.expires_at)}"
                self._write(Action.POOL_EXPIRE, entry.symbol, detail, None)
            return tuple(expired)

    def bought(self, symbol: str, thesis: InvestmentThesis, last_review_day: date) -> PoolEntry:
        """这只股票的开仓单有成交（部分成交也算）：候选和关注变为持仓，带上开仓方案附的投资论点和
        给出开仓方案结论的那一天。持仓不变，不保存也不写条目。

        不在池子里时抛 ValueError。
        """
        with self._lock:
            entry = self._latest(symbol)
            bought = entry.bought(thesis, last_review_day)
            if bought == entry:
                return entry
            self._save(bought)
            detail = f"{_STATUSES[entry.status]}变为持仓：{_thesis_dates(thesis, last_review_day)}"
            self._write(Action.POOL_BOUGHT, symbol, detail, None)
            return bought

    def closed_by_code(self, symbol: str) -> PoolEntry:
        """代码触发的清仓（止损单、紧急风险退出）完成：持仓转为候选。不是持仓时抛 ValueError。"""
        with self._lock:
            now = self._clock.now()
            expires_at = self._expiry(now)
            closed = self._save(self._latest(symbol).closed_to_candidate(expires_at, now))
            detail = f"代码触发，转为候选：过期时间 {_utc(expires_at)}"
            self._write(Action.POOL_CLOSED, symbol, detail, None)
            return closed

    def closed_to_watch(self, symbol: str, thesis: WatchThesis, last_review_day: date) -> PoolEntry:
        """PM 发起的清仓完成，PM 选择转为关注：带上 PM 写的关注论点和上次复查的那一天。

        不是持仓时抛 ValueError。
        """
        with self._lock:
            closed = self._save(self._latest(symbol).closed_to_watch(thesis, last_review_day))
            detail = f"PM 发起，转为关注：{_thesis_dates(thesis, last_review_day)}"
            self._write(Action.POOL_CLOSED, symbol, detail, None)
            return closed

    def closed_and_removed(self, symbol: str) -> PoolEntry:
        """PM 发起的清仓完成，PM 选择移出。不是持仓时抛 ValueError。"""
        with self._lock:
            now = self._clock.now()
            removed = self._save(self._latest(symbol).removed(RemovalKind.CLOSED, now))
            self._write(Action.POOL_CLOSED, symbol, "PM 发起，移出", None)
            return removed

    def _in_pool(self, symbol: str) -> PoolEntry | None:
        entry = self._store.get(symbol)
        return entry if entry is not None and entry.in_pool else None

    def _latest(self, symbol: str) -> PoolEntry:
        """代码发起的操作用的记录：从来没有加入过时抛 ValueError，已经移出的由记录的方法报错。"""
        entry = self._store.get(symbol)
        if entry is None:
            raise ValueError(f"{symbol} 不在池子里")
        return entry

    def _save(self, entry: PoolEntry) -> PoolEntry:
        self._store.save(entry)
        return entry

    def _write(self, action: Action, symbol: str, detail: str, case_id: UUID | None) -> None:
        self._log.write(Actor.STOCK_POOL, action, symbol, detail, case_id=case_id)

    def _expiry(self, now: datetime) -> datetime:
        return candidate_expiry(now, self._calendar, self._config)

    def _changes_today(self, now: datetime) -> int:
        """now 归属的交易日里已经通过的增删次数。这个交易日从上一个交易日的收盘时刻开始。"""
        start = self._calendar.last_session_before(now).close
        return changes_since(self._store.changed_since(start), start)


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def _rejected(rejection: Rejection) -> str:
    return f"被拒绝：{_REJECTIONS[rejection]}"


def _thesis_dates(thesis: WatchThesis | InvestmentThesis, last_review_day: date) -> str:
    """论点里的复查日、催化剂截止日期和上次复查的那一天；论点全文在记录里。"""
    return (
        f"复查日 {thesis.review_day}，催化剂截止日期 {thesis.catalyst.deadline}，"
        f"上次复查的那一天 {last_review_day}"
    )
