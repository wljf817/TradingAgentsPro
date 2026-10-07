"""风控测试的数据：risk-rules spec「风控的输入和判定」里的基准数据、常用的方案和风控用例。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID

from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.risk import ReservationStore, RiskConfig, RiskRequest, RiskService

NYSE = sample_nyse_calendar()
BASELINE_NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)  # 周三，盘中

# 基准数据的四个比例，不是默认值：spec 的场景按这几个数算。默认值按 2,000 左右的小资金账户设定，
# 在权益 100,000 的基准数据下很少触及上限
BASELINE_RATIOS: dict[str, Any] = {
    "max_trade_risk_ratio": Decimal("0.01"),
    "max_total_risk_ratio": Decimal("0.05"),
    "max_position_ratio": Decimal("0.20"),
    "max_sector_ratio": Decimal("0.40"),
}

_plan_ids = count(1)


def next_plan_id() -> UUID:
    """每次给出一个新的方案 ID：UUID(int=1)、UUID(int=2)……测试只要求它们互不相同。"""
    return UUID(int=next(_plan_ids))


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
    """买入 BBB（医疗）100 股，限价 50，止损价 45，不填紧急阈值；方案 ID 每次都是新的。"""
    fields: dict[str, Any] = {
        "plan_id": next_plan_id(),
        "symbol": "BBB",
        "sector": "医疗",
        "action": TradeAction.BUY,
        "quantity": 100,
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal(50),
        "stop_price": Decimal(45),
    }
    return TradePlan(**(fields | changes))


def add_aaa(
    quantity: int, limit_price: int, stop_price: int, plan_id: UUID | None = None
) -> TradePlan:
    """加仓 AAA（科技）。没有指定方案 ID 时用一个新的。"""
    return TradePlan(
        plan_id=plan_id or next_plan_id(),
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(limit_price),
        stop_price=Decimal(stop_price),
    )


def reduce_plan(symbol: str, sector: str, quantity: int, plan_id: UUID | None = None) -> TradePlan:
    """市价减仓。没有指定方案 ID 时用一个新的。"""
    return TradePlan(
        plan_id=plan_id or next_plan_id(),
        symbol=symbol,
        sector=sector,
        action=TradeAction.REDUCE,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


def close_plan(symbol: str, sector: str, plan_id: UUID | None = None) -> TradePlan:
    """市价清仓。没有指定方案 ID 时用一个新的。"""
    return TradePlan(
        plan_id=plan_id or next_plan_id(),
        symbol=symbol,
        sector=sector,
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )


def baseline_config(**changes: Any) -> RiskConfig:
    """基准配置：基准数据的四个比例，每笔佣金为 0，其余用默认值。

    spec 的基准数据不计佣金，佣金的场景另外写明。
    """
    return RiskConfig(**(BASELINE_RATIOS | {"commission_per_order": Decimal(0)} | changes))


def baseline_request(**changes: Any) -> RiskRequest:
    """基准数据加基准方案：没有预留，熔断没有触发，不处于降级模式，基准配置，纽交所样例日历。"""
    fields: dict[str, Any] = {
        "plan": baseline_plan(),
        "account": baseline_account(),
        "opening_reservations": (),
        "sell_reservations": (),
        "daily_loss_breaker_tripped": False,
        "degraded": False,
        "now": BASELINE_NOW,
        "calendar": NYSE,
        "config": baseline_config(),
    }
    return RiskRequest(**(fields | changes))


def risk_service(store: ReservationStore, **changes: Any) -> RiskService:
    """风控用例：基准时刻的测试时钟、纽交所样例日历、基准配置；changes 覆盖其中的参数。"""
    fields: dict[str, Any] = {
        "store": store,
        "clock": FakeClock(BASELINE_NOW),
        "calendar": NYSE,
        "config": baseline_config(),
    }
    return RiskService(**(fields | changes))
