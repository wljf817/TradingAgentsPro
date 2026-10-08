"""股票池用例的测试。每个测试对应 openspec 的 stock-pool spec 里的一个场景。

基准数据见 tests/modules/stockpool/builders.py。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.fakes.clock import FakeClock
from tests.modules.stockpool.builders import (
    AAA,
    ADD_REASON,
    BASELINE_NOW,
    BBB,
    CCC,
    REMOVE_REASON,
    THESIS,
    at,
    listing,
    stock_pool_service,
)
from tradingagentspro.contracts.listing import Exchange, SecurityType
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.stockpool import (
    PoolEntry,
    PoolSource,
    Rejection,
    Removal,
    RemovalKind,
    StockPoolService,
)

CANDIDATE, WATCH, HOLDING = StockStatus.CANDIDATE, StockStatus.WATCH, StockStatus.HOLDING
EXPLORATION, MANUAL = PoolSource.EXPLORATION, PoolSource.MANUAL
DELETED, EXPIRED, CLOSED = RemovalKind.DELETED, RemovalKind.EXPIRED, RemovalKind.CLOSED


def _add(service: StockPoolService, symbol: str = "DDD", **changes: Any) -> PoolEntry | Rejection:
    """spec 里的"加入 DDD"：来源 PM 探索，基准理由，基准上市信息；changes 覆盖其中的参数。"""
    fields: dict[str, Any] = {"source": EXPLORATION, "reason": ADD_REASON, "listing": listing()}
    return service.add(symbol, **(fields | changes))


def _remove(
    service: StockPoolService, symbol: str, reason: str = REMOVE_REASON
) -> PoolEntry | Rejection:
    """删除 symbol，默认是 spec 里删除的理由。"""
    return service.remove(symbol, reason)


def _passed(result: PoolEntry | Rejection) -> PoolEntry:
    assert isinstance(result, PoolEntry), result
    return result


def _clocked(now: datetime = BASELINE_NOW, **config: Any) -> tuple[StockPoolService, FakeClock]:
    """从 now 开始的股票池用例和它的时钟。"""
    clock = FakeClock(now)
    return stock_pool_service(clock, **config), clock


def _symbols(service: StockPoolService) -> list[str]:
    return [e.symbol for e in service.entries()]


def _status(service: StockPoolService, symbol: str) -> StockStatus | None:
    entry = service.entry(symbol)
    return None if entry is None else entry.status


# 股票池的记录


def test_record_score() -> None:
    """spec「记录评分」。"""
    service, clock = _clocked()

    aaa = _passed(service.record_score("AAA", 1))
    assert (aaa.score, aaa.reviewed_at) == (1, at(15))

    clock.set(at(16))
    service.record_score("AAA", -1)
    service.record_score("CCC", 2)

    assert service.entry("AAA") == AAA.scored(-1, at(16))
    assert service.entry("CCC") == CCC.scored(2, at(16))


@pytest.mark.parametrize("score", [3, -3])
def test_score_out_of_range(score: int) -> None:
    """spec「评分超出范围」。"""
    service = stock_pool_service()

    with pytest.raises(ValueError):
        service.record_score("AAA", score)
    assert service.entry("AAA") == AAA


def test_cannot_score_a_stock_outside_the_pool() -> None:
    """spec「不在池子里的股票不能记录评分」。"""
    assert stock_pool_service().record_score("ZZZ", 1) == Rejection.NOT_IN_POOL


def test_not_in_the_pool_after_removal() -> None:
    """spec「移出后不在池子里」。"""
    service = stock_pool_service()

    _remove(service, "AAA")

    assert _symbols(service) == ["BBB", "CCC"]
    assert service.entry("AAA") is None


# 状态变化


def test_added_as_a_candidate() -> None:
    """spec「加入成为候选」。"""
    service = stock_pool_service()

    ddd = _passed(_add(service))

    assert service.entry("DDD") == ddd
    assert (ddd.status, ddd.source, ddd.reason, ddd.sector) == (
        CANDIDATE,
        EXPLORATION,
        ADD_REASON,
        "科技",
    )
    assert (ddd.added_at, ddd.expires_at) == (at(15), at(20, day=28))
    assert (ddd.thesis, ddd.score, ddd.reviewed_at) == (None, None, None)


def test_candidate_promoted_to_watch() -> None:
    """spec「候选升级为关注」。"""
    service = stock_pool_service()

    aaa = _passed(service.promote("AAA", THESIS))

    assert (aaa.status, aaa.thesis, aaa.expires_at) == (WATCH, THESIS, None)
    assert service.entry("AAA") == aaa


def test_candidate_kept() -> None:
    """spec「候选保持」。"""
    service = stock_pool_service()

    aaa = _passed(service.keep("AAA"))

    assert (aaa.status, aaa.expires_at) == (CANDIDATE, at(20, day=28))
    assert service.entry("AAA") == aaa


def test_delete_a_candidate_or_a_watch() -> None:
    """spec「删除候选或关注」。"""
    service = stock_pool_service()

    aaa = _passed(_remove(service, "AAA"))
    bbb = _passed(_remove(service, "BBB"))

    assert aaa.removal == bbb.removal == Removal(DELETED, at(15), REMOVE_REASON)
    assert _symbols(service) == ["CCC"]


def test_watch_demoted_to_candidate() -> None:
    """spec「关注降为候选」。"""
    service = stock_pool_service()

    bbb = _passed(service.demote("BBB"))

    assert (bbb.status, bbb.thesis, bbb.expires_at) == (CANDIDATE, None, at(20, day=28))
    assert service.entry("BBB") == bbb


def test_operations_the_status_does_not_allow() -> None:
    """spec「状态不允许的操作」。"""
    service = stock_pool_service()
    before = service.entries()

    results = [
        service.promote("BBB", THESIS),
        service.promote("CCC", THESIS),
        service.demote("AAA"),
        service.demote("CCC"),
        service.keep("BBB"),
        service.keep("CCC"),
    ]

    assert results == [Rejection.WRONG_STATUS] * 6
    assert service.entries() == before


def test_promote_demote_or_keep_a_stock_outside_the_pool() -> None:
    """spec「对不在池子里的股票升降级或保持」。"""
    service = stock_pool_service()
    _remove(service, "AAA")

    results = [
        service.promote("AAA", THESIS),
        service.promote("ZZZ", THESIS),
        service.demote("ZZZ"),
        service.keep("AAA"),
    ]

    assert results == [Rejection.NOT_IN_POOL] * 4


# 加入候选


@pytest.mark.parametrize("reason", ["", "   "])
def test_add_without_a_reason(reason: str) -> None:
    """spec「缺少理由」。"""
    service = stock_pool_service()

    assert _add(service, reason=reason) == Rejection.MISSING_REASON
    assert service.entry("DDD") is None


def test_add_a_stock_already_in_the_pool() -> None:
    """spec「已在池子里」。"""
    service = stock_pool_service()
    before = service.entries()

    for symbol in ("AAA", "BBB", "CCC"):
        assert _add(service, symbol) == Rejection.ALREADY_IN_POOL
    assert service.entries() == before


def test_add_an_unknown_symbol() -> None:
    """spec「股票代码不存在」。"""
    assert _add(stock_pool_service(), listing=None) == Rejection.UNKNOWN_SYMBOL


@pytest.mark.parametrize(
    ("exchange", "security_type"),
    [
        (Exchange.NYSE, SecurityType.ETF),
        (Exchange.NASDAQ, SecurityType.LEVERAGED),
        (Exchange.OTHER, SecurityType.COMMON_STOCK),
    ],
)
def test_allowed_range(exchange: Exchange, security_type: SecurityType) -> None:
    """spec「允许交易的范围」。"""
    service = stock_pool_service()

    outside = listing(exchange=exchange, security_type=security_type)
    assert _add(service, listing=outside) == Rejection.NOT_ALLOWED

    adr = listing(exchange=Exchange.NASDAQ, security_type=SecurityType.ADR)
    assert _passed(_add(service, listing=adr)).status is CANDIDATE


def test_liquidity_boundary() -> None:
    """spec「流动性门槛的边界」。"""
    service = stock_pool_service()

    below = listing(average_dollar_volume=Decimal("9999999.99"))
    assert _add(service, listing=below) == Rejection.ILLIQUID

    at_threshold = listing(average_dollar_volume=Decimal(10_000_000))
    assert _passed(_add(service, listing=at_threshold)).status is CANDIDATE


def test_first_failing_check_is_given() -> None:
    """spec「多项不通过时给出第一项」。"""
    etf = listing(security_type=SecurityType.ETF, average_dollar_volume=Decimal(1_000_000))

    assert _add(stock_pool_service(), listing=etf) == Rejection.NOT_ALLOWED


def test_manual_add() -> None:
    """spec「手动添加」。"""
    service = stock_pool_service()

    etf = listing(security_type=SecurityType.ETF)
    assert _add(service, source=MANUAL, listing=etf) == Rejection.NOT_ALLOWED

    ddd = _passed(_add(service, source=MANUAL))
    assert (ddd.status, ddd.source) == (CANDIDATE, MANUAL)


# 删除


def test_delete_needs_a_reason() -> None:
    """spec「删除要写理由」。"""
    service = stock_pool_service()

    assert _remove(service, "AAA", " ") == Rejection.MISSING_REASON
    assert _status(service, "AAA") is CANDIDATE


def test_delete_a_stock_outside_the_pool() -> None:
    """spec「删除不在池子里的股票」。"""
    service = stock_pool_service()

    assert _remove(service, "ZZZ") == Rejection.NOT_IN_POOL

    _passed(_remove(service, "AAA"))
    assert _remove(service, "AAA") == Rejection.NOT_IN_POOL


def test_holdings_cannot_be_deleted() -> None:
    """spec「持仓不能删除」。"""
    service = stock_pool_service()

    assert _remove(service, "CCC") == Rejection.HOLDING
    assert _status(service, "CCC") is HOLDING


# 重新加入的冷却期


def test_cooldown_boundary() -> None:
    """spec「冷却期的边界」。"""
    service, clock = _clocked()
    _remove(service, "AAA")

    clock.set(at(19, 59, day=20))
    assert _add(service, "AAA") == Rejection.COOLING_DOWN

    clock.set(at(20, day=20))
    aaa = _passed(_add(service, "AAA"))
    assert (aaa.status, aaa.added_at, aaa.score) == (CANDIDATE, at(20, day=20), None)


def test_cooldown_after_a_removal_after_the_close() -> None:
    """spec「收盘后移出从下一个交易日算起」。"""
    service, clock = _clocked(at(20))
    _remove(service, "AAA")

    clock.set(at(19, 59, day=21))
    assert _add(service, "AAA") == Rejection.COOLING_DOWN

    clock.set(at(20, day=21))
    assert _passed(_add(service, "AAA")).status is CANDIDATE


# 池子容量和关注名额


def test_pool_size_boundary() -> None:
    """spec「池子容量的边界」。"""
    service = stock_pool_service(max_pool_size=3)

    _passed(_add(service))
    assert [e.symbol for e in service.entries() if e.status is not HOLDING] == ["AAA", "BBB", "DDD"]

    assert _add(service, "EEE") == Rejection.POOL_FULL


def test_watch_quota_boundary() -> None:
    """spec「关注名额的边界」。"""
    service = stock_pool_service(max_watch=2)

    _passed(service.promote("AAA", THESIS))
    assert [e.symbol for e in service.entries() if e.status is WATCH] == ["AAA", "BBB"]

    _passed(_add(service))
    assert service.promote("DDD", THESIS) == Rejection.WATCH_FULL
    assert _status(service, "DDD") is CANDIDATE


# 每天的增删次数


def test_daily_limit_boundary() -> None:
    """spec「次数上限的边界」。"""
    service = stock_pool_service(max_daily_changes=2)

    _passed(_add(service))
    _passed(_remove(service, "AAA"))

    assert _add(service, "EEE") == Rejection.DAILY_LIMIT
    assert _remove(service, "BBB") == Rejection.DAILY_LIMIT


def test_rejected_operations_do_not_count() -> None:
    """spec「被拒绝的操作不计入」。"""
    service = stock_pool_service(max_daily_changes=1)

    assert _add(service, "ZZZ", listing=None) == Rejection.UNKNOWN_SYMBOL
    assert _remove(service, "CCC") == Rejection.HOLDING

    assert _passed(_add(service)).status is CANDIDATE


def test_changes_after_the_close_count_for_the_next_trading_day() -> None:
    """spec「收盘后的增删算下一个交易日」。"""
    service, clock = _clocked(at(19, 59), max_daily_changes=1)
    _passed(_add(service))

    clock.set(at(20))
    _passed(_add(service, "EEE"))

    clock.set(at(15, day=15))
    assert _add(service, "FFF") == Rejection.DAILY_LIMIT


def test_weekend_counts_for_the_next_trading_day() -> None:
    """spec「周末并进下一个交易日」。"""
    service, clock = _clocked(at(15, day=17), max_daily_changes=1)
    _passed(_add(service))

    clock.set(at(15, day=19))
    assert _add(service, "EEE") == Rejection.DAILY_LIMIT

    clock.set(at(20, day=19))
    _passed(_add(service, "EEE"))


# 候选的过期时间


def test_expiry_counts_trading_days() -> None:
    """spec「过期时间按交易日计算」。"""
    service, clock = _clocked()
    assert _passed(_add(service)).expires_at == at(20, day=28)

    clock.set(at(20, 30))
    assert _passed(_add(service, "EEE")).expires_at == at(20, day=29)

    clock.set(at(15, day=20))
    assert _passed(service.keep("AAA")).expires_at == at(21, day=3, month=11)


# 股票池配置


def test_allow_etfs() -> None:
    """spec「允许 ETF」。"""
    types = frozenset({SecurityType.COMMON_STOCK, SecurityType.ADR, SecurityType.ETF})
    service = stock_pool_service(allowed_security_types=types)

    etf = listing(security_type=SecurityType.ETF)
    assert _passed(_add(service, listing=etf)).status is CANDIDATE

    leveraged = listing(exchange=Exchange.NASDAQ, security_type=SecurityType.LEVERAGED)
    assert _add(service, "EEE", listing=leveraged) == Rejection.NOT_ALLOWED


# 代码发起的操作：状态变化


def test_expired_candidate_removed_by_code() -> None:
    """spec「候选超期由代码移出」。"""
    service = stock_pool_service(FakeClock(at(20, day=27)))

    [aaa] = service.expire_candidates()

    assert aaa.symbol == "AAA"
    assert aaa.removal == Removal(EXPIRED, at(20, day=27), None)
    assert service.entry("AAA") is None


def test_watch_bought_becomes_a_holding() -> None:
    """spec「关注股买入成交变为持仓」。"""
    service = stock_pool_service()

    bbb = service.bought("BBB")

    assert (bbb.status, bbb.thesis, bbb.expires_at) == (HOLDING, None, None)
    assert service.entry("BBB") == bbb


def test_candidate_bought_becomes_a_holding_too() -> None:
    """spec「候选买入成交也变为持仓」。"""
    service = stock_pool_service()

    aaa = service.bought("AAA")

    assert (aaa.status, aaa.expires_at) == (HOLDING, None)
    assert service.entry("AAA") == aaa


def test_adding_to_a_holding_changes_nothing() -> None:
    """spec「加仓成交不改变持仓」。"""
    service = stock_pool_service()

    assert service.bought("CCC") == CCC
    assert service.entry("CCC") == CCC


def test_closed_by_code_becomes_a_candidate() -> None:
    """spec「代码触发的清仓转为候选」。"""
    service = stock_pool_service()

    ccc = service.closed_by_code("CCC")

    assert (ccc.status, ccc.expires_at) == (CANDIDATE, at(20, day=28))
    assert (ccc.sector, ccc.source, ccc.reason, ccc.score) == (
        CCC.sector,
        CCC.source,
        CCC.reason,
        CCC.score,
    )
    assert service.entry("CCC") == ccc


def test_closed_by_pm_to_watch() -> None:
    """spec「PM 清仓后转为关注」。"""
    service = stock_pool_service()

    ccc = service.closed_by_pm("CCC", THESIS)

    assert (ccc.status, ccc.thesis) == (WATCH, THESIS)
    assert service.entry("CCC") == ccc


def test_closed_by_pm_and_removed() -> None:
    """spec「PM 清仓后移出」。"""
    service = stock_pool_service()

    ccc = service.closed_by_pm("CCC", None)

    assert ccc.removal == Removal(CLOSED, at(15), None)
    assert service.entry("CCC") is None


def test_code_operations_on_the_wrong_status() -> None:
    """spec「代码发起的操作遇到不对的状态」。"""
    service = stock_pool_service()
    before = service.entries()

    with pytest.raises(ValueError):
        service.bought("ZZZ")
    for symbol in ("AAA", "BBB"):
        with pytest.raises(ValueError):
            service.closed_by_code(symbol)
    for thesis in (THESIS, None):
        with pytest.raises(ValueError):
            service.closed_by_pm("BBB", thesis)

    assert service.entries() == before


# 代码发起的操作：重新加入的冷却期


def test_expiry_cools_down_too() -> None:
    """spec「超期移出也进入冷却期」。"""
    service, clock = _clocked(at(20, day=27))
    service.expire_candidates()

    clock.set(at(15, day=28))
    assert _add(service, "AAA") == Rejection.COOLING_DOWN


def test_removal_after_closing_cools_down_too() -> None:
    """spec「清仓后移出也进入冷却期」。"""
    service, clock = _clocked()
    service.closed_by_pm("CCC", None)

    clock.set(at(15, day=15))
    assert _add(service, "CCC") == Rejection.COOLING_DOWN


# 代码发起的操作：池子容量和关注名额


def test_holdings_do_not_take_pool_size() -> None:
    """spec「持仓不占池子容量」。"""
    service = stock_pool_service(max_pool_size=2)

    assert _add(service) == Rejection.POOL_FULL

    service.bought("BBB")
    assert _passed(_add(service)).status is CANDIDATE


def test_closed_to_watch_can_exceed_the_watch_quota() -> None:
    """spec「清仓后转为关注可以超出关注名额」。"""
    service = stock_pool_service(max_watch=1)

    service.closed_by_pm("CCC", THESIS)
    assert [e.symbol for e in service.entries() if e.status is WATCH] == ["BBB", "CCC"]

    assert service.promote("AAA", THESIS) == Rejection.WATCH_FULL


def test_closed_to_candidate_can_exceed_the_pool_size() -> None:
    """spec「清仓后转为候选可以超出池子容量」。"""
    service = stock_pool_service(max_pool_size=2)

    service.closed_by_code("CCC")
    assert [e.status for e in service.entries()] == [CANDIDATE, WATCH, CANDIDATE]

    assert _add(service) == Rejection.POOL_FULL


# 代码发起的操作：每天的增删次数


def test_changes_other_than_adds_and_deletions_do_not_count() -> None:
    """spec「不是增删的变化不计入」。"""
    service = stock_pool_service(max_daily_changes=1)

    _passed(service.promote("AAA", THESIS))
    _passed(service.demote("BBB"))
    _passed(service.record_score("BBB", 1))
    service.closed_by_pm("CCC", None)

    assert _passed(_add(service)).status is CANDIDATE
    assert _add(service, "EEE") == Rejection.DAILY_LIMIT


# 代码发起的操作：候选的过期时间


def test_expiry_boundary() -> None:
    """spec「到期的边界」。"""
    service, clock = _clocked(at(19, 59, 59, day=27))

    assert service.expire_candidates() == ()
    assert _status(service, "AAA") is CANDIDATE

    clock.set(at(20, day=27))
    [aaa] = service.expire_candidates()
    assert aaa.symbol == "AAA"
    assert aaa.removal is not None and aaa.removal.kind is EXPIRED
    assert (service.entry("BBB"), service.entry("CCC")) == (BBB, CCC)


def test_missed_the_run_after_the_close() -> None:
    """spec「错过收盘后的那一次」。"""
    service = stock_pool_service(FakeClock(at(15, day=29)))

    assert [e.symbol for e in service.expire_candidates()] == ["AAA"]


def test_kept_candidate_expires_at_the_new_time() -> None:
    """spec「保持以后按新的过期时间」。"""
    service, clock = _clocked(at(15, day=20))
    service.keep("AAA")

    clock.set(at(20, day=27))
    assert service.expire_candidates() == ()
    assert _status(service, "AAA") is CANDIDATE


def test_promoted_stock_no_longer_expires() -> None:
    """spec「升级以后不再过期」。"""
    service, clock = _clocked()
    service.promote("AAA", THESIS)

    clock.set(at(15, day=29))
    assert service.expire_candidates() == ()
    assert _status(service, "AAA") is WATCH
