"""事件检测：在自己的守护线程里一轮一轮地读交易记录、行情、公告、财报日历和新闻，按事件规则检测股票池
里的股票的事件，记下、写程序日志、排进案件；并给每笔已经结束、有成交的卖出排一个卖出复盘。

见 event-detection spec 和 add-event-detection 的 design D4 到 D6、D11、D12。规则全在事件模块的
纯函数里，这里只读数据、组出输入、记下和排队。一轮开始时读一次时钟，这一轮的检测时刻、交易日、回看
起点都按它算。行情、券商账户、新闻源、公告源读不到时只跳过那一部分，写 WARNING；数据库出错原样抛出，
这一轮剩下的不做。

事件检测只读执行模块（账户数据、已经结束的卖出），不拿全程序共用的那把锁；排队经 CaseIntake 交给
排队运行。
"""

import logging
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome
from tradingagentspro.contracts.snapshot import EarningsTime
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.companydata import CompanyDataService
from tradingagentspro.modules.events import (
    CalendarRelease,
    Detection,
    Discard,
    DiscardReason,
    EventKind,
    EventRulesConfig,
    EventService,
    Monitored,
    PricedStock,
    Reading,
    detection,
    earnings_window_start,
    event_id,
    judge_article,
    judge_filing,
    price_event,
    release_event,
    volume_event,
)
from tradingagentspro.modules.execution import BrokerUnavailableError, ExecutionService
from tradingagentspro.modules.marketdata import MarketDataService, QuoteUnavailableError
from tradingagentspro.modules.news import NewsService, SourceUnavailableError
from tradingagentspro.modules.scheduler import EventTrigger, SellTrigger, Trigger
from tradingagentspro.modules.stockpool import StockPoolService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar

_log = logging.getLogger(__name__)


class EventDetectionConfig(BaseModel):
    """事件检测的配置和默认值，含义见 event-detection spec「事件检测的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一轮做完以后等这么久再做下一轮
    interval: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # 距上一次取公告满这么久才再取：SEC 一只股票一个请求
    filing_interval: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
    # 新闻和公告的起始时刻、财报日历的预计发布时刻、卖出的成交日从检测时刻往前这么久算起
    lookback: timedelta = Field(default=timedelta(hours=24), gt=timedelta(0))


class CaseIntake(Protocol):
    """事件检测排案件的地方；生产是排队运行，测试换成记下排队的替身。"""

    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> UUID:
        """排进一个案件，交回案件 ID。"""
        ...

    def queued_sales(self) -> frozenset[str]:
        """排过的卖出成交的客户端订单 ID。"""
        ...


@dataclass(frozen=True, slots=True)
class DetectionParts:
    """事件检测用到的部件和事件规则的配置。"""

    pool: StockPoolService
    execution: ExecutionService
    market_data: MarketDataService
    news: NewsService
    company_data: CompanyDataService
    events: EventService
    intake: CaseIntake
    clock: Clock
    calendar: TradingCalendar
    rules: EventRulesConfig


class EventDetector:
    """事件检测，见 event-detection spec。

    run_round 做一轮，可以直接调用；start 开守护线程循环，立即做第一轮；stop 停下并等线程结束。
    """

    def __init__(self, parts: DetectionParts, config: EventDetectionConfig) -> None:
        self._parts = parts
        self._config = config
        self._filings_at: datetime | None = None  # 上一次取公告的时刻，取到和取不到都算
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """开守护线程 event-detection，立即做第一轮。写一条 INFO：事件检测已开始和间隔。"""
        self._thread = threading.Thread(target=self._loop, name="event-detection", daemon=True)
        self._thread.start()
        _log.info("事件检测已开始，间隔 %g 秒", self._config.interval.total_seconds())

    def stop(self) -> None:
        """不再开始新的一轮，等正在做的一轮做完、线程结束；没有开始过时什么都不做。"""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        """做一轮，等一个间隔，直到停止；一轮出错写 ERROR，下一轮照常。"""
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            try:
                self.run_round()
            except Exception:
                _log.exception("事件检测：这一轮出错")
            self._stopping.wait(seconds)

    def run_round(self) -> None:
        """做一轮，见 spec「一轮」：卖出成交、价格和成交量、公告、财报日历、新闻。"""
        parts = self._parts
        at = detection(parts.clock.now(), parts.calendar)
        since = at.at - self._config.lookback
        monitored = {
            e.symbol: Monitored(e.symbol, e.status, e.added_at)
            for e in sorted(parts.pool.entries(), key=lambda e: e.symbol)
        }
        self._sales(at, since)
        if not monitored:
            return
        self._prices(at, monitored)
        self._filings(at, since, monitored)
        self._calendar(at, since, monitored)
        self._news(at, since, monitored)

    # 卖出成交

    def _sales(self, at: Detection, since: datetime) -> None:
        """回看起点归属的交易日以来已经结束的卖出，还没排过的每笔排一个卖出复盘。"""
        parts = self._parts
        day = parts.calendar.session_closing_after(since).day
        queued = parts.intake.queued_sales()
        for trade in parts.execution.sales(day):
            if trade.client_order_id not in queued:
                parts.intake.enqueue(SellTrigger(trade.symbol, trade.client_order_id), at.at)

    # 价格和成交量

    def _prices(self, at: Detection, monitored: Mapping[str, Monitored]) -> None:
        """检测时刻在常规交易时段里时，读一次账户、一次取齐行情，每只股票判断价格和成交量。"""
        parts = self._parts
        if parts.calendar.session_at(at.at) is None:
            return
        try:
            account = parts.execution.account()
            quotes = parts.market_data.quotes(monitored)
        except (BrokerUnavailableError, QuoteUnavailableError) as error:
            _log.warning("事件检测：读不到行情或账户，这一轮不判断价格和成交量：%s", error)
            return
        held = {p.symbol: p for p in account.positions}
        for symbol, stock in monitored.items():
            quote = quotes.get(symbol)
            if quote is None:
                continue
            if stock.status is StockStatus.HOLDING:
                position = held.get(symbol)
                if position is None:  # 股票池还没跟上持仓
                    continue
                priced = PricedStock(
                    symbol, stock.status, position.emergency_threshold, position.entry_price_today
                )
            else:
                priced = PricedStock(symbol, stock.status, None, None)
            reading = Reading(
                quote.last_price, quote.previous_close, quote.volume_ratio, quote.updated_at
            )
            for judge in (price_event, volume_event):
                event = judge(priced, reading, at, parts.calendar, parts.rules)
                if event is not None:
                    self._record(event, stock.status, at)

    # 公告和新闻

    def _filings(self, at: Detection, since: datetime, monitored: Mapping[str, Monitored]) -> None:
        """启动以后的第一轮、距上一次取公告满公告间隔时取公告，判断新存下的。"""
        if self._filings_at is not None and at.at - self._filings_at < self._config.filing_interval:
            return
        self._filings_at = at.at
        parts = self._parts
        try:
            filings = parts.news.fetch_filings(monitored, since)
        except SourceUnavailableError as error:
            _log.warning("事件检测：公告源不可用，这一轮不判断公告：%s", error)
            return
        for symbol, filing in _by_stock(((f.symbols, f) for f in filings), monitored):
            stock = monitored[symbol]
            listed = f"，item {'、'.join(filing.items)}" if filing.items else ""
            item = f"公告 {filing.form}{listed}"
            judged = judge_filing(filing, stock, at, parts.rules)
            self._screened(judged, stock, at, filing.accession_number, item)

    def _news(self, at: Detection, since: datetime, monitored: Mapping[str, Monitored]) -> None:
        """取新闻，判断新存下的。"""
        parts = self._parts
        try:
            articles = parts.news.fetch_articles(monitored, since)
        except SourceUnavailableError as error:
            _log.warning("事件检测：新闻源不可用，这一轮不判断新闻：%s", error)
            return
        for symbol, article in _by_stock(((a.symbols, a) for a in articles), monitored):
            judged = judge_article(article, monitored[symbol], at, parts.rules)
            item = f"新闻「{article.headline}」"
            self._screened(judged, monitored[symbol], at, str(article.id), item)

    def _screened(
        self, judged: Event | Discard, stock: Monitored, at: Detection, ref: str, item: str
    ) -> None:
        """一条新闻或一份公告判断的结果：没成为事件的写丢弃事件；财报发布看间隔，新闻和公告看当天
        有没有财报发布事件和同一个事件 ID。
        """
        events = self._parts.events
        if isinstance(judged, Discard):
            events.discard(stock.symbol, ref, item, judged.reason)
            return
        if judged.category is EventCategory.EARNINGS:
            if self._recent_earnings(stock.symbol, at):
                events.discard(stock.symbol, ref, item, DiscardReason.RECENT_EARNINGS)
                return
        elif events.event(event_id(stock.symbol, at.day, EventKind.EARNINGS)) is not None:
            events.discard(stock.symbol, ref, item, DiscardReason.EARNINGS_TODAY)
            return
        if not self._record(judged, stock.status, at):
            events.discard(stock.symbol, ref, item, DiscardReason.SAME_DAY)

    # 财报日历

    def _calendar(self, at: Detection, since: datetime, monitored: Mapping[str, Monitored]) -> None:
        """每只股票财报日历上预计发布时刻在回看起点和检测时刻之间、最近没有财报发布事件的发布。"""
        parts = self._parts
        for symbol, stock in monitored.items():
            for release in parts.company_data.releases(symbol):
                time = None if release.time is None else EarningsTime(release.time.value)
                listed = CalendarRelease(
                    symbol, release.fiscal_year, release.fiscal_quarter, release.day, time
                )
                event = release_event(listed, at, since, parts.calendar, parts.rules)
                if event is not None and not self._recent_earnings(symbol, at):
                    self._record(event, stock.status, at)

    def _recent_earnings(self, symbol: str, at: Detection) -> bool:
        """这只股票最近「财报发布的间隔」个交易日（含今天）里已经有财报发布事件。"""
        parts = self._parts
        start = earnings_window_start(at.at, parts.calendar, parts.rules.earnings_sessions)
        found = parts.events.events(symbol, start)
        return any(e.category is EventCategory.EARNINGS for e in found)

    # 记下和排队

    def _record(self, event: Event, status: StockStatus, at: Detection) -> bool:
        """记下事件；是新事件并且处理是排队时排进一个案件，触发时刻是检测时刻。给出是不是新事件。"""
        parts = self._parts
        if not parts.events.record(event, status):
            return False
        if event.outcome is EventOutcome.QUEUED:
            trigger = EventTrigger(event.symbol, event.category, event.holding_risk, event.event_id)
            parts.intake.enqueue(trigger, at.at)
        return True


def _by_stock[T](
    items: Iterable[tuple[tuple[str, ...], T]], monitored: Mapping[str, Monitored]
) -> list[tuple[str, T]]:
    """每条新闻或公告对它标注的每只监控股票各一项，按股票代码；同一只股票保持给出的先后。"""
    pairs = [
        (symbol, item)
        for symbols, item in items
        for symbol in dict.fromkeys(symbols)
        if symbol in monitored
    ]
    return sorted(pairs, key=lambda pair: pair[0])
