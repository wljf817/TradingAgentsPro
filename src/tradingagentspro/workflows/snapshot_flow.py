"""生成快照：案件开始时读各模块的事实，组成一版快照交给状态库存下。

每种案件放哪些事实、条目和条目 ID、回看起点、新的版本和同一版再生成见 state-snapshot spec，做法见
add-state-snapshot 的 design D7、D8、D9。快照的格式在 contracts.snapshot，存储、读取和程序日志在
状态库模块。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.case import CaseType
from tradingagentspro.contracts.event import Event
from tradingagentspro.contracts.news import Filing, NewsArticle
from tradingagentspro.contracts.snapshot import (
    AccountEntry,
    EarningsEntry,
    EarningsTime,
    EntryKind,
    EventEntry,
    FilingEntry,
    HeldStock,
    MarketEntry,
    NewsEntry,
    PoolMember,
    QuoteEntry,
    ScreenCandidate,
    ScreenCondition,
    ScreenEntry,
    Snapshot,
    StockPoolEntry,
    SurpriseQuarter,
    SurprisesEntry,
    check_reason,
    entry_id,
)
from tradingagentspro.modules.companydata import (
    CompanyDataService,
    EarningsRelease,
    SurpriseHistory,
)
from tradingagentspro.modules.events import EventService
from tradingagentspro.modules.execution import Account, ExecutionService
from tradingagentspro.modules.marketdata import MarketDataService, Quote
from tradingagentspro.modules.news import NewsService
from tradingagentspro.modules.screener import ScreenerService, ScreenRun
from tradingagentspro.modules.statestore import SnapshotService
from tradingagentspro.modules.stockpool import PoolEntry, StockPoolService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar

# 没有案件股票的事实的案件类型；其余 11 种针对单只股票，由 PM 处理
_NO_STOCK_FACTS: Final = frozenset(
    {CaseType.EXPLORATION, CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE}
)


class SnapshotConfig(BaseModel):
    """快照的配置和默认值，含义见 state-snapshot spec「快照配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每份快照都带这些指数的行情
    indexes: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        default=("SPY", "QQQ"), min_length=1
    )
    # 新闻回看的交易日数，含当前交易日
    news_sessions: int = Field(default=5, gt=0)
    # 公告回看的交易日数，含当前交易日；约一个季度
    filing_sessions: int = Field(default=65, gt=0)


def lookback_start(calendar: TradingCalendar, now: datetime, sessions: int) -> datetime:
    """从 now 归属的交易日往前数 sessions 个交易日，那一天的收盘时刻。"""
    session = calendar.session_closing_after(now)
    for _ in range(sessions):
        session = calendar.previous_session(session.day)
    return session.close


@dataclass(frozen=True, slots=True)
class _StockFacts:
    """案件股票的新闻、公告、财报发布和 surprise 的条目；没有案件股票的事实时都为空。"""

    news: tuple[NewsEntry, ...] = ()
    filings: tuple[FilingEntry, ...] = ()
    earnings: tuple[EarningsEntry, ...] = ()
    surprises: SurprisesEntry | None = None
    events: tuple[EventEntry, ...] = ()


class SnapshotFlow:
    """生成快照：读各模块的事实，组成一版快照存下，见 state-snapshot spec。

    只读执行模块、股票池、新闻和公告、公司数据、事件，不拿锁；寻股的粗筛写的是粗筛模块自己的表。
    """

    def __init__(
        self,
        execution: ExecutionService,
        market_data: MarketDataService,
        pool: StockPoolService,
        news: NewsService,
        company_data: CompanyDataService,
        screener: ScreenerService,
        events: EventService,
        snapshots: SnapshotService,
        clock: Clock,
        calendar: TradingCalendar,
        config: SnapshotConfig,
    ) -> None:
        self._execution = execution
        self._market_data = market_data
        self._pool = pool
        self._news = news
        self._company_data = company_data
        self._screener = screener
        self._events = events
        self._snapshots = snapshots
        self._clock = clock
        self._calendar = calendar
        self._config = config

    def take(
        self,
        case_id: UUID,
        case_type: CaseType,
        symbol: str | None,
        *,
        version: int = 1,
        reason: str | None = None,
    ) -> Snapshot:
        """生成并保存这个案件的第 version 版；这一版已经存在时返回存下的，不读数据。

        原因不对、针对单只股票的案件没给股票、案件股票不在股票池里时抛 ValueError；
        数据读不到时原样抛出，什么都不保存。
        """
        check_reason(version, reason)
        existing = self._snapshots.snapshot(case_id, version)
        if existing is not None:
            return existing
        now = self._clock.now()
        members = sorted(self._pool.entries(), key=lambda e: e.symbol)
        stock = None if case_type in _NO_STOCK_FACTS else _case_stock(case_type, symbol, members)
        account = self._execution.account()
        facts = _StockFacts()
        symbols = [*self._config.indexes, *(p.symbol for p in account.positions)]
        if stock is not None:
            symbols += [stock.symbol, stock.sector]
            facts = self._stock_facts(version, stock.symbol, now)
        screen = None
        if case_type is CaseType.EXPLORATION:
            screen = _screen(version, self._screener.screen(self._pool.unavailable()))
        quotes = self._market_data.quotes(symbols)
        snapshot = Snapshot(
            case_id=case_id,
            version=version,
            case_type=case_type,
            symbol=symbol,
            taken_at=now,
            reason=reason,
            account=_account(version, account),
            market=self._market(version, now),
            pool=_pool(version, members),
            quotes=tuple(_quote(version, q) for q in sorted(quotes.values(), key=_symbol)),
            news=facts.news,
            filings=facts.filings,
            earnings=facts.earnings,
            surprises=facts.surprises,
            events=facts.events,
            screen=screen,
        )
        self._snapshots.save(snapshot)
        return snapshot

    def _stock_facts(self, version: int, symbol: str, now: datetime) -> _StockFacts:
        """案件股票在状态库里的新闻、公告（各自的回看起点以来）、财报发布、surprise 和事件（新闻的
        回看起点以来）。
        """
        config, calendar = self._config, self._calendar
        news_since = lookback_start(calendar, now, config.news_sessions)
        articles = self._news.articles(symbol, news_since)
        filings = self._news.filings(symbol, lookback_start(calendar, now, config.filing_sessions))
        history = self._company_data.surprises(symbol)
        return _StockFacts(
            news=tuple(_news(version, a) for a in articles),
            filings=tuple(_filing(version, f) for f in filings),
            earnings=tuple(_earnings(version, r) for r in self._company_data.releases(symbol)),
            surprises=None if history is None else _surprises(version, history),
            events=tuple(_event(version, e) for e in self._events.events(symbol, news_since)),
        )

    def _market(self, version: int, now: datetime) -> MarketEntry:
        session = self._calendar.session_closing_after(now)
        return MarketEntry(
            entry_id=entry_id(version, EntryKind.MARKET),
            trading_day=session.day,
            in_session=self._calendar.session_at(now) is not None,
            session_open=session.open,
            session_close=session.close,
        )


def _case_stock(case_type: CaseType, symbol: str | None, members: Iterable[PoolEntry]) -> PoolEntry:
    """针对单只股票的案件的股票在股票池里的记录；没给股票、不在股票池里时抛 ValueError。"""
    if symbol is None:
        raise ValueError(f"案件类型 {case_type} 针对单只股票，要给出股票")
    stock = next((e for e in members if e.symbol == symbol), None)
    if stock is None:
        raise ValueError(f"{symbol} 不在股票池里，生成不了 {case_type} 的快照")
    return stock


def _symbol(quote: Quote) -> str:
    return quote.symbol


def _account(version: int, account: Account) -> AccountEntry:
    return AccountEntry(
        entry_id=entry_id(version, EntryKind.ACCOUNT),
        equity=account.equity,
        previous_close_equity=account.previous_close_equity,
        day_pnl=account.equity - account.previous_close_equity,
        settled_cash=account.settled_cash,
        positions=tuple(
            HeldStock(
                symbol=p.symbol,
                sector=p.sector,
                quantity=p.quantity,
                average_cost=p.average_cost,
                stop_price=p.stop_price,
            )
            for p in sorted(account.positions, key=lambda p: p.symbol)
        ),
    )


def _pool(version: int, members: Iterable[PoolEntry]) -> StockPoolEntry:
    return StockPoolEntry(
        entry_id=entry_id(version, EntryKind.POOL),
        members=tuple(
            PoolMember(symbol=e.symbol, status=e.status, sector=e.sector) for e in members
        ),
    )


def _quote(version: int, quote: Quote) -> QuoteEntry:
    return QuoteEntry(
        entry_id=entry_id(version, EntryKind.QUOTE, quote.symbol),
        symbol=quote.symbol,
        last_price=quote.last_price,
        previous_close=quote.previous_close,
        updated_at=quote.updated_at,
    )


def _news(version: int, article: NewsArticle) -> NewsEntry:
    return NewsEntry(entry_id=entry_id(version, EntryKind.NEWS, str(article.id)), article=article)


def _filing(version: int, filing: Filing) -> FilingEntry:
    key = filing.accession_number
    return FilingEntry(entry_id=entry_id(version, EntryKind.FILING, key), filing=filing)


def _event(version: int, event: Event) -> EventEntry:
    key = event.event_id
    return EventEntry(entry_id=entry_id(version, EntryKind.EVENT, key), event=event)


def _earnings(version: int, release: EarningsRelease) -> EarningsEntry:
    return EarningsEntry(
        entry_id=entry_id(version, EntryKind.EARNINGS, release.key),
        symbol=release.symbol,
        fiscal_year=release.fiscal_year,
        fiscal_quarter=release.fiscal_quarter,
        day=release.day,
        time=None if release.time is None else EarningsTime(release.time.value),
        eps_estimate=release.eps_estimate,
        eps_actual=release.eps_actual,
        revenue_estimate=release.revenue_estimate,
        revenue_actual=release.revenue_actual,
    )


def _surprises(version: int, history: SurpriseHistory) -> SurprisesEntry:
    return SurprisesEntry(
        entry_id=entry_id(version, EntryKind.SURPRISES, history.symbol),
        symbol=history.symbol,
        quarters=tuple(
            SurpriseQuarter(
                period=q.period,
                fiscal_year=q.fiscal_year,
                fiscal_quarter=q.fiscal_quarter,
                eps_actual=q.eps_actual,
                eps_estimate=q.eps_estimate,
                surprise=q.surprise,
                surprise_percent=q.surprise_percent,
            )
            for q in history.quarters
        ),
    )


def _screen(version: int, run: ScreenRun) -> ScreenEntry:
    return ScreenEntry(
        entry_id=entry_id(version, EntryKind.SCREEN),
        day=run.day,
        universe=run.universe,
        eligible=run.eligible,
        trending=run.trending,
        candidates=tuple(
            ScreenCandidate(
                symbol=c.symbol,
                listing=c.listing,
                relative_strength=c.metrics.relative_strength,
                pullback=c.metrics.pullback,
                volume_ratio=c.metrics.volume_ratio,
                conditions=tuple(ScreenCondition(x.value) for x in c.conditions),
            )
            for c in run.candidates
        ),
    )
