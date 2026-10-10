"""紧急退出流程的 spec 场景，经过巡检的 run_round。

场景见 openspec 的 emergency-exit-flow spec，基准数据见 emergency_builders.py。"做一轮"就是在测试
时钟的当前时刻调用一次 run_round，"之后一轮"先把时钟拨后 15 秒；券商的回报由替身安排。
「做某一项时停止」在 test_patrol_thread.py。
"""

import logging
import threading
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.fakes.notifier import FakeNotifier
from tests.fakes.reservations import InMemoryReservationStore
from tests.workflows.builders import World, at
from tests.workflows.emergency_builders import (
    AAA_STOP_1,
    AAA_STOP_2,
    BBB_STOP_1,
    BBB_STOP_2,
    P3_ID,
    P4_ID,
    aaa_close,
    add_aaa_p3,
    add_aaa_p7,
    add_bbb_p6,
    bbb_close,
    buy_ccc_p5,
    emergency_world,
    place,
    quote_at,
    reduce_aaa_p4,
    working_reduce,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradePlan
from tradingagentspro.modules.execution import BrokerOrderStatus, Order, OrderStatus
from tradingagentspro.modules.health import HealthSignals
from tradingagentspro.modules.risk import Reservation
from tradingagentspro.workflows.account_snapshot import account_snapshot

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
INTERVAL = timedelta(seconds=15)
EMERGENCY_LOGGER = "tradingagentspro.workflows.emergency_flow"


def _next_round(world: World) -> None:
    """之后一轮：时钟拨后 15 秒，做一轮。"""
    world.clock.advance(INTERVAL)
    world.patrol.run_round()


def _placed_sells(world: World, plan: TradePlan) -> list[Order]:
    """券商受理的这个方案的提交。"""
    return [o for o in world.broker.placed if o.plan_id == plan.plan_id]


def _emergency_alerts(world: World) -> list[str]:
    """紧急退出推送的告警（不含巡检的无保护告警和收盘对账的结果）。"""
    return [
        a
        for a in world.notifier.alerts
        if a.startswith("单日亏损熔断") or any(w in a for w in ("紧急退出", "紧急清仓", "清仓单"))
    ]


def _make_unprotected(world: World) -> None:
    """BBB 从现在起是无保护仓位，没有挂着的止损单；券商会一直拒绝挂 BBB 的止损单。"""
    world.stops.update(BBB_STOP_1, CANCELLED)
    world.stops.reject_next("place", 1_000)
    world.execution.protect("BBB")
    protection = world.execution.protection("BBB")
    assert protection is not None and protection.unprotected_since is not None
    world.forget_calls()


def _restart(world: World) -> None:
    """进程重启：同一套存储和替身重新建各个用例和巡检，启动对账一次，清掉以前的告警。"""
    world.restart()
    world.execution.reconcile()
    world.forget_calls()
    world.notifier.alerts.clear()


# 巡检里的紧急退出


def test_triggered_and_closed_within_rounds() -> None:
    """spec「盘中一轮里触发并清仓」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))

    world.patrol.run_round()

    order = world.order(aaa_close())
    assert (order.quantity, order.status, order.order_type) == (
        50,
        OrderStatus.PENDING,
        OrderType.MARKET,
    )
    assert AAA_STOP_1 in world.stops.cancel_requests
    assert len(_emergency_alerts(world)) == 1

    world.stops.update(AAA_STOP_1, CANCELLED)
    _next_round(world)

    [sell] = _placed_sells(world, aaa_close())
    assert (sell.symbol, sell.quantity, sell.order_type) == ("AAA", 50, OrderType.MARKET)
    assert len(_emergency_alerts(world)) == 1


def test_each_round_judges_once() -> None:
    """spec「每一轮判定一次」。"""
    world = emergency_world()

    world.patrol.run_round()
    _next_round(world)

    assert world.broker.calls.count(("account", "*")) == 2
    assert world.source.requests == [("AAA", "BBB"), ("AAA", "BBB")]


def test_no_judgement_outside_the_session() -> None:
    """spec「盘外不判定」。"""
    world = emergency_world()
    world.clock.set(at(21))
    world.source.put(quote_at("AAA", "80", "100"))

    world.patrol.run_round()

    assert ("account", "*") not in world.broker.calls
    assert world.source.requests == []
    assert world.orders.get(aaa_close().plan_id) is None
    assert _emergency_alerts(world) == []


def test_drop_before_the_open_triggers_in_the_first_round() -> None:
    """spec「盘前的跌幅在开盘后的第一轮触发」。"""
    world = emergency_world()
    world.clock.set(at(13, 29, 45))

    world.patrol.run_round()

    assert ("account", "*") not in world.broker.calls
    world.source.put(quote_at("AAA", "91", "100"))
    _next_round(world)  # 13:30:00Z
    assert world.order(aaa_close()).quantity == 50


def test_closes_as_usual_in_degraded_mode() -> None:
    """spec「降级模式下照常清仓」。"""
    world = emergency_world()
    now = world.clock.now()
    world.health.check(
        HealthSignals(
            pm_model_available=True,
            critic_model_available=True,
            broker_connected=False,
            last_quote_at=now,
            reserve_budget_exhausted=False,
            scheduler_heartbeat_at=now,
        )
    )
    assert world.health.status.degraded
    world.source.put(quote_at("AAA", "92", "100"))

    world.patrol.run_round()

    assert world.order(aaa_close()).quantity == 50


# 判定的输入


def test_today_buy_measured_from_its_fill_price() -> None:
    """spec「今天有开仓成交的股票从成交均价算」。"""
    world = emergency_world()
    world.clock.set(at(14))
    world.submit(add_aaa_p7(), 10)
    world.stops.update(AAA_STOP_1, CANCELLED)
    world.execution.track(add_aaa_p7().plan_id)
    world.broker.update(str(add_aaa_p7().plan_id), FILLED, 10, "98")
    world.broker.positions["AAA"] = 60
    world.execution.track(add_aaa_p7().plan_id)
    assert world.stops.stop(AAA_STOP_2).quantity == 60
    world.clock.set(at(15))
    world.source.put(quote_at("AAA", "91", "100"))

    world.patrol.run_round()
    assert world.orders.get(aaa_close().plan_id) is None

    world.source.put(quote_at("AAA", "90.16", "100"))
    _next_round(world)
    assert world.order(aaa_close()).quantity == 60


def test_no_stock_drop_without_a_last_price(caplog: pytest.LogCaptureFixture) -> None:
    """spec「开盘后还没有成交的股票不判定个股跌幅」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", None, "100"))

    with caplog.at_level(logging.WARNING, logger=EMERGENCY_LOGGER):
        world.patrol.run_round()

    assert world.orders.get(aaa_close().plan_id) is None
    assert [r for r in caplog.records if r.name == EMERGENCY_LOGGER] == []


def test_only_the_breaker_when_quotes_are_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    """spec「行情不可用时只判定熔断」。"""
    world = emergency_world()
    world.source.failing = True

    with caplog.at_level(logging.WARNING, logger=EMERGENCY_LOGGER):
        world.patrol.run_round()

    assert "行情不可用" in caplog.text and "只判定熔断" in caplog.text
    assert world.orders.get(aaa_close().plan_id) is None
    assert world.orders.get(bbb_close().plan_id) is None

    world.broker.equity = Decimal(9_300)
    _next_round(world)

    assert world.order(aaa_close()).quantity == 50
    assert world.order(bbb_close()).quantity == 100


def test_source_lacking_a_stock(caplog: pytest.LogCaptureFixture) -> None:
    """spec「行情源没有某只股票」。"""
    world = emergency_world()
    world.source.drop("AAA")
    world.source.put(quote_at("BBB", "34", "40"))

    with caplog.at_level(logging.WARNING, logger=EMERGENCY_LOGGER):
        world.patrol.run_round()

    [warning] = [r for r in caplog.records if r.name == EMERGENCY_LOGGER and "行情源" in r.message]
    assert "AAA" in warning.getMessage()
    assert world.order(bbb_close()).quantity == 100
    assert world.orders.get(aaa_close().plan_id) is None


def test_account_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """spec「读券商账户超时」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.broker.fail_next("account")

    with caplog.at_level(logging.WARNING, logger=EMERGENCY_LOGGER):
        world.patrol.run_round()

    assert "读取券商账户失败" in caplog.text
    assert world.source.requests == []
    assert world.orders.get(aaa_close().plan_id) is None

    _next_round(world)
    assert world.order(aaa_close()).quantity == 50


def test_no_quotes_requested_without_positions() -> None:
    """spec「没有持仓时不取行情」。"""
    world = World()

    world.patrol.run_round()

    assert world.broker.calls.count(("account", "*")) == 1
    assert world.source.requests == []
    assert world.notifier.alerts == []


# 要清仓的股票


def test_backup_stop_for_an_unprotected_position() -> None:
    """spec「无保护仓位跌到止损价」。"""
    world = emergency_world()
    _make_unprotected(world)
    world.source.put(quote_at("BBB", "35.01", "40"))

    world.patrol.run_round()
    assert world.orders.get(bbb_close().plan_id) is None

    world.source.put(quote_at("BBB", "35", "40"))
    _next_round(world)

    [sell] = _placed_sells(world, bbb_close())
    assert (sell.quantity, sell.order_type) == (100, OrderType.MARKET)
    [alert] = _emergency_alerts(world)
    assert alert.startswith("紧急退出 BBB：本地备用止损（无保护仓位），最新价 35 不高于止损价 35。")


def test_backup_stop_while_staggered_cancels_the_buy_first() -> None:
    """spec「错开中跌到止损价时先撤买单」。"""
    world = emergency_world()
    p6 = add_bbb_p6()
    world.submit(p6, 20)
    world.patrol.run_round()  # 撤 BBB-STOP-1
    world.stops.update(BBB_STOP_1, CANCELLED)
    _next_round(world)  # 止损单撤掉以后提交 P6
    assert world.order(p6).status is OrderStatus.SUBMITTED
    world.forget_calls()
    world.source.put(quote_at("BBB", "35", "40"))

    _next_round(world)

    assert world.broker.cancel_requests == [str(p6.plan_id)]
    assert world.orders.get(bbb_close().plan_id) is None
    [alert] = _emergency_alerts(world)
    assert "本地备用止损（错开中）" in alert and str(p6.plan_id) in alert
    assert alert.endswith("订单都结束以后市价清仓")

    world.broker.update(str(p6.plan_id), CANCELLED, 0)
    world.source.put(quote_at("BBB", "35.5", "40"))
    _next_round(world)

    stop_2 = world.stops.stop(BBB_STOP_2)
    assert (stop_2.quantity, stop_2.stop_price) == (100, Decimal(35))
    assert world.order(bbb_close()).quantity == 100
    assert BBB_STOP_2 in world.stops.cancel_requests

    world.stops.update(BBB_STOP_2, CANCELLED)
    _next_round(world)

    [sell] = _placed_sells(world, bbb_close())
    assert (sell.quantity, sell.order_type) == (100, OrderType.MARKET)


def test_stock_drop_and_backup_stop_together() -> None:
    """spec「同时触及个股跌幅和本地备用止损」。"""
    world = emergency_world()
    _make_unprotected(world)
    world.source.put(quote_at("BBB", "34", "40"))

    world.patrol.run_round()

    assert [o.plan_id for o in world.broker.placed if o.symbol == "BBB"] == [bbb_close().plan_id]
    [alert] = _emergency_alerts(world)
    assert alert.startswith("紧急退出 BBB：个股跌幅")


def test_rebound_while_cancelling_still_closes() -> None:
    """spec「撤单期间价格回升照样清仓」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    assert world.broker.cancel_requests == [str(p2.plan_id)]

    world.broker.update(str(p2.plan_id), CANCELLED, 0)
    world.source.put(quote_at("AAA", "95", "100"))
    _next_round(world)

    assert world.order(aaa_close()).quantity == 50


def _rejected_close(world: World) -> None:
    """AAA 最新价 92 时做一轮建立清仓单；券商拒绝它；之后一轮跟进以后它已拒绝。"""
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    world.broker.reject_next_place()
    world.stops.update(AAA_STOP_1, CANCELLED)
    _next_round(world)


def test_no_second_close_after_a_rejection() -> None:
    """spec「被拒以后当天不再清仓」。"""
    world = emergency_world()
    _rejected_close(world)

    assert world.order(aaa_close()).status is OrderStatus.REJECTED
    stop_2 = world.stops.stop(AAA_STOP_2)
    assert (stop_2.quantity, stop_2.stop_price) == (50, Decimal(90))
    alerts = len(world.notifier.alerts)
    calls = len(world.broker.placed)

    world.source.put(quote_at("AAA", "90.5", "100"))
    _next_round(world)

    assert len(world.broker.placed) == calls
    assert len(world.notifier.alerts) == alerts


def test_judged_again_on_the_next_trading_day() -> None:
    """spec「下一个交易日重新判定」。"""
    world = emergency_world()
    _rejected_close(world)

    world.clock.set(at(13, 30, 5, day=15))
    world.source.put(quote_at("AAA", "82", "90"))
    world.patrol.run_round()

    assert world.order(aaa_close(at(15, day=15).date())).quantity == 50


# 一只股票的清仓


def test_cancel_working_orders_then_close() -> None:
    """spec「先撤掉挂着的订单，结束以后再清仓」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))

    world.patrol.run_round()

    assert world.broker.cancel_requests == [str(p2.plan_id)]
    assert world.orders.get(aaa_close().plan_id) is None
    [alert] = _emergency_alerts(world)
    assert str(p2.plan_id) in alert and alert.endswith("订单都结束以后市价清仓")

    world.broker.update(str(p2.plan_id), CANCELLED, 0)
    _next_round(world)

    assert world.order(p2).status is OrderStatus.CANCELLED
    assert world.remaining(p2) is None
    order = world.order(aaa_close())
    assert (order.quantity, order.order_type) == (50, OrderType.MARKET)


def test_fill_during_the_cancel() -> None:
    """spec「撤单期间有成交」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()

    world.broker.update(str(p2.plan_id), CANCELLED, 10, "105")
    world.broker.positions["AAA"] = 40
    _next_round(world)

    assert world.order(aaa_close()).quantity == 40


def test_cancel_takes_long() -> None:
    """spec「撤单迟迟不结束」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()

    _next_round(world)
    _next_round(world)

    assert world.orders.get(aaa_close().plan_id) is None
    assert world.broker.cancel_requests == [str(p2.plan_id)]


def test_plans_without_orders_are_voided() -> None:
    """spec「作废这只股票还没下单的方案」。"""
    world = emergency_world()
    world.reserve(add_aaa_p3(), 5)
    world.reserve(reduce_aaa_p4(), 10)
    world.reserve(buy_ccc_p5(), 100)
    world.source.put(quote_at("AAA", "92", "100"))

    world.patrol.run_round()

    assert world.remaining(add_aaa_p3()) is None
    assert world.remaining(reduce_aaa_p4()) is None
    assert world.remaining(buy_ccc_p5()) == 100
    assert world.order(aaa_close()).quantity == 50
    [alert] = _emergency_alerts(world)
    assert str(P3_ID) in alert and str(P4_ID) in alert

    snapshot = account_snapshot(world.execution.account(), {})
    result = world.risk.recheck(
        P3_ID, snapshot, Decimal(99), daily_loss_breaker_tripped=False, degraded=False
    )
    assert [v.rule for v in result.violations] == ["方案已结束"]


def test_nothing_left_to_sell() -> None:
    """spec「没有可卖的股数」。"""
    world = emergency_world()
    p2 = working_reduce(world, quantity=50, limit="93")
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    assert world.broker.cancel_requests == [str(p2.plan_id)]

    world.broker.update(str(p2.plan_id), FILLED, 50, "93")
    world.broker.positions["AAA"] = 0
    _next_round(world)

    assert world.orders.get(aaa_close().plan_id) is None
    assert _emergency_alerts(world)[-1] == "AAA 没有可卖的股数，紧急退出结束"
    alerts = len(world.notifier.alerts)

    _next_round(world)
    assert len(world.notifier.alerts) == alerts


def test_submit_times_out() -> None:
    """spec「提交时券商超时」。"""
    world = emergency_world()
    _make_unprotected(world)
    world.broker.fail_next("place", after_effect=True)
    world.source.put(quote_at("BBB", "35", "40"))

    world.patrol.run_round()

    assert world.order(bbb_close()).status is OrderStatus.UNKNOWN
    world.clock.set(at(15, 0, 30))
    world.patrol.run_round()

    assert world.order(bbb_close()).status is OrderStatus.SUBMITTED
    assert world.broker.order_count(str(bbb_close().plan_id)) == 1


# 单日亏损熔断


def test_breaker_cancels_buys_and_closes_everything() -> None:
    """spec「熔断：撤掉全部买单，清仓全部持仓」。"""
    world = emergency_world()
    p5 = place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal(9_300)

    world.patrol.run_round()

    assert world.broker.cancel_requests == [str(p5.plan_id)]
    assert world.order(aaa_close()).quantity == 50
    assert world.order(bbb_close()).quantity == 100
    assert {AAA_STOP_1, BBB_STOP_1} <= set(world.stops.cancel_requests)
    breaker, aaa, bbb = _emergency_alerts(world)
    assert breaker.startswith("单日亏损熔断")
    assert aaa.startswith("紧急退出 AAA：组合熔断。")
    assert bbb.startswith("紧急退出 BBB：组合熔断。")


def test_rounds_after_the_breaker() -> None:
    """spec「熔断以后的轮次」。"""
    world = emergency_world()
    p5 = place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal(9_300)
    world.patrol.run_round()
    alerts = len(_emergency_alerts(world))

    world.broker.equity = Decimal(9_800)
    _next_round(world)

    assert world.emergency.breaker_tripped()
    assert world.broker.cancel_requests == [str(p5.plan_id)]
    assert len(_placed_sells(world, aaa_close())) == 0
    assert world.broker.order_count(str(aaa_close().plan_id)) == 0
    assert len(_emergency_alerts(world)) == alerts


def test_loss_just_short_of_the_breaker_line() -> None:
    """spec「单日亏损差一点不到熔断线」。"""
    world = emergency_world()
    place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal("9300.01")

    world.patrol.run_round()

    assert not world.emergency.breaker_tripped()
    assert world.broker.cancel_requests == []
    assert world.orders.get(aaa_close().plan_id) is None


def test_breaker_without_positions_cancels_buys() -> None:
    """spec「没有持仓时熔断照样撤买单」。"""
    world = World()
    p5 = place(world, buy_ccc_p5(), 100)
    world.broker.equity = Decimal(9_300)

    world.patrol.run_round()

    assert world.broker.cancel_requests == [str(p5.plan_id)]
    assert world.source.requests == []
    [alert] = _emergency_alerts(world)
    assert alert.startswith("单日亏损熔断")


# 告警


def test_start_alert(caplog: pytest.LogCaptureFixture) -> None:
    """spec「开始紧急退出的告警」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))

    with caplog.at_level(logging.WARNING, logger=EMERGENCY_LOGGER):
        world.patrol.run_round()

    [alert] = _emergency_alerts(world)
    assert alert == (
        "紧急退出 AAA：个股跌幅 8.0%（参考价 100 → 最新价 92），达到紧急阈值 8.0%。"
        "已建立清仓单：市价卖出 50 股"
    )
    assert [r.getMessage() for r in caplog.records if r.name == EMERGENCY_LOGGER] == [alert]


def test_breaker_alert() -> None:
    """spec「熔断告警的内容」。"""
    world = emergency_world()
    world.broker.equity = Decimal(9_300)

    world.patrol.run_round()

    assert _emergency_alerts(world)[0] == (
        "单日亏损熔断：前一交易日收盘时的权益 10,000，当前权益 9,300，单日亏损 700（7.0%），"
        "达到熔断比例 7.0%。清仓全部持仓，撤掉全部买单（0 笔），收盘前不再开仓"
    )


def test_filled_close_alert() -> None:
    """spec「清仓完成的告警」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    world.stops.update(AAA_STOP_1, CANCELLED)
    _next_round(world)
    assert _placed_sells(world, aaa_close())

    world.broker.update(str(aaa_close().plan_id), FILLED, 50, "91.5")
    world.broker.positions["AAA"] = 0
    _next_round(world)

    assert _emergency_alerts(world)[-1] == "AAA 紧急清仓完成：卖出 50 股，成交均价 91.5"
    alerts = len(world.notifier.alerts)
    _next_round(world)
    assert len(world.notifier.alerts) == alerts


def test_rejected_close_alert() -> None:
    """spec「清仓被拒的告警」。"""
    world = emergency_world()

    _rejected_close(world)

    assert _emergency_alerts(world)[-1] == (
        "AAA 紧急清仓单已拒绝：成交 0 / 50 股。今天不再清仓 AAA，剩下的持仓由止损单保护"
    )


def test_no_close_order_by_the_close() -> None:
    """spec「到收盘还没有清仓单」。"""
    world = emergency_world()
    world.clock.set(at(19, 59, 50))
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    assert world.broker.cancel_requests == [str(p2.plan_id)]

    _next_round(world)  # 20:00:05Z

    assert _emergency_alerts(world)[-1] == "AAA 到收盘还没下清仓单（等撤单结束），今天不再清仓 AAA"
    assert world.orders.get(aaa_close().plan_id) is None
    assert world.remaining(aaa_close()) is None


def test_alert_cannot_be_sent(caplog: pytest.LogCaptureFixture) -> None:
    """spec「告警发不出去」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.notifier.fail_next = True

    with caplog.at_level(logging.ERROR):
        world.patrol.run_round()

    assert world.order(aaa_close()).quantity == 50
    assert "告警没有发出去" in caplog.text and "紧急退出 AAA" in caplog.text

    _next_round(world)
    assert len(world.notifier.alerts) == 1


# 重启以后


def test_restart_while_the_close_order_is_working() -> None:
    """spec「清仓单挂着时重启」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()
    world.stops.update(AAA_STOP_1, CANCELLED)
    _next_round(world)
    assert world.order(aaa_close()).status is OrderStatus.SUBMITTED

    _restart(world)
    world.source.put(quote_at("AAA", "91", "100"))
    world.patrol.run_round()

    assert _emergency_alerts(world) == []
    assert world.broker.order_count(str(aaa_close().plan_id)) == 1

    world.broker.update(str(aaa_close().plan_id), FILLED, 50, "90.8")
    world.broker.positions["AAA"] = 0
    _next_round(world)

    assert _emergency_alerts(world) == ["AAA 紧急清仓完成：卖出 50 股，成交均价 90.8"]


def test_restart_while_cancelling_still_triggered() -> None:
    """spec「等撤单时重启，仍然触发」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()

    _restart(world)
    world.source.put(quote_at("AAA", "91", "100"))
    world.patrol.run_round()

    assert world.broker.cancel_requests == [str(p2.plan_id)]
    [alert] = _emergency_alerts(world)
    assert alert.startswith("紧急退出 AAA：")

    world.broker.update(str(p2.plan_id), CANCELLED, 0)
    _next_round(world)

    assert world.order(aaa_close()).quantity == 50


def test_restart_while_cancelling_no_longer_triggered() -> None:
    """spec「等撤单时重启，不再触发」。"""
    world = emergency_world()
    p2 = working_reduce(world)
    world.source.put(quote_at("AAA", "92", "100"))
    world.patrol.run_round()

    world.broker.update(str(p2.plan_id), CANCELLED, 0)
    _restart(world)
    world.source.put(quote_at("AAA", "95", "100"))
    world.patrol.run_round()

    assert world.order(p2).status is OrderStatus.CANCELLED
    assert world.orders.get(aaa_close().plan_id) is None
    assert _emergency_alerts(world) == []


def test_restart_on_a_breaker_day() -> None:
    """spec「熔断当天重启」。"""
    world = emergency_world()
    world.broker.equity = Decimal(9_300)
    world.patrol.run_round()

    _restart(world)
    world.broker.equity = Decimal(9_250)
    world.patrol.run_round()

    assert len([a for a in world.notifier.alerts if a.startswith("单日亏损熔断")]) == 1
    assert world.emergency.breaker_tripped()


# 一轮里的失败


class _FailingReservations(InMemoryReservationStore):
    """读 fail_on 这个方案的预留时出错一次。"""

    def __init__(self) -> None:
        super().__init__()
        self.fail_on: UUID | None = None

    def get(self, plan_id: UUID) -> Reservation | None:
        if plan_id == self.fail_on:
            self.fail_on = None
            raise RuntimeError("状态库出错")
        return super().get(plan_id)


def test_error_for_one_stock_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    """spec「一只股票出错不影响别的股票」。"""
    reservations = _FailingReservations()
    world = emergency_world(reservations=reservations)
    world.broker.equity = Decimal(9_300)
    reservations.fail_on = aaa_close().plan_id

    with caplog.at_level(logging.ERROR):
        world.patrol.run_round()

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "紧急退出 AAA 出错" in error.getMessage() and error.exc_info is not None
    assert world.orders.get(aaa_close().plan_id) is None
    assert world.order(bbb_close()).quantity == 100

    _next_round(world)
    assert world.order(aaa_close()).quantity == 50


# 执行模块和风控的调用串行


def test_waits_while_another_thread_holds_the_lock() -> None:
    """spec「别的调用拿着锁时等待」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    holding, done = threading.Event(), threading.Event()

    def hold_lock() -> None:
        with world.lock:
            holding.set()
            done.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert holding.wait(5)
    worker = threading.Thread(target=world.patrol.run_round)
    worker.start()
    worker.join(0.2)

    assert worker.is_alive()
    assert world.orders.get(aaa_close().plan_id) is None
    assert AAA_STOP_1 not in world.stops.cancel_requests
    done.set()
    worker.join(5)
    holder.join(5)
    assert world.order(aaa_close()).quantity == 50


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


def test_alert_is_pushed_without_the_lock() -> None:
    """spec「推送告警时不拿锁」。"""
    notifier = _BlockingNotifier()
    world = emergency_world(notifier=notifier)
    world.source.put(quote_at("AAA", "92", "100"))
    worker = threading.Thread(target=world.patrol.run_round)
    worker.start()

    assert notifier.entered.wait(5)
    assert world.lock.acquire(blocking=False)
    world.lock.release()
    notifier.release.set()
    worker.join(5)
    assert not worker.is_alive()
