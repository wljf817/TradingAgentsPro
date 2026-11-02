"""论点条件：event-rules spec「论点条件」。

关注论点的入场条件、投资论点的目标价（换成"价格不低于目标价"）和证伪条件中代码能检测的部分各是
一组，一组里任一条件满足就是这一组的一个事件。和价格异动用同一个「行情要是当天的」；涨跌幅用乘法
比较，和价格异动一样不受 Decimal 舍入影响。见 add-thesis-conditions-and-price-alerts 的 design D2。
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome, EventSource
from tradingagentspro.contracts.thesis import (
    Comparison,
    Condition,
    ConditionKind,
    InvestmentThesis,
    WatchThesis,
)
from tradingagentspro.modules.events.domain.ids import Detection, EventKind, event_id
from tradingagentspro.modules.events.domain.price import Reading, today_session
from tradingagentspro.modules.events.domain.text import number, percent
from tradingagentspro.shared.trading_calendar import TradingCalendar

_CHANGE: Final = Decimal("0.0001")  # 说明里的当日涨跌幅四舍五入到 4 位，和价格异动的强度一样

# 每一组的事件：说明的开头和类别；证伪条件触发是持仓风险级别
_GROUPS: Final = {
    EventKind.ENTRY: ("入场条件满足", EventCategory.ENTRY),
    EventKind.TARGET: ("达到目标价", EventCategory.THESIS_CONDITION),
    EventKind.INVALIDATION: ("证伪条件触发", EventCategory.THESIS_CONDITION),
}
_KINDS: Final = {
    ConditionKind.PRICE: "价格",
    ConditionKind.CHANGE: "涨跌幅",
    ConditionKind.VOLUME: "成交量",
}
_COMPARISONS: Final = {Comparison.AT_LEAST: "不低于", Comparison.AT_MOST: "不高于"}


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    """一只股票这一轮要判断的一组条件，任一满足就是这一组的事件。

    kind 是 EventKind.ENTRY、TARGET 或 INVALIDATION，决定事件 ID 的种类、类别和持仓风险级别。
    """

    kind: EventKind
    conditions: tuple[Condition, ...]


def condition_groups(
    watch: WatchThesis | None, investment: InvestmentThesis | None
) -> tuple[ConditionGroup, ...]:
    """股票池里一只股票的论点给出的条件组：关注论点给入场条件；投资论点给目标价，证伪条件中代码
    能检测的部分不为空时再给一组；候选两样都没有，为空。
    """
    groups: list[ConditionGroup] = []
    if watch is not None:
        groups.append(ConditionGroup(EventKind.ENTRY, watch.entry_conditions))
    if investment is not None:
        target = Condition(
            kind=ConditionKind.PRICE, comparison=Comparison.AT_LEAST, value=investment.target_price
        )
        groups.append(ConditionGroup(EventKind.TARGET, (target,)))
        if investment.invalidation_conditions:
            groups.append(
                ConditionGroup(EventKind.INVALIDATION, investment.invalidation_conditions)
            )
    return tuple(groups)


def condition_event(
    symbol: str,
    group: ConditionGroup,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
) -> Event | None:
    """判断一组条件，见 spec「论点条件」；行情不是当天的、没有一条满足时为 None。"""
    if today_session(reading, at, calendar) is None:
        return None
    met = [condition for condition in group.conditions if _met(condition, reading)]
    if not met:
        return None
    title, category = _GROUPS[group.kind]
    described = "；".join(f"{_condition_text(c)}，{_value_text(c, reading)}" for c in met)
    return Event(
        event_id=event_id(symbol, at.day, group.kind),
        symbol=symbol,
        category=category,
        holding_risk=group.kind is EventKind.INVALIDATION,
        source=EventSource.QUOTES,
        strength=None,
        occurred_at=reading.updated_at,
        detected_at=at.at,
        summary=f"{title}：{described}",
        refs=(),
        outcome=EventOutcome.QUEUED,
    )


def _met(condition: Condition, reading: Reading) -> bool:
    """一条条件满足没有。最新价由「行情要是当天的」保证有；没有成交量时成交量条件不满足。"""
    last = reading.last_price
    assert last is not None  # 当天的行情有最新价
    match condition.kind:
        case ConditionKind.PRICE:
            value: Decimal | int | None = last
            threshold = condition.value
        case ConditionKind.CHANGE:
            value = last
            threshold = reading.previous_close * (1 + condition.value)
        case ConditionKind.VOLUME:
            value = reading.volume
            threshold = condition.value
    if value is None:
        return False
    if condition.comparison is Comparison.AT_LEAST:
        return value >= threshold
    return value <= threshold


def _condition_text(condition: Condition) -> str:
    """条件的写法：价格不高于 95、涨跌幅不低于 3%、成交量不低于 2000000 股。"""
    head = f"{_KINDS[condition.kind]}{_COMPARISONS[condition.comparison]}"
    match condition.kind:
        case ConditionKind.PRICE:
            return f"{head} {number(condition.value)}"
        case ConditionKind.CHANGE:
            return f"{head} {percent(condition.value)}"
        case ConditionKind.VOLUME:
            return f"{head} {number(condition.value)} 股"


def _value_text(condition: Condition, reading: Reading) -> str:
    """条件比较的量这时的值：最新价、当日涨跌幅或当日累计成交量。"""
    last = reading.last_price
    assert last is not None  # 满足的条件都在当天的行情上判断
    match condition.kind:
        case ConditionKind.PRICE:
            return f"最新价 {number(last)}"
        case ConditionKind.CHANGE:
            previous = reading.previous_close
            change = ((last - previous) / previous).quantize(_CHANGE, ROUND_HALF_UP)
            return f"当日涨跌幅 {percent(change)}，最新价 {number(last)}"
        case ConditionKind.VOLUME:
            return f"当日累计成交量 {reading.volume} 股"
