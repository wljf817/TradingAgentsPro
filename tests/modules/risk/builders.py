"""风控测试的数据：risk-rules spec「风控的输入和判定」里的基准数据，以及常用的方案。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tests.fakes.calendar import sample_nyse_calendar
from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.risk import RiskConfig, RiskRequest

NYSE = sample_nyse_calendar()
BASELINE_NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)  # 周三，盘中


def holding(
    symbol: str,
    sector: str,
    quantity: int,
    average_cost: int | str,
    last_price: int | str,
    stop_price: int | str,
) -> Position:
    """一只持仓；价格写成整数或字符串，换算成 Decimal。"""
    return Position(
        symbol=symbol,
        sector=sector,
        quantity=quantity,
        average_cost=Decimal(average_cost),
        last_price=Decimal(last_price),
        stop_price=Decimal(stop_price),
    )


AAA = holding("AAA", "科技", 100, 100, 110, 90)


def baseline_account(**changes: Any) -> AccountSnapshot:
    """权益 100,000，已结算资金 40,000，持有 AAA，AAA 的最近成交日是 2026-10-05。"""
    fields: dict[str, Any] = {
        "equity": Decimal(100_000),
        "settled_cash": Decimal(40_000),
        "positions": (AAA,),
        "last_trade_days": {"AAA": date(2026, 10, 5)},
    }
    return AccountSnapshot(**(fields | changes))


def baseline_plan(**changes: Any) -> TradePlan:
    """买入 BBB（医疗）100 股，限价 50，止损价 45，不填紧急阈值。"""
    fields: dict[str, Any] = {
        "symbol": "BBB",
        "sector": "医疗",
        "action": TradeAction.BUY,
        "quantity": 100,
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal(50),
        "stop_price": Decimal(45),
    }
    return TradePlan(**(fields | changes))


def add_aaa(quantity: int, limit_price: int, stop_price: int) -> TradePlan:
    """加仓 AAA（科技）。"""
    return TradePlan(
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(limit_price),
        stop_price=Decimal(stop_price),
    )


def reduce_plan(symbol: str, sector: str, quantity: int) -> TradePlan:
    """市价减仓。"""
    return TradePlan(
        symbol=symbol,
        sector=sector,
        action=TradeAction.REDUCE,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


def close_plan(symbol: str, sector: str) -> TradePlan:
    """市价清仓。"""
    return TradePlan(
        symbol=symbol, sector=sector, action=TradeAction.CLOSE, order_type=OrderType.MARKET
    )


def baseline_request(**changes: Any) -> RiskRequest:
    """基准数据加基准方案：没有预留，熔断没有触发，默认阈值，纽交所样例日历。"""
    fields: dict[str, Any] = {
        "plan": baseline_plan(),
        "account": baseline_account(),
        "opening_reservations": (),
        "sell_reservations": (),
        "daily_loss_breaker_tripped": False,
        "now": BASELINE_NOW,
        "calendar": NYSE,
        "config": RiskConfig(),
    }
    return RiskRequest(**(fields | changes))
