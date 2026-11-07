"""公司数据的表：财报日历的版本表 companydata_earnings_releases、surprise 的版本表
companydata_earnings_surprises、财务事实表 companydata_financial_facts，以及 EarningsStore 和
FinancialFactStore 的数据库实现。

见 state-store spec、company-data spec「财报的存储」「财务事实的存储」、
add-earnings-calendar-and-sector 的 design D5 和 add-financial-figures 的 design D2。
"""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Engine, MetaData, Numeric, String, Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tradingagentspro.modules.companydata.domain.earnings import EarningsRelease, SurpriseHistory
from tradingagentspro.modules.companydata.domain.financials import FinancialFact
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

# 一条语句最多写这么多行：PostgreSQL 一条语句最多 65,535 个参数，一行 10 个
_BATCH = 1000

# 一条财务事实一行，只追加；主键就是 company-data spec 里"同一条事实"的几项
FACTS = Table(
    "companydata_financial_facts",
    MetaData(),
    Column("symbol", String, primary_key=True),
    Column("concept", String, primary_key=True),
    Column("unit", String, primary_key=True),
    Column("period_start", Date, primary_key=True),
    Column("period_end", Date, primary_key=True),
    Column("accession_number", String, primary_key=True),
    Column("value", Numeric, nullable=False),
    Column("form", String, nullable=False),
    Column("filed", Date, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
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

    def releases_as_of(self, symbol: str, at: datetime) -> tuple[EarningsRelease, ...]:
        return self._releases.as_of(at, symbol=symbol)

    def surprises_as_of(self, symbol: str, at: datetime) -> SurpriseHistory | None:
        found = self._surprises.as_of(at, symbol=symbol)
        return found[0] if found else None


class PostgresFinancialFactStore:
    """满足 FinancialFactStore：财务事实存在 companydata_financial_facts 表里，一条一行。

    一个事务里分批写入全部事实，已经有的什么都不做；同时写同一条时，后到的等先到的结束。数据库的错误
    原样抛出。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def add(self, facts: Sequence[FinancialFact]) -> None:
        if not facts:  # 空的 VALUES 不是合法的 SQL
            return
        recorded_at = self._clock.now()
        rows = [
            {
                "symbol": fact.symbol,
                "concept": fact.concept,
                "unit": fact.unit,
                "period_start": fact.start,
                "period_end": fact.end,
                "accession_number": fact.accession_number,
                "value": fact.value,
                "form": fact.form,
                "filed": fact.filed,
                "recorded_at": recorded_at,
            }
            for fact in facts
        ]
        with self._engine.begin() as connection:
            for first in range(0, len(rows), _BATCH):
                batch = rows[first : first + _BATCH]
                connection.execute(pg_insert(FACTS).values(batch).on_conflict_do_nothing())

    def facts(self, symbol: str, filed_before: date) -> tuple[FinancialFact, ...]:
        query = select(FACTS).where(FACTS.c.symbol == symbol, FACTS.c.filed < filed_before)
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(
            FinancialFact(
                symbol=row.symbol,
                concept=row.concept,
                unit=row.unit,
                start=row.period_start,
                end=row.period_end,
                value=row.value,
                accession_number=row.accession_number,
                form=row.form,
                filed=row.filed,
            )
            for row in rows
        )
