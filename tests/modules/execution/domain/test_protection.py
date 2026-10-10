"""止损保护纯逻辑的测试，直接构造订单、止损单和券商回报。

场景见 openspec 的 stop-protection spec「止损价」「止损目标」「对齐止损单」「卖单先让出股数」
「买单和止损单错开」「本地备用止损的触发」「止损单的券商调用失败」。
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from tests.modules.execution.builders import (
    BASELINE_NOW,
    BASELINE_SESSION,
    add_bbb,
    baseline_plan,
    close_bbb,
    decision,
    reduce_bbb,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.execution import (
    BrokerOrderStatus,
    Order,
    OrderStatus,
    Protection,
    StopAction,
    StopOrder,
    StopReport,
    StopStatus,
    StopTarget,
    backup_stop_due,
    new_order,
    next_action,
    stop_price_of,
    stop_target,
    stop_withdrawn,
    stop_yields,
)

DELAY = timedelta(seconds=30)


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """基准交易日 2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


def _order(
    plan: TradePlan,
    status: OrderStatus,
    filled: int = 0,
    *,
    quantity: int | None = None,
    at: datetime = BASELINE_NOW,
) -> Order:
    """方案在 at 时刻建立的订单，委托股数默认是方案的股数（清仓是 100），已经成交 filled 股。"""
    final = quantity or plan.quantity or 100
    order = new_order(plan, decision(final), BASELINE_SESSION, at)
    return replace(order, status=status, filled=filled)


BOUGHT = _order(baseline_plan(), OrderStatus.FILLED, 100)  # P1 全部成交，止损价 45


def _stop(**changes: Any) -> StopOrder:
    """挂着的 BBB-STOP-1：100 股，止损价 45，券商订单号 S1。"""
    stop = StopOrder(
        symbol="BBB",
        client_order_id="BBB-STOP-1",
        quantity=100,
        stop_price=Decimal(45),
        created_at=BASELINE_NOW,
        status=StopStatus.WORKING,
        broker_order_id="S1",
    )
    return replace(stop, **changes)


def _report(
    status: BrokerOrderStatus, quantity: int = 100, stop_price: str = "45", **fills: Any
) -> StopReport:
    """券商回报；fills 是累计成交、成交均价和累计费用，没给时用回报的默认值。"""
    return StopReport(
        broker_order_id="S1",
        status=status,
        quantity=quantity,
        stop_price=Decimal(stop_price),
        **fills,
    )


# 止损价


def test_add_filled_in_part_switches_to_its_stop_price() -> None:
    """spec「加仓部分成交后换成加仓方案的止损价」。"""
    added = _order(add_bbb(), OrderStatus.PARTIALLY_FILLED, 20, at=_at(15, 10))

    assert stop_price_of([added, BOUGHT]) == Decimal(48)


def test_add_without_fills_keeps_the_stop_price() -> None:
    """spec「加仓一股没成交就结束，止损价不变」。"""
    added = _order(add_bbb(), OrderStatus.CANCELLED, 0, at=_at(15, 10))

    assert stop_price_of([BOUGHT, added]) == Decimal(45)


def test_a_later_add_without_fills_keeps_the_earlier_add_price() -> None:
    first_add = _order(add_bbb(), OrderStatus.FILLED, 50, at=_at(15, 10))
    second_add = _order(add_bbb(), OrderStatus.EXPIRED, 0, at=_at(15, 20))  # 一股没成交就到期

    assert stop_price_of([second_add, BOUGHT, first_add]) == Decimal(48)


def test_no_stop_price_without_a_filled_opening_order() -> None:
    """spec「没有开仓订单的持仓不挂止损单」：没有止损价。"""
    sold = _order(reduce_bbb(), OrderStatus.FILLED, 60)
    unfilled_buy = _order(baseline_plan(), OrderStatus.SUBMITTED, 0)

    assert stop_price_of([]) is None
    assert stop_price_of([sold, unfilled_buy]) is None


# 止损目标


def test_a_pending_sell_yields_its_shares() -> None:
    """spec「卖单待提交时目标让出它的股数」。"""
    reducing = _order(reduce_bbb(), OrderStatus.PENDING)

    assert stop_target(100, [BOUGHT, reducing]) == StopTarget(40, Decimal(45))


def test_target_is_unchanged_while_the_sell_fills() -> None:
    """spec「卖单部分成交时目标不变」。"""
    reducing = _order(reduce_bbb(), OrderStatus.PARTIALLY_FILLED, 20)

    assert stop_target(80, [BOUGHT, reducing]).quantity == 40


def test_target_returns_to_the_holding_after_the_sell_ends() -> None:
    """spec「卖单结束后目标恢复为剩下的持仓」。"""
    reduced = _order(reduce_bbb(), OrderStatus.EXPIRED, 20)

    assert stop_target(80, [BOUGHT, reduced]).quantity == 80


def test_a_close_yields_every_share() -> None:
    """spec「清仓卖单让出全部股数」。"""
    closing = _order(close_bbb(), OrderStatus.PENDING)

    assert stop_target(100, [BOUGHT, closing]).quantity == 0


def test_target_is_never_negative() -> None:
    """spec「卖单股数超过持仓时目标为 0」：超出的股数记为未覆盖。"""
    closing = _order(close_bbb(), OrderStatus.PENDING)

    assert stop_target(70, [BOUGHT, closing]) == StopTarget(0, Decimal(45), uncovered=30)
    assert stop_target(100, [BOUGHT, closing]).uncovered == 0


def test_unknown_sells_also_yield_their_shares() -> None:
    reducing = _order(reduce_bbb(), OrderStatus.UNKNOWN)

    assert stop_target(100, [BOUGHT, reducing]).quantity == 40


def test_buy_orders_do_not_count_as_sells() -> None:
    buying = _order(baseline_plan(), OrderStatus.PARTIALLY_FILLED, 30)

    assert stop_target(30, [buying]) == StopTarget(30, Decimal(45))


def test_no_stop_price_in_the_target_without_opening_orders() -> None:
    assert stop_target(100, []) == StopTarget(100, None)


# 买单和止损单错开时的止损目标（spec「止损目标」「买单和止损单错开」）


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.UNKNOWN]
)
def test_staggered_target_is_zero_while_a_buy_is_unfinished(status: OrderStatus) -> None:
    """spec「错开时有买单没结束，目标为 0」：止损价照常，这只股票错开中。"""
    adding = _order(add_bbb(), status, at=_at(15, 10))

    assert stop_target(100, [BOUGHT, adding], stagger=True) == StopTarget(
        0, Decimal(45), staggered=True
    )


def test_target_counts_unfinished_buys_only_when_staggering() -> None:
    """spec「错开时有买单没结束，目标为 0」：开关关闭时目标是 100 股。"""
    adding = _order(add_bbb(), OrderStatus.SUBMITTED, at=_at(15, 10))

    assert stop_target(100, [BOUGHT, adding]) == StopTarget(100, Decimal(45))
    assert stop_target(100, [BOUGHT, adding], stagger=False) == StopTarget(100, Decimal(45))


def test_staggered_target_takes_the_add_price_once_it_fills() -> None:
    """spec「加仓部分成交时不挂止损单」：止损目标是 0 股，止损价 48。"""
    adding = _order(add_bbb(), OrderStatus.PARTIALLY_FILLED, 20, at=_at(15, 10))

    assert stop_target(120, [BOUGHT, adding], stagger=True) == StopTarget(
        0, Decimal(48), staggered=True
    )


@pytest.mark.parametrize(
    "status", [OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED]
)
def test_staggered_target_returns_once_the_buys_end(status: OrderStatus) -> None:
    """spec「错开时买单都结束了，目标恢复」。"""
    added = _order(add_bbb(), status, at=_at(15, 10))

    assert stop_target(100, [BOUGHT, added], stagger=True) == StopTarget(100, Decimal(45))


def test_a_new_buy_is_staggered_only_once_it_has_shares() -> None:
    """spec「新买入部分成交时不挂止损单，买单结束后再挂」：持仓为 0 时不是错开中。"""
    unfilled = _order(baseline_plan(), OrderStatus.SUBMITTED)
    filling = _order(baseline_plan(), OrderStatus.PARTIALLY_FILLED, 30)

    assert stop_target(0, [unfilled], stagger=True) == StopTarget(0, None)
    assert stop_target(30, [filling], stagger=True) == StopTarget(0, Decimal(45), staggered=True)


def test_staggered_target_keeps_the_sell_rules() -> None:
    """卖单照常让出股数：卖单让出全部股数时不错开目标也是 0，不算错开中；未覆盖的股数照常。"""
    adding = _order(add_bbb(), OrderStatus.SUBMITTED, at=_at(15, 10))
    reducing = _order(reduce_bbb(), OrderStatus.PENDING)
    closing = _order(close_bbb(), OrderStatus.PENDING)

    assert stop_target(100, [BOUGHT, adding, reducing], stagger=True) == StopTarget(
        0, Decimal(45), staggered=True
    )
    assert stop_target(100, [BOUGHT, adding, closing], stagger=True) == StopTarget(0, Decimal(45))
    assert stop_target(70, [BOUGHT, adding, closing], stagger=True) == StopTarget(
        0, Decimal(45), uncovered=30
    )


# 按券商回报更新止损单（spec「对齐止损单」第一张表）


def test_working_report_records_the_broker_order_quantity_and_price() -> None:
    pending = _stop(status=StopStatus.PENDING, broker_order_id=None, quantity=30)

    working = pending.apply(_report(BrokerOrderStatus.WORKING, 30), BASELINE_NOW)

    assert working == _stop(quantity=30)


def test_working_report_replaces_the_recorded_quantity_and_price() -> None:
    working = _stop().apply(_report(BrokerOrderStatus.WORKING, 120, "48"), BASELINE_NOW)

    assert (working.quantity, working.stop_price) == (120, Decimal(48))


def test_a_modify_request_is_recorded_until_the_broker_shows_it() -> None:
    requested = _stop(quantity=30).request(100, _at(15))

    assert (requested.requested, requested.requested_at) == (100, _at(15))
    assert (requested.quantity, requested.stop_price) == (30, Decimal(45))
    pending = requested.apply(_report(BrokerOrderStatus.WORKING, 30), _at(15, 0, 5))
    assert (pending.requested, pending.requested_at) == (100, _at(15))
    effective = requested.apply(_report(BrokerOrderStatus.WORKING, 100), _at(15, 0, 5))
    assert (effective.requested, effective.requested_at) == (None, None)


@pytest.mark.parametrize(
    "status",
    [BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED],
)
def test_filled_cancelled_or_rejected_report_ends_the_stop(status: BrokerOrderStatus) -> None:
    assert _stop().apply(_report(status, 0), BASELINE_NOW).status is StopStatus.ENDED


@pytest.mark.parametrize(
    "status",
    [BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED],
)
def test_an_ended_stop_drops_its_modify_request(status: BrokerOrderStatus) -> None:
    requested = _stop(quantity=30).request(100, _at(15))

    ended = requested.apply(_report(status, 0), _at(15, 0, 5))

    assert (ended.requested, ended.requested_at) == (None, None)


def test_working_report_records_fills_fees_and_when_they_were_seen() -> None:
    """spec「止损单部分成交后记下成交，不改单」：股数是未成交股数。"""
    report = _report(
        BrokerOrderStatus.WORKING,
        60,
        filled=40,
        average_price=Decimal("44.90"),
        fees=Decimal(2),
    )

    working = _stop().apply(report, _at(15, 30))

    assert (working.status, working.quantity) == (StopStatus.WORKING, 60)
    assert (working.filled, working.average_price, working.fees) == (
        40,
        Decimal("44.90"),
        Decimal(2),
    )
    assert working.filled_at == _at(15, 30)


@pytest.mark.parametrize(
    ("status", "unfilled", "filled"),
    [
        (BrokerOrderStatus.FILLED, 0, 100),
        (BrokerOrderStatus.CANCELLED, 100, 0),
        (BrokerOrderStatus.CANCELLED, 60, 40),
        (BrokerOrderStatus.REJECTED, 100, 0),
    ],
    ids=["filled", "cancelled", "cancelled-after-fills", "rejected"],
)
def test_ended_report_records_the_final_quantity_and_fills(
    status: BrokerOrderStatus, unfilled: int, filled: int
) -> None:
    """spec「止损单成交后不再挂新的」：结束时也记下未成交股数、累计成交、成交均价和费用。"""
    price = Decimal("44.80") if filled else None
    report = _report(status, unfilled, filled=filled, average_price=price, fees=Decimal(filled > 0))

    ended = _stop().apply(report, _at(15, 30))

    assert (ended.status, ended.quantity, ended.filled) == (StopStatus.ENDED, unfilled, filled)
    assert (ended.average_price, ended.fees) == (price, Decimal(filled > 0))
    assert ended.filled_at == (_at(15, 30) if filled else None)


def test_the_time_fills_were_seen_changes_only_when_they_grow() -> None:
    seen = _stop(quantity=60, filled=40, filled_at=_at(15, 30))

    same = seen.apply(_report(BrokerOrderStatus.WORKING, 60, filled=40), _at(15, 40))
    more = same.apply(_report(BrokerOrderStatus.WORKING, 50, filled=50), _at(15, 50))

    assert same.filled_at == _at(15, 30)
    assert more.filled_at == _at(15, 50)


def test_unknown_report_keeps_the_recorded_fills() -> None:
    seen = _stop(quantity=60, filled=40, average_price=Decimal("44.90"), filled_at=_at(15, 30))

    unknown = seen.apply(_report(BrokerOrderStatus.UNKNOWN), _at(15, 40))

    assert (unknown.quantity, unknown.filled, unknown.average_price) == (60, 40, Decimal("44.90"))
    assert unknown.filled_at == _at(15, 30)


def test_unknown_report_records_the_time() -> None:
    unknown = _stop().apply(_report(BrokerOrderStatus.UNKNOWN), _at(15, 10))

    assert (unknown.status, unknown.unknown_since) == (StopStatus.UNKNOWN, _at(15, 10))
    assert (unknown.quantity, unknown.broker_order_id) == (100, "S1")


def test_report_clears_the_unknown_time() -> None:
    unknown = _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15))

    working = unknown.apply(_report(BrokerOrderStatus.WORKING), _at(15, 0, 30))

    assert (working.status, working.unknown_since) == (StopStatus.WORKING, None)


# 止损单的券商调用失败


def test_a_stop_the_broker_accepted_but_cannot_find_is_unknown() -> None:
    """spec「受理过的止损单查不到」。"""
    lost = _stop().not_found(_at(15, 10))

    assert (lost.status, lost.unknown_since) == (StopStatus.UNKNOWN, _at(15, 10))
    assert (lost.quantity, lost.broker_order_id) == (100, "S1")


@pytest.mark.parametrize("status", [StopStatus.PENDING, StopStatus.UNKNOWN])
def test_a_stop_the_broker_never_accepted_is_pending_again(status: StopStatus) -> None:
    """spec「挂单超时，券商没有收到」：查不到、没有券商订单号，记回还没挂出去。"""
    stop = _stop(status=status, broker_order_id=None, unknown_since=_at(15))

    pending = stop.not_found(_at(15, 0, 30))

    assert (pending.status, pending.unknown_since) == (StopStatus.PENDING, None)


def test_mark_unknown_keeps_the_recorded_fields() -> None:
    unknown = _stop().mark_unknown(_at(15))

    assert unknown == _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15))


def test_lookup_waits_for_the_delay_after_unknown() -> None:
    unknown = _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15))

    assert not unknown.lookup_due(_at(15, 0, 29), DELAY)
    assert unknown.lookup_due(_at(15, 0, 30), DELAY)
    assert _stop().lookup_due(_at(15), DELAY)


# 止损保护记录


def test_each_new_stop_uses_the_next_sequence() -> None:
    """spec「止损单」：客户端订单 ID 是股票、STOP 和序号。"""
    first = Protection("BBB").new_stop(StopTarget(30, Decimal(45)), BASELINE_NOW)
    second = replace(first, stop=None).new_stop(StopTarget(100, Decimal(48)), BASELINE_NOW)

    assert first.used == 1
    assert first.stop == StopOrder(
        symbol="BBB",
        client_order_id="BBB-STOP-1",
        quantity=30,
        stop_price=Decimal(45),
        created_at=BASELINE_NOW,
    )
    assert second.used == 2
    assert second.stop is not None
    assert second.stop.client_order_id == "BBB-STOP-2"


def test_a_new_stop_records_when_it_was_created() -> None:
    """spec「对齐止损单」：新建止损单时记下创建时刻。"""
    protection = Protection("BBB").new_stop(StopTarget(30, Decimal(45)), _at(15, 10))

    assert protection.stop is not None
    assert protection.stop.created_at == _at(15, 10)
    assert (protection.stop.filled, protection.stop.filled_at) == (0, None)


def test_marking_unprotected_keeps_the_first_time() -> None:
    """spec「已经无保护时保留原来的开始时刻」。"""
    unprotected = Protection("BBB").mark_unprotected(_at(15))

    assert unprotected.mark_unprotected(_at(15, 5)).unprotected_since == _at(15)
    assert unprotected.mark_protected().unprotected_since is None


def test_a_protection_is_not_staggered_by_default() -> None:
    assert Protection("BBB").staggered is False


def test_with_staggered_changes_only_that_flag() -> None:
    protection = Protection("BBB", used=1, stop=_stop(), unprotected_since=_at(15))

    assert protection.with_staggered(True) == replace(protection, staggered=True)
    assert protection.with_staggered(True).with_staggered(False) == protection


# 需要对齐（state-store spec「读取最新版本」止损保护表那一行）


@pytest.mark.parametrize(
    "stop",
    [
        _stop(status=StopStatus.PENDING, broker_order_id=None),
        _stop(),
        _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15)),
    ],
    ids=["pending", "working", "unknown"],
)
def test_needs_alignment_while_the_stop_has_not_ended(stop: StopOrder) -> None:
    assert Protection("BBB", used=1, stop=stop).is_active


@pytest.mark.parametrize("stop", [None, _stop(status=StopStatus.ENDED)], ids=["none", "ended"])
def test_no_alignment_needed_without_a_live_stop(stop: StopOrder | None) -> None:
    """state-store spec「止损单结束后不再需要对齐」。"""
    assert not Protection("BBB", used=1, stop=stop).is_active


@pytest.mark.parametrize("stop", [None, _stop(status=StopStatus.ENDED)], ids=["none", "ended"])
def test_unprotected_position_needs_alignment(stop: StopOrder | None) -> None:
    """state-store spec「无保护仓位需要对齐」：不论有没有止损单。"""
    assert Protection("BBB", used=1, stop=stop, unprotected_since=_at(15)).is_active


# 对齐时的处理（spec「对齐止损单」第二张表）

TARGET = StopTarget(100, Decimal(45))


def _with(stop: StopOrder | None) -> Protection:
    return Protection("BBB", used=1, stop=stop)


def _next(protection: Protection, target: StopTarget, now: datetime = BASELINE_NOW) -> StopAction:
    """等待时长是默认的 30 秒时，now 这次对齐的处理。"""
    return next_action(protection, target, now, DELAY)


@pytest.mark.parametrize("stop", [None, _stop(status=StopStatus.ENDED)], ids=["none", "ended"])
def test_place_when_there_is_no_live_stop(stop: StopOrder | None) -> None:
    assert _next(_with(stop), TARGET) is StopAction.PLACE


def test_place_a_stop_that_never_went_out() -> None:
    pending = _stop(status=StopStatus.PENDING, broker_order_id=None)

    assert _next(_with(pending), TARGET) is StopAction.PLACE


def test_leave_an_aligned_stop_alone() -> None:
    assert _next(_with(_stop()), TARGET) is StopAction.NONE


@pytest.mark.parametrize(
    "stop",
    [_stop(quantity=30), _stop(stop_price=Decimal(48))],
    ids=["quantity", "stop-price"],
)
def test_modify_when_the_quantity_or_stop_price_differs(stop: StopOrder) -> None:
    assert _next(_with(stop), TARGET) is StopAction.MODIFY


def test_modify_again_when_a_pending_request_differs_from_the_target() -> None:
    """spec「改大的请求还没生效时不提交卖单」：按目标再请求一次，覆盖之前的请求。"""
    stale = _stop(quantity=30, requested=100, requested_at=_at(15))

    assert _next(_with(stale), StopTarget(30, Decimal(45)), _at(15, 0, 30)) is StopAction.MODIFY


def test_cancel_when_the_target_is_zero() -> None:
    assert _next(_with(_stop()), StopTarget(0, Decimal(45))) is StopAction.CANCEL


def test_drop_a_stop_that_never_went_out_when_the_target_is_zero() -> None:
    """spec「还没挂出去的止损单在目标为 0 时放弃」。"""
    pending = _stop(status=StopStatus.PENDING, broker_order_id=None)

    assert _next(_with(pending), StopTarget(0, Decimal(45))) is StopAction.DROP


@pytest.mark.parametrize("stop", [None, _stop(status=StopStatus.ENDED)], ids=["none", "ended"])
def test_nothing_to_do_without_a_stop_or_shares(stop: StopOrder | None) -> None:
    assert _next(_with(stop), StopTarget(0, Decimal(45))) is StopAction.NONE


@pytest.mark.parametrize("stop", [None, _stop(quantity=30)], ids=["none", "working"])
def test_nothing_to_do_without_a_stop_price(stop: StopOrder | None) -> None:
    assert _next(_with(stop), StopTarget(100, None)) is StopAction.NONE


def test_nothing_to_do_while_the_stop_is_unknown() -> None:
    unknown = _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15), quantity=30)

    assert _next(_with(unknown), TARGET) is StopAction.NONE


# 改单请求在等待生效（spec「对齐止损单」）

REQUESTED = _stop(quantity=30).request(100, _at(15))  # 15:00Z 请求改为 100 股，券商还是 30 股


def test_a_modify_request_awaits_until_the_wait_is_over() -> None:
    assert REQUESTED.awaits_modify(_at(15), DELAY)
    assert REQUESTED.awaits_modify(_at(15, 0, 29), DELAY)
    assert not REQUESTED.awaits_modify(_at(15, 0, 30), DELAY)


def test_nothing_awaits_without_a_modify_request() -> None:
    assert not _stop().awaits_modify(_at(15), DELAY)


def test_an_old_record_without_the_request_time_does_not_wait() -> None:
    """本变更之前保存的记录只有请求的股数，当作已经满等待时长。"""
    assert not _stop(quantity=30, requested=100).awaits_modify(_at(15), DELAY)


@pytest.mark.parametrize(
    "stop",
    [
        replace(REQUESTED, status=StopStatus.UNKNOWN, unknown_since=_at(15)),
        replace(REQUESTED, status=StopStatus.ENDED),
    ],
    ids=["unknown", "ended"],
)
def test_only_a_working_stop_awaits_a_modify(stop: StopOrder) -> None:
    assert not stop.awaits_modify(_at(15, 0, 1), DELAY)


@pytest.mark.parametrize(
    "target",
    [
        TARGET,
        StopTarget(30, Decimal(45)),
        StopTarget(0, Decimal(45)),
        StopTarget(100, None),
    ],
    ids=["quantity-differs", "only-request-differs", "zero", "no-stop-price"],
)
def test_leave_the_stop_alone_while_a_modify_awaits(target: StopTarget) -> None:
    """spec「改单的结果以查询为准」「清仓时改单请求还没生效，先不撤止损单」：不改单，也不撤单。"""
    assert _next(_with(REQUESTED), target, _at(15, 0, 29)) is StopAction.NONE


def test_modify_again_once_the_wait_is_over() -> None:
    """spec「改单请求满等待时长还没生效，再改一次」。"""
    assert _next(_with(REQUESTED), TARGET, _at(15, 0, 30)) is StopAction.MODIFY


def test_cancel_once_the_wait_is_over() -> None:
    assert _next(_with(REQUESTED), StopTarget(0, Decimal(45)), _at(15, 0, 30)) is StopAction.CANCEL


# 卖单先让出股数

YIELDED = StopTarget(40, Decimal(45))  # 持有 100 股，减仓 60 股待提交


@pytest.mark.parametrize(
    "protection",
    [
        None,
        _with(None),
        _with(_stop(status=StopStatus.ENDED)),
        _with(_stop(status=StopStatus.PENDING, broker_order_id=None)),
    ],
    ids=["no-record", "no-stop", "ended", "pending"],
)
def test_yields_without_a_working_stop(protection: Protection | None) -> None:
    assert stop_yields(protection, YIELDED)


def test_working_stop_yields_once_the_broker_shows_the_target() -> None:
    assert stop_yields(_with(_stop(quantity=40)), YIELDED)
    assert stop_yields(_with(_stop(quantity=30)), YIELDED)
    assert not stop_yields(_with(_stop(quantity=100)), YIELDED)


@pytest.mark.parametrize(
    "protection",
    [None, _with(_stop(status=StopStatus.ENDED)), _with(_stop(quantity=0))],
    ids=["no-record", "ended", "working"],
)
def test_no_yield_while_sells_exceed_the_position(protection: Protection | None) -> None:
    """spec「等待让出时止损单触发，不再提交卖单」：有未覆盖的股数时不管止损单怎样。"""
    assert not stop_yields(protection, StopTarget(0, Decimal(45), uncovered=60))


def test_no_yield_until_the_last_modify_request_shows_up() -> None:
    """spec「改大的请求还没生效时不提交卖单」。"""
    assert not stop_yields(_with(_stop(quantity=40, requested=40)), YIELDED)
    assert not stop_yields(_with(_stop(quantity=30, requested=100)), YIELDED)


def test_unknown_stop_does_not_yield() -> None:
    """spec「止损单状态未知时不提交卖单」。"""
    unknown = _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15), quantity=40)

    assert not stop_yields(_with(unknown), YIELDED)


# 买单和止损单错开：止损单是否已经撤掉


@pytest.mark.parametrize(
    "protection",
    [
        None,
        _with(None),
        _with(_stop(status=StopStatus.ENDED)),
        _with(_stop(status=StopStatus.PENDING, broker_order_id=None)),
    ],
    ids=["no-record", "no-stop", "ended", "pending"],
)
def test_withdrawn_without_a_working_stop(protection: Protection | None) -> None:
    assert stop_withdrawn(protection)


@pytest.mark.parametrize(
    "stop",
    [_stop(), _stop(status=StopStatus.UNKNOWN, unknown_since=_at(15))],
    ids=["working", "unknown"],
)
def test_not_withdrawn_while_the_stop_is_working_or_unknown(stop: StopOrder) -> None:
    """spec「止损单状态未知时不提交买单」；挂着的止损单撤单请求还没生效时也一样。"""
    assert not stop_withdrawn(_with(stop))


# 本地备用止损的触发

UNPROTECTED = Protection("BBB", used=1, unprotected_since=_at(15))


@pytest.mark.parametrize("last_price", ["45", "44"])
def test_backup_stop_fires_when_unprotected_in_session_at_the_stop(last_price: str) -> None:
    """spec「无保护、盘中、价格跌到止损价时触发」。"""
    assert backup_stop_due(UNPROTECTED, Decimal(45), Decimal(last_price), in_session=True)


def test_no_backup_stop_above_the_stop_price() -> None:
    """spec「价格高于止损价时不触发」。"""
    assert not backup_stop_due(UNPROTECTED, Decimal(45), Decimal("45.01"), in_session=True)


def test_no_backup_stop_outside_the_session() -> None:
    """spec「常规交易时段之外不触发」。"""
    assert not backup_stop_due(UNPROTECTED, Decimal(45), Decimal(40), in_session=False)


@pytest.mark.parametrize("protection", [None, _with(_stop())], ids=["no-record", "protected"])
def test_no_backup_stop_while_protected(protection: Protection | None) -> None:
    """spec「有保护时不触发」。"""
    assert not backup_stop_due(protection, Decimal(45), Decimal(40), in_session=True)


def test_no_backup_stop_without_a_stop_price() -> None:
    assert not backup_stop_due(UNPROTECTED, None, Decimal(40), in_session=True)


STAGGERED = Protection("BBB", used=1, stop=_stop(status=StopStatus.ENDED), staggered=True)


@pytest.mark.parametrize("last_price", ["45", "44"])
def test_backup_stop_fires_while_staggered(last_price: str) -> None:
    """spec「错开中、价格跌到止损价时触发」。"""
    assert backup_stop_due(STAGGERED, Decimal(45), Decimal(last_price), in_session=True)


def test_staggered_backup_stop_keeps_the_other_conditions() -> None:
    assert not backup_stop_due(STAGGERED, Decimal(45), Decimal("45.01"), in_session=True)
    assert not backup_stop_due(STAGGERED, Decimal(45), Decimal(40), in_session=False)
    assert not backup_stop_due(STAGGERED, None, Decimal(40), in_session=True)
