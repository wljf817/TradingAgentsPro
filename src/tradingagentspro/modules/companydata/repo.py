"""公司数据的表：财报日历的版本表 companydata_earnings_releases、surprise 的版本表
companydata_earnings_surprises，以及 EarningsStore 的数据库实现。

见 state-store spec、company-data spec「财报的存储」和 add-earnings-calendar-and-sector 的
design D5。
"""

from sqlalchemy import Column, Engine, String

from tradingagentspro.modules.companydata.domain.earnings import EarningsRelease, SurpriseHistory
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

RELEASES = versioned_table(
    "companydata_earnings_releases",
    Column("release_key", String, nullable=False),
    Column("symbol", String, nullable=False),
)

SURPRISES = versioned_table(
    "companydata_earnings_surprises",
    Column("symbol", String, nullable=False),
)


class PostgresEarningsStore:
    """满足 EarningsStore：财报日历的键是一次发布的键（股票代码和财年季度），筛选列 symbol 用来按
    股票列出；surprise 的键是股票代码。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._releases = VersionedStore(engine, clock, RELEASES, EarningsRelease)
        self._surprises = VersionedStore(engine, clock, SURPRISES, SurpriseHistory)

    def save_release(self, release: EarningsRelease) -> None:
        self._releases.save(release.key, release, symbol=release.symbol)

    def releases(self, symbol: str) -> tuple[EarningsRelease, ...]:
        return self._releases.latest(symbol=symbol)

    def save_surprises(self, history: SurpriseHistory) -> None:
        self._surprises.save(history.symbol, history)

    def surprises(self, symbol: str) -> SurpriseHistory | None:
        return self._surprises.get(symbol)
