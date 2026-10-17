"""寻股粗筛的表：粗筛结果的版本表 screener_runs，以及 ScreenStore 的数据库实现。

见 state-store spec、stock-screen spec「粗筛结果的存储」和 add-stock-screen 的 design D9。
"""

from datetime import date

from sqlalchemy import Column, Date, Engine

from tradingagentspro.modules.screener.domain.screen import ScreenRun
from tradingagentspro.modules.statestore import VersionedStore, versioned_table
from tradingagentspro.shared.clock import Clock

RUNS = versioned_table("screener_runs", Column("day", Date, nullable=False))


class PostgresScreenStore:
    """满足 ScreenStore：键是截止交易日，没有筛选列。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._runs = VersionedStore(engine, clock, RUNS, ScreenRun)

    def save(self, run: ScreenRun) -> None:
        self._runs.save(run.day, run)

    def get(self, day: date) -> ScreenRun | None:
        return self._runs.get(day)
