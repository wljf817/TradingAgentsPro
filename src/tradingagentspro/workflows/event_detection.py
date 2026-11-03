"""事件检测：在自己的守护线程里一轮一轮地读交易记录、行情、公告、财报日历和新闻，按事件规则检测股票池
里的股票的事件（包括论点里的条件），记下、写程序日志、排进案件；给每笔已经结束、有成交的卖出排一个
卖出复盘；每一轮最后把行情源的到价提醒同步成论点需要的那一组。

见 event-detection spec、add-event-detection 的 design D4 到 D6、D11、D12 和
add-thesis-conditions-and-price-alerts 的 design D1、D3、D6、D8。规则全在事件模块的纯函数里，这里
只读数据、组出输入、记下和排队。一轮开始时读一次时钟，这一轮的检测时刻、交易日、回看起点都按它算。
行情、券商账户、新闻源、公告源、到价提醒读不到时只跳过那一部分，写 WARNING；数据库出错原样抛出，
这一轮剩下的不做。

论点条件只由每一轮按行情判断。到价提醒的推送在行情源自己的线程里到达，只调用 wake 叫醒这个线程提前
做下一轮，不读写数据库、不拿锁；所以推送丢了、OpenD 断过，都只让事件晚到一轮，不用另外检测断线。

事件检测只读执行模块（账户数据、已经结束的卖出），不拿全程序共用的那把锁；排队经 CaseIntake 交给
排队运行。
"""

import logging
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome
from tradingagentspro.contracts.snapshot import EarningsTime
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.companydata import CompanyDataService
from tradingagentspro.modules.events import (
    AlertKind,
    AlertService,
    CalendarRelease,
    ConditionGroup,
    Detection,
    Discard,
    DiscardReason,
    EventKind,
    EventRulesConfig,
    EventService,
    Monitored,
    PriceAlertsUnavailableError,
    PricedStock,
    Reading,
    condition_event,
    condition_groups,
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

_ALERT_KINDS: Final = {
    AlertKind.PRICE_UP: "价格涨到",
    AlertKind.PRICE_DOWN: "价格跌到",
    AlertKind.CHANGE_UP: "当日涨幅达到",
    AlertKind.CHANGE_DOWN: "当日跌幅达到",
    AlertKind.VOLUME_UP: "当日成交量达到",
}


class EventDetectionConfig(BaseModel):
    """事件检测的配置和默认值，含义见 event-detection spec「事件检测的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一轮做完以后等这么久再做下一轮
    interval: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # 推送叫醒的一轮最早在上一轮开始以后这么久开始：一批提醒同时触发时不超过行情源的频率限制
    min_gap: timedelta = Field(default=timedelta(seconds=5), gt=timedelta(0))
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
    alerts: AlertService
    clock: Clock
    calendar: TradingCalendar
    rules: EventRulesConfig


class EventDetector:
    """事件检测，见 event-detection spec。

    run_round 做一轮，可以直接调用；start 开守护线程循环，立即做第一轮；wake 叫醒它提前做下一轮；
    stop 停下并等线程结束。
    """

    def __init__(self, parts: DetectionParts, config: EventDetectionConfig) -> None:
        self._parts = parts
        self._config = config
        self._filings_at: datetime | None = None  # 上一次取公告的时刻，取到和取不到都算
        self._stopping = threading.Event()
        self._woken = threading.Event()  # 等下一轮时被它提前叫醒：到价提醒的推送、停止
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """登记到价提醒推送的回调，开守护线程 event-detection，立即做第一轮。写一条 INFO：事件检测
        已开始和间隔。
        """
        self._parts.alerts.listen(self.wake)
        self._thread = threading.Thread(target=self._loop, name="event-detection", daemon=True)
        self._thread.start()
        _log.info("事件检测已开始，间隔 %g 秒", self._config.interval.total_seconds())

    def wake(self) -> None:
        """叫醒事件检测：在等下一轮的立即做下一轮，在做一轮的做完以后再做一轮。可以从任何线程调用，
        只设一个线程事件。
        """
        self._woken.set()

    def stop(self) -> None:
        """不再开始新的一轮，等正在做的一轮做完、线程结束；没有开始过时什么都不做。"""
        self._stopping.set()
        self._woken.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        """做一轮，等一个间隔或者被叫醒，直到停止；一轮出错写 ERROR，下一轮照常。

        一轮开始时清掉叫醒：这一轮里来的推送让等待立即返回，几次都只多做一轮。被叫醒时离上一轮开始
        不到最短间隔，就等到那一刻；等的时候来的推送也只算下一轮。见
        add-thesis-conditions-and-price-alerts 的 design D8。
        """
        seconds = self._config.interval.total_seconds()
        while not self._stopping.is_set():
            self._woken.clear()
            started = self._parts.clock.now()
            try:
                self.run_round()
            except Exception:
                _log.exception("事件检测：这一轮出错")
            if self._woken.wait(seconds) and not self._stopping.is_set():
                gap = self._config.min_gap - (self._parts.clock.now() - started)
                self._stopping.wait(max(gap.total_seconds(), 0))

    def run_round(self) -> None:
        """做一轮，见 spec「一轮」：卖出成交、价格成交量和论点条件、公告、财报日历、新闻、到价
        提醒。
        """
        parts = self._parts
        at = detection(parts.clock.now(), parts.calendar)
        since = at.at - self._config.lookback
        entries = sorted(parts.pool.entries(), key=lambda e: e.symbol)
        monitored = {e.symbol: Monitored(e.symbol, e.status, e.added_at) for e in entries}
        groups = {e.symbol: condition_groups(e.watch_thesis, e.investment_thesis) for e in entries}
        self._sales(at, since)
        if monitored:
            self._prices(at, monitored, groups)
            self._filings(at, since, monitored)
            self._calendar(at, since, monitored)
            self._news(at, since, monitored)
        self._sync_alerts(groups)

    # 卖出成交

    def _sales(self, at: Detection, since: datetime) -> None:
        """回看起点归属的交易日以来已经结束的卖出，还没排过的每笔排一个卖出复盘。"""
        parts = self._parts
        day = parts.calendar.session_closing_after(since).day
        queued = parts.intake.queued_sales()
        for trade in parts.execution.sales(day):
            if trade.client_order_id not in queued:
                parts.intake.enqueue(SellTrigger(trade.symbol, trade.client_order_id), at.at)

    # 价格、成交量和论点条件

    def _prices(
        self,
        at: Detection,
        monitored: Mapping[str, Monitored],
        groups: Mapping[str, tuple[ConditionGroup, ...]],
    ) -> None:
        """检测时刻在常规交易时段里时，读一次账户、一次取齐行情，每只股票依次判断价格、成交量和
        论点条件（入场条件、目标价、证伪条件）。
        """
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
                quote.last_price,
                quote.previous_close,
                quote.volume_ratio,
                quote.volume,
                quote.updated_at,
            )
            for judge in (price_event, volume_event):
                event = judge(priced, reading, at, parts.calendar, parts.rules)
                if event is not None:
                    self._record(event, stock.status, at)
            for group in groups[symbol]:
                event = condition_event(symbol, group, reading, at, parts.calendar)
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

    # 到价提醒

    def _sync_alerts(self, groups: Mapping[str, tuple[ConditionGroup, ...]]) -> None:
        """把行情源的到价提醒同步成股票池的论点需要的，见 price-alerts spec「同步」「同步失败」。"""
        try:
            changes = self._parts.alerts.sync(groups)
        except PriceAlertsUnavailableError as error:
            _log.warning("事件检测：读不到到价提醒，这一轮不同步：%s", error)
            return
        if changes.added or changes.removed:
            _log.info("事件检测：到价提醒设了 %d 个，删了 %d 个", changes.added, changes.removed)
        for alert, reason in changes.failed:
            value = f"{alert.value.normalize():f}"
            described = f"{alert.symbol} {_ALERT_KINDS[alert.kind]} {value}"
            _log.warning("事件检测：到价提醒 %s 没有同步：%s", described, reason)

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
