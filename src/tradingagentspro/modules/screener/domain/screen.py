"""粗筛的类型和规则：截止交易日和交易日窗口、股票范围、上升趋势、三个条件、得分和排序。

见 stock-screen spec 和 add-stock-screen 的 design D6、D7。查板块和取前 N 只要向数据源请求，在
service 里做。
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tradingagentspro.contracts.listing import Exchange, Listing, SecurityType
from tradingagentspro.modules.screener.config import ScreenerConfig
from tradingagentspro.modules.screener.domain.indicators import (
    DailyBar,
    average_dollar_volume,
    pullback,
    relative_strength,
    uptrend,
    volume_ratio,
)
from tradingagentspro.shared.trading_calendar import TradingCalendar


class Condition(StrEnum):
    """打分的三个条件，顺序就是结果里列出的顺序。"""

    RELATIVE_STRENGTH = "relative_strength"  # 相对强度
    PULLBACK = "pullback"  # 回调
    VOLUME_SURGE = "volume_surge"  # 成交量异常


@dataclass(frozen=True, slots=True)
class Security:
    """股票列表数据源给出的一只股票。数据来自数据源，不加约束。"""

    symbol: str
    exchange: Exchange
    security_type: SecurityType


@dataclass(frozen=True, slots=True)
class Scope:
    """允许的范围和流动性门槛，取自股票池的配置，见 design D4。"""

    exchanges: frozenset[Exchange]
    security_types: frozenset[SecurityType]
    min_average_dollar_volume: Decimal

    def allows(self, security: Security) -> bool:
        return security.exchange in self.exchanges and security.security_type in self.security_types


@dataclass(frozen=True, slots=True)
class Metrics:
    """一只在上升趋势里的合格股票在截止交易日的指标。"""

    relative_strength: Decimal
    pullback: Decimal  # 回调幅度
    volume_ratio: Decimal  # 量比


@dataclass(frozen=True, slots=True)
class Candidate:
    """名单里的一只股票。listing 在加入股票池时直接用。"""

    symbol: str
    listing: Listing
    metrics: Metrics
    conditions: tuple[Condition, ...]  # 满足的条件，按 Condition 的顺序

    @property
    def score(self) -> int:
        return len(self.conditions)


@dataclass(frozen=True, slots=True)
class ScreenRun:
    """一次粗筛的结果，见 stock-screen spec「粗筛的结果」。"""

    day: date  # 截止交易日
    universe: int  # 数据源给出的股票数
    eligible: int  # 合格的股票数，包括被排除的
    trending: int  # 合格的股票里在上升趋势里的
    candidates: tuple[Candidate, ...]  # 按名次


@dataclass(frozen=True, slots=True)
class Ranked:
    """排进去的一只股票，还没有查板块。"""

    security: Security
    average_dollar_volume: Decimal
    metrics: Metrics
    conditions: tuple[Condition, ...]

    def candidate(self, sector: str) -> Candidate:
        """查到板块以后，名单里的这一只。"""
        listing = Listing(
            exchange=self.security.exchange,
            security_type=self.security.security_type,
            average_dollar_volume=self.average_dollar_volume,
            sector=sector,
        )
        return Candidate(self.security.symbol, listing, self.metrics, self.conditions)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """一次粗筛在查板块之前的结果。"""

    eligible: int
    trending: int
    ranked: tuple[Ranked, ...]  # 按 spec「得分和排序」，0 分的不在里面


def cutoff(calendar: TradingCalendar, now: datetime, delay: timedelta) -> date:
    """截止交易日：收盘时刻不晚于 now - delay 的最后一个交易日。"""
    return calendar.last_session_before(now - delay).day


def sessions(calendar: TradingCalendar, last: date, count: int) -> tuple[date, ...]:
    """last 及之前共 count 个交易日，从早到晚。"""
    days = [last]
    while len(days) < count:
        days.append(calendar.previous_session(days[-1]).day)
    return tuple(reversed(days))


def history_sessions(config: ScreenerConfig) -> int:
    """H：粗筛要取的交易日数，见 stock-screen spec「截止交易日」。"""
    return max(
        config.relative_strength_sessions + 1,
        config.long_ma_sessions,
        config.volume_recent_sessions + config.volume_base_sessions,
        config.pullback_sessions,
        config.dollar_volume_sessions,
    )


def evaluate(
    securities: Sequence[Security],
    bars: Mapping[str, Sequence[DailyBar]],
    days: Sequence[date],
    scope: Scope,
    config: ScreenerConfig,
) -> Evaluation:
    """范围、合格、上升趋势、条件和排序。

    days 是截止交易日及之前 H 个交易日；bars 是数据源给出的日线，含基准。基准缺日线时抛
    ValueError，写明基准和缺的交易日。
    """
    benchmark = bars.get(config.benchmark, ())
    missing = sorted(set(days) - {b.day for b in benchmark})
    if missing:
        raise ValueError(f"基准 {config.benchmark} 缺 {', '.join(map(str, missing))} 的日线")

    adv_days = days[-config.dollar_volume_sessions :]
    eligible: list[tuple[Security, Decimal, Sequence[DailyBar]]] = []
    for security in securities:
        own = bars.get(security.symbol, ())
        if (
            security.symbol == config.benchmark
            or not scope.allows(security)
            or tuple(b.day for b in own) != tuple(days)
        ):
            continue
        adv = average_dollar_volume(own, adv_days)
        if adv >= scope.min_average_dollar_volume:
            eligible.append((security, adv, own))

    strengths = {s.symbol: relative_strength(own, benchmark, config) for s, _, own in eligible}
    ordered = sorted(strengths.values())
    top = config.relative_strength_top * len(eligible)
    trending = [e for e in eligible if uptrend(e[2], config)]
    ranked: list[Ranked] = []
    for security, adv, own in trending:
        metrics = Metrics(
            relative_strength=strengths[security.symbol],
            pullback=pullback(own, config),
            volume_ratio=volume_ratio(own, config),
        )
        higher = len(ordered) - bisect_right(ordered, metrics.relative_strength)
        met = {
            Condition.RELATIVE_STRENGTH: higher < top,
            Condition.PULLBACK: config.min_pullback <= metrics.pullback <= config.max_pullback,
            Condition.VOLUME_SURGE: metrics.volume_ratio >= config.min_volume_ratio,
        }
        conditions = tuple(c for c in Condition if met[c])
        if conditions:
            ranked.append(Ranked(security, adv, metrics, conditions))
    ranked.sort(key=lambda r: (-len(r.conditions), -r.metrics.relative_strength, r.security.symbol))
    return Evaluation(len(eligible), len(trending), tuple(ranked))
