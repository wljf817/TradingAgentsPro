"""执行模块测试的数据：基准数据、常用的方案、再校验结果和执行用例。

基准数据见 order-lifecycle spec「提交订单」。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID

from tests.fakes.calendar import sample_nyse_calendar
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.execution import Broker, ExecutionConfig, ExecutionService, OrderStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingSession

NYSE = sample_nyse_calendar()
BASELINE_NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)  # 周三，盘中
BASELINE_CLOSE = datetime(2026, 10, 14, 20, tzinfo=UTC)

_plan_ids = count(1)


def next_plan_id() -> UUID:
    """每次给出一个新的方案 ID：UUID(int=1)、UUID(int=2)……测试只要求它们互不相同。"""
    return UUID(int=next(_plan_ids))


def session(day: date) -> TradingSession:
    """样例日历里 day 的交易时段；day 必须是交易日。"""
    found = NYSE.session(day)
    assert found is not None
    return found


BASELINE_SESSION = session(BASELINE_NOW.date())


def baseline_plan(**changes: Any) -> TradePlan:
    """基准方案：买入 BBB（医疗）100 股，限价 50，止损价 45；方案 ID 每次都是新的。"""
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


def add_aaa(quantity: int = 50) -> TradePlan:
    """加仓 AAA（科技），限价 110，止损价 95。"""
    return TradePlan(
        plan_id=next_plan_id(),
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(110),
        stop_price=Decimal(95),
    )


def reduce_aaa(quantity: int = 60) -> TradePlan:
    """市价减仓 AAA（科技）。"""
    return TradePlan(
        plan_id=next_plan_id(),
        symbol="AAA",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


def close_aaa() -> TradePlan:
    """市价清仓 AAA（科技），不写股数。"""
    return TradePlan(
        plan_id=next_plan_id(),
        symbol="AAA",
        sector="科技",
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )


def decision(quantity: int = 100, expires_at: datetime = BASELINE_CLOSE) -> RiskDecision:
    """再校验通过的结果，最终股数和生效的有效期。默认值就是基准结果：100 股、当天收盘。"""
    return RiskDecision(
        outcome=RiskOutcome.PASSED,
        quantity=quantity,
        emergency_threshold=None,
        expires_at=expires_at,
        violations=(),
    )


def execution_service(
    store: OrderStore, broker: Broker, clock: Clock, config: ExecutionConfig | None = None
) -> ExecutionService:
    """执行用例：纽交所样例日历；没有指定配置时用默认配置。"""
    return ExecutionService(
        broker=broker,
        store=store,
        clock=clock,
        calendar=NYSE,
        config=config or ExecutionConfig(),
    )
