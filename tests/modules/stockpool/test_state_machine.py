"""股票池的状态机性质测试（add-stock-pool 的 design D10）。

随机交错全部操作和拨动时间，每一步之后检查记录的不变量、被拒绝的操作不改变记录、容量和关注名额、
每个交易日的增删次数和冷却期。配置用较小的容量、名额和次数上限，让上限经常被碰到。
"""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from tests.fakes.clock import FakeClock
from tests.fakes.stock_pool import InMemoryStockPoolStore
from tests.modules.stockpool.builders import (
    ADD_REASON,
    BASELINE_ENTRIES,
    BASELINE_NOW,
    NYSE,
    REMOVE_REASON,
    THESIS,
    listing,
)
from tradingagentspro.contracts.listing import Listing, SecurityType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.stockpool import (
    PoolEntry,
    PoolSource,
    Rejection,
    StockPoolConfig,
    StockPoolService,
)

CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
CONFIG = StockPoolConfig(
    max_pool_size=2,
    max_watch=1,
    max_daily_changes=2,
    candidate_ttl_sessions=2,
    readd_cooldown_sessions=2,
)
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
# 合格的上市信息和理由出现得最多，让加入、删除常常走到容量、次数的检查
LISTINGS: tuple[Listing | None, ...] = (
    *[listing()] * 5,
    None,
    listing(security_type=SecurityType.ETF),
    listing(average_dollar_volume=Decimal(1_000_000)),
)
ADD_REASONS = (*[ADD_REASON] * 5, " ")
REMOVE_REASONS = (*[REMOVE_REASON] * 3, "")
# 时间不超过样例日历的范围，留出算过期时间的余量
LATEST = datetime(2027, 1, 15, 15, tzinfo=UTC)

_REJECTION_EVENTS = {
    Rejection.POOL_FULL: "池子已满",
    Rejection.WATCH_FULL: "关注名额已满",
    Rejection.DAILY_LIMIT: "增删次数已用完",
    Rejection.COOLING_DOWN: "冷却期内",
}

Snapshot = dict[str, PoolEntry | None]


def _day(moment: datetime) -> date:
    return NYSE.session_closing_after(moment).day


def _pool_size(entries: tuple[PoolEntry, ...]) -> int:
    return sum(1 for e in entries if e.status is not HOLDING)


def _watch_count(entries: tuple[PoolEntry, ...]) -> int:
    return sum(1 for e in entries if e.status is WATCH)


class StockPoolMachine(RuleBasedStateMachine):
    """从基准数据开始。参考模型自己记下每个交易日通过的增删次数和每只股票最近一次移出的时刻，
    不读股票池的实现。
    """

    def __init__(self) -> None:
        super().__init__()
        self.store = InMemoryStockPoolStore()
        for entry in BASELINE_ENTRIES:
            self.store.save(entry)
        self.clock = FakeClock(BASELINE_NOW)
        self.service = StockPoolService(self.store, self.clock, NYSE, CONFIG)
        self.changes: Counter[date] = Counter()  # 每个交易日通过的加入和删除
        self.removed_at: dict[str, datetime] = {}  # 每只股票最近一次移出的时刻

    @property
    def now(self) -> datetime:
        return self.clock.now()

    def _snapshot(self) -> Snapshot:
        return {s: self.store.get(s) for s in SYMBOLS}

    def _cooling_down(self, symbol: str) -> bool:
        removed = self.removed_at.get(symbol)
        if removed is None or self.service.entry(symbol) is not None:
            return False
        sessions = NYSE.count_sessions(_day(removed), _day(self.now))
        return sessions < CONFIG.readd_cooldown_sessions

    def _pm(self, call: Callable[[], PoolEntry | Rejection]) -> PoolEntry | Rejection:
        """PM 和手动发起的操作：被拒绝时记录都不变。"""
        before = self._snapshot()
        result = call()
        if isinstance(result, Rejection):
            assert self._snapshot() == before
            if result in _REJECTION_EVENTS:
                event(_REJECTION_EVENTS[result])
        return result

    def _count_change(self) -> None:
        day = _day(self.now)
        self.changes[day] += 1
        assert self.changes[day] <= CONFIG.max_daily_changes

    def _code(self, call: Callable[[], PoolEntry], allowed: bool) -> PoolEntry | None:
        """代码发起的操作：状态不对时必须报错，记录都不变。通过时照常变化，统计这次变化让候选和
        关注超出池子容量、让关注超出关注名额的情况。
        """
        before = self._snapshot()
        if not allowed:
            try:
                call()
            except ValueError:
                assert self._snapshot() == before
                return None
            raise AssertionError("状态不对的代码操作应该报错")
        pool_size, watch_count = (
            _pool_size(self.store.in_pool()),
            _watch_count(self.store.in_pool()),
        )
        result = call()
        in_pool = self.store.in_pool()
        if pool_size < _pool_size(in_pool) and _pool_size(in_pool) > CONFIG.max_pool_size:
            event("成交后的状态变化超出池子容量")
        if watch_count < _watch_count(in_pool) and _watch_count(in_pool) > CONFIG.max_watch:
            event("成交后的状态变化超出关注名额")
        return result

    def _is_holding(self, symbol: str) -> bool:
        entry = self.service.entry(symbol)
        return entry is not None and entry.status is HOLDING

    # PM 和手动发起的操作

    @rule(
        symbol=st.sampled_from(SYMBOLS),
        source=st.sampled_from(PoolSource),
        reason=st.sampled_from(ADD_REASONS),
        found=st.sampled_from(LISTINGS),
    )
    def add(self, symbol: str, source: PoolSource, reason: str, found: Listing | None) -> None:
        cooling = self._cooling_down(symbol)
        pool_size = _pool_size(self.store.in_pool())
        result = self._pm(lambda: self.service.add(symbol, source, reason, found))
        if cooling:
            assert isinstance(result, Rejection), "冷却期内的加入必须被拒绝"
        if result is Rejection.COOLING_DOWN:
            assert cooling
        if result is Rejection.POOL_FULL:
            assert pool_size >= CONFIG.max_pool_size
        if result is Rejection.DAILY_LIMIT:
            assert self.changes[_day(self.now)] >= CONFIG.max_daily_changes
        if isinstance(result, PoolEntry):
            assert pool_size < CONFIG.max_pool_size
            self._count_change()

    @rule(symbol=st.sampled_from(SYMBOLS), reason=st.sampled_from(REMOVE_REASONS))
    def remove(self, symbol: str, reason: str) -> None:
        result = self._pm(lambda: self.service.remove(symbol, reason))
        if result is Rejection.DAILY_LIMIT:
            assert self.changes[_day(self.now)] >= CONFIG.max_daily_changes
        if isinstance(result, PoolEntry):
            self._count_change()
            self.removed_at[symbol] = self.now

    @rule(symbol=st.sampled_from(SYMBOLS))
    def promote(self, symbol: str) -> None:
        watch_count = _watch_count(self.store.in_pool())
        result = self._pm(lambda: self.service.promote(symbol, THESIS))
        if result is Rejection.WATCH_FULL:
            assert watch_count >= CONFIG.max_watch
        if isinstance(result, PoolEntry):
            assert watch_count < CONFIG.max_watch

    @rule(symbol=st.sampled_from(SYMBOLS))
    def demote(self, symbol: str) -> None:
        self._pm(lambda: self.service.demote(symbol))

    @rule(symbol=st.sampled_from(SYMBOLS))
    def keep(self, symbol: str) -> None:
        self._pm(lambda: self.service.keep(symbol))

    @rule(symbol=st.sampled_from(SYMBOLS), score=st.integers(-2, 2))
    def record_score(self, symbol: str, score: int) -> None:
        self._pm(lambda: self.service.record_score(symbol, score))

    # 代码发起的操作

    @rule()
    def expire_candidates(self) -> None:
        expected = {
            e.symbol
            for e in self.store.in_pool()
            if e.status is CANDIDATE and e.expires_at is not None and e.expires_at <= self.now
        }
        expired = self.service.expire_candidates()
        assert {e.symbol for e in expired} == expected
        for entry in expired:
            self.removed_at[entry.symbol] = self.now
            event("候选超期移出")

    @rule(symbol=st.sampled_from(SYMBOLS))
    def bought(self, symbol: str) -> None:
        allowed = self.service.entry(symbol) is not None
        self._code(lambda: self.service.bought(symbol), allowed)

    @rule(symbol=st.sampled_from(SYMBOLS))
    def closed_by_code(self, symbol: str) -> None:
        allowed = self._is_holding(symbol)
        self._code(lambda: self.service.closed_by_code(symbol), allowed)

    @rule(symbol=st.sampled_from(SYMBOLS), to_watch=st.booleans())
    def closed_by_pm(self, symbol: str, to_watch: bool) -> None:
        allowed = self._is_holding(symbol)
        thesis = THESIS if to_watch else None
        result = self._code(lambda: self.service.closed_by_pm(symbol, thesis), allowed)
        if result is not None and not result.in_pool:
            self.removed_at[symbol] = self.now

    # 拨动时间

    @rule(minutes=st.integers(1, 24 * 60))
    def advance(self, minutes: int) -> None:
        """往后拨最多一天，常常跨过收盘，有时跨过周末。"""
        self.clock.set(min(self.now + timedelta(minutes=minutes), LATEST))

    @rule(seconds=st.sampled_from([-1, 0, 1]))
    def to_the_close(self, seconds: int) -> None:
        """拨到当前交易日收盘的前后一秒或收盘时刻，碰归属的交易日和到期的边界。"""
        close = NYSE.session_closing_after(self.now).close
        target = close + timedelta(seconds=seconds)
        self.clock.set(min(target if target > self.now else close, LATEST))

    # 不变量

    @invariant()
    def entries_hold_the_invariants(self) -> None:
        """候选有过期时间、没有关注论点；关注有关注论点、没有过期时间；持仓两者都没有。"""
        for entry in self._snapshot().values():
            if entry is None:
                continue
            if entry.status is CANDIDATE:
                assert entry.expires_at is not None and entry.thesis is None
            elif entry.status is WATCH:
                assert entry.expires_at is None and entry.thesis is not None
            else:
                assert (entry.expires_at, entry.thesis) == (None, None)

    @invariant()
    def entries_and_entry_agree(self) -> None:
        in_pool = {e.symbol for e in self.service.entries()}
        assert in_pool == {s for s in SYMBOLS if self.service.entry(s) is not None}


TestStockPoolMachine = StockPoolMachine.TestCase
