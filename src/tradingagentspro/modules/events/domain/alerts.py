"""需要的到价提醒：price-alerts spec「需要的到价提醒」。

和论点条件的事件用同一份条件组（目标价换成条件只写一次）；一条条件对应一个或零个到价提醒。涨跌幅
存比例，换成行情源的单位在适配器里。见 add-thesis-conditions-and-price-alerts 的 design D5、D6。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tradingagentspro.contracts.thesis import Comparison, Condition, ConditionKind
from tradingagentspro.modules.events.domain.conditions import ConditionGroup


class AlertKind(StrEnum):
    """到价提醒的类型。"""

    PRICE_UP = "price_up"  # 价格涨到
    PRICE_DOWN = "price_down"  # 价格跌到
    CHANGE_UP = "change_up"  # 当日涨幅达到，比例
    CHANGE_DOWN = "change_down"  # 当日跌幅达到，比例，正数
    VOLUME_UP = "volume_up"  # 当日成交量达到，股


@dataclass(frozen=True, slots=True)
class PriceAlert:
    """一个到价提醒，由股票、类型和值确定。"""

    symbol: str
    kind: AlertKind
    value: Decimal


@dataclass(frozen=True, slots=True)
class AlertChanges:
    """一次同步的结果：设上和删掉了几个，设不上或删不掉的提醒和行情源给的原因。"""

    added: int
    removed: int
    failed: tuple[tuple[PriceAlert, str], ...]


def wanted_alerts(groups: Mapping[str, tuple[ConditionGroup, ...]]) -> frozenset[PriceAlert]:
    """股票池里每只股票的条件组需要的到价提醒；一样的只留一个。"""
    found = (
        _alert(symbol, condition)
        for symbol, stock_groups in groups.items()
        for group in stock_groups
        for condition in group.conditions
    )
    return frozenset(alert for alert in found if alert is not None)


def _alert(symbol: str, condition: Condition) -> PriceAlert | None:
    """一条条件对应的到价提醒，见 spec 的表；涨跌幅不低于负数、不高于正数时没有。"""
    at_least = condition.comparison is Comparison.AT_LEAST
    value = condition.value
    match condition.kind:
        case ConditionKind.PRICE:
            kind = AlertKind.PRICE_UP if at_least else AlertKind.PRICE_DOWN
        case ConditionKind.CHANGE if at_least and value >= 0:
            kind = AlertKind.CHANGE_UP
        case ConditionKind.CHANGE if not at_least and value <= 0:
            kind, value = AlertKind.CHANGE_DOWN, -value
        case ConditionKind.CHANGE:
            return None
        case ConditionKind.VOLUME:
            kind = AlertKind.VOLUME_UP
    return PriceAlert(symbol, kind, value)
