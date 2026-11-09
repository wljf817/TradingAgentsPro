"""公司数据模块需要的外部接口：数据源、财务事实的数据源、价格和拆股的数据源，以及财报和财务事实的存储。"""

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Protocol

from tradingagentspro.modules.companydata.domain.earnings import (
    EarningsRelease,
    EarningsSurprise,
    SurpriseHistory,
)
from tradingagentspro.modules.companydata.domain.financials import FinancialFact
from tradingagentspro.modules.companydata.domain.valuation import DailyClose, Split


class CompanyDataUnavailableError(RuntimeError):
    """数据源超时、连不上、限频或服务端出错：这次没有取到，以后再取。"""


class CompanyDataSource(Protocol):
    """公司数据的数据源。每次调用发一个请求，失败时抛 CompanyDataUnavailableError，不重试。"""

    def industry(self, symbol: str) -> str | None:
        """数据源给出的行业，原样；查不到这只股票或者没有行业时为 None。"""
        ...

    def earnings_calendar(self, symbol: str, start: date, end: date) -> Sequence[EarningsRelease]:
        """发布日在 start 到 end（含两端）之间的财报发布，顺序和数据源给的相同。"""
        ...

    def earnings_surprises(self, symbol: str) -> Sequence[EarningsSurprise]:
        """数据源给出的最近几个季度的 surprise，顺序和数据源给的相同。"""
        ...


class EarningsStore(Protocol):
    """财报的存储：财报日历按一次发布的键、surprise 按股票保存各个版本。

    数据库实现在 repo.py，只追加不覆盖：内容和最新版本不同时才写入新版本，见 state-store spec。
    数据库的错误和读不出来的记录原样抛出，不当作没有记录。
    """

    def save_release(self, release: EarningsRelease) -> None:
        """保存这次发布最新的内容。"""
        ...

    def releases(self, symbol: str) -> tuple[EarningsRelease, ...]:
        """这只股票每次发布的最新版本，顺序不保证。"""
        ...

    def save_surprises(self, history: SurpriseHistory) -> None:
        """保存这只股票最新的一组 surprise。"""
        ...

    def surprises(self, symbol: str) -> SurpriseHistory | None:
        """最近一次保存的那一组；没有保存过时为 None。"""
        ...

    def releases_as_of(self, symbol: str, at: datetime) -> tuple[EarningsRelease, ...]:
        """这只股票每次发布写入时刻早于 at 的最新版本，顺序不保证；at 以前没有版本的发布没有。"""
        ...

    def surprises_as_of(self, symbol: str, at: datetime) -> SurpriseHistory | None:
        """写入时刻早于 at 的最近一组；at 以前没有保存过时为 None。"""
        ...


class FinancialFactSource(Protocol):
    """财务事实的数据源。每次调用发请求，失败时抛 CompanyDataUnavailableError，不重试。"""

    def financial_facts(
        self, symbol: str, concepts: Collection[str]
    ) -> tuple[FinancialFact, ...] | None:
        """这只股票 concepts 这些科目的全部事实；数据源没有这只股票的财务事实时为 None。"""
        ...


class FinancialFactStore(Protocol):
    """财务事实的存储：只追加，同一条事实只存一次，见 company-data spec「财务事实的存储」。

    数据库的错误和读不出来的记录原样抛出，不当作没有记录。
    """

    def add(self, facts: Sequence[FinancialFact]) -> None:
        """存下还没有的事实；已经有的不改动。同时存同一条时只存一条，都不报错。"""
        ...

    def facts(self, symbol: str, filed_before: date) -> tuple[FinancialFact, ...]:
        """这只股票提交日早于 filed_before 的全部事实，顺序不保证。"""
        ...


class PriceSource(Protocol):
    """原始收盘价的数据源。每次调用发请求，失败时抛 CompanyDataUnavailableError，不重试。"""

    def daily_closes(self, symbol: str, start: date, end: date) -> tuple[DailyClose, ...]:
        """start 到 end（含两端）每个交易日的原始收盘价，按交易日从早到晚；没有时为空。"""
        ...


class SplitSource(Protocol):
    """拆股记录的数据源。每次调用发请求，失败时抛 CompanyDataUnavailableError，不重试。"""

    def splits(self, symbol: str, end: date) -> tuple[Split, ...]:
        """除权日不晚于 end 的全部拆股，按除权日从早到晚；没有时为空。"""
        ...
