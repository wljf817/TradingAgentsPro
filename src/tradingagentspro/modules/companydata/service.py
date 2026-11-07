"""公司数据的用例：查一只股票的板块，刷新和读取一只股票的财报；取、读财务事实，给出 financials 的
结果。

见 company-data spec、financials-tool spec、add-earnings-calendar-and-sector 的 design D6、D9 和
add-financial-figures 的 design D2、D3、D7。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from tradingagentspro.modules.companydata.config import CompanyDataConfig
from tradingagentspro.modules.companydata.domain.earnings import (
    EarningsRelease,
    ReleaseBeat,
    SurpriseHistory,
    next_release,
)
from tradingagentspro.modules.companydata.domain.financials import (
    CONCEPTS,
    FinancialFact,
    FinancialReport,
    quarters,
)
from tradingagentspro.modules.companydata.domain.sector import SectorEtf, sector_of
from tradingagentspro.modules.companydata.ports import (
    CompanyDataSource,
    CompanyDataUnavailableError,
    EarningsStore,
    FinancialFactSource,
    FinancialFactStore,
)
from tradingagentspro.shared.clock import Clock

_EASTERN: Final = ZoneInfo("America/New_York")

_log = logging.getLogger(__name__)


class CompanyDataService:
    """公司数据的入口。数据源的失败原样抛出，不重试；一次只处理一只股票，请求的节奏由调用方控制。"""

    def __init__(
        self,
        source: CompanyDataSource,
        store: EarningsStore,
        clock: Clock,
        config: CompanyDataConfig,
    ) -> None:
        self._source = source
        self._store = store
        self._clock = clock
        self._config = config

    def sector(self, symbol: str) -> SectorEtf | None:
        """这只股票的板块：每次向数据源取行业，按代码表对应，不保存。

        数据源查不到这只股票、或者没有行业时为 None；行业不在代码表里时为 None，并写一条 WARNING
        日志。数据源不可用时抛 CompanyDataUnavailableError。
        """
        industry = self._source.industry(symbol)
        if industry is None:
            return None
        sector = sector_of(industry)
        if sector is None:
            _log.warning("%s 的行业「%s」不在板块代码表里，没有板块", symbol, industry)
        return sector

    def refresh_earnings(self, symbol: str) -> None:
        """向数据源取这只股票窗口内的财报日历和最近几个季度的 surprise，两项都取到才保存。

        窗口按当前时刻的 UTC 日期算：它只是请求的范围，不是交易日。任何一项取不到时抛
        CompanyDataUnavailableError，什么都不保存；这次结果里没有的发布保持原来的版本。
        """
        now = self._clock.now()
        start = (now - timedelta(days=self._config.lookback_days)).date()  # 范围的边界，不是交易日
        end = (now + timedelta(days=self._config.lookahead_days)).date()
        releases = self._source.earnings_calendar(symbol, start, end)
        history = SurpriseHistory.of(symbol, self._source.earnings_surprises(symbol))
        for release in releases:
            self._store.save_release(release)
        self._store.save_surprises(history)

    def releases(self, symbol: str) -> tuple[EarningsRelease, ...]:
        """状态库里这只股票每次发布的最新版本，按发布日从早到晚；没有刷新过时为空。"""
        return _by_day(self._store.releases(symbol))

    def surprises(self, symbol: str) -> SurpriseHistory | None:
        """状态库里这只股票最近一次保存的 surprise，按报告期从新到旧；没有刷新过时为 None。"""
        return self._store.surprises(symbol)

    def releases_as_of(self, symbol: str, at: datetime) -> tuple[EarningsRelease, ...]:
        """每次发布写入时刻早于 at 的最新版本，按发布日从早到晚；见 spec「按时刻读财报」。"""
        return _by_day(self._store.releases_as_of(symbol, at))

    def surprises_as_of(self, symbol: str, at: datetime) -> SurpriseHistory | None:
        """写入时刻早于 at 的最近一组 surprise；at 以前没有保存过时为 None。"""
        return self._store.surprises_as_of(symbol, at)


class FinancialsService:
    """财务事实和 financials 的结果，见 company-data spec「取一只股票的财务事实」「读取财务事实」和
    financials-tool spec。数据源不可用按 spec 写进结果，别的错误原样抛出。
    """

    def __init__(
        self,
        company_data: CompanyDataService,
        source: FinancialFactSource,
        store: FinancialFactStore,
        config: CompanyDataConfig,
    ) -> None:
        self._company_data = company_data
        self._source = source
        self._store = store
        self._config = config

    def fetch_facts(self, symbol: str) -> bool:
        """向数据源取这只股票的财务事实，存下还没有的；数据源没有这只股票时为 False，什么都不存。

        数据源不可用时抛 CompanyDataUnavailableError。
        """
        facts = self._source.financial_facts(symbol, CONCEPTS)
        if facts is None:
            return False
        self._store.add(facts)
        return True

    def facts(self, symbol: str, filed_before: datetime) -> tuple[FinancialFact, ...]:
        """状态库里这只股票提交日早于 filed_before 的美东日期的全部事实；只读状态库。"""
        return self._store.facts(symbol, _eastern_day(filed_before))

    def report(self, symbol: str, before: datetime) -> FinancialReport:
        """financials 的结果：before 是快照时刻 T。先向 SEC 取事实，再按 T 读事实和财报。"""
        try:
            found, unavailable = self.fetch_facts(symbol), False
        except CompanyDataUnavailableError:  # 用存过的，见 financials-tool spec「补取」
            found, unavailable = True, True
        listed = (
            quarters(self.facts(symbol, before), self._config.financial_quarters) if found else ()
        )
        releases = self._company_data.releases_as_of(symbol, before)
        surprises = self._company_data.surprises_as_of(symbol, before)
        refreshed = estimates_unavailable = False
        if not releases and surprises is None:
            try:
                self._company_data.refresh_earnings(symbol)
                refreshed = True
                releases = self._company_data.releases(symbol)
                surprises = self._company_data.surprises(symbol)
            except CompanyDataUnavailableError:
                estimates_unavailable = True
        day = _eastern_day(before)
        newest = sorted(releases, key=lambda r: r.day, reverse=True)[
            : self._config.financial_quarters
        ]
        return FinancialReport(
            symbol=symbol,
            filed_before=day,
            covered=bool(listed) or unavailable,
            facts_unavailable=unavailable,
            quarters=listed,
            releases=tuple(ReleaseBeat.of(release) for release in newest),
            next_release=next_release(releases, day),
            surprises=surprises,
            estimates_refreshed=refreshed,
            estimates_unavailable=estimates_unavailable,
        )


def _by_day(releases: tuple[EarningsRelease, ...]) -> tuple[EarningsRelease, ...]:
    return tuple(sorted(releases, key=lambda r: (r.day, r.fiscal_year, r.fiscal_quarter)))


def _eastern_day(moment: datetime) -> date:
    """这个时刻的美东日期，见 add-financial-figures 的 design D3。"""
    return moment.astimezone(_EASTERN).date()
