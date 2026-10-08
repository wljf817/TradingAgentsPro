"""股票池的规则：候选的过期时间、重新加入的冷却期、当天的增删次数，以及加入、删除、升降级和
保持的检查。

检查都按 stock-pool spec 表里的顺序给出第一项不通过的原因，全部通过时为 None。拒绝是 PM 提议的
正常结果，所以作为返回值，不抛异常。
"""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from tradingagentspro.contracts.listing import Listing
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.stockpool.config import StockPoolConfig
from tradingagentspro.modules.stockpool.domain.entry import PoolEntry, RemovalKind
from tradingagentspro.shared.trading_calendar import TradingCalendar


class Rejection(StrEnum):
    """PM 和手动发起的操作不通过的原因。"""

    MISSING_REASON = "missing_reason"  # 缺少理由
    ALREADY_IN_POOL = "already_in_pool"  # 已在池子里
    COOLING_DOWN = "cooling_down"  # 冷却期内
    UNKNOWN_SYMBOL = "unknown_symbol"  # 股票代码不存在
    NOT_ALLOWED = "not_allowed"  # 不在允许交易的范围内
    ILLIQUID = "illiquid"  # 流动性不达标
    POOL_FULL = "pool_full"  # 池子已满
    WATCH_FULL = "watch_full"  # 关注名额已满
    DAILY_LIMIT = "daily_limit"  # 增删次数已用完
    NOT_IN_POOL = "not_in_pool"  # 不在池子里
    HOLDING = "holding"  # 持仓不能删除
    WRONG_STATUS = "wrong_status"  # 状态不允许


def candidate_expiry(
    moment: datetime, calendar: TradingCalendar, config: StockPoolConfig
) -> datetime:
    """在 moment 成为候选或保持时的过期时间：moment 归属的交易日之后，
    第 candidate_ttl_sessions 个交易日的收盘时刻。
    """
    day = calendar.session_closing_after(moment).day
    return calendar.add_sessions(day, config.candidate_ttl_sessions).close


def cooling_down(
    entry: PoolEntry | None, moment: datetime, calendar: TradingCalendar, config: StockPoolConfig
) -> bool:
    """在 moment 重新加入时还在冷却期内：从移出时刻归属的交易日到 moment 归属的交易日，
    不满 readd_cooldown_sessions 个交易日。从来没有加入过、还在池子里的都不在冷却期。
    """
    if entry is None or entry.removal is None:
        return False
    removed = calendar.session_closing_after(entry.removal.at).day
    today = calendar.session_closing_after(moment).day
    return calendar.count_sessions(removed, today) < config.readd_cooldown_sessions


def changes_since(entries: Iterable[PoolEntry], start: datetime) -> int:
    """start 以后通过的加入和删除：加入时刻不早于 start 的记录计一次，删除时刻不早于 start 的
    再计一次。

    每次加入都会新建记录，删除会记下时刻，所以次数从每只股票最新的记录推出。同一只股票同一个
    交易日里不能删除以后又加入（冷却期至少 1 个交易日），新记录不会盖掉当天的删除。
    """
    count = 0
    for entry in entries:
        if entry.added_at >= start:
            count += 1
        removal = entry.removal
        if removal is not None and removal.kind is RemovalKind.DELETED and removal.at >= start:
            count += 1
    return count


def _pool_size(in_pool: Iterable[PoolEntry]) -> int:
    """候选和关注合计，持仓不计入。"""
    return sum(1 for e in in_pool if e.status is not StockStatus.HOLDING)


def _watch_count(in_pool: Iterable[PoolEntry]) -> int:
    return sum(1 for e in in_pool if e.status is StockStatus.WATCH)


def add_rejection(
    reason: str,
    listing: Listing | None,
    previous: PoolEntry | None,
    in_pool: Iterable[PoolEntry],
    changes: int,
    now: datetime,
    calendar: TradingCalendar,
    config: StockPoolConfig,
) -> Rejection | None:
    """在 now 加入一只股票的检查，见 spec「加入候选」。

    - listing：调用方查到的上市信息；查不到时为 None
    - previous：这只股票最新的记录，包括已经移出的；从来没有加入过时为 None
    - in_pool：池子里的全部记录
    - changes：今天已经通过的增删次数
    """
    if not reason.strip():
        return Rejection.MISSING_REASON
    if previous is not None and previous.in_pool:
        return Rejection.ALREADY_IN_POOL
    if cooling_down(previous, now, calendar, config):
        return Rejection.COOLING_DOWN
    if listing is None:
        return Rejection.UNKNOWN_SYMBOL
    if (
        listing.exchange not in config.allowed_exchanges
        or listing.security_type not in config.allowed_security_types
    ):
        return Rejection.NOT_ALLOWED
    if listing.average_dollar_volume < config.min_average_dollar_volume:
        return Rejection.ILLIQUID
    if _pool_size(in_pool) >= config.max_pool_size:
        return Rejection.POOL_FULL
    if changes >= config.max_daily_changes:
        return Rejection.DAILY_LIMIT
    return None


def remove_rejection(
    reason: str, entry: PoolEntry | None, changes: int, config: StockPoolConfig
) -> Rejection | None:
    """删除一只股票的检查，见 spec「删除」。entry 是这只股票最新的记录，changes 是今天已经
    通过的增删次数。
    """
    if not reason.strip():
        return Rejection.MISSING_REASON
    if entry is None or not entry.in_pool:
        return Rejection.NOT_IN_POOL
    if entry.status is StockStatus.HOLDING:
        return Rejection.HOLDING
    if changes >= config.max_daily_changes:
        return Rejection.DAILY_LIMIT
    return None


def status_rejection(entry: PoolEntry | None, required: StockStatus) -> Rejection | None:
    """PM 发起的升级、降为候选和保持要求的状态：升级、保持要求候选，降为候选要求关注。"""
    if entry is None or not entry.in_pool:
        return Rejection.NOT_IN_POOL
    if entry.status is not required:
        return Rejection.WRONG_STATUS
    return None


def promote_rejection(
    entry: PoolEntry | None, in_pool: Iterable[PoolEntry], config: StockPoolConfig
) -> Rejection | None:
    """升级的检查：先要求候选，再要求池子里的关注少于关注名额。"""
    rejection = status_rejection(entry, StockStatus.CANDIDATE)
    if rejection is None and _watch_count(in_pool) >= config.max_watch:
        return Rejection.WATCH_FULL
    return rejection
