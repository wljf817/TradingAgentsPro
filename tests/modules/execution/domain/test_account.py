"""账户数据的计算的测试：已结算资金、最近成交日、持仓的开仓信息，输入直接构造。

场景见 openspec 的 trade-records spec「已结算资金」「最近成交日」「账户数据」。
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.modules.execution.builders import NYSE, add_bbb, baseline_plan, decision, session
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    Account,
    BrokerAccount,
    BrokerPosition,
    HeldPosition,
    Order,
    OrderStatus,
    Trade,
    TradeKind,
    account_of,
    held_position,
    last_trade_days,
    new_order,
    unsettled_proceeds,
)

MONDAY, TUESDAY, WEDNESDAY = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)
THURSDAY, FRIDAY, NEXT_MONDAY = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _trade(kind: TradeKind, symbol: str, filled: int, price: str, traded_on: date | None) -> Trade:
    """有成交的交易记录，结算日是成交日的下一个交易日。"""
    return Trade(
        client_order_id=f"{symbol}-{kind}-{traded_on}",
        kind=kind,
        symbol=symbol,
        plan_id=None,
        quantity=filled,
        filled=filled,
        average_price=Decimal(price) if filled else None,
        fees=Decimal(0),
        status=OrderStatus.FILLED if filled else OrderStatus.CANCELLED,
        created_at=_at(traded_on or WEDNESDAY, 15),
        traded_on=traded_on,
        settles_on=None if traded_on is None else NYSE.next_session(traded_on).day,
    )


def _filled(
    plan: TradePlan,
    created_at: datetime,
    filled: int,
    price: str,
    threshold: str = "0.08",
) -> Order:
    """方案在 created_at 建立的订单，生效的紧急阈值是 threshold，已经成交 filled 股。"""
    result = decision(plan.quantity or 100, emergency_threshold=Decimal(threshold))
    order = new_order(plan, result, session(created_at.date()), created_at)
    return replace(order, filled=filled, average_price=Decimal(price) if filled else None)


P1_ON_TUESDAY = _filled(baseline_plan(), _at(TUESDAY, 15), 100, "49.95")
BBB_100 = BrokerPosition("BBB", 100, Decimal("49.95"))


# 已结算资金


@pytest.mark.parametrize(
    ("trade", "day", "proceeds"),
    [
        (_trade(TradeKind.SELL, "AAA", 60, "101", WEDNESDAY), WEDNESDAY, "6060"),
        (_trade(TradeKind.SELL, "AAA", 20, "101", WEDNESDAY), WEDNESDAY, "2020"),
        (_trade(TradeKind.STOP, "BBB", 100, "44.80", WEDNESDAY), WEDNESDAY, "4480.00"),
        (_trade(TradeKind.SELL, "AAA", 60, "101", WEDNESDAY), THURSDAY, "0"),
        (_trade(TradeKind.SELL, "AAA", 60, "101", TUESDAY), WEDNESDAY, "0"),
        (_trade(TradeKind.BUY, "BBB", 100, "50", WEDNESDAY), WEDNESDAY, "0"),
        (_trade(TradeKind.SELL, "AAA", 60, "101", FRIDAY), FRIDAY, "6060"),
        (_trade(TradeKind.SELL, "AAA", 60, "101", FRIDAY), NEXT_MONDAY, "0"),
        (_trade(TradeKind.SELL, "AAA", 0, "0", None), WEDNESDAY, "0"),
    ],
    ids=[
        "sale-today-in-session",
        "partly-filled-sale",
        "stop-filled-today",
        "sale-today-after-close",
        "sale-the-day-before",
        "buy",
        "friday-sale-on-friday",
        "friday-sale-at-the-weekend",
        "no-fills",
    ],
)
def test_unsettled_proceeds(trade: Trade, day: date, proceeds: str) -> None:
    """spec「已结算资金」：day 是执行交易日，收盘以后和周末是下一个开盘的交易日。"""
    assert unsettled_proceeds([trade], day) == Decimal(proceeds)


def test_unsettled_proceeds_add_up() -> None:
    trades = [
        _trade(TradeKind.SELL, "AAA", 60, "101", WEDNESDAY),
        _trade(TradeKind.STOP, "BBB", 100, "44.80", WEDNESDAY),
        _trade(TradeKind.SELL, "CCC", 10, "20", TUESDAY),
    ]

    assert unsettled_proceeds(trades, WEDNESDAY) == Decimal("10540.00")


# 最近成交日


def test_last_trade_day_is_the_latest_of_buys_sells_and_stops() -> None:
    """spec「买入、止损都算，取最晚的」「没有成交的订单不算」「清仓以后仍有最近成交日」。"""
    trades = [
        _trade(TradeKind.BUY, "BBB", 100, "50", TUESDAY),
        _trade(TradeKind.STOP, "BBB", 100, "44.80", WEDNESDAY),
        _trade(TradeKind.SELL, "AAA", 100, "101", MONDAY),
        _trade(TradeKind.BUY, "AAA", 50, "100", TUESDAY),
        _trade(TradeKind.BUY, "CCC", 0, "0", None),
    ]

    assert last_trade_days(trades) == {"BBB": WEDNESDAY, "AAA": TUESDAY}


# 持仓的开仓信息


def test_opening_details_come_from_the_latest_filled_opening_order() -> None:
    """spec「读取账户数据」。"""
    position = held_position(BBB_100, [P1_ON_TUESDAY], session(WEDNESDAY))

    assert position == HeldPosition(
        symbol="BBB",
        sector="医疗",
        quantity=100,
        average_cost=Decimal("49.95"),
        stop_price=Decimal(45),
        emergency_threshold=Decimal("0.08"),
        entry_price_today=None,
    )


def test_an_add_filled_today_switches_the_details_and_gives_the_entry_price() -> None:
    """spec「今天加仓成交后换成加仓方案的值」。"""
    added = _filled(add_bbb(50), _at(WEDNESDAY, 15), 20, "51.80", threshold="0.06")
    held = BrokerPosition("BBB", 120, Decimal("50.26"))

    position = held_position(held, [P1_ON_TUESDAY, added], session(WEDNESDAY))

    assert position is not None
    assert (position.quantity, position.average_cost) == (120, Decimal("50.26"))
    assert (position.stop_price, position.emergency_threshold) == (Decimal(48), Decimal("0.06"))
    assert position.entry_price_today == Decimal("51.80")


def test_no_entry_price_outside_the_session() -> None:
    """spec「不在常规交易时段时没有今天开仓成交的均价」。"""
    added = _filled(add_bbb(50), _at(WEDNESDAY, 15), 20, "51.80", threshold="0.06")

    position = held_position(BBB_100, [P1_ON_TUESDAY, added], None)

    assert position is not None
    assert position.entry_price_today is None
    assert (position.stop_price, position.emergency_threshold) == (Decimal(48), Decimal("0.06"))


def test_an_add_without_fills_today_gives_no_entry_price() -> None:
    unfilled = _filled(add_bbb(50), _at(WEDNESDAY, 15), 0, "0", threshold="0.06")

    position = held_position(BBB_100, [P1_ON_TUESDAY, unfilled], session(WEDNESDAY))

    assert position is not None
    assert position.entry_price_today is None
    assert (position.stop_price, position.emergency_threshold) == (Decimal(45), Decimal("0.08"))


def test_a_holding_without_a_filled_opening_order_is_left_out() -> None:
    """spec「来历不明的持仓不列出」。"""
    unfilled = _filled(baseline_plan(), _at(WEDNESDAY, 15), 0, "0")

    assert held_position(BBB_100, [], session(WEDNESDAY)) is None
    assert held_position(BBB_100, [unfilled], session(WEDNESDAY)) is None


# 账户数据


def _broker(*positions: BrokerPosition) -> BrokerAccount:
    """trade-records spec 基准数据里的券商账户。"""
    return BrokerAccount(
        equity=Decimal(100_000),
        cash=Decimal(40_000),
        previous_close_equity=Decimal(101_000),
        positions=positions,
    )


def test_account_combines_the_broker_and_the_records() -> None:
    """spec「读取账户数据」「来历不明的持仓不列出」「盘中当天的卖出未结算」。"""
    broker = _broker(BBB_100, BrokerPosition("CCC", 50, Decimal(20)))
    trades = [
        _trade(TradeKind.BUY, "BBB", 100, "49.95", TUESDAY),
        _trade(TradeKind.SELL, "AAA", 60, "101", WEDNESDAY),
    ]

    account = account_of(broker, {"BBB": [P1_ON_TUESDAY]}, trades, _at(WEDNESDAY, 19), NYSE)

    assert account == Account(
        equity=Decimal(100_000),
        settled_cash=Decimal(33_940),
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
        last_trade_days={"BBB": TUESDAY, "AAA": WEDNESDAY},
    )


@pytest.mark.parametrize(
    ("now", "settled_cash"),
    [
        (_at(WEDNESDAY, 19), "33940"),
        (_at(WEDNESDAY, 21), "40000"),
        (_at(THURSDAY, 15), "40000"),
    ],
    ids=["in-session", "after-close", "next-day"],
)
def test_settled_cash_follows_the_execution_day(now: datetime, settled_cash: str) -> None:
    """spec「盘中当天的卖出未结算」「收盘以后当天的卖出算已结算」。"""
    trades = [_trade(TradeKind.SELL, "AAA", 60, "101", WEDNESDAY)]

    account = account_of(_broker(), {}, trades, now, NYSE)

    assert account.settled_cash == Decimal(settled_cash)


def test_friday_sales_are_settled_at_the_weekend() -> None:
    """spec「周五的卖出在周末算已结算」。"""
    trades = [_trade(TradeKind.SELL, "AAA", 60, "101", FRIDAY)]

    friday = account_of(_broker(), {}, trades, _at(FRIDAY, 19), NYSE)
    saturday = account_of(_broker(), {}, trades, _at(date(2026, 10, 17), 15), NYSE)

    assert (friday.settled_cash, saturday.settled_cash) == (Decimal(33_940), Decimal(40_000))
