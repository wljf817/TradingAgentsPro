"""价格和成交量异动：event-rules spec「价格异动」「行情要是当天的」「成交量异动」。

判断用乘法比较（最新价不高于参考价乘以 1 减阈值），和紧急退出的 drop_triggered 一样，Decimal 的
边界不受舍入影响；强度另外算出涨跌幅，四舍五入到 4 位。见 add-event-detection 的 design D10。
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from tradingagentspro.contracts.event import Event, EventCategory, EventOutcome, EventSource
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.events.config import EventRulesConfig
from tradingagentspro.modules.events.domain.ids import Detection, EventKind, event_id
from tradingagentspro.modules.events.domain.text import number, percent
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

_STRENGTH: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class PricedStock:
    """判断价格的一只股票。emergency_threshold、entry_price_today 来自账户数据，只有持仓有：
    紧急阈值，以及今天开仓成交的均价（今天没有开仓成交时为 None）。
    """

    symbol: str
    status: StockStatus
    emergency_threshold: Decimal | None
    entry_price_today: Decimal | None


@dataclass(frozen=True, slots=True)
class Reading:
    """一只股票的行情读数，来自行情模块的行情。最新价、量比没有时为 None。"""

    last_price: Decimal | None
    previous_close: Decimal
    volume_ratio: Decimal | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class _Move:
    """越过阈值的一次涨跌。"""

    kind: EventKind
    threshold: Decimal
    holding_risk: bool = False
    emergency: Decimal | None = None  # 持仓风险级别的写明紧急阈值
    outcome: EventOutcome = EventOutcome.QUEUED


def today_session(
    reading: Reading, at: Detection, calendar: TradingCalendar
) -> TradingSession | None:
    """「行情要是当天的」：检测时刻在常规交易时段里、有最新价、更新时刻不早于这个时段的开盘时，
    给出这个交易时段；否则为 None，这次不判断价格和成交量。
    """
    session = calendar.session_at(at.at)
    if session is None or reading.last_price is None or reading.updated_at < session.open:
        return None
    return session


def price_event(
    stock: PricedStock,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None:
    """按股票这时的状态判断价格，见 spec「价格异动」的表；不是事件时为 None。"""
    if today_session(reading, at, calendar) is None:
        return None
    last = reading.last_price
    assert last is not None  # 当天的行情有最新价
    reference, described = _reference(stock, reading)
    move = _move(stock, last, reference, config)
    if move is None:
        return None
    strength = ((last - reference) / reference).quantize(_STRENGTH, ROUND_HALF_UP)
    direction = "涨" if move.kind is EventKind.PRICE_UP else "跌"
    summary = (
        f"{direction} {percent(abs(strength))}：最新价 {number(last)}，参考价 {number(reference)}"
        f"（{described}），阈值 {percent(move.threshold)}"
    )
    if move.emergency is not None:
        summary += f"；持仓风险级别，紧急阈值 {percent(move.emergency)}"
    if move.outcome is EventOutcome.EMERGENCY_EXIT:
        summary += "；达到紧急阈值，交给紧急风险退出、不排队"
    return Event(
        event_id=event_id(stock.symbol, at.day, move.kind),
        symbol=stock.symbol,
        category=EventCategory.PRICE_VOLUME,
        holding_risk=move.holding_risk,
        source=EventSource.QUOTES,
        strength=strength,
        occurred_at=reading.updated_at,
        detected_at=at.at,
        summary=summary,
        refs=(),
        outcome=move.outcome,
    )


def volume_event(
    stock: PricedStock,
    reading: Reading,
    at: Detection,
    calendar: TradingCalendar,
    config: EventRulesConfig,
) -> Event | None:
    """按量比判断成交量，见 spec「成交量异动」；开盘以后过了「成交量开始判断」才判断。"""
    session = today_session(reading, at, calendar)
    ratio = reading.volume_ratio
    if session is None or ratio is None or at.at < session.open + config.volume_delay:
        return None
    candidate = stock.status is StockStatus.CANDIDATE
    threshold = config.candidate_volume_ratio if candidate else config.volume_ratio
    if ratio < threshold:
        return None
    return Event(
        event_id=event_id(stock.symbol, at.day, EventKind.VOLUME),
        symbol=stock.symbol,
        category=EventCategory.PRICE_VOLUME,
        holding_risk=False,
        source=EventSource.QUOTES,
        strength=ratio,
        occurred_at=reading.updated_at,
        detected_at=at.at,
        summary=f"量比 {number(ratio)}，阈值 {number(threshold)}",
        refs=(),
        outcome=EventOutcome.QUEUED,
    )


def _reference(stock: PricedStock, reading: Reading) -> tuple[Decimal, str]:
    """参考价和它是哪一种：持仓今天有开仓成交时是成交均价，否则是前一交易日收盘价。"""
    if stock.entry_price_today is not None:
        return stock.entry_price_today, "今天开仓成交的均价"
    return reading.previous_close, "前一交易日收盘价"


def _move(
    stock: PricedStock, last: Decimal, reference: Decimal, config: EventRulesConfig
) -> _Move | None:
    """spec「价格异动」的表：越过哪一行的阈值；都没越过时为 None。"""
    if stock.status is StockStatus.HOLDING:
        emergency = stock.emergency_threshold
        assert emergency is not None  # 持仓都有紧急阈值
        risk = emergency * config.holding_risk_ratio
        if last >= reference * (1 + config.price_threshold):
            return _Move(EventKind.PRICE_UP, config.price_threshold)
        if last <= reference * (1 - emergency):
            exit_ = EventOutcome.EMERGENCY_EXIT
            return _Move(EventKind.PRICE_DOWN, risk, True, emergency, exit_)
        if last <= reference * (1 - risk):
            return _Move(EventKind.PRICE_DOWN, risk, True, emergency)
        return None
    if stock.status is StockStatus.CANDIDATE:
        threshold = config.candidate_price_threshold
    else:
        threshold = config.price_threshold
    if last >= reference * (1 + threshold):
        return _Move(EventKind.PRICE_UP, threshold)
    if last <= reference * (1 - threshold):
        return _Move(EventKind.PRICE_DOWN, threshold)
    return None
