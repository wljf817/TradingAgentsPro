"""交易记录：每笔买单、卖单和止损单各一条，像银行 App 的交易流水。

见 trade-records spec。交易记录不单独维护状态，由订单和止损单推出，见 add-trade-records 的
design D1；成交日和结算日见 design D6。
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tradingagentspro.modules.execution.domain.order import Order, OrderSide, OrderStatus
from tradingagentspro.modules.execution.domain.protection import StopOrder, StopStatus
from tradingagentspro.shared.trading_calendar import TradingCalendar


class TradeKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class Trade:
    """一条交易记录，内容见 trade-records spec「交易记录的内容」。

    - client_order_id：键；订单是方案 ID，止损单是 BBB-STOP-1 这样的
    - plan_id：来源方案；止损单是止损价来自的开仓订单的方案，没有这样的订单时为 None
    - quantity：委托股数；止损单是未成交股数加累计成交
    - status：和订单的 8 种状态一致
    - traded_on、settles_on：成交日和结算日；累计成交为 0 时都为 None
    """

    client_order_id: str
    kind: TradeKind
    symbol: str
    plan_id: UUID | None
    quantity: int
    filled: int
    average_price: Decimal | None
    fees: Decimal
    status: OrderStatus
    created_at: datetime
    traded_on: date | None
    settles_on: date | None

    @property
    def is_sale(self) -> bool:
        """卖出：卖单和止损单。"""
        return self.kind is not TradeKind.BUY

    def settled(self, day: date) -> bool:
        """执行交易日为 day 时已经结算：有结算日，并且 day 不早于它。"""
        return self.settles_on is not None and day >= self.settles_on


def trade_of_order(order: Order, calendar: TradingCalendar) -> Trade:
    """订单的交易记录。成交日是订单创建时所在时段的交易日：当日有效单只在这个时段成交。"""
    session = calendar.session_at(order.created_at)
    assert session is not None  # 订单只在常规交易时段内新建
    traded_on = session.day if order.filled > 0 else None
    return Trade(
        client_order_id=order.client_order_id,
        kind=TradeKind.BUY if order.side is OrderSide.BUY else TradeKind.SELL,
        symbol=order.symbol,
        plan_id=order.plan_id,
        quantity=order.quantity,
        filled=order.filled,
        average_price=order.average_price,
        fees=order.fees,
        status=order.status,
        created_at=order.created_at,
        traded_on=traded_on,
        settles_on=_settlement(traded_on, calendar),
    )


def trade_of_stop(stop: StopOrder, plan_id: UUID | None, calendar: TradingCalendar) -> Trade:
    """止损单的交易记录。plan_id 是这只股票止损价来自的开仓订单的方案 ID。

    成交日是最近一次发现累计成交增加的时刻所在时段的交易日；不在盘中时是这个时刻之前最后一个
    收盘的交易日，因为止损单只在常规交易时段触发。
    """
    traded_on = None
    if stop.filled > 0 and stop.filled_at is not None:
        session = calendar.session_at(stop.filled_at)
        traded_on = (session or calendar.last_session_before(stop.filled_at)).day
    return Trade(
        client_order_id=stop.client_order_id,
        kind=TradeKind.STOP,
        symbol=stop.symbol,
        plan_id=plan_id,
        quantity=stop.quantity + stop.filled,
        filled=stop.filled,
        average_price=stop.average_price,
        fees=stop.fees,
        status=_stop_status(stop),
        created_at=stop.created_at,
        traded_on=traded_on,
        settles_on=_settlement(traded_on, calendar),
    )


def _stop_status(stop: StopOrder) -> OrderStatus:
    """止损单交易记录的状态，见 trade-records spec「止损单交易记录的状态」。"""
    match stop.status:
        case StopStatus.PENDING:
            return OrderStatus.PENDING
        case StopStatus.WORKING:
            return OrderStatus.PARTIALLY_FILLED if stop.filled > 0 else OrderStatus.SUBMITTED
        case StopStatus.UNKNOWN:
            return OrderStatus.UNKNOWN
    if stop.quantity == 0 and stop.filled > 0:
        return OrderStatus.FILLED
    if stop.broker_order_id is None:
        return OrderStatus.REJECTED
    return OrderStatus.CANCELLED


def _settlement(traded_on: date | None, calendar: TradingCalendar) -> date | None:
    """结算日：成交日之后的下一个交易日（T+1）；没有成交日时为 None。"""
    return None if traded_on is None else calendar.next_session(traded_on).day
