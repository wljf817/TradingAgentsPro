"""公司数据的用例：查一只股票的板块，刷新和读取一只股票的财报。

见 company-data spec 和 add-earnings-calendar-and-sector 的 design D6、D9。
"""

import logging
from datetime import timedelta

from tradingagentspro.modules.companydata.config import CompanyDataConfig
from tradingagentspro.modules.companydata.domain.earnings import EarningsRelease, SurpriseHistory
from tradingagentspro.modules.companydata.domain.sector import SectorEtf, sector_of
from tradingagentspro.modules.companydata.ports import CompanyDataSource, EarningsStore
from tradingagentspro.shared.clock import Clock

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
        return tuple(
            sorted(
                self._store.releases(symbol),
                key=lambda r: (r.day, r.fiscal_year, r.fiscal_quarter),
            )
        )

    def surprises(self, symbol: str) -> SurpriseHistory | None:
        """状态库里这只股票最近一次保存的 surprise，按报告期从新到旧；没有刷新过时为 None。"""
        return self._store.surprises(symbol)
