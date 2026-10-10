"""紧急退出流程：判定、清仓、熔断、告警，以及巡检每一轮里的紧急退出。

见 emergency-exit-flow spec 和 add-emergency-exit-flow 的 design。基准数据见 emergency_builders.py。
第一部分直接调用 EmergencyFlow 的方法；spec 场景的测试经过巡检的 run_round。
"""

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.workflows.builders import World, at
from tests.workflows.emergency_builders import (
    AAA_STOP_1,
    BBB_STOP_1,
    EQUITY,
    NOW,
    aaa_close,
    add_aaa_p7,
    add_bbb_p6,
    bbb_close,
    emergency_world,
    quote_at,
)
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.emergency import Holding
from tradingagentspro.modules.execution import BrokerOrderStatus
from tradingagentspro.workflows.account_snapshot import account_snapshot
from tradingagentspro.workflows.emergency_flow import ExitCause, Trigger, holdings_of

WORKING = BrokerOrderStatus.WORKING
FILLED = BrokerOrderStatus.FILLED
CANCELLED = BrokerOrderStatus.CANCELLED
REJECTED = BrokerOrderStatus.REJECTED


def _trigger(world: World, plan: TradePlan) -> Trigger:
    """这次运行里这个清仓方案第一次触发的原因；一定已经触发过。"""
    trigger = world.flow.trigger_of(plan)
    assert trigger is not None
    return trigger


def _make_unprotected(world: World, symbol: str = "BBB", stop: str = BBB_STOP_1) -> None:
    """券商撤掉这只股票的止损单，之后一直拒绝补挂：对齐以后它是无保护仓位。"""
    world.stops.update(stop, CANCELLED)
    world.stops.reject_next("place", 1_000)
    world.execution.protect(symbol)
    protection = world.execution.protection(symbol)
    assert protection is not None and protection.unprotected_since is not None
    world.forget_calls()


def _stagger_bbb(world: World) -> None:
    """P6 加仓 BBB 的订单已提交、没有成交；BBB-STOP-1 已经撤销，BBB 错开中。"""
    world.submit(add_bbb_p6(), 20)
    world.stops.update(BBB_STOP_1, CANCELLED)
    world.execution.track(add_bbb_p6().plan_id)
    protection = world.execution.protection("BBB")
    assert protection is not None and protection.staggered
    world.forget_calls()


# holdings_of：判定用的持仓（design D3）


def test_holdings_of_takes_prices_from_quotes() -> None:
    world = emergency_world()
    account = world.execution.account()

    holdings = holdings_of(account, {"AAA": quote_at("AAA", "99", "100")})

    assert holdings == (
        Holding("AAA", "科技", Decimal("0.08"), Decimal(100), Decimal(99), None),
        Holding("BBB", "医疗", Decimal("0.15"), None, None, None),
    )


def test_holdings_of_without_last_price() -> None:
    world = emergency_world()
    account = world.execution.account()

    (aaa, _) = holdings_of(account, {"AAA": quote_at("AAA", None, "100")})

    assert (aaa.previous_close, aaa.last_price) == (Decimal(100), None)


# judge：判定的时机和输入（spec「巡检里的紧急退出」「判定的输入」）


def test_judge_does_nothing_outside_the_session() -> None:
    """spec「盘外不判定」：不读账户，不取行情。"""
    world = emergency_world()
    world.clock.set(at(21))
    world.source.put(quote_at("AAA", "80", "100"))

    assert world.flow.judge() is None
    assert ("account", "*") not in world.broker.calls
    assert world.source.requests == []


def test_judge_skips_the_round_when_the_account_times_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spec「读券商账户超时」：写 WARNING，不取行情，这一轮不判定。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.broker.fail_next("account")

    with caplog.at_level(logging.WARNING):
        assert world.flow.judge() is None

    assert "读取券商账户失败" in caplog.text
    assert world.source.requests == []
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == (aaa_close(),)


def test_judge_requests_quotes_of_all_holdings_once() -> None:
    """spec「每一轮判定一次」：读一次账户，一次取 AAA 和 BBB 的行情。"""
    world = emergency_world()

    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == () and judgement.breaker is None
    assert world.broker.calls.count(("account", "*")) == 1
    assert world.source.requests == [("AAA", "BBB")]


def test_judge_without_positions_requests_no_quotes() -> None:
    """spec「没有持仓时不取行情」。"""
    world = World()

    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == ()
    assert world.broker.calls == [("account", "*")]
    assert world.source.requests == []


def test_judge_only_judges_the_breaker_when_quotes_are_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """spec「行情不可用时只判定熔断」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "80", "100"))
    world.source.failing = True

    with caplog.at_level(logging.WARNING):
        judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == () and judgement.breaker is None
    assert "行情不可用" in caplog.text and "只判定熔断" in caplog.text

    world.broker.equity = Decimal(9_300)
    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == (aaa_close(), bbb_close())
    assert judgement.breaker is not None


def test_judge_warns_about_symbols_the_source_lacks(caplog: pytest.LogCaptureFixture) -> None:
    """spec「行情源没有某只股票」。"""
    world = emergency_world()
    world.source.drop("AAA")
    world.source.put(quote_at("BBB", "34", "40"))

    with caplog.at_level(logging.WARNING):
        judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == (bbb_close(),)
    assert "AAA" in caplog.text and "BBB" not in caplog.text


def test_judge_without_last_price_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """spec「开盘后还没有成交的股票不判定个股跌幅」。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", None, "100"))

    with caplog.at_level(logging.WARNING):
        judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == ()
    assert caplog.records == []


def test_judge_measures_today_buys_from_their_fill_price() -> None:
    """spec「今天有开仓成交的股票从成交均价算」：P7 今天 14:00Z 成交，均价 98。"""
    world = emergency_world()
    world.clock.set(at(14))
    world.submit(add_aaa_p7(), 10)
    world.stops.update(AAA_STOP_1, CANCELLED)
    world.execution.track(add_aaa_p7().plan_id)  # 错开：止损单撤掉以后才提交买单
    world.broker.update(str(add_aaa_p7().plan_id), FILLED, 10, "98")
    world.broker.positions["AAA"] = 60
    world.execution.track(add_aaa_p7().plan_id)
    world.clock.set(NOW)

    world.source.put(quote_at("AAA", "91", "100"))
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == ()

    world.source.put(quote_at("AAA", "90.16", "100"))
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == (aaa_close(),)


def test_judge_backup_stop_for_unprotected_positions() -> None:
    """spec「无保护仓位跌到止损价」的判定部分。"""
    world = emergency_world()
    _make_unprotected(world)

    world.source.put(quote_at("BBB", "35.01", "40"))
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == ()

    world.source.put(quote_at("BBB", "35", "40"))
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == (bbb_close(),)
    assert _trigger(world, bbb_close()).cause is ExitCause.BACKUP_STOP
    assert "无保护仓位" in _trigger(world, bbb_close()).detail


def test_judge_backup_stop_while_staggered() -> None:
    """spec「错开中跌到止损价时先撤买单」的判定部分。"""
    world = emergency_world()
    _stagger_bbb(world)
    world.source.put(quote_at("BBB", "35", "40"))

    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == (bbb_close(),)
    trigger = _trigger(world, bbb_close())
    assert trigger.cause is ExitCause.BACKUP_STOP
    assert "错开中" in trigger.detail and "35" in trigger.detail


def test_judge_backup_stop_needs_a_last_price() -> None:
    world = emergency_world()
    _make_unprotected(world)
    world.source.put(quote_at("BBB", None, "40"))

    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == ()


def test_judge_prefers_the_stock_drop_over_the_backup_stop() -> None:
    """spec「同时触及个股跌幅和本地备用止损」的判定部分。"""
    world = emergency_world()
    _make_unprotected(world)
    world.source.put(quote_at("BBB", "34", "40"))

    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == (bbb_close(),)
    assert _trigger(world, bbb_close()).cause is ExitCause.STOCK_DROP


def test_judge_prefers_the_breaker_over_the_stock_drop() -> None:
    world = emergency_world()
    world.broker.equity = Decimal(9_300)
    world.source.put(quote_at("AAA", "92", "100"))

    judgement = world.flow.judge()

    assert judgement is not None and judgement.breaker is not None
    assert (judgement.breaker.equity, judgement.breaker.previous_close_equity) == (
        Decimal(9_300),
        EQUITY,
    )
    assert _trigger(world, aaa_close()).cause is ExitCause.BREAKER


def test_judge_keeps_stocks_triggered_today() -> None:
    """当天做到底（design D2）：触发以后价格回升，仍在要清仓的股票里。"""
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.flow.judge()

    world.source.put(quote_at("AAA", "95", "100"))
    world.clock.set(NOW + timedelta(seconds=15))
    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == (aaa_close(),)


def test_judge_skips_stocks_whose_close_plan_has_an_order() -> None:
    """当天的清仓方案已经有订单（不论状态）的股票不在要清仓的股票里。"""
    world = emergency_world()
    world.submit(aaa_close(), 50)
    world.source.put(quote_at("AAA", "92", "100"))

    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == ()

    world.stops.update(AAA_STOP_1, CANCELLED)  # 止损单撤掉以后才提交清仓卖单
    world.broker.reject_next_place()
    world.execution.track(aaa_close().plan_id)
    assert world.order(aaa_close()).status.is_terminal
    judgement = world.flow.judge()
    assert judgement is not None and judgement.exits == ()


def test_judge_forgets_records_of_earlier_days() -> None:
    world = emergency_world()
    world.source.put(quote_at("AAA", "92", "100"))
    world.flow.judge()

    world.clock.set(at(15, day=15))
    world.source.put(quote_at("AAA", "92", "92"))
    judgement = world.flow.judge()

    assert judgement is not None and judgement.exits == ()


def test_judge_gives_the_risk_snapshot_of_the_same_reads() -> None:
    world = emergency_world()

    judgement = world.flow.judge()

    assert judgement is not None
    account = world.execution.account()
    quotes = {"AAA": quote_at("AAA", "99", "100"), "BBB": quote_at("BBB", "39.5", "40")}
    assert judgement.snapshot == account_snapshot(account, quotes)
    assert judgement.day == NOW.date()
