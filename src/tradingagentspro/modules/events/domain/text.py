"""事件说明的文字：数值、比例、级别和发布时段的写法。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from tradingagentspro.contracts.snapshot import EarningsTime

LEVELS: Final = {1: "一般", 2: "重大"}
RELEASE_TIMES: Final = {
    EarningsTime.BEFORE_OPEN: "盘前",
    EarningsTime.DURING_MARKET: "盘中",
    EarningsTime.AFTER_CLOSE: "盘后",
}


def number(value: Decimal) -> str:
    """去掉末尾的 0、不用科学计数法：96.000 写成 96，1E+2 写成 100。"""
    return f"{value.normalize():f}"


def percent(value: Decimal) -> str:
    """比例写成百分数：0.045 写成 4.5%。"""
    return f"{number(value * 100)}%"


def negative_text(negative: bool) -> str:
    return "负面" if negative else "不是负面"


def utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
