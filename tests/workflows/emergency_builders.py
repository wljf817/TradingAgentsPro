"""紧急退出流程测试的基准数据，见 emergency-exit-flow spec「巡检里的紧急退出」的基准数据。

当前时刻 2026-10-14 15:00Z（周三，盘中）；买单和止损单错开打开；券商账户的权益和前一交易日
收盘时的权益都是 10,000，现金 1,000。持有两只股票，今天都没有开仓成交：

- AAA 50 股，均价 100：2026-10-12 买入，开仓订单板块"科技"、止损价 90、紧急阈值 0.08；
  AAA-STOP-1 挂着 50 股，止损价 90
- BBB 100 股，均价 40：2026-10-13 买入，开仓订单板块"医疗"、止损价 35、紧急阈值 0.15；
  BBB-STOP-1 挂着 100 股，止损价 35

行情：AAA 前一交易日收盘价 100、最新价 99；BBB 前一交易日收盘价 40、最新价 39.5。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.modules.execution.builders import decision
from tests.workflows.builders import NYSE, World, at
from tests.workflows.order_builders import quote
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.emergency import close_plan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionConfig,
    Order,
    OrderStatus,
)
from tradingagentspro.modules.marketdata import Quote

NOW = at(15)
TODAY = date(2026, 10, 14)
EQUITY = Decimal(10_000)

AAA_OPEN, BBB_OPEN = UUID(int=101), UUID(int=102)
P2_ID, P3_ID, P4_ID, P5_ID, P6_ID, P7_ID = (UUID(int=n) for n in range(2, 8))

AAA_STOP_1, BBB_STOP_1, BBB_STOP_2, AAA_STOP_2 = (
    "AAA-STOP-1",
    "BBB-STOP-1",
    "BBB-STOP-2",
    "AAA-STOP-2",
)


def quote_at(symbol: str, last: str | None, previous_close: str) -> Quote:
    """一只股票的行情，更新时刻就是基准的当前时刻；last 为 None 表示没有最新价。"""
    return quote(symbol, last, previous_close, NOW)


def aaa_close(day: date = TODAY) -> TradePlan:
    """这个交易日 AAA 的代码触发的清仓方案。"""
    return close_plan("AAA", "科技", day)


def bbb_close(day: date = TODAY) -> TradePlan:
    """这个交易日 BBB 的代码触发的清仓方案。"""
    return close_plan("BBB", "医疗", day)


def reduce_aaa(quantity: int = 20, limit: str = "105", plan_id: UUID = P2_ID) -> TradePlan:
    """P2：减仓 AAA，限价单。"""
    return TradePlan(
        plan_id=plan_id,
        symbol="AAA",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(limit),
    )


def add_aaa_p3() -> TradePlan:
    """P3：加仓 AAA 5 股，限价 99，止损价 92。"""
    return TradePlan(
        plan_id=P3_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=5,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(99),
        stop_price=Decimal(92),
    )


def reduce_aaa_p4() -> TradePlan:
    """P4：减仓 AAA 10 股，市价单。"""
    return TradePlan(
        plan_id=P4_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=10,
        order_type=OrderType.MARKET,
    )


def buy_ccc_p5() -> TradePlan:
    """P5：买入 CCC，板块"能源"，100 股，限价 20，止损价 18。"""
    return TradePlan(
        plan_id=P5_ID,
        symbol="CCC",
        sector="能源",
        action=TradeAction.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(20),
        stop_price=Decimal(18),
    )


def add_bbb_p6() -> TradePlan:
    """P6：加仓 BBB 20 股，限价 34，止损价 30。"""
    return TradePlan(
        plan_id=P6_ID,
        symbol="BBB",
        sector="医疗",
        action=TradeAction.ADD,
        quantity=20,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(34),
        stop_price=Decimal(30),
    )


def add_aaa_p7() -> TradePlan:
    """P7：加仓 AAA 10 股，限价 98，止损价 88。"""
    return TradePlan(
        plan_id=P7_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=10,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(98),
        stop_price=Decimal(88),
    )


def emergency_world(**fields: Any) -> World:
    """基准数据上装好的巡检和紧急退出流程。做完以后时钟在 NOW，替身里的调用记录已经清掉。

    fields 原样交给 World，比如换一个通知通道的替身。
    """
    world = World(execution_config=ExecutionConfig(stagger_buys_and_stops=True), **fields)
    _open(world, AAA_OPEN, "AAA", "科技", 50, "100", "90", "0.08", at(15, day=12))
    _open(world, BBB_OPEN, "BBB", "医疗", 100, "40", "35", "0.15", at(15, day=13))
    world.broker.equity = world.broker.previous_close_equity = EQUITY
    world.broker.cash = Decimal(1_000)
    world.source.put(quote_at("AAA", "99", "100"), quote_at("BBB", "39.5", "40"))
    world.clock.set(NOW)
    world.forget_calls()
    world.broker.placed.clear()
    world.stops.placed.clear()
    return world


def place(world: World, plan: TradePlan, quantity: int) -> Order:
    """这个方案已经通过风控、预留 quantity 股，并在当前时刻提交了订单；之后清掉调用记录。"""
    order = world.submit(plan, quantity)
    world.broker.placed.clear()
    world.stops.placed.clear()
    world.source.requests.clear()
    return order


def working_reduce(world: World, quantity: int = 20, limit: str = "105") -> TradePlan:
    """P2（减仓 AAA）的订单已提交、没有成交：AAA-STOP-1 先改为剩下的股数，生效以后卖单才提交。

    之后清掉调用记录。
    """
    plan = reduce_aaa(quantity, limit)
    world.submit(plan, quantity)
    world.stops.settle()
    world.execution.track(plan.plan_id)
    assert world.order(plan).status is OrderStatus.SUBMITTED
    world.forget_calls()
    world.broker.placed.clear()
    return plan


def _open(
    world: World,
    plan_id: UUID,
    symbol: str,
    sector: str,
    quantity: int,
    price: str,
    stop: str,
    threshold: str,
    moment: datetime,
) -> None:
    """在 moment 买入 symbol 并全部成交，跟进以后挂上止损单。"""
    session = NYSE.session(moment.date())
    assert session is not None
    world.clock.set(moment)
    plan = TradePlan(
        plan_id=plan_id,
        symbol=symbol,
        sector=sector,
        action=TradeAction.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(price),
        stop_price=Decimal(stop),
    )
    result = decision(
        quantity=quantity, expires_at=session.close, emergency_threshold=Decimal(threshold)
    )
    world.execution.submit(plan, result)
    world.broker.update(str(plan_id), BrokerOrderStatus.FILLED, quantity, price)
    world.broker.positions[symbol] = world.broker.positions.get(symbol, 0) + quantity
    world.broker.average_costs[symbol] = Decimal(price)
    world.execution.track(plan_id)
