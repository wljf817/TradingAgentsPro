"""收盘检查：每个交易日收盘以后，按一份快照决定这次开哪些定时心跳案件，见 close-check spec。

顺延不单独记：到期而还没有续期的日期每次都算到期，已经在排队或运行的不重复开。降级模式和调用预算
不进快照：收盘检查照常开，案件什么时候启动由队列决定。
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.modules.heartbeat.config import HeartbeatConfig
from tradingagentspro.shared.trading_calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class CheckItem:
    """定时心跳的一个检查项：开出的案件类型；针对单只股票的带股票，寻股和每周复盘不带。

    和调度器的 HeartbeatTrigger 一一对应，M4 用 HeartbeatTrigger(item.case_type, item.symbol)
    入队；不合法的组合在那时报错，这里不重复检查。
    """

    case_type: CaseType
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class ThesisDates:
    """一只持仓或关注股的论点里的复查日和催化剂截止日期。"""

    symbol: str
    review_day: date
    catalyst_deadline: date


@dataclass(frozen=True, slots=True)
class PoolCandidate:
    """股票池里的一只候选。"""

    symbol: str
    score: int | None  # 最新评分；还没有时为 None
    expires_at: datetime  # 过期时间，带时区
    new: bool  # 新候选：加入或代码触发的清仓转为候选以后还没有候选评估得出结论；降为候选的不算


@dataclass(frozen=True, slots=True)
class CloseSnapshot:
    """收盘检查用的一份快照，由调用方在收盘以后从各模块组出。"""

    today: date  # 刚收盘的交易日
    holdings: tuple[ThesisDates, ...]
    watches: tuple[ThesisDates, ...]
    candidates: tuple[PoolCandidate, ...]  # 股票池里的全部候选
    # 最近一次得出结论的寻股，结论时刻归属的交易日；还没有时为 None
    last_exploration_day: date | None
    # 定时心跳开出的、在排队或运行的案件。只用来判断在不在和计数，不遍历出输出：
    # frozenset 的遍历顺序随字符串哈希变化，输出的顺序全部来自排序
    in_flight: frozenset[CheckItem]


_REVIEW_TYPES: Final = frozenset(
    {
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.WATCH_REVIEW,
        CaseType.WATCH_CATALYST_REVIEW,
    }
)
# 复查日到期开的、催化剂截止日期到期开的
_HOLDING_TYPES: Final = (CaseType.HOLDING_REVIEW, CaseType.HOLDING_CATALYST_REVIEW)
_WATCH_TYPES: Final = (CaseType.WATCH_REVIEW, CaseType.WATCH_CATALYST_REVIEW)
_STRONG_SCORE: Final = 1  # 好候选：最新评分 ≥ 1，Proposal 的定义


def close_check(
    snapshot: CloseSnapshot, calendar: TradingCalendar, config: HeartbeatConfig
) -> tuple[CheckItem, ...]:
    """这次收盘要开的检查项，按 spec「开的顺序」。today 不是交易日时抛 ValueError。"""
    if not calendar.is_session(snapshot.today):
        raise ValueError(f"{snapshot.today} 不是交易日")
    items = [
        *_reviews(snapshot, snapshot.holdings, _HOLDING_TYPES),
        *_reviews(snapshot, snapshot.watches, _WATCH_TYPES),
        *_evaluations(snapshot, config),
    ]
    if _exploration_due(snapshot, calendar, config):
        items.append(CheckItem(CaseType.EXPLORATION))
    if _weekly_due(snapshot, calendar):
        items.append(CheckItem(CaseType.WEEKLY_RETROSPECTIVE))
    return tuple(items)


def _reviews(
    snapshot: CloseSnapshot,
    stocks: tuple[ThesisDates, ...],
    types: tuple[CaseType, CaseType],
) -> list[CheckItem]:
    """到期的复查类案件，按股票代码；每只股票最多一个，催化剂到期时开催化剂复查。

    到期是不晚于今天：错过的收盘检查、没有结论的案件，日期没续期就一直到期。四种复查类案件任何
    一种在排队或运行都挡：在跑的案件续期时，复查日和到期的催化剂都要写新的。
    """
    review, catalyst_review = types
    busy = {i.symbol for i in snapshot.in_flight if i.case_type in _REVIEW_TYPES}
    items: list[CheckItem] = []
    for stock in sorted(stocks, key=lambda s: s.symbol):
        if stock.symbol in busy:
            continue
        if stock.catalyst_deadline <= snapshot.today:
            items.append(CheckItem(catalyst_review, stock.symbol))
        elif stock.review_day <= snapshot.today:
            items.append(CheckItem(review, stock.symbol))
    return items


def _evaluations(snapshot: CloseSnapshot, config: HeartbeatConfig) -> list[CheckItem]:
    """新候选的候选评估：在排队或运行的也占每天的上限，其余按 spec 的顺序选。

    在排队或运行的超过上限（比如调低了配置）时不开，也避免负数切片从末尾取。选不上的不记下来：
    它们仍是新候选，下一次收盘检查重新排。
    """
    queued = {i.symbol for i in snapshot.in_flight if i.case_type is CaseType.CANDIDATE_EVALUATION}
    slots = max(config.max_daily_evaluations - len(queued), 0)
    waiting = sorted(
        (c for c in snapshot.candidates if c.new and c.symbol not in queued),
        key=_evaluation_order,
    )
    return [CheckItem(CaseType.CANDIDATE_EVALUATION, c.symbol) for c in waiting[:slots]]


def _evaluation_order(candidate: PoolCandidate) -> tuple[bool, int, datetime, str]:
    """没有评分的在最后；评分从高到低；评分相同的先过期的在前；再按股票代码。"""
    score = candidate.score
    return (score is None, -(score or 0), candidate.expires_at, candidate.symbol)


def _exploration_due(
    snapshot: CloseSnapshot, calendar: TradingCalendar, config: HeartbeatConfig
) -> bool:
    """三个条件任一项满足，并且没有寻股在排队或运行、不在冷却期内。

    上一次寻股的那一天可以晚于今天（今天收盘后得出的结论），这时冷却期结束的那一天也晚于今天。
    临近过期和过期时间比：过期时间是某个交易日的收盘时刻，今天之后第 n 个交易日的收盘时刻也算。
    "超过一半"用乘法比较；没有候选时 0 > 0 不满足，由有效候选不足开寻股。
    """
    if CheckItem(CaseType.EXPLORATION) in snapshot.in_flight:
        return False
    last = snapshot.last_exploration_day
    if last is not None:
        resume = calendar.add_sessions(last, config.exploration_cooldown_sessions).day
        if snapshot.today < resume:
            return False
    candidates = snapshot.candidates
    viable = sum(1 for c in candidates if c.score is None or c.score >= 0)
    strong = sum(1 for c in candidates if c.score is not None and c.score >= _STRONG_SCORE)
    horizon = calendar.add_sessions(snapshot.today, config.expiring_sessions).close
    expiring = sum(1 for c in candidates if c.expires_at <= horizon)
    return (
        viable < config.min_viable_candidates
        or strong < config.min_strong_candidates
        or expiring * 2 > len(candidates)
    )


def _weekly_due(snapshot: CloseSnapshot, calendar: TradingCalendar) -> bool:
    """今天是这周最后一个交易日：下一个交易日在下一周（ISO 周，周一到周日）。

    比较 ISO 的年和周，跨年也对，比如 2026-12-31（2026 年第 53 周）和 2027-01-04（2027 年第 1 周）。
    """
    if CheckItem(CaseType.WEEKLY_RETROSPECTIVE) in snapshot.in_flight:
        return False
    following = calendar.next_session(snapshot.today).day
    return following.isocalendar()[:2] != snapshot.today.isocalendar()[:2]
