"""下单流程测试的基准数据，见 order-flow spec「提交方案」的基准数据。

当前时刻 2026-10-14 15:00Z（周三，盘中）；券商账户的权益、现金和前一交易日收盘时的权益都是 10,000；
持有 AAA 10 股：2026-10-12 买入，均价 100，开仓订单的板块"科技"、止损价 90、紧急阈值 0.08，
止损单已对齐；行情 AAA 最新价 110、前一交易日收盘价 108，BBB 最新价 50、前一交易日收盘价 49.5。
一套用替身装好的下单流程见 order_desk.py。
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from tests.fakes.broker import FakeBroker
from tests.fakes.calendar import sample_nyse_calendar
from tests.fakes.clock import FakeClock
from tests.fakes.closes import InMemoryCloseReconciliationStore
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.modules.execution.builders import decision
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    ExecutionConfig,
    ExecutionService,
)
from tradingagentspro.modules.marketdata import Quote

NYSE = sample_nyse_calendar()
NOW = datetime(2026, 10, 14, 15, tzinfo=UTC)  # 周三，盘中
EQUITY = Decimal(10_000)

# 方案 ID：P1 到 P4 固定，测试里写得出来；买入 AAA 的那个开仓方案是 AAA_BUY
P1_ID, P2_ID, P3_ID, P4_ID = UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4)
AAA_BUY = UUID(int=99)


def at(hour: int, minute: int = 0, second: int = 0, day: int = 14) -> datetime:
    """2026-10 某一天（默认 14 日，周三）的某个 UTC 时刻。"""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def quote(symbol: str, last: str | None, previous_close: str, updated_at: datetime = NOW) -> Quote:
    """一只股票的行情；last 为 None 表示没有最新价。"""
    return Quote(
        symbol=symbol,
        last_price=None if last is None else Decimal(last),
        previous_close=Decimal(previous_close),
        updated_at=updated_at,
    )


AAA_QUOTE = quote("AAA", "110", "108")
BBB_QUOTE = quote("BBB", "50", "49.5")


def p1(**changes: object) -> TradePlan:
    """P1：买入 BBB，板块"医疗"，20 股，限价 50，止损价 45。"""
    fields: dict[str, object] = {
        "plan_id": P1_ID,
        "symbol": "BBB",
        "sector": "医疗",
        "action": TradeAction.BUY,
        "quantity": 20,
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal(50),
        "stop_price": Decimal(45),
    }
    return TradePlan.model_validate(fields | changes)


def p2() -> TradePlan:
    """P2：减仓 AAA 4 股，市价单。"""
    return TradePlan(
        plan_id=P2_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.REDUCE,
        quantity=4,
        order_type=OrderType.MARKET,
    )


def p3() -> TradePlan:
    """P3：清仓 AAA，市价单。"""
    return TradePlan(
        plan_id=P3_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )


def p4() -> TradePlan:
    """P4：加仓 AAA 5 股，限价 110，止损价 100。"""
    return TradePlan(
        plan_id=P4_ID,
        symbol="AAA",
        sector="科技",
        action=TradeAction.ADD,
        quantity=5,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(110),
        stop_price=Decimal(100),
    )


def new_execution(
    broker: FakeBroker,
    stops: FakeStopBroker,
    orders: InMemoryOrderStore,
    protections: InMemoryProtectionStore,
    trades: InMemoryTradeStore,
    clock: FakeClock,
) -> ExecutionService:
    """执行模块：样例日历、默认执行配置（买单和止损单错开关闭）、新的收盘对账记录存储。

    还没有对过账。
    """
    return ExecutionService(
        broker=broker,
        stops=stops,
        store=orders,
        protections=protections,
        trades=trades,
        closes=InMemoryCloseReconciliationStore(),
        clock=clock,
        calendar=NYSE,
        config=ExecutionConfig(),
    )


def hold_aaa(execution: ExecutionService, broker: FakeBroker, clock: FakeClock) -> None:
    """基准数据的 AAA：2026-10-12 15:00Z 买入 10 股、成交均价 100、挂上止损单；券商账户 10,000。

    做完以后时钟在 NOW，执行模块已经完成一次没有不一致的对账，替身里的调用记录已经清掉。
    """
    clock.set(at(15, day=12))
    assert execution.reconcile().clean
    plan = TradePlan(
        plan_id=AAA_BUY,
        symbol="AAA",
        sector="科技",
        action=TradeAction.BUY,
        quantity=10,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(100),
        stop_price=Decimal(90),
    )
    execution.submit(plan, decision(quantity=10, expires_at=at(20, day=12)))
    broker.update(str(AAA_BUY), BrokerOrderStatus.FILLED, 10, "100")
    broker.positions["AAA"] = 10
    broker.average_costs["AAA"] = Decimal(100)
    execution.track(AAA_BUY)
    broker.equity = broker.cash = broker.previous_close_equity = EQUITY
    clock.set(NOW)
    broker.calls.clear()
