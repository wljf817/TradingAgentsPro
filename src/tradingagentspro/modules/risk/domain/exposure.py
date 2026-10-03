"""风控的计算：计划风险、市值、可卖数量、执行交易日、生效的紧急阈值、有效期和允许价格偏离。

公式见 risk-rules spec「计划风险和市值的算法」。全部是十进制精确计算，不做舍入。
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.modules.risk.domain.request import OpeningReservation, SellReservation
from tradingagentspro.shared.trading_calendar import TradingCalendar, TradingSession

ZERO = Decimal(0)


def holding_of(account: AccountSnapshot, symbol: str) -> Position | None:
    """这只股票的持仓；没有持仓时为 None。"""
    return next((p for p in account.positions if p.symbol == symbol), None)


def holding_risk(position: Position, buffer_ratio: Decimal) -> Decimal:
    """已有持仓的计划风险，按均价计算；算出负数时记 0。"""
    quantity, cost = position.quantity, position.average_cost
    return max(ZERO, quantity * (cost - position.stop_price) + quantity * cost * buffer_ratio)


def merged_risk(
    holding: Position | None,
    quantity: int,
    limit_price: Decimal,
    stop_price: Decimal,
    buffer_ratio: Decimal,
) -> Decimal:
    """持仓加上方案买入部分后的计划风险，整只股票用方案的止损价；算出负数时记 0。

    按展开式计算，不先算合并后的均价，避免除法。没有持仓时就是新买入的计划风险。
    """
    held, cost = (holding.quantity, holding.average_cost) if holding else (0, ZERO)
    risk = (
        held * (cost - stop_price)
        + quantity * (limit_price - stop_price)
        + (held * cost + quantity * limit_price) * buffer_ratio
    )
    return max(ZERO, risk)


def position_value(holding: Position | None, quantity: int, limit_price: Decimal) -> Decimal:
    """这只股票的持仓市值（按最新价）加上方案买入部分的市值（按限价）。"""
    held_value = holding.quantity * holding.last_price if holding else ZERO
    return held_value + quantity * limit_price


def sector_value(
    account: AccountSnapshot, reservations: Iterable[OpeningReservation], sector: str
) -> Decimal:
    """这个板块的持仓市值加上开仓预留的市值，不含正在检查的方案。"""
    held = sum((p.quantity * p.last_price for p in account.positions if p.sector == sector), ZERO)
    reserved = sum((r.market_value for r in reservations if r.sector == sector), ZERO)
    return held + reserved


def sellable_quantity(
    account: AccountSnapshot, reservations: Iterable[SellReservation], symbol: str
) -> int:
    """持仓股数减去卖出预留的股数；没有持仓时持仓股数为 0。

    不扣除券商端止损单占用的股数：卖出前由执行模块先修改或撤销止损单。
    """
    holding = holding_of(account, symbol)
    held = holding.quantity if holding else 0
    return held - sum(r.quantity for r in reservations if r.symbol == symbol)


def execution_session(now: datetime, calendar: TradingCalendar) -> TradingSession:
    """方案会在哪个交易时段执行：盘中是当天的，否则是下一个开盘的。"""
    session = calendar.session_at(now)
    if session is None:
        session = calendar.next_session_after(now)
    return session


def execution_day(now: datetime, calendar: TradingCalendar) -> date:
    """方案会在哪个交易日执行：盘中是当天，否则是下一个开盘的交易日。"""
    return execution_session(now, calendar).day


def effective_emergency_threshold(plan: TradePlan, config: RiskConfig) -> Decimal:
    """方案填了紧急阈值就用它，没填时用默认值。"""
    if plan.emergency_threshold is None:
        return config.emergency_threshold_default
    return plan.emergency_threshold


def effective_expiry(plan: TradePlan, now: datetime, calendar: TradingCalendar) -> datetime:
    """方案写了有效期就用它，没写时是执行交易日的收盘时刻。"""
    if plan.expires_at is None:
        return execution_session(now, calendar).close
    return plan.expires_at


def effective_price_deviation(plan: TradePlan, config: RiskConfig) -> Decimal:
    """方案填了允许价格偏离就用它，没填时用默认值。"""
    if plan.max_price_deviation is None:
        return config.price_deviation_default
    return plan.max_price_deviation
