"""账户数据：券商账户加上本系统的记录，给风控和紧急退出用。

见 trade-records spec「已结算资金」「最近成交日」「账户数据」，以及 add-trade-records 的
design D6、D7。拼成风控和紧急退出的输入由调用方做。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from tradingagentspro.modules.execution.domain.order import Order, opening_order
from tradingagentspro.modules.execution.domain.trade import Trade
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """券商那边的一只持仓：股数和均价。"""

    symbol: str
    quantity: int
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    """券商账户。positions 只含股数大于 0 的持仓。

    现金包含还没结算的卖出款：模拟盘是保证金账户，券商不区分已结算和未结算的资金。
    """

    equity: Decimal
    cash: Decimal
    previous_close_equity: Decimal
    positions: tuple[BrokerPosition, ...]


@dataclass(frozen=True, slots=True)
class HeldPosition:
    """账户数据里的一只持仓。

    - quantity、average_cost：券商的
    - sector、stop_price、emergency_threshold：最近创建的、累计成交大于 0 的开仓订单的
    - entry_price_today：盘中，这只股票当天创建、有成交的开仓订单里最近那笔的成交均价；
      不在盘中或者当天没有开仓成交时为 None
    """

    symbol: str
    sector: str
    quantity: int
    average_cost: Decimal
    stop_price: Decimal
    emergency_threshold: Decimal
    entry_price_today: Decimal | None


@dataclass(frozen=True, slots=True)
class Account:
    """账户数据，见 trade-records spec「账户数据」。

    last_trade_days 是每只有成交的股票最近一次成交的交易日，包括已经没有持仓的。
    """

    equity: Decimal
    settled_cash: Decimal
    previous_close_equity: Decimal
    positions: tuple[HeldPosition, ...]
    last_trade_days: Mapping[str, date]


def unsettled_proceeds(trades: Iterable[Trade], day: date) -> Decimal:
    """执行交易日为 day 时还没结算的卖出成交额：卖单和止损单的累计成交乘以成交均价，不扣费用。"""
    return sum(
        (
            t.filled * t.average_price
            for t in trades
            if t.is_sale and t.average_price is not None and not t.settled(day)
        ),
        ZERO,
    )


def last_trade_days(trades: Iterable[Trade]) -> dict[str, date]:
    """每只股票有成交的交易记录里最晚的成交日；没有成交的股票不出现。"""
    days: dict[str, date] = {}
    for t in trades:
        if t.traded_on is not None and t.traded_on > days.get(t.symbol, date.min):
            days[t.symbol] = t.traded_on
    return days


def held_position(
    position: BrokerPosition, orders: Iterable[Order], session: TradingSession | None
) -> HeldPosition | None:
    """券商的一只持仓加上开仓信息；没有累计成交大于 0 的开仓订单时为 None，不列入账户数据。

    orders 是这只股票的全部订单；session 是当前所在的常规交易时段，不在盘中时为 None。
    """
    orders = tuple(orders)
    opening = opening_order(orders)
    if opening is None:
        return None
    assert opening.stop_price is not None  # 开仓订单都有止损价和生效的紧急阈值
    assert opening.emergency_threshold is not None
    today = None
    if session is not None:
        today = opening_order(o for o in orders if session.open <= o.created_at < session.close)
    return HeldPosition(
        symbol=position.symbol,
        sector=opening.sector,
        quantity=position.quantity,
        average_cost=position.average_cost,
        stop_price=opening.stop_price,
        emergency_threshold=opening.emergency_threshold,
        entry_price_today=None if today is None else today.average_price,
    )


def account_of(
    broker: BrokerAccount,
    orders: Mapping[str, Iterable[Order]],
    trades: Iterable[Trade],
    now: datetime,
    calendar: TradingCalendar,
) -> Account:
    """在 now 的账户数据。orders 是每只券商持仓的全部订单，trades 是有成交的交易记录。

    已结算资金是券商现金减去执行交易日还没结算的卖出成交额。
    """
    trades = tuple(trades)
    session = calendar.session_at(now)
    positions = (held_position(p, orders.get(p.symbol, ()), session) for p in broker.positions)
    return Account(
        equity=broker.equity,
        settled_cash=broker.cash - unsettled_proceeds(trades, _execution_day(now, calendar)),
        previous_close_equity=broker.previous_close_equity,
        positions=tuple(p for p in positions if p is not None),
        last_trade_days=last_trade_days(trades),
    )


def _execution_day(now: datetime, calendar: TradingCalendar) -> date:
    """执行交易日：盘中是当天，否则是下一个开盘的交易日，和风控的定义相同。"""
    session = calendar.session_at(now)
    return (session or calendar.next_session_after(now)).day
