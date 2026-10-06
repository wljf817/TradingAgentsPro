"""订单：一个方案的订单记录，以及它怎样随券商回报、券商调用失败、到期撤单和提前撤单变化。

见 order-lifecycle spec。一个方案最多一个订单，客户端订单 ID 就是方案 ID。
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from tradingagentspro.contracts.risk import RiskDecision
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.shared.trading_calendar import TradingSession


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """订单的 8 种状态，含义见 order-lifecycle spec「订单状态和券商回报」。"""

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        """全部成交、已撤销、已过期、已拒绝是终态，进入后不再变化。"""
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        )


class BrokerOrderStatus(StrEnum):
    """券商回报的状态。已提交还是部分成交、已撤销还是已过期，由订单按累计成交和截止时刻判断。"""

    WORKING = "working"  # 已受理、未结束，包括部分成交和正在撤单
    FILLED = "filled"
    CANCELLED = "cancelled"  # 撤销或失效
    REJECTED = "rejected"
    UNKNOWN = "unknown"  # 券商自己也不知道结果


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """按客户端订单 ID 查到的、券商那边这笔订单的状态。

    filled 是累计成交股数；fees 是这笔订单累计的佣金和其他费用。
    """

    broker_order_id: str
    status: BrokerOrderStatus
    filled: int
    average_price: Decimal | None
    fees: Decimal = Decimal(0)


class OrderPausedError(RuntimeError):
    """这只股票有其他方案的订单待提交或状态未知，暂停提交新订单。"""


@dataclass(frozen=True, slots=True)
class Order:
    """一个方案的订单。

    - sector：方案的板块
    - quantity：委托股数，是再校验的最终股数
    - deadline：截止时刻，到了还没结束就撤单
    - stop_price：买入、加仓订单取方案的止损价，卖单没有；止损单用它，见 stop-protection spec
    - emergency_threshold：买入、加仓订单取再校验结果里生效的紧急阈值，卖单没有
    - broker_order_id：券商订单号；券商受理过才有
    - filled、average_price、fees：累计成交股数、成交均价和累计费用
    - unknown_since：变为状态未知的时刻；不是状态未知时为 None
    - cancel_requested：已经成功请求过撤单，到期撤单和提前撤单都算
    - cancel_wanted：有撤单要求，跟进时不再提交，挂着的就请求撤单；见 spec「提前撤单」

    状态变化的方法都返回新的订单；终态的订单原样返回。
    """

    plan_id: UUID
    symbol: str
    sector: str
    side: OrderSide
    order_type: OrderType
    limit_price: Decimal | None
    quantity: int
    deadline: datetime
    created_at: datetime
    stop_price: Decimal | None = None
    emergency_threshold: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: str | None = None
    filled: int = 0
    average_price: Decimal | None = None
    fees: Decimal = Decimal(0)
    unknown_since: datetime | None = None
    cancel_requested: bool = False
    cancel_wanted: bool = False

    @property
    def client_order_id(self) -> str:
        """客户端订单 ID：方案 ID 的字符串形式，36 个字符。"""
        return str(self.plan_id)

    @property
    def blocks_new_orders(self) -> bool:
        """待提交或状态未知：还没确认券商那边的情况，这只股票暂停新订单。"""
        return self.status in (OrderStatus.PENDING, OrderStatus.UNKNOWN)

    @property
    def unsent(self) -> bool:
        """还没确认券商收到：待提交，或者券商从没受理过（没有券商订单号）的状态未知订单。"""
        return self.status is OrderStatus.PENDING or (
            self.status is OrderStatus.UNKNOWN and self.broker_order_id is None
        )

    def lookup_due(self, now: datetime, delay: timedelta) -> bool:
        """可以查询券商了：不是状态未知，或者变为状态未知已经满 delay。"""
        if self.status is not OrderStatus.UNKNOWN:
            return True
        assert self.unknown_since is not None  # 状态未知时总会记下时刻
        return now >= self.unknown_since + delay

    def cancel_due(self, now: datetime) -> bool:
        """要请求撤单了：已提交或部分成交，到了截止时刻或者有撤单要求，还没请求过撤单。"""
        return (
            self.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)
            and (now >= self.deadline or self.cancel_wanted)
            and not self.cancel_requested
        )

    def apply(self, report: BrokerOrder, now: datetime) -> Self:
        """按券商回报更新；累计成交比已记录的少的回报是过时的，忽略。"""
        if self.status.is_terminal or report.filled < self.filled:
            return self
        status = _status_of(report, self.deadline, now)
        return replace(
            self,
            status=status,
            broker_order_id=report.broker_order_id,
            filled=report.filled,
            average_price=report.average_price,
            fees=report.fees,
            unknown_since=now if status is OrderStatus.UNKNOWN else None,
        )

    def mark_unknown(self, now: datetime) -> Self:
        """券商调用超时或断线，或者受理过的订单查不到；再次调用时重新计时。"""
        if self.status.is_terminal:
            return self
        return replace(self, status=OrderStatus.UNKNOWN, unknown_since=now)

    def reject(self) -> Self:
        """券商拒绝受理，没有生成订单。"""
        return self._end(OrderStatus.REJECTED)

    def expire_unsent(self) -> Self:
        """截止时刻之后才确认券商没收到这笔订单。"""
        return self._end(OrderStatus.EXPIRED)

    def end_missing(self, now: datetime) -> Self:
        """受理过、查不到、券商挂单里也没有：早于截止时刻记为已撤销，否则记为已过期。

        累计成交和成交均价保留已经记下的。只在对账时使用，见 reconciliation spec
        「结束查不到的订单和止损单」。
        """
        return self._end(OrderStatus.CANCELLED if now < self.deadline else OrderStatus.EXPIRED)

    def want_cancel(self) -> Self:
        """记下撤单要求；状态不变，之后的跟进按它撤单。"""
        if self.status.is_terminal:
            return self
        return replace(self, cancel_wanted=True)

    def cancel_unsent(self) -> Self:
        """有撤单要求，截止时刻之前确认券商没收到这笔订单：结束为已撤销，不再提交。"""
        return self._end(OrderStatus.CANCELLED)

    def request_cancel(self) -> Self:
        """已经成功请求撤单；状态不变，等券商回报撤销或失效。"""
        if self.status.is_terminal:
            return self
        return replace(self, cancel_requested=True)

    def _end(self, status: OrderStatus) -> Self:
        if self.status.is_terminal:
            return self
        return replace(self, status=status, unknown_since=None)


def new_order(
    plan: TradePlan, decision: RiskDecision, session: TradingSession, now: datetime
) -> Order:
    """方案按再校验结果（通过或调整）建立的订单，状态是待提交。

    买入、加仓是买单，减仓、清仓是卖单；截止时刻是生效的有效期和当天收盘中较早的那个。
    买单记下方案的止损价和再校验结果里生效的紧急阈值。
    """
    assert decision.quantity is not None  # 通过或调整的结果才有最终股数和生效的有效期
    assert decision.expires_at is not None
    opening = plan.action.is_opening
    return Order(
        plan_id=plan.plan_id,
        symbol=plan.symbol,
        sector=plan.sector,
        side=OrderSide.BUY if opening else OrderSide.SELL,
        order_type=plan.order_type,
        limit_price=plan.limit_price,
        quantity=decision.quantity,
        deadline=min(decision.expires_at, session.close),
        created_at=now,
        stop_price=plan.stop_price,
        emergency_threshold=decision.emergency_threshold if opening else None,
    )


def opening_order(orders: Iterable[Order]) -> Order | None:
    """最近创建的、累计成交大于 0 的开仓订单（买单）；没有时为 None。

    一只股票的止损价、板块和紧急阈值都取这笔订单的，见 stop-protection spec「止损价」
    和 trade-records spec「账户数据」。
    """
    filled = [o for o in orders if o.side is OrderSide.BUY and o.filled > 0]
    return max(filled, key=lambda o: o.created_at, default=None)


def _status_of(report: BrokerOrder, deadline: datetime, now: datetime) -> OrderStatus:
    """券商回报对应的订单状态，见 order-lifecycle spec「订单状态和券商回报」的第二张表。"""
    match report.status:
        case BrokerOrderStatus.WORKING:
            return OrderStatus.PARTIALLY_FILLED if report.filled > 0 else OrderStatus.SUBMITTED
        case BrokerOrderStatus.FILLED:
            return OrderStatus.FILLED
        case BrokerOrderStatus.CANCELLED:
            return OrderStatus.EXPIRED if now >= deadline else OrderStatus.CANCELLED
        case BrokerOrderStatus.REJECTED:
            return OrderStatus.REJECTED
        case BrokerOrderStatus.UNKNOWN:
            return OrderStatus.UNKNOWN
