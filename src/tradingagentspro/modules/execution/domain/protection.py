"""止损保护：每只股票一张券商端止损单，以及它怎样对齐到止损目标。

见 stop-protection spec。止损单的股数和止损价只来自券商回报，改单、撤单只是请求，
是否生效以之后查询到的回报为准。
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Self

from tradingagentspro.modules.execution.domain.order import (
    BrokerOrderStatus,
    Order,
    OrderSide,
    opening_order,
)


class UnprotectedPositionError(RuntimeError):
    """这只股票是无保护仓位，补挂成功之前不能提交新的买单。"""


class StopStatus(StrEnum):
    """止损单的 4 种状态，见 stop-protection spec「保存止损保护」。"""

    PENDING = "pending"  # 还没挂出去：已经保存，还没确认券商收到
    WORKING = "working"  # 挂着：券商已受理、未结束
    UNKNOWN = "unknown"  # 券商调用超时或断线，或者受理过的止损单查不到
    ENDED = "ended"  # 成交、撤销、失效或被拒，不再使用


@dataclass(frozen=True, slots=True)
class StopReport:
    """券商那边这张止损单的情况。

    quantity 是未成交股数；filled、average_price、fees 是这张止损单累计的成交股数、成交均价和费用，
    包括改单之前的成交。
    """

    broker_order_id: str
    status: BrokerOrderStatus
    quantity: int
    stop_price: Decimal
    filled: int = 0
    average_price: Decimal | None = None
    fees: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class StopTarget:
    """止损单应该是的样子：股数和止损价；不知道止损价时 stop_price 为 None，不挂止损单。

    uncovered 是未结束卖单超出持仓的股数，比如等待让出时止损单已经触发；有这样的股数时卖单不提交。
    """

    quantity: int
    stop_price: Decimal | None
    uncovered: int = 0


@dataclass(frozen=True, slots=True)
class StopOrder:
    """一张券商端止损单：长期有效的止损市价卖单，只在常规交易时段触发。

    - quantity、stop_price：挂着和结束时是券商回报的未成交股数和止损价；还没挂出去时是要挂的
    - created_at：新建这张止损单的时刻
    - broker_order_id：券商订单号；券商受理过才有
    - unknown_since：变为状态未知的时刻；不是状态未知时为 None
    - requested：最近一次改单请求的股数，券商回报的股数等于它时清掉；没有请求在路上时为 None
    - filled、average_price、fees：券商回报的累计成交股数、成交均价和累计费用
    - filled_at：最近一次发现累计成交增加的时刻；还没有成交时为 None。交易记录按它算成交日

    状态变化的方法都返回新的止损单。
    """

    symbol: str
    client_order_id: str
    quantity: int
    stop_price: Decimal
    created_at: datetime
    status: StopStatus = StopStatus.PENDING
    broker_order_id: str | None = None
    unknown_since: datetime | None = None
    requested: int | None = None
    filled: int = 0
    average_price: Decimal | None = None
    fees: Decimal = Decimal(0)
    filled_at: datetime | None = None

    def lookup_due(self, now: datetime, delay: timedelta) -> bool:
        """可以调用券商了：不是状态未知，或者变为状态未知已经满 delay。"""
        if self.status is not StopStatus.UNKNOWN:
            return True
        assert self.unknown_since is not None  # 状态未知时总会记下时刻
        return now >= self.unknown_since + delay

    def apply(self, report: StopReport, now: datetime) -> Self:
        """按券商回报更新，见 stop-protection spec「对齐止损单」第一张表。

        挂着和结束时记下累计成交、成交均价和费用；结果未知时这些都不变。
        """
        match report.status:
            case BrokerOrderStatus.WORKING:
                return replace(
                    self._with_fills(report, now),
                    status=StopStatus.WORKING,
                    broker_order_id=report.broker_order_id,
                    quantity=report.quantity,
                    stop_price=report.stop_price,
                    unknown_since=None,
                    requested=None if report.quantity == self.requested else self.requested,
                )
            case BrokerOrderStatus.UNKNOWN:
                return replace(
                    self,
                    status=StopStatus.UNKNOWN,
                    broker_order_id=report.broker_order_id,
                    unknown_since=now,
                )
            case _:  # 全部成交、撤销或失效、拒绝
                return replace(
                    self._with_fills(report, now),
                    status=StopStatus.ENDED,
                    broker_order_id=report.broker_order_id,
                    quantity=report.quantity,
                    unknown_since=None,
                    requested=None,
                )

    def _with_fills(self, report: StopReport, now: datetime) -> Self:
        """记下回报的累计成交、成交均价和费用；累计成交比记下的多时，记下这次发现的时刻。"""
        return replace(
            self,
            filled=report.filled,
            average_price=report.average_price,
            fees=report.fees,
            filled_at=now if report.filled > self.filled else self.filled_at,
        )

    def not_found(self, now: datetime) -> Self:
        """按客户端订单 ID 查不到：受理过的记为状态未知；没受理过的记回还没挂出去。"""
        if self.broker_order_id is not None:
            return self.mark_unknown(now)
        return replace(self, status=StopStatus.PENDING, unknown_since=None)

    def mark_unknown(self, now: datetime) -> Self:
        """券商调用超时或断线；再次失败时重新计时。"""
        return replace(self, status=StopStatus.UNKNOWN, unknown_since=now)

    def request(self, quantity: int) -> Self:
        """记下改单请求的股数；券商回报的股数和止损价不变，等之后的查询。"""
        return replace(self, requested=quantity)

    def aim(self, target: StopTarget) -> Self:
        """还没挂出去的止损单按目标挂：改成目标的股数和止损价。"""
        assert target.stop_price is not None  # 没有止损价时不挂
        return replace(self, quantity=target.quantity, stop_price=target.stop_price)

    def end(self) -> Self:
        """这张止损单不再使用：挂单被拒，或者还没挂出去就放弃。"""
        return replace(self, status=StopStatus.ENDED, unknown_since=None)


@dataclass(frozen=True, slots=True)
class Protection:
    """一只股票的止损保护。

    - used：已经用过的止损单序号，下一张是 used + 1；记录一直保留，所以序号不会重复
    - stop：当前这张止损单；还没有时为 None
    - unprotected_since：无保护仓位开始的时刻；不是无保护仓位时为 None
    """

    symbol: str
    used: int = 0
    stop: StopOrder | None = None
    unprotected_since: datetime | None = None

    @property
    def is_active(self) -> bool:
        """需要对齐：当前止损单还没结束（还没挂出去、挂着、状态未知），或者是无保护仓位。"""
        live = self.stop is not None and self.stop.status is not StopStatus.ENDED
        return live or self.unprotected_since is not None

    def new_stop(self, target: StopTarget, now: datetime) -> Self:
        """在 now 新建一张序号加 1、还没挂出去的止损单，按目标的股数和止损价。"""
        assert target.stop_price is not None  # 没有止损价时不挂
        sequence = self.used + 1
        stop = StopOrder(
            symbol=self.symbol,
            client_order_id=f"{self.symbol}-STOP-{sequence}",
            quantity=target.quantity,
            stop_price=target.stop_price,
            created_at=now,
        )
        return replace(self, used=sequence, stop=stop)

    def with_stop(self, stop: StopOrder) -> Self:
        return replace(self, stop=stop)

    def mark_unprotected(self, now: datetime) -> Self:
        """标记为无保护仓位；已经是的，保留原来的开始时刻。"""
        if self.unprotected_since is not None:
            return self
        return replace(self, unprotected_since=now)

    def mark_protected(self) -> Self:
        return replace(self, unprotected_since=None)


def stop_price_of(orders: Iterable[Order]) -> Decimal | None:
    """最近创建的、累计成交大于 0 的开仓订单的止损价；没有时为 None。

    见 stop-protection spec「止损价」。
    """
    opening = opening_order(orders)
    return None if opening is None else opening.stop_price


def stop_target(position: int, orders: Iterable[Order]) -> StopTarget:
    """止损目标：股数是持仓减去未结束卖单的未成交股数，小于 0 时为 0，超出的股数记为未覆盖。

    orders 是这只股票的全部订单。见 stop-protection spec「止损目标」。
    """
    orders = tuple(orders)
    selling = sum(
        o.quantity - o.filled
        for o in orders
        if o.side is OrderSide.SELL and not o.status.is_terminal
    )
    return StopTarget(
        quantity=max(0, position - selling),
        stop_price=stop_price_of(orders),
        uncovered=max(0, selling - position),
    )


class StopAction(StrEnum):
    """对齐时对止损单的处理，见 stop-protection spec「对齐止损单」第二张表。"""

    NONE = "none"
    PLACE = "place"  # 按目标挂单；没有或已经结束时先新建一张序号加 1 的
    MODIFY = "modify"  # 改单为目标的股数和止损价
    CANCEL = "cancel"
    DROP = "drop"  # 放弃还没挂出去的止损单，不调用券商


def is_aligned(stop: StopOrder | None, target: StopTarget) -> bool:
    """止损单挂着，券商确认的股数和止损价都等于目标，并且没有股数不同的改单请求在路上。"""
    return (
        stop is not None
        and stop.status is StopStatus.WORKING
        and (stop.quantity, stop.stop_price) == (target.quantity, target.stop_price)
        and stop.requested in (None, target.quantity)
    )


def next_action(protection: Protection, target: StopTarget) -> StopAction:
    """对齐时怎样处理当前这张止损单。状态未知的止损单不处理，由调用方先查清。"""
    status = protection.stop.status if protection.stop else StopStatus.ENDED
    if target.quantity == 0:
        if status is StopStatus.WORKING:
            return StopAction.CANCEL
        return StopAction.DROP if status is StopStatus.PENDING else StopAction.NONE
    if target.stop_price is None or status is StopStatus.UNKNOWN:
        return StopAction.NONE
    if status in (StopStatus.ENDED, StopStatus.PENDING):
        return StopAction.PLACE
    return StopAction.NONE if is_aligned(protection.stop, target) else StopAction.MODIFY


def stop_yields(protection: Protection | None, target: StopTarget) -> bool:
    """止损单已经为卖单让出股数，见 stop-protection spec「卖单先让出股数」。

    target 已经扣除了这笔卖单。卖单合计超过持仓（有未覆盖的股数）：没有让出。否则没有止损单、
    已经结束、还没挂出去，或者挂着、券商确认的股数不超过目标、没有改单请求在路上：已让出。
    改单请求还没生效时，券商之后可能把止损单改大。状态未知：没有让出。
    """
    if target.uncovered > 0:
        return False
    stop = protection.stop if protection else None
    if stop is None or stop.status in (StopStatus.ENDED, StopStatus.PENDING):
        return True
    return (
        stop.status is StopStatus.WORKING
        and stop.quantity <= target.quantity
        and stop.requested is None
    )


def backup_stop_due(
    protection: Protection | None,
    stop_price: Decimal | None,
    last_price: Decimal,
    in_session: bool,
) -> bool:
    """本地备用止损该触发了：无保护仓位、在常规交易时段内、最新价不高于止损价。"""
    return (
        protection is not None
        and protection.unprotected_since is not None
        and in_session
        and stop_price is not None
        and last_price <= stop_price
    )
