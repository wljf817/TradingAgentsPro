"""寻股粗筛的用例：粗筛、读粗筛结果、组装一只股票的上市信息。

见 stock-screen spec 和 add-stock-screen 的 design D4、D5、D8、D12。
"""

from collections.abc import Collection, Sequence
from datetime import date

from tradingagentspro.contracts.listing import Listing
from tradingagentspro.modules.screener.config import ScreenerConfig
from tradingagentspro.modules.screener.domain.indicators import average_dollar_volume
from tradingagentspro.modules.screener.domain.screen import (
    Candidate,
    Ranked,
    Scope,
    ScreenRun,
    cutoff,
    evaluate,
    history_sessions,
    sessions,
)
from tradingagentspro.modules.screener.ports import (
    BarSource,
    ScreenStore,
    SectorSource,
    SecuritySource,
)
from tradingagentspro.modules.stockpool import StockPoolConfig
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar


class ScreenerService:
    """寻股粗筛的入口。

    数据源的失败原样抛出，不重试，不保存任何东西；不拿锁，寻股同一时间只跑一个。允许的范围和流动性
    门槛取股票池的配置，和加入股票池时的检查一致。
    """

    def __init__(
        self,
        securities: SecuritySource,
        bars: BarSource,
        sectors: SectorSource,
        store: ScreenStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: ScreenerConfig,
        pool: StockPoolConfig,
    ) -> None:
        self._securities = securities
        self._bars = bars
        self._sectors = sectors
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._scope = Scope(
            exchanges=pool.allowed_exchanges,
            security_types=pool.allowed_security_types,
            min_average_dollar_volume=pool.min_average_dollar_volume,
        )

    def screen(self, excluded: Collection[str]) -> ScreenRun:
        """粗筛出前 N 只并保存；excluded 是现在不能加入股票池的股票，按名次取的时候跳过。"""
        config = self._config
        day = self._cutoff()
        days = sessions(self._calendar, day, history_sessions(config))
        securities = self._securities.universe(config.min_market_cap)
        allowed = [s for s in securities if self._scope.allows(s)]
        symbols = list(dict.fromkeys([*(s.symbol for s in allowed), config.benchmark]))
        bars = self._bars.daily_bars(symbols, days[0], day)
        evaluation = evaluate(allowed, bars, days, self._scope, config)
        run = ScreenRun(
            day=day,
            universe=len(securities),
            eligible=evaluation.eligible,
            trending=evaluation.trending,
            candidates=self._pick(evaluation.ranked, excluded),
        )
        self._store.save(run)
        return run

    def run(self, day: date) -> ScreenRun | None:
        """这个截止交易日最新的粗筛结果；没有粗筛过时为 None。"""
        return self._store.get(day)

    def listing(self, symbol: str) -> Listing | None:
        """一只股票的上市信息；数据源查不到这只股票或者没有板块时为 None。不检查允许的范围。"""
        security = self._securities.security(symbol)
        if security is None:
            return None
        sector = self._sectors.sector(symbol)
        if sector is None:
            return None
        day = self._cutoff()
        days = sessions(self._calendar, day, self._config.dollar_volume_sessions)
        bars = self._bars.daily_bars([symbol], days[0], day).get(symbol, ())
        return Listing(
            exchange=security.exchange,
            security_type=security.security_type,
            average_dollar_volume=average_dollar_volume(bars, days),
            sector=sector,
        )

    def _cutoff(self) -> date:
        return cutoff(self._calendar, self._clock.now(), self._config.bar_delay)

    def _pick(self, ranked: Sequence[Ranked], excluded: Collection[str]) -> tuple[Candidate, ...]:
        """按名次取前 N 只：跳过要排除的和没有板块的，取够就不再查板块。"""
        picked: list[Candidate] = []
        for entry in ranked:
            if len(picked) == self._config.top_n:
                break
            if entry.security.symbol in excluded:
                continue
            sector = self._sectors.sector(entry.security.symbol)
            if sector is not None:
                picked.append(entry.candidate(sector))
        return tuple(picked)
