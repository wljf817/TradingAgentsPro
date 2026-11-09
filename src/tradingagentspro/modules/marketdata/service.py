"""行情的用例：取一组股票的行情，给出行情最近一次更新的时刻；市场背景的用例：VIX 收盘值和重要日程；
K 线的用例：按周期和范围给出几只股票的 K 线；量化指标的用例：按一个 Quant 任务算出全部数字。

见 market-data spec、market-context spec、price-bars spec 和 quant-indicators spec。
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime

from tradingagentspro.contracts.quant import QuantIndicators
from tradingagentspro.contracts.snapshot import ScheduleItem, VixClose
from tradingagentspro.contracts.task import BarPeriod
from tradingagentspro.modules.marketdata.config import MarketDataConfig
from tradingagentspro.modules.marketdata.domain.bars import (
    Bar,
    Bars,
    SourceBar,
    SourceDayBar,
    build_bars,
    plan_bars,
)
from tradingagentspro.modules.marketdata.domain.context import latest_close, upcoming
from tradingagentspro.modules.marketdata.domain.indicators import (
    IndicatorBars,
    IndicatorRequest,
    compute_indicators,
    history_sessions,
    quarter_sessions,
)
from tradingagentspro.modules.marketdata.domain.quote import Quote
from tradingagentspro.modules.marketdata.ports import (
    BarHistorySource,
    QuoteSource,
    QuoteUnavailableError,
    VixSource,
    VixUnavailableError,
)
from tradingagentspro.shared.trading_calendar import TradingCalendar


class MarketDataService:
    """行情的入口。不保存状态，每次调用都向行情源取；行情源的失败原样抛出，不重试。"""

    def __init__(self, source: QuoteSource, config: MarketDataConfig) -> None:
        self._source = source
        self._config = config

    def quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        """这些股票的行情：去重后一次向行情源取；没有股票时直接返回空，不调用行情源。

        行情源没有数据的股票不在结果里。行情源失败时抛 QuoteUnavailableError。
        """
        unique = tuple(dict.fromkeys(symbols))
        if not unique:
            return {}
        return dict(self._source.quotes(unique))

    def last_update(self) -> datetime:
        """行情最近一次更新的时刻：参考股票的行情的更新时刻，给健康监控判断数据延迟。

        见 add-moomoo-quotes 的 design D3。行情源没有参考股票的数据、或者行情源失败时，
        抛 QuoteUnavailableError。
        """
        symbol = self._config.reference_symbol
        quote = self._source.quotes((symbol,)).get(symbol)
        if quote is None:
            raise QuoteUnavailableError(f"行情源没有参考股票 {symbol} 的数据")
        return quote.updated_at


class MarketContextService:
    """市场背景的入口。不保存状态，每次都向 VIX 源取；VIX 源的失败原样抛出，不重试。"""

    def __init__(
        self, source: VixSource, calendar: TradingCalendar, config: MarketDataConfig
    ) -> None:
        self._source = source
        self._calendar = calendar
        self._config = config

    def vix(self, day: date) -> VixClose:
        """日期早于交易日 day 的最近一个收盘值。没有时抛 VixUnavailableError。"""
        close = latest_close(self._source.closes(), day)
        if close is None:
            raise VixUnavailableError(f"没有早于 {day} 的 VIX 收盘值")
        return close

    def schedule(self, day: date) -> tuple[ScheduleItem, ...]:
        """交易日 day 到它之后配置的交易日数内的重要日程，按日期。"""
        last = self._calendar.add_sessions(day, self._config.schedule_sessions)
        return upcoming(self._config.schedule, day, last.day)


class BarService:
    """K 线的入口：每次调用都向 K 线源取，不保存；K 线源的失败原样抛出，不重试。"""

    def __init__(
        self, source: BarHistorySource, calendar: TradingCalendar, config: MarketDataConfig
    ) -> None:
        self._source = source
        self._calendar = calendar
        self._config = config

    def bars(
        self, symbols: Sequence[str], period: BarPeriod, sessions: int, taken_at: datetime
    ) -> Bars:
        """这些股票按快照时刻 taken_at 往前 sessions 个交易日的 K 线，见 price-bars spec。

        请求按 spec「取数的请求」组合，结束时刻都是截止时刻。任何请求失败时抛
        BarsUnavailableError。
        """
        plan = plan_bars(self._calendar, period, sessions, taken_at, self._config.bar_delay)
        quarters: Mapping[str, tuple[SourceBar, ...]] = {}
        days: Mapping[str, tuple[SourceDayBar, ...]] = {}
        if period in (BarPeriod.QUARTER_HOUR, BarPeriod.HOUR):
            quarters = self._source.quarter_hours(symbols, plan.first.open, plan.cutoff)
        else:
            days = self._source.days(symbols, plan.first.day, plan.cutoff)
            if plan.last_open:
                quarters = self._source.quarter_hours(symbols, plan.last.open, plan.cutoff)
        splits = {symbol: self._source.splits(symbol, plan.price_day) for symbol in symbols}
        return Bars(
            cutoff=plan.cutoff,
            by_symbol={
                symbol: build_bars(
                    self._calendar,
                    period,
                    plan,
                    quarters.get(symbol, ()),
                    days.get(symbol, ()),
                    splits[symbol],
                )
                for symbol in symbols
            },
        )


class IndicatorService:
    """量化指标的入口：按 spec「取数」用 K 线的用例取数，再算出全部数字；不保存。"""

    def __init__(
        self, bars: BarService, calendar: TradingCalendar, config: MarketDataConfig
    ) -> None:
        self._bars = bars
        self._calendar = calendar
        self._config = config

    def indicators(self, request: IndicatorRequest) -> QuantIndicators:
        """这只股票的量化指标，见 quant-indicators spec。取数失败时抛 BarsUnavailableError。"""
        calendar, config = self._calendar, self._config.indicators
        window, taken_at = request.window, request.taken_at
        plan = plan_bars(calendar, window.period, window.days, taken_at, self._config.bar_delay)
        sector = () if request.sector is None else (request.sector,)
        symbols = [request.symbol, *sector, config.benchmark]
        need = max(config.ma_lengths) + config.change_bars
        history = history_sessions(calendar, window.period, need, plan.last)
        period = self._bars.bars(symbols, window.period, max(window.days, history), taken_at)
        year = config.year_sessions + config.atr_bars + 1
        daily = self._bars.bars([request.symbol], BarPeriod.DAY, year, taken_at)
        quarters: Mapping[str, tuple[Bar, ...]] = {}
        if sessions := quarter_sessions(calendar, request, plan.cutoff, plan.last, config):
            fetched = self._bars.bars(symbols, BarPeriod.QUARTER_HOUR, sessions, taken_at)
            quarters = fetched.by_symbol
        data = IndicatorBars(period.by_symbol, daily.by_symbol[request.symbol], quarters)
        return compute_indicators(request, plan, data, calendar, config)
