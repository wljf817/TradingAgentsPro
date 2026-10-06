"""执行用例给出账户数据的测试：已结算资金、最近成交日、持仓的开仓信息。

场景见 openspec 的 trade-records spec「已结算资金」「最近成交日」「账户数据」，
以及「保存交易记录」的「重启后给出同样的账户数据」。券商账户是 spec 基准数据里的：
权益 100,000，现金 40,000，前一交易日收盘时的权益 101,000；现金已经包含当天卖出成交的钱。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.fakes.orders import InMemoryOrderStore
from tests.fakes.protections import InMemoryProtectionStore
from tests.fakes.stops import FakeStopBroker
from tests.fakes.trades import InMemoryTradeStore
from tests.modules.execution.builders import (
    add_bbb,
    baseline_plan,
    close_bbb,
    decision,
    execution_service,
    reduce_aaa,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    Account,
    BrokerOrderStatus,
    BrokerUnavailableError,
    ExecutionService,
    HeldPosition,
)

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
STOP_1 = "BBB-STOP-1"
TUESDAY, WEDNESDAY, FRIDAY = date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 16)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@dataclass
class _Env:
    """一套执行用例和它的存储、假券商、假止损单接口、测试时钟。"""

    store: InMemoryOrderStore
    broker: FakeBroker
    stops: FakeStopBroker
    protections: InMemoryProtectionStore
    trades: InMemoryTradeStore
    clock: FakeClock
    service: ExecutionService

    def restart(self) -> None:
        """进程重启：用同一套存储和假券商重新创建执行用例，还没有对过账。"""
        self.service = execution_service(
            self.store,
            self.broker,
            self.clock,
            None,
            self.stops,
            self.protections,
            self.trades,
            reconciled=False,
        )

    def account(self, at: datetime) -> Account:
        self.clock.set(at)
        return self.service.account()

    def filled(
        self,
        plan: TradePlan,
        at: datetime,
        filled: int,
        average_price: str,
        *,
        threshold: str = "0.08",
        working: bool = False,
    ) -> None:
        """at 时刻提交方案（有效期到当天收盘），券商回报成交 filled 股，跟进一次。

        working 为真时回报仍挂着（部分成交），否则全部成交。
        """
        self.clock.set(at)
        close = datetime(at.year, at.month, at.day, 20, tzinfo=UTC)
        result = decision(plan.quantity or 100, close, Decimal(threshold))
        self.service.submit(plan, result)
        status = WORKING if working else FILLED
        self.broker.update(str(plan.plan_id), status, filled, average_price)
        self.service.track(plan.plan_id)

    def hold_bbb(self, at: datetime, average_price: str = "49.95") -> TradePlan:
        """at 时刻买入 BBB 100 股，全部成交，券商持仓 BBB 100 股，止损单挂上。"""
        p1 = baseline_plan()
        self.broker.positions["BBB"] = 100
        self.broker.average_costs["BBB"] = Decimal(average_price)
        self.filled(p1, at, 100, average_price)
        return p1


def _env(now: datetime) -> _Env:
    store, broker, clock = InMemoryOrderStore(), FakeBroker(), FakeClock(now)
    stops, protections, trades = FakeStopBroker(), InMemoryProtectionStore(), InMemoryTradeStore()
    service = execution_service(store, broker, clock, None, stops, protections, trades)
    broker.equity, broker.cash = Decimal(100_000), Decimal(40_000)
    broker.previous_close_equity = Decimal(101_000)
    return _Env(store, broker, stops, protections, trades, clock, service)


def _sold_aaa(env: _Env, at: datetime, filled: int = 60, working: bool = False) -> None:
    """at 时刻提交 P5（减仓 AAA 60 股），成交 filled 股，成交均价 101。"""
    env.broker.positions["AAA"] = 100
    env.filled(reduce_aaa(60), at, filled, "101", working=working)


# 已结算资金


def test_todays_sale_is_unsettled_in_session() -> None:
    """spec「盘中当天的卖出未结算」：不扣费用。"""
    env = _env(_at(WEDNESDAY, 15))
    _sold_aaa(env, _at(WEDNESDAY, 15))

    assert env.account(_at(WEDNESDAY, 19)).settled_cash == Decimal(33_940)


def test_a_partly_filled_sale_counts_its_fills() -> None:
    """spec「部分成交的卖单按已成交的部分算」。"""
    env = _env(_at(WEDNESDAY, 15))
    _sold_aaa(env, _at(WEDNESDAY, 15), filled=20, working=True)

    assert env.account(_at(WEDNESDAY, 19)).settled_cash == Decimal(37_980)


def test_a_stop_filled_today_is_unsettled() -> None:
    """spec「当天成交的止损单未结算」。"""
    env = _env(_at(WEDNESDAY, 15))
    env.hold_bbb(_at(WEDNESDAY, 15), "50")
    env.stops.update(STOP_1, FILLED, quantity=0, filled=100, average_price="44.80")
    env.broker.positions["BBB"] = 0
    env.clock.set(_at(WEDNESDAY, 15, 30))
    env.service.protect("BBB")

    assert env.account(_at(WEDNESDAY, 19)).settled_cash == Decimal(35_520)


def test_todays_sale_counts_as_settled_after_the_close() -> None:
    """spec「收盘以后当天的卖出算已结算」。"""
    env = _env(_at(WEDNESDAY, 15))
    _sold_aaa(env, _at(WEDNESDAY, 15))

    assert env.account(_at(WEDNESDAY, 21)).settled_cash == Decimal(40_000)


def test_yesterdays_sale_is_settled() -> None:
    """spec「前一交易日的卖出已结算」。"""
    env = _env(_at(TUESDAY, 15))
    _sold_aaa(env, _at(TUESDAY, 15))

    assert env.account(_at(WEDNESDAY, 15)).settled_cash == Decimal(40_000)


def test_buys_leave_the_settled_cash_alone() -> None:
    """spec「买入不调整已结算资金」。"""
    env = _env(_at(WEDNESDAY, 15))
    env.hold_bbb(_at(WEDNESDAY, 15), "50")

    assert env.account(_at(WEDNESDAY, 19)).settled_cash == Decimal(40_000)


def test_fridays_sale_is_settled_at_the_weekend() -> None:
    """spec「周五的卖出在周末算已结算」。"""
    env = _env(_at(FRIDAY, 15))
    _sold_aaa(env, _at(FRIDAY, 15))

    assert env.account(_at(FRIDAY, 19)).settled_cash == Decimal(33_940)
    assert env.account(_at(date(2026, 10, 17), 15)).settled_cash == Decimal(40_000)


# 最近成交日


def test_last_trade_day_counts_stop_fills() -> None:
    """spec「买入、止损都算，取最晚的」。"""
    env = _env(_at(TUESDAY, 15))
    env.hold_bbb(_at(TUESDAY, 15))
    env.stops.update(STOP_1, FILLED, quantity=0, filled=100, average_price="44.80")
    env.broker.positions["BBB"] = 0
    env.clock.set(_at(WEDNESDAY, 15, 30))
    env.service.protect("BBB")

    assert env.account(_at(WEDNESDAY, 19)).last_trade_days == {"BBB": WEDNESDAY}


def test_orders_without_fills_give_no_last_trade_day() -> None:
    """spec「没有成交的订单不算」。"""
    env = _env(_at(WEDNESDAY, 15))
    ccc = baseline_plan(symbol="CCC")
    env.service.submit(ccc, decision())
    env.broker.update(str(ccc.plan_id), CANCELLED, 0)
    env.service.track(ccc.plan_id)

    assert "CCC" not in env.account(_at(WEDNESDAY, 19)).last_trade_days


def test_a_closed_stock_keeps_its_last_trade_day() -> None:
    """spec「清仓以后仍有最近成交日」。"""
    env = _env(_at(TUESDAY, 15))
    env.hold_bbb(_at(TUESDAY, 15))
    p4 = close_bbb()
    env.clock.set(_at(WEDNESDAY, 15))
    env.service.submit(p4, decision())
    env.stops.settle()  # BBB-STOP-1 撤销
    env.service.track(p4.plan_id)
    env.broker.update(str(p4.plan_id), FILLED, 100, "51")
    env.broker.positions["BBB"] = 0
    env.service.track(p4.plan_id)

    account = env.account(_at(WEDNESDAY, 19))

    assert account.positions == ()
    assert account.last_trade_days == {"BBB": WEDNESDAY}


# 账户数据


def _bbb_since_tuesday() -> _Env:
    """spec「读取账户数据」的数据：2026-10-13 买入 BBB 100 股，成交均价 49.95。"""
    env = _env(_at(TUESDAY, 15))
    env.hold_bbb(_at(TUESDAY, 15))
    return env


def _add_today(env: _Env) -> None:
    """spec「今天加仓成交后换成加仓方案的值」的数据：2026-10-14 15:00Z 加仓部分成交 20 股。"""
    env.broker.positions["BBB"] = 120
    env.broker.average_costs["BBB"] = Decimal("50.26")
    env.filled(add_bbb(50), _at(WEDNESDAY, 15), 20, "51.80", threshold="0.06", working=True)


def test_reading_the_account() -> None:
    """spec「读取账户数据」。"""
    env = _bbb_since_tuesday()

    assert env.account(_at(WEDNESDAY, 15)) == Account(
        equity=Decimal(100_000),
        settled_cash=Decimal(40_000),
        previous_close_equity=Decimal(101_000),
        positions=(
            HeldPosition(
                symbol="BBB",
                sector="医疗",
                quantity=100,
                average_cost=Decimal("49.95"),
                stop_price=Decimal(45),
                emergency_threshold=Decimal("0.08"),
                entry_price_today=None,
            ),
        ),
        last_trade_days={"BBB": TUESDAY},
    )


def test_an_add_filled_today_switches_to_its_values() -> None:
    """spec「今天加仓成交后换成加仓方案的值」。"""
    env = _bbb_since_tuesday()
    _add_today(env)

    account = env.account(_at(WEDNESDAY, 15, 10))

    [bbb] = account.positions
    assert (bbb.quantity, bbb.average_cost) == (120, Decimal("50.26"))
    assert (bbb.stop_price, bbb.emergency_threshold) == (Decimal(48), Decimal("0.06"))
    assert bbb.entry_price_today == Decimal("51.80")
    assert account.last_trade_days == {"BBB": WEDNESDAY}


def test_no_entry_price_outside_the_session() -> None:
    """spec「不在常规交易时段时没有今天开仓成交的均价」。"""
    env = _bbb_since_tuesday()
    _add_today(env)

    [bbb] = env.account(_at(WEDNESDAY, 21)).positions

    assert bbb.entry_price_today is None
    assert (bbb.stop_price, bbb.emergency_threshold) == (Decimal(48), Decimal("0.06"))


def test_a_holding_without_opening_orders_is_left_out() -> None:
    """spec「来历不明的持仓不列出」。"""
    env = _bbb_since_tuesday()
    env.broker.positions["CCC"] = 50
    env.broker.average_costs["CCC"] = Decimal(20)

    assert [p.symbol for p in env.account(_at(WEDNESDAY, 15)).positions] == ["BBB"]


def test_reading_the_broker_account_fails() -> None:
    """spec「读取券商账户断线」。"""
    env = _env(_at(WEDNESDAY, 15))
    env.broker.fail_next("account")

    with pytest.raises(BrokerUnavailableError):
        env.service.account()


# 保存交易记录


def test_the_same_account_after_a_restart() -> None:
    """spec「重启后给出同样的账户数据」。"""
    env = _env(_at(WEDNESDAY, 15))
    _sold_aaa(env, _at(WEDNESDAY, 15))

    env.restart()
    account = env.account(_at(WEDNESDAY, 19))

    assert account.settled_cash == Decimal(33_940)
    assert account.last_trade_days["AAA"] == WEDNESDAY
