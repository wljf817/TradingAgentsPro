"""紧急退出流程的第二部分：一只股票的清仓、熔断撤买单、报告结果、告警文字和锁。

直接调用 EmergencyFlow 的方法，巡检的第 1、2 步由测试手动做（见 _sync）。见 add-emergency-exit-flow
的 design D5 到 D8；经过巡检的 spec 场景见 test_emergency_rounds.py。
"""

import logging
import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.fakes.notifier import FakeNotifier
from tests.workflows.builders import World, at
from tests.workflows.emergency_builders import (
    AAA_STOP_1,
    BBB_STOP_1,
    P3_ID,
    P4_ID,
    aaa_close,
    add_aaa_p3,
    bbb_close,
    buy_ccc_p5,
    emergency_world,
    place,
    quote_at,
    reduce_aaa_p4,
    working_reduce,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.modules.execution import BrokerOrderStatus, OrderStatus
from tradingagentspro.workflows.account_snapshot import account_snapshot
from tradingagentspro.workflows.emergency_flow import Judgement

FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED


def _judge(world: World) -> Judgement:
    judgement = world.flow.judge()
    assert judgement is not None
    return judgement


def _advance_all(world: World) -> Judgement:
    """判定一次，熔断时撤买单，再逐只推进：巡检的紧急退出这一步里除了报告结果的部分。"""
    judgement = _judge(world)
    if judgement.breaker is not None:
        world.flow.cancel_buys(judgement)
    for plan in judgement.exits:
        world.flow.advance(plan, judgement)
    return judgement


def _sync(world: World, plan: TradePlan) -> None:
    """跟进这个方案的订单并同步预留：巡检第 1、2 步对它做的事。"""
    world.execution.track(plan.plan_id)
    order = world.order(plan)
    world.risk.record_fill(plan.plan_id, order.filled)
    if order.status.is_terminal:
        world.risk.release(plan.plan_id)


def _make_unprotected(world: World) -> None:
    """券商撤掉 BBB-STOP-1，之后一直拒绝补挂：对齐以后 BBB 是无保护仓位，没有挂着的止损单。"""
    world.stops.update(BBB_STOP_1, CANCELLED)
    world.stops.reject_next("place", 1_000)
    world.execution.protect("BBB")
    world.forget_calls()


# 一只股票的清仓（spec「一只股票的清仓」，design D5、D6）


def test_advance_submits_the_close_plan(caplog: pytest.LogCaptureFixture) -> None:
    """spec「盘中一轮里触发并清仓」「开始紧急退出的告警」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))

    with caplog.at_level(logging.WARNING):
        _advance_all(world)

    order = world.order(aaa_close())
    assert (order.quantity, order.status) == (50, OrderStatus.PENDING)
    assert order.order_type is OrderType.MARKET
    assert world.stops.cancel_requests == [AAA_STOP_1]  # 卖单等止损单撤掉
    assert world.remaining(aaa_close()) == 50
    (text,) = world.notifier.alerts
    assert text.startswith(
        "紧急退出 AAA：个股跌幅 8.0%（参考价 100 → 最新价 92），达到紧急阈值 8.0%。"
    )
    assert "已建立清仓单：市价卖出 50 股" in text
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [text]


def test_advance_cancels_working_orders_first() -> None:
    """spec「先撤掉挂着的订单，结束以后再清仓」的第一轮。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))

    _advance_all(world)

    assert world.broker.cancel_requests == [str(p2.plan_id)]
    assert world.orders.get(aaa_close().plan_id) is None
    (text,) = world.notifier.alerts
    assert f"撤掉挂着的订单：卖出 20 股（方案 {p2.plan_id}）" in text
    assert "订单都结束以后市价清仓" in text


def test_advance_does_not_cancel_twice(caplog: pytest.LogCaptureFixture) -> None:
    """spec「撤单迟迟不结束」：都有撤单要求的订单不再撤，每一轮写一条等订单结束的 INFO。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)
    world.clock.advance(timedelta(seconds=15))

    with caplog.at_level(logging.INFO):
        _advance_all(world)

    assert world.broker.cancel_requests == [str(p2.plan_id)]
    assert len(world.notifier.alerts) == 1
    assert "等 1 笔挂着的订单结束" in caplog.text


def test_advance_closes_once_the_orders_end() -> None:
    """spec「撤单期间有成交」「撤单期间价格回升照样清仓」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)

    world.broker.update(str(p2.plan_id), CANCELLED, 10, "105")
    world.broker.positions["AAA"] = 40
    _sync(world, p2)
    world.source.put(quote_at("AAA", "95", "100"))
    world.clock.advance(timedelta(seconds=15))
    _advance_all(world)

    assert world.order(aaa_close()).quantity == 40
    assert len(world.notifier.alerts) == 1  # 建立清仓单只写日志


def test_advance_voids_plans_without_orders() -> None:
    """spec「作废这只股票还没下单的方案」。"""
    world = emergency_world()
    world.reserve(add_aaa_p3(), 5)
    world.reserve(reduce_aaa_p4(), 10)
    world.reserve(buy_ccc_p5(), 100)
    world.source.put(quote_at("AAA", "92", "100"))

    _advance_all(world)

    assert world.remaining(add_aaa_p3()) is None
    assert world.remaining(reduce_aaa_p4()) is None
    assert world.remaining(buy_ccc_p5()) == 100
    assert world.order(aaa_close()).quantity == 50  # P4 的卖出预留已经释放
    (text,) = world.notifier.alerts
    assert f"作废还没下单的方案：加仓 AAA（方案 {P3_ID}）、减仓 AAA（方案 {P4_ID}）" in text

    snapshot = account_snapshot(world.execution.account(), {})
    result = world.risk.recheck(
        P3_ID, snapshot, Decimal(99), daily_loss_breaker_tripped=False, degraded=False
    )
    assert [v.rule for v in result.violations] == ["方案已结束"]


def test_advance_keeps_the_close_plan_reservation() -> None:
    """上一轮检查以后提交出错，清仓方案已经有预留：不当作还没下单的方案作废，照常提交。"""
    world = emergency_world()
    world.reserve(aaa_close(), 50)
    world.source.put(quote_at("AAA", "92", "100"))

    _advance_all(world)

    assert world.order(aaa_close()).quantity == 50
    assert "作废" not in world.notifier.alerts[0]


def test_advance_ends_when_nothing_is_left_to_sell() -> None:
    """spec「没有可卖的股数」。"""
    world = emergency_world()
    p2 = working_reduce(world, quantity=50, limit="93")
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)

    world.broker.update(str(p2.plan_id), FILLED, 50, "93")
    world.broker.positions["AAA"] = 0
    _sync(world, p2)
    _advance_all(world)

    assert world.orders.get(aaa_close().plan_id) is None
    assert world.notifier.alerts[-1] == "AAA 没有可卖的股数，紧急退出结束"
    assert _judge(world).exits == ()
    assert len(world.notifier.alerts) == 2


def test_advance_hands_an_unknown_order_to_the_patrol() -> None:
    """spec「提交时券商超时」：没有止损单要撤，立即提交，超时记为状态未知。"""
    world = emergency_world()
    _make_unprotected(world)
    world.source.put(quote_at("BBB", "35", "40"))
    world.broker.fail_next("place", after_effect=True)

    _advance_all(world)

    assert world.order(bbb_close()).status is OrderStatus.UNKNOWN
    assert world.broker.order_count(str(bbb_close().plan_id)) == 1


def test_advance_lets_unexpected_errors_through() -> None:
    """出错由巡检的一项接住（spec「一只股票出错不影响别的股票」），之后再推进照常。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    judgement = _judge(world)
    world.broker.raise_next("position", RuntimeError("意外的错"))

    with pytest.raises(RuntimeError):
        world.flow.advance(aaa_close(), judgement)

    world.flow.advance(aaa_close(), _judge(world))
    assert world.order(aaa_close()).quantity == 50
    assert len(world.notifier.alerts) == 1


# 熔断（spec「单日亏损熔断」，design D7）


def test_cancel_buys_when_the_breaker_trips() -> None:
    """spec「熔断：撤掉全部买单，清仓全部持仓」「熔断告警的内容」。"""
    world = emergency_world()
    p5 = place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal(9_300)

    _advance_all(world)

    assert world.broker.cancel_requests == [str(p5.plan_id)]
    assert world.notifier.alerts[0] == (
        "单日亏损熔断：前一交易日收盘时的权益 10,000，当前权益 9,300，单日亏损 700（7.0%），"
        "达到熔断比例 7.0%。清仓全部持仓，撤掉全部买单（1 笔），收盘前不再开仓"
    )
    assert world.notifier.alerts[1].startswith("紧急退出 AAA：组合熔断。")
    assert world.notifier.alerts[2].startswith("紧急退出 BBB：组合熔断。")
    assert world.order(aaa_close()).quantity == 50
    assert world.order(bbb_close()).quantity == 100


def test_cancel_buys_alerts_once_per_day() -> None:
    """spec「熔断以后的轮次」：不再撤已经撤过的买单，不再推送熔断告警。"""
    world = emergency_world()
    p5 = place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal(9_300)
    _advance_all(world)
    world.broker.equity = Decimal(9_800)
    world.clock.advance(timedelta(seconds=15))

    judgement = _advance_all(world)

    assert judgement.breaker is not None
    assert world.broker.cancel_requests == [str(p5.plan_id)]
    assert len(world.notifier.alerts) == 3


# 报告结果（spec「告警」，design D8）


def test_report_a_filled_close() -> None:
    """spec「清仓完成的告警」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)
    world.stops.update(AAA_STOP_1, CANCELLED)
    world.execution.track(aaa_close().plan_id)
    world.broker.update(str(aaa_close().plan_id), FILLED, 50, "91.5")
    world.broker.positions["AAA"] = 0
    world.execution.track(aaa_close().plan_id)

    world.flow.report()
    world.flow.report()

    assert world.notifier.alerts[1:] == ["AAA 紧急清仓完成：卖出 50 股，成交均价 91.5"]


def test_report_a_rejected_close() -> None:
    """spec「清仓被拒的告警」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)
    world.stops.update(AAA_STOP_1, CANCELLED)
    world.broker.reject_next_place()
    world.execution.track(aaa_close().plan_id)

    world.flow.report()

    assert world.notifier.alerts[1:] == [
        "AAA 紧急清仓单已拒绝：成交 0 / 50 股。今天不再清仓 AAA，剩下的持仓由止损单保护"
    ]


def test_report_nothing_while_waiting_in_session() -> None:
    world = emergency_world()
    working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)

    world.flow.report()

    assert len(world.notifier.alerts) == 1


def test_report_a_close_missed_at_the_close() -> None:
    """spec「到收盘还没有清仓单」：告警，释放清仓方案剩下的预留。"""
    world = emergency_world()
    world.clock.set(at(19, 59, 50))
    working_reduce(world)
    world.reserve(aaa_close(), 50)  # 比如上一轮检查以后提交出错
    world.source.put(quote_at("AAA", "92", "100"))
    _advance_all(world)

    world.clock.set(at(20, 0, 5))
    world.flow.report()
    world.flow.report()

    assert world.notifier.alerts[1:] == ["AAA 到收盘还没下清仓单（等撤单结束），今天不再清仓 AAA"]
    assert world.remaining(aaa_close()) is None


def test_failed_alerts_are_logged_and_not_resent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        _advance_all(world)

    assert world.order(aaa_close()).quantity == 50
    assert "告警没有发出去" in caplog.text and "紧急退出 AAA" in caplog.text
    world.clock.advance(timedelta(seconds=15))
    _advance_all(world)
    world.flow.report()
    assert len(world.notifier.alerts) == 1


# 锁（spec「执行模块和风控的调用串行」，design D5）


class _BlockingNotifier(FakeNotifier):
    """推送告警时一直等到测试放行。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def alert(self, text: str) -> None:
        self.entered.set()
        assert self.release.wait(5)
        super().alert(text)


def test_advance_waits_for_the_lock() -> None:
    """spec「别的调用拿着锁时等待」的紧急退出部分。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    judgement = _judge(world)
    holding, done = threading.Event(), threading.Event()

    def hold_lock() -> None:
        with world.lock:
            holding.set()
            done.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert holding.wait(5)
    worker = threading.Thread(target=world.flow.advance, args=(aaa_close(), judgement))
    worker.start()
    worker.join(0.2)

    assert worker.is_alive()
    assert world.orders.get(aaa_close().plan_id) is None
    done.set()
    worker.join(5)
    holder.join(5)
    assert world.order(aaa_close()).quantity == 50


def test_alerts_are_pushed_without_the_lock() -> None:
    """spec「推送告警时不拿锁」的紧急退出部分。"""
    notifier = _BlockingNotifier()
    world = emergency_world(notifier=notifier)
    world.source.put(quote_at("AAA", "92", "100"))
    judgement = _judge(world)
    worker = threading.Thread(target=world.flow.advance, args=(aaa_close(), judgement))
    worker.start()

    assert notifier.entered.wait(5)
    assert world.lock.acquire(blocking=False)
    world.lock.release()
    notifier.release.set()
    worker.join(5)
    assert not worker.is_alive()
