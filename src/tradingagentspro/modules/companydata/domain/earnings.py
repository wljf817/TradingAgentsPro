"""财报：一次财报发布和最近几个季度的 EPS surprise，见 company-data spec「财报的记录」和
add-earnings-calendar-and-sector 的 design D4。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self


class ReleaseTime(StrEnum):
    """财报在发布日的什么时段发布。"""

    BEFORE_OPEN = "before_open"  # 盘前
    AFTER_CLOSE = "after_close"  # 盘后
    DURING_MARKET = "during_market"  # 盘中


@dataclass(frozen=True, slots=True)
class EarningsRelease:
    """一只股票的一次财报发布。数据来自数据源，不加约束。

    - fiscal_year、fiscal_quarter：数据源给出的财年和季度；同一只股票的同一个财年季度是同一次发布，
      发布日和数值变化时是它的新版本
    - day：发布日，美东日期
    - time：发布时段；数据源没写时为 None
    - eps_*：每股收益的一致预期和实际值；revenue_*：营收的一致预期和实际值，按数据源给的货币。
      没有时为 None
    """

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    day: date
    time: ReleaseTime | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None

    @property
    def key(self) -> str:
        """一次发布的键：股票代码和财年季度，比如 AAPL:2026Q4。"""
        return f"{self.symbol}:{self.fiscal_year}Q{self.fiscal_quarter}"


@dataclass(frozen=True, slots=True)
class EarningsSurprise:
    """一个季度的 EPS surprise。数据来自数据源，不加约束，数值没有时为 None。

    - period：报告期，这个季度最后一天
    - surprise、surprise_percent：数据源算好的实际减预期、以及它占预期的百分比，不重新计算
    """

    period: date
    fiscal_year: int
    fiscal_quarter: int
    eps_actual: Decimal | None
    eps_estimate: Decimal | None
    surprise: Decimal | None
    surprise_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class SurpriseHistory:
    """一只股票最近几个季度的 surprise，按报告期从新到旧；数据源没有时是空的一组。"""

    symbol: str
    quarters: tuple[EarningsSurprise, ...]

    @classmethod
    def of(cls, symbol: str, surprises: Iterable[EarningsSurprise]) -> Self:
        """按报告期从新到旧排好：数据源换了顺序时内容不变，不会多存一个版本。"""
        return cls(symbol, tuple(sorted(surprises, key=lambda s: s.period, reverse=True)))
