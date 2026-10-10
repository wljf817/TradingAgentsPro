"""收盘对账纯逻辑的测试：时机、差异的几类、连续天数，直接构造订单和止损保护。

场景见 openspec 的 close-reconciliation spec「收盘对账的时机」「差异」「连续天数」
「收盘对账配置」。用到执行模块的样例日历：2026-10-14（周三）收盘 20:00Z，2026-11-25
收盘 21:00Z，2026-11-26 感恩节休市，2026-11-27 半日市 18:00Z 收盘。
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tests.modules.execution.builders import BASELINE_SESSION, NYSE, baseline_plan, decision
from tradingagentspro.modules.execution import (
    CloseReconciliation,
    LeftoverOrder,
    NewStop,
    Order,
    OrderSide,
    OrderStatus,
    Protection,
    Reconciliation,
    StopOrder,
    StopStatus,
    UnprotectedSpell,
    WorkingOrder,
    close_day,
    close_streak,
    close_window_start,
    leftover_orders,
    new_order,
    new_stops,
    unprotected_spells,
)

DELAY = timedelta(minutes=10)
DAY = date(2026, 10, 14)


def _at(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


# 收盘对账的时机


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(14, 15), None),  # 盘中
        (_at(14, 19, 59, 59), None),  # 盘中，收盘前一秒
        (_at(14, 20, 5), None),  # 收盘以后，不到延迟
        (_at(14, 20, 9, 59), None),  # 差一秒到期
        (_at(14, 20, 10), DAY),  # 收盘加延迟
        (_at(15, 3), DAY),  # 夜里
        (_at(15, 13), DAY),  # 下一个交易日开盘前 30 分钟
        (_at(15, 13, 30), None),  # 下一个交易日开盘：不再补做
        (_at(15, 20, 10), date(2026, 10, 15)),  # 下一个交易日的收盘对账
    ],
)
def test_close_day(now: datetime, expected: date | None) -> None:
    """spec「收盘加延迟以后到期」「盘中不做」「开盘以前重启时补做」「开盘以后不再补做」。"""
    assert close_day(NYSE, now, DELAY) == expected


def test_close_day_on_a_half_day() -> None:
    """spec「半日市按实际收盘」：2026-11-27 18:00Z 收盘，18:10Z 到期。"""
    assert close_day(NYSE, _at(27, 18, 9, 59, month=11), DELAY) is None
    assert close_day(NYSE, _at(27, 18, 10, month=11), DELAY) == date(2026, 11, 27)


def test_close_day_on_a_weekend() -> None:
    """spec「周末补做周五的」：周六还属于周五收盘到周一开盘之间。"""
    assert close_day(NYSE, _at(17, 15, 0, 10), DELAY) == date(2026, 10, 16)


def test_close_day_on_a_holiday() -> None:
    """spec「节假日不另做」：感恩节当天还是 2026-11-25 的；做没做过由执行模块看记录。"""
    assert close_day(NYSE, _at(26, 18, month=11), DELAY) == date(2026, 11, 25)


def test_close_day_with_a_longer_delay() -> None:
    """spec「修改延迟」：延迟 30 分钟时 20:30Z 才到期。"""
    delay = timedelta(minutes=30)
    assert close_day(NYSE, _at(14, 20, 29, 59), delay) is None
    assert close_day(NYSE, _at(14, 20, 30), delay) == DAY


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (DAY, _at(13, 20, 10)),
        (date(2026, 10, 19), _at(16, 20, 10)),  # 周一：上一个交易日是周五
        (date(2026, 11, 27), _at(25, 21, 10, month=11)),  # 跨过感恩节，按 11-25 的收盘
    ],
)
def test_close_window_start(day: date, expected: datetime) -> None:
    """spec「差异」：当天出现过的无保护仓位从上一个交易日收盘加延迟算起。"""
    assert close_window_start(NYSE, day, DELAY) == expected


# 差异


def _order(status: OrderStatus, at: datetime) -> Order:
    plan = baseline_plan()
    order = new_order(plan, decision(), BASELINE_SESSION, at)
    return replace(order, status=status, broker_order_id="B1")


def test_leftover_orders_keep_every_order_open_before() -> None:
    """spec「收盘时还状态未知的订单」「券商查不到而结束的订单」：对账以前没结束的都算，带对账以后的状态。"""
    first = _order(OrderStatus.UNKNOWN, _at(14, 15))
    second = _order(OrderStatus.UNKNOWN, _at(14, 16))
    after = {
        first.plan_id: replace(first, status=OrderStatus.EXPIRED),
        second.plan_id: second,
    }

    leftovers = leftover_orders((second, first), after)

    assert leftovers == (
        LeftoverOrder(first.plan_id, "BBB", OrderSide.BUY, OrderStatus.EXPIRED),
        LeftoverOrder(second.plan_id, "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),
    )


def test_no_leftover_orders() -> None:
    """spec「巡检在收盘对账以前结束的订单不算」：对账以前没有未结束的订单。"""
    assert leftover_orders((), {}) == ()


def _stop(symbol: str, sequence: int, quantity: int = 100, **changes: Any) -> StopOrder:
    stop = StopOrder(
        symbol=symbol,
        client_order_id=f"{symbol}-STOP-{sequence}",
        quantity=quantity,
        stop_price=Decimal(45),
        created_at=_at(14, 15),
        status=StopStatus.WORKING,
        broker_order_id=f"S{sequence}",
    )
    return replace(stop, **changes)


def test_new_stops_are_the_symbols_whose_sequence_grew() -> None:
    """spec「对账重挂的止损单」「对账给还没有止损单的持仓补挂」。

    序号变大的才算，以前没有记录的算 0，改单不算。
    """
    used = {"BBB": 1, "CCC": 1}
    after = (
        Protection("BBB", used=2, stop=_stop("BBB", 2)),
        Protection("CCC", used=1, stop=_stop("CCC", 1, quantity=50)),  # 改单，不算
        Protection("DDD", used=1, stop=_stop("DDD", 1)),  # 以前没有记录
        Protection("EEE"),  # 没有记录也不需要止损单
    )

    assert new_stops(used, after) == (NewStop("BBB", "BBB-STOP-2"), NewStop("DDD", "DDD-STOP-1"))


def test_a_rejected_new_stop_still_counts() -> None:
    """spec「对账补挂又被拒」：挂单被拒结束的止损单也用掉了序号。"""
    ended = _stop("BBB", 2, status=StopStatus.ENDED, broker_order_id=None)
    after = (Protection("BBB", used=2, stop=ended, unprotected_since=_at(14, 20, 10)),)

    assert new_stops({"BBB": 1}, after) == (NewStop("BBB", "BBB-STOP-2"),)


def test_unprotected_spells_one_per_spell() -> None:
    """spec「当天出现过的无保护仓位」「两段无保护各一条」：同一段的多个版本只算一条。"""
    history = (
        Protection("BBB", used=1, stop=_stop("BBB", 1)),
        Protection("BBB", used=2, unprotected_since=_at(14, 15)),
        Protection("BBB", used=3, unprotected_since=_at(14, 15)),
        Protection("BBB", used=4, stop=_stop("BBB", 4)),
        Protection("BBB", used=5, unprotected_since=_at(14, 16)),
        Protection("BBB", used=6, stop=_stop("BBB", 6)),
        Protection("AAA", used=1, unprotected_since=_at(14, 17)),
    )

    assert unprotected_spells(history) == (
        UnprotectedSpell("AAA", _at(14, 17)),
        UnprotectedSpell("BBB", _at(14, 15)),
        UnprotectedSpell("BBB", _at(14, 16)),
    )


def test_a_spell_ongoing_at_the_window_start_counts() -> None:
    """spec「跨过上一个交易日收盘对账的无保护」：时间段开始时的版本就是无保护。"""
    history = (
        Protection("BBB", used=2, unprotected_since=_at(13, 20, 5)),
        Protection("BBB", used=3, stop=_stop("BBB", 3)),
    )

    assert unprotected_spells(history) == (UnprotectedSpell("BBB", _at(13, 20, 5)),)


def test_a_spell_ended_before_the_window_does_not_count() -> None:
    """spec「上一个交易日收盘对账以前结束的无保护不算」：时间段开始时的版本已经不是无保护。"""
    history = (Protection("BBB", used=3, stop=_stop("BBB", 3)),)

    assert unprotected_spells(history) == ()


# 连续天数


def _record(**changes: Any) -> CloseReconciliation:
    """没有差异、收盘时有 1 只持仓的 2026-10-14 记录。"""
    record = CloseReconciliation(
        day=DAY,
        finished_at=_at(14, 20, 10),
        result=Reconciliation(),
        leftover_orders=(),
        new_stops=(),
        unprotected=(),
        holdings=1,
        resumed=False,
        streak=0,
    )
    return replace(record, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"result": Reconciliation(unexplained=(("CCC", 100),))},
        {"result": Reconciliation(stray_orders=(WorkingOrder("CCC", "X1", "B9"),))},
        {"result": Reconciliation(extra_stops=(WorkingOrder("BBB", "X2", "S9"),))},
        {
            "leftover_orders": (
                LeftoverOrder(UUID(int=1), "BBB", OrderSide.BUY, OrderStatus.UNKNOWN),
            )
        },
        {"new_stops": (NewStop("BBB", "BBB-STOP-2"),)},
        {"unprotected": (UnprotectedSpell("BBB", _at(14, 15)),)},
    ],
)
def test_any_difference_makes_the_day_unclean(changes: dict[str, Any]) -> None:
    """spec「差异」：任何一类不为空就有差异，连续天数清零（spec「有差异时清零」）。"""
    record = _record(**changes)

    assert not record.clean
    assert not record.counted
    assert close_streak(record, _record(streak=4)) == 0


def test_first_day() -> None:
    """spec「第一个交易日」：上一个交易日没有记录时为 1。"""
    assert close_streak(_record(), None) == 1


def test_following_the_previous_day() -> None:
    """spec「接着上一个交易日」。"""
    assert close_streak(_record(), _record(day=date(2026, 10, 13), streak=4)) == 5


def test_after_a_day_with_differences() -> None:
    """spec「有差异时清零」的第二天：上一个交易日连续天数 0，这一天是 1。"""
    assert close_streak(_record(), _record(day=date(2026, 10, 13), streak=0)) == 1


def test_no_holdings_at_the_close() -> None:
    """spec「收盘时没有持仓」：没有差异，但不计入，连续天数 0。"""
    record = _record(holdings=0)

    assert record.clean
    assert not record.counted
    assert close_streak(record, _record(streak=4)) == 0
