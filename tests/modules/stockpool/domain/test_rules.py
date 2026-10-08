"""股票池检查的测试：过期时间、冷却期、当天的增删次数和加入、删除、升降级的检查，用纽交所样例日历。

场景见 openspec 的 stock-pool spec「加入候选」「删除」「重新加入的冷却期」「池子容量和关注名额」
「每天的增删次数」「候选的过期时间」。基准数据见 tests/modules/stockpool/builders.py。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.modules.stockpool.builders import (
    AAA,
    ADD_REASON,
    BASELINE_ENTRIES,
    BASELINE_NOW,
    BBB,
    CCC,
    NYSE,
    REMOVE_REASON,
    THESIS,
    at,
    listing,
)
from tradingagentspro.contracts.listing import Exchange, SecurityType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.stockpool import (
    PoolEntry,
    PoolSource,
    Rejection,
    RemovalKind,
    StockPoolConfig,
    add_rejection,
    candidate_expiry,
    changes_since,
    cooling_down,
    promote_rejection,
    remove_rejection,
    status_rejection,
)

CONFIG = StockPoolConfig()
CANDIDATE, WATCH = StockStatus.CANDIDATE, StockStatus.WATCH
DELETED, EXPIRED, CLOSED = RemovalKind.DELETED, RemovalKind.EXPIRED, RemovalKind.CLOSED
DAY_START = at(20, day=13)  # 2026-10-14 这个交易日开始的时刻：上一个交易日的收盘


def _deleted_aaa(when: datetime) -> PoolEntry:
    return AAA.removed(DELETED, when, REMOVE_REASON)


def _new_entry(symbol: str, added_at: datetime) -> PoolEntry:
    """added_at 加入的候选。"""
    return PoolEntry(
        symbol=symbol,
        status=CANDIDATE,
        source=PoolSource.EXPLORATION,
        reason=ADD_REASON,
        sector="科技",
        added_at=added_at,
        expires_at=candidate_expiry(added_at, NYSE, CONFIG),
    )


# 候选的过期时间


@pytest.mark.parametrize(
    ("moment", "expires_at"),
    [
        (BASELINE_NOW, at(20, day=28)),  # 盘中加入
        (at(20, 30), at(20, day=29)),  # 收盘后加入，归属 2026-10-15
        (at(15, day=20), at(21, day=3, month=11)),  # 夏令时结束后，收盘是 21:00Z
    ],
)
def test_candidate_expiry(moment: datetime, expires_at: datetime) -> None:
    """spec「过期时间按交易日计算」。"""
    assert candidate_expiry(moment, NYSE, CONFIG) == expires_at


def test_candidate_expiry_follows_the_config() -> None:
    config = StockPoolConfig(candidate_ttl_sessions=1)

    assert candidate_expiry(BASELINE_NOW, NYSE, config) == at(20, day=15)


# 冷却期


@pytest.mark.parametrize(
    ("moment", "cooling"),
    [
        (at(19, 59, day=20), True),  # 归属 2026-10-20，过了 4 个交易日
        (at(20, day=20), False),  # 归属 2026-10-21，过了 5 个交易日
    ],
)
def test_cooldown_boundary(moment: datetime, cooling: bool) -> None:
    """spec「冷却期的边界」：2026-10-14 15:00Z 删除。"""
    assert cooling_down(_deleted_aaa(BASELINE_NOW), moment, NYSE, CONFIG) is cooling


@pytest.mark.parametrize(
    ("moment", "cooling"),
    [(at(19, 59, day=21), True), (at(20, day=21), False)],
)
def test_cooldown_after_a_removal_after_the_close(moment: datetime, cooling: bool) -> None:
    """spec「收盘后移出从下一个交易日算起」：2026-10-14 20:00Z 删除，归属 2026-10-15。"""
    assert cooling_down(_deleted_aaa(at(20)), moment, NYSE, CONFIG) is cooling


@pytest.mark.parametrize(
    "entry", [AAA.removed(EXPIRED, BASELINE_NOW), CCC.removed(CLOSED, BASELINE_NOW)]
)
def test_expiry_and_removal_after_closing_cool_down_too(entry: PoolEntry) -> None:
    assert cooling_down(entry, at(15, day=15), NYSE, CONFIG)


def test_never_added_or_still_in_the_pool_is_not_cooling_down() -> None:
    assert not cooling_down(None, BASELINE_NOW, NYSE, CONFIG)
    assert not cooling_down(AAA, BASELINE_NOW, NYSE, CONFIG)


def test_cooldown_follows_the_config() -> None:
    config = StockPoolConfig(readd_cooldown_sessions=1)

    assert cooling_down(_deleted_aaa(BASELINE_NOW), at(19, 59), NYSE, config)
    assert not cooling_down(_deleted_aaa(BASELINE_NOW), at(20), NYSE, config)


# 当天的增删次数


def test_changes_since_counts_adds_and_deletions_from_the_start() -> None:
    added = _new_entry("DDD", DAY_START)
    added_then_deleted = _new_entry("EEE", at(14)).removed(DELETED, at(16), REMOVE_REASON)
    deleted = _deleted_aaa(at(15))

    assert changes_since([added], DAY_START) == 1
    assert changes_since([deleted], DAY_START) == 1
    assert changes_since([added_then_deleted], DAY_START) == 2
    assert changes_since([added, added_then_deleted, deleted], DAY_START) == 4


def test_expiry_and_removal_after_closing_do_not_count() -> None:
    expired = AAA.removed(EXPIRED, at(15))
    closed = CCC.removed(CLOSED, at(15))

    assert changes_since([expired, closed], DAY_START) == 0


def test_adds_and_deletions_before_the_start_do_not_count() -> None:
    before = at(19, 59, 59, day=13)
    added = _new_entry("DDD", before)
    deleted = _deleted_aaa(before)

    assert changes_since([added, deleted, *BASELINE_ENTRIES], DAY_START) == 0


def test_promotions_and_scores_do_not_count() -> None:
    entries = [AAA.promoted(THESIS), BBB.demoted(at(20, day=28)).scored(1, at(15)), CCC.bought()]

    assert changes_since(entries, DAY_START) == 0


# 加入的检查


def _add(**changes: Any) -> Rejection | None:
    """基准数据下检查"加入 DDD"：从来没有加入过，今天还没有增删，默认配置。

    changes 覆盖其中的参数。
    """
    fields: dict[str, Any] = {
        "reason": ADD_REASON,
        "listing": listing(),
        "previous": None,
        "in_pool": BASELINE_ENTRIES,
        "changes": 0,
        "now": BASELINE_NOW,
        "calendar": NYSE,
        "config": CONFIG,
    }
    return add_rejection(**(fields | changes))


def test_add_passes() -> None:
    assert _add() is None


@pytest.mark.parametrize(("reason", "passes"), [("", False), ("   ", False), (" 理由 ", True)])
def test_add_reason(reason: str, passes: bool) -> None:
    """表的第 1 行。"""
    assert _add(reason=reason) == (None if passes else Rejection.MISSING_REASON)


@pytest.mark.parametrize("previous", [AAA, BBB, CCC])
def test_add_already_in_the_pool(previous: PoolEntry) -> None:
    """表的第 2 行不通过。"""
    assert _add(previous=previous) == Rejection.ALREADY_IN_POOL


@pytest.mark.parametrize(
    ("removed_at", "passes"),
    [
        (at(15, day=13), False),  # 到今天过了 1 个交易日
        (at(15, day=7), True),  # 到今天过了 5 个交易日
    ],
)
def test_add_after_a_removal(removed_at: datetime, passes: bool) -> None:
    """表的第 2 行通过；第 3 行：过了冷却期才能重新加入。"""
    result = _add(previous=_deleted_aaa(removed_at))

    assert result == (None if passes else Rejection.COOLING_DOWN)


def test_add_unknown_symbol() -> None:
    """表的第 4 行。"""
    assert _add(listing=None) == Rejection.UNKNOWN_SYMBOL


@pytest.mark.parametrize(
    ("exchange", "security_type", "passes"),
    [
        (Exchange.NYSE, SecurityType.COMMON_STOCK, True),
        (Exchange.NASDAQ, SecurityType.ADR, True),
        (Exchange.NYSE, SecurityType.ETF, False),
        (Exchange.NASDAQ, SecurityType.LEVERAGED, False),
        (Exchange.NYSE, SecurityType.OTHER, False),
        (Exchange.OTHER, SecurityType.COMMON_STOCK, False),
    ],
)
def test_add_allowed_range(exchange: Exchange, security_type: SecurityType, passes: bool) -> None:
    """表的第 5 行。"""
    result = _add(listing=listing(exchange=exchange, security_type=security_type))

    assert result == (None if passes else Rejection.NOT_ALLOWED)


def test_add_allowed_range_follows_the_config() -> None:
    config = StockPoolConfig(
        allowed_security_types=frozenset({SecurityType.COMMON_STOCK, SecurityType.ETF})
    )

    assert _add(listing=listing(security_type=SecurityType.ETF), config=config) is None
    assert (
        _add(listing=listing(security_type=SecurityType.ADR), config=config)
        == Rejection.NOT_ALLOWED
    )


@pytest.mark.parametrize(
    ("volume", "passes"), [("10000000", True), ("9999999.99", False), ("0", False)]
)
def test_add_liquidity_boundary(volume: str, passes: bool) -> None:
    """表的第 6 行：等于门槛通过，低 0.01 美元就不通过。"""
    result = _add(listing=listing(average_dollar_volume=Decimal(volume)))

    assert result == (None if passes else Rejection.ILLIQUID)


@pytest.mark.parametrize(("max_pool_size", "passes"), [(3, True), (2, False)])
def test_add_pool_size_boundary(max_pool_size: int, passes: bool) -> None:
    """表的第 7 行：基准数据有候选和关注各一只，持仓不计入。"""
    config = StockPoolConfig(max_pool_size=max_pool_size)

    assert _add(config=config) == (None if passes else Rejection.POOL_FULL)


@pytest.mark.parametrize(("changes", "passes"), [(9, True), (10, False), (11, False)])
def test_add_daily_limit_boundary(changes: int, passes: bool) -> None:
    """表的第 8 行。"""
    assert _add(changes=changes) == (None if passes else Rejection.DAILY_LIMIT)


_FULL = StockPoolConfig(max_pool_size=2)
_ETF = listing(security_type=SecurityType.ETF, average_dollar_volume=Decimal(1_000_000))
_ILLIQUID = listing(average_dollar_volume=Decimal(1_000_000))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"reason": " ", "previous": AAA, "listing": None}, Rejection.MISSING_REASON),
        ({"previous": AAA, "listing": None}, Rejection.ALREADY_IN_POOL),
        ({"previous": _deleted_aaa(at(15, day=13)), "listing": None}, Rejection.COOLING_DOWN),
        ({"listing": None}, Rejection.UNKNOWN_SYMBOL),
        ({"listing": _ETF}, Rejection.NOT_ALLOWED),
        ({"listing": _ILLIQUID}, Rejection.ILLIQUID),
        ({}, Rejection.POOL_FULL),
    ],
)
def test_add_gives_the_first_failing_row(changes: dict[str, Any], expected: Rejection) -> None:
    """每一行不通过时，之后的各行也都不通过（池子已满、次数用完），给出的是这一行。"""
    assert _add(**({"config": _FULL, "changes": 10} | changes)) == expected


# 删除的检查


def _remove(**changes: Any) -> Rejection | None:
    """基准数据下检查删除 AAA，今天还没有增删，默认配置；changes 覆盖其中的参数。"""
    fields: dict[str, Any] = {"reason": REMOVE_REASON, "entry": AAA, "changes": 0, "config": CONFIG}
    return remove_rejection(**(fields | changes))


@pytest.mark.parametrize("entry", [AAA, BBB])
def test_remove_passes(entry: PoolEntry) -> None:
    assert _remove(entry=entry) is None


def test_remove_rows() -> None:
    """表的每一行不通过。"""
    assert _remove(reason=" ") == Rejection.MISSING_REASON
    assert _remove(entry=None) == Rejection.NOT_IN_POOL
    assert _remove(entry=_deleted_aaa(BASELINE_NOW)) == Rejection.NOT_IN_POOL
    assert _remove(entry=CCC) == Rejection.HOLDING
    assert _remove(changes=9) is None
    assert _remove(changes=10) == Rejection.DAILY_LIMIT


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"reason": "", "entry": None}, Rejection.MISSING_REASON),
        ({"entry": None}, Rejection.NOT_IN_POOL),
        ({"entry": CCC}, Rejection.HOLDING),
        ({}, Rejection.DAILY_LIMIT),
    ],
)
def test_remove_gives_the_first_failing_row(changes: dict[str, Any], expected: Rejection) -> None:
    assert _remove(**({"changes": 10} | changes)) == expected


# 升降级和保持的检查


@pytest.mark.parametrize(
    ("entry", "required", "expected"),
    [
        (None, CANDIDATE, Rejection.NOT_IN_POOL),
        (_deleted_aaa(BASELINE_NOW), CANDIDATE, Rejection.NOT_IN_POOL),
        (BBB, CANDIDATE, Rejection.WRONG_STATUS),
        (CCC, WATCH, Rejection.WRONG_STATUS),
        (AAA, CANDIDATE, None),
        (BBB, WATCH, None),
    ],
)
def test_status_rejection(
    entry: PoolEntry | None, required: StockStatus, expected: Rejection | None
) -> None:
    assert status_rejection(entry, required) == expected


@pytest.mark.parametrize(("max_watch", "passes"), [(2, True), (1, False)])
def test_promote_watch_quota_boundary(max_watch: int, passes: bool) -> None:
    """基准数据有一只关注。"""
    config = StockPoolConfig(max_watch=max_watch)

    result = promote_rejection(AAA, BASELINE_ENTRIES, config)

    assert result == (None if passes else Rejection.WATCH_FULL)


def test_promote_checks_the_status_before_the_quota() -> None:
    config = StockPoolConfig(max_watch=1)

    assert promote_rejection(None, BASELINE_ENTRIES, config) == Rejection.NOT_IN_POOL
    assert promote_rejection(BBB, BASELINE_ENTRIES, config) == Rejection.WRONG_STATUS
