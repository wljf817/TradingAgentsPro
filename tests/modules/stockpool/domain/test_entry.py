"""股票池记录的测试：状态变化的方法和记录的不变量，直接构造记录。

状态变化见 openspec 的 stock-pool spec「状态变化」表，记录见「股票池的记录」。
基准数据见 tests/modules/stockpool/builders.py。
"""

from collections.abc import Callable

import pytest

from tests.modules.stockpool.builders import (
    AAA,
    BASELINE_ENTRIES,
    BASELINE_NOW,
    BBB,
    CCC,
    THESIS,
    at,
)
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.stockpool import PoolEntry, Removal, RemovalKind

CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
DELETED, EXPIRED, CLOSED = RemovalKind.DELETED, RemovalKind.EXPIRED, RemovalKind.CLOSED
NEW_EXPIRY = at(20, day=28)


def _assert_invariants(entry: PoolEntry) -> None:
    """候选有过期时间、没有关注论点；关注有关注论点、没有过期时间；持仓两者都没有。"""
    if entry.status is CANDIDATE:
        assert entry.expires_at is not None
        assert entry.thesis is None
    elif entry.status is WATCH:
        assert entry.expires_at is None
        assert entry.thesis is not None
    else:
        assert (entry.expires_at, entry.thesis) == (None, None)


def _assert_rest_unchanged(before: PoolEntry, after: PoolEntry) -> None:
    """状态、过期时间、关注论点和移出以外的字段都不变。"""
    assert (after.symbol, after.source, after.reason, after.sector, after.added_at) == (
        before.symbol,
        before.source,
        before.reason,
        before.sector,
        before.added_at,
    )
    assert (after.score, after.reviewed_at) == (before.score, before.reviewed_at)


def _assert_changed(
    before: PoolEntry, after: PoolEntry, status: StockStatus, expires_at: object, thesis: object
) -> None:
    assert (after.status, after.expires_at, after.thesis) == (status, expires_at, thesis)
    assert after.in_pool
    _assert_rest_unchanged(before, after)
    _assert_invariants(after)


@pytest.mark.parametrize("entry", BASELINE_ENTRIES)
def test_baseline_entries_hold_the_invariants(entry: PoolEntry) -> None:
    _assert_invariants(entry)
    assert entry.in_pool
    assert entry.removal is None


# 状态变化表的每一行；"不在池子里 | 加入"是新建记录，见 test_service.py


def test_candidate_promoted() -> None:
    """状态表"候选 | 升级"。"""
    _assert_changed(AAA, AAA.promoted(THESIS), WATCH, None, THESIS)


def test_candidate_kept() -> None:
    """状态表"候选 | 保持"。"""
    _assert_changed(AAA, AAA.kept(NEW_EXPIRY), CANDIDATE, NEW_EXPIRY, None)


@pytest.mark.parametrize("entry", [AAA, BBB])
def test_candidate_or_watch_deleted(entry: PoolEntry) -> None:
    """状态表"候选 | 删除"和"关注 | 删除"。"""
    removed = entry.removed(DELETED, BASELINE_NOW, "论点不再成立")

    assert removed.removal == Removal(DELETED, BASELINE_NOW, "论点不再成立")
    assert not removed.in_pool
    assert removed.status is entry.status
    _assert_rest_unchanged(entry, removed)


def test_candidate_expired() -> None:
    """状态表"候选 | 过期时间已到，代码移出"。"""
    removed = AAA.removed(EXPIRED, at(20, day=27))

    assert removed.removal == Removal(EXPIRED, at(20, day=27), None)
    assert not removed.in_pool


def test_candidate_bought() -> None:
    """状态表"候选 | 买入成交"。"""
    _assert_changed(AAA, AAA.bought(), HOLDING, None, None)


def test_watch_demoted() -> None:
    """状态表"关注 | 降为候选"。"""
    _assert_changed(BBB, BBB.demoted(NEW_EXPIRY), CANDIDATE, NEW_EXPIRY, None)


def test_watch_bought() -> None:
    """状态表"关注 | 买入成交"。"""
    _assert_changed(BBB, BBB.bought(), HOLDING, None, None)


def test_holding_bought_again_is_unchanged() -> None:
    """状态表"持仓 | 买入成交（加仓）"。"""
    assert CCC.bought() == CCC


def test_holding_closed_by_code() -> None:
    """状态表"持仓 | 代码触发的清仓"。"""
    _assert_changed(CCC, CCC.closed_to_candidate(NEW_EXPIRY), CANDIDATE, NEW_EXPIRY, None)


def test_holding_closed_by_pm_to_watch() -> None:
    """状态表"持仓 | PM 发起的清仓，PM 选择转为关注"。"""
    _assert_changed(CCC, CCC.closed_to_watch(THESIS), WATCH, None, THESIS)


def test_holding_closed_by_pm_and_removed() -> None:
    """状态表"持仓 | PM 发起的清仓，PM 选择移出"。"""
    removed = CCC.removed(CLOSED, BASELINE_NOW)

    assert removed.removal == Removal(CLOSED, BASELINE_NOW, None)
    assert not removed.in_pool
    _assert_rest_unchanged(CCC, removed)


# 代码发起的变化遇到不对的状态


def test_bought_after_removal_is_an_error() -> None:
    with pytest.raises(ValueError, match="不在池子里"):
        AAA.removed(DELETED, BASELINE_NOW, "论点不再成立").bought()


_CLOSINGS: list[Callable[[PoolEntry], PoolEntry]] = [
    lambda e: e.closed_to_candidate(NEW_EXPIRY),
    lambda e: e.closed_to_watch(THESIS),
    lambda e: e.removed(CLOSED, BASELINE_NOW),
]


@pytest.mark.parametrize("close", _CLOSINGS)
@pytest.mark.parametrize("entry", [AAA, BBB, CCC.removed(CLOSED, at(15, day=13))])
def test_closing_anything_but_a_holding_is_an_error(
    entry: PoolEntry, close: Callable[[PoolEntry], PoolEntry]
) -> None:
    """候选、关注和已经移出的记录都不能清仓。"""
    with pytest.raises(ValueError, match="不是池子里的持仓"):
        close(entry)


# 记录评分


@pytest.mark.parametrize("score", [-2, -1, 0, 1, 2])
def test_scored(score: int) -> None:
    scored = AAA.scored(score, at(16))

    assert (scored.score, scored.reviewed_at) == (score, at(16))
    assert (scored.status, scored.expires_at, scored.thesis) == (CANDIDATE, AAA.expires_at, None)


@pytest.mark.parametrize("score", [3, -3])
def test_score_out_of_range_is_an_error(score: int) -> None:
    with pytest.raises(ValueError, match="-2 到 \\+2"):
        AAA.scored(score, at(16))
