"""紧急退出用例的测试：判定的时机、个股跌幅、组合熔断、熔断状态和清仓方案。

每个测试对应 openspec 的 emergency-exit spec 里的一个场景。
基准数据见 tests/modules/emergency/builders.py。
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.clock import FakeClock
from tests.modules.emergency.builders import (
    AAA,
    BASELINE_DAY,
    BASELINE_NOW,
    BBB,
    EQUITY,
    PREVIOUS_CLOSE_EQUITY,
    emergency_service,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction
from tradingagentspro.modules.emergency import (
    EmergencyExit,
    EmergencyService,
    ExitReason,
    Holding,
    close_plan,
)

STOCK_DROP = ExitReason.STOCK_DROP
BREAKER = ExitReason.DAILY_LOSS_BREAKER


def _check(
    service: EmergencyService,
    holdings: Iterable[Holding] = (AAA, BBB),
    equity: Decimal | int | str = EQUITY,
    previous_close_equity: Decimal | int = PREVIOUS_CLOSE_EQUITY,
) -> tuple[EmergencyExit, ...]:
    """用基准数据判定一次；参数覆盖持仓和权益。"""
    return service.check(holdings, Decimal(equity), Decimal(previous_close_equity))


def _summary(exits: Iterable[EmergencyExit]) -> list[tuple[str, ExitReason]]:
    """判定结果里的股票和原因，按结果的顺序。"""
    return [(e.plan.symbol, e.reason) for e in exits]


def _at(clock: FakeClock, hour: int, minute: int = 0, day: int = 14) -> None:
    clock.set(datetime(2026, 10, day, hour, minute, tzinfo=UTC))


def _aaa(
    last_price: str | int,
    previous_close: Decimal | None = AAA.previous_close,
    entry_price_today: Decimal | None = None,
) -> Holding:
    """AAA 换成这个最新价；可以同时换掉前一交易日收盘价、加上今天开仓成交的均价。"""
    return replace(
        AAA,
        last_price=Decimal(last_price),
        previous_close=previous_close,
        entry_price_today=entry_price_today,
    )


# 判定的时机和输入


def test_no_trigger_gives_an_empty_result() -> None:
    """spec「没有触发时结果为空」。"""
    assert _check(emergency_service()) == ()


def test_premarket_is_not_judged_but_the_first_price_after_the_open_is() -> None:
    """spec「盘前不判定，开盘后的第一个价格就判定」。"""
    clock = FakeClock(datetime(2026, 10, 14, 12, tzinfo=UTC))
    service = emergency_service(clock)

    assert _check(service, [_aaa(80), BBB]) == ()

    _at(clock, 13, 30)
    exits = _check(service, [_aaa(80), BBB])

    assert _summary(exits) == [("AAA", STOCK_DROP)]
    assert exits[0].plan == close_plan("AAA", "科技", BASELINE_DAY)


def test_close_and_after_hours_are_not_judged() -> None:
    """spec「收盘时刻和盘后不判定」。"""
    for hour in (20, 21):
        clock = FakeClock(datetime(2026, 10, 14, hour, tzinfo=UTC))
        service = emergency_service(clock)

        assert _check(service, [_aaa(80), BBB], equity=90_000) == ()
        assert not service.breaker_tripped()


def test_no_holdings() -> None:
    """spec「没有持仓」。"""
    assert _check(emergency_service(), []) == ()


# 个股跌幅


def test_drop_equal_to_the_threshold_triggers() -> None:
    """spec「跌幅等于紧急阈值时触发」。"""
    exits = _check(emergency_service(), [_aaa(92), BBB])

    assert _summary(exits) == [("AAA", STOCK_DROP)]
    assert exits[0].plan == close_plan("AAA", "科技", BASELINE_DAY)


def test_drop_below_the_threshold_does_not_trigger() -> None:
    """spec「跌幅小于紧急阈值时不触发」。"""
    assert _check(emergency_service(), [_aaa("92.01"), BBB]) == ()


def test_each_stock_uses_its_own_threshold() -> None:
    """spec「每只股票用自己的紧急阈值」。"""
    bbb = replace(BBB, last_price=Decimal("45.5"))

    assert _summary(_check(emergency_service(), [_aaa(91), bbb])) == [("AAA", STOCK_DROP)]


def test_drop_is_measured_from_todays_fill_price() -> None:
    """spec「今天有开仓成交时从成交均价算」。"""
    service = emergency_service()
    fill = Decimal(92)

    assert _check(service, [_aaa(85, entry_price_today=fill), BBB]) == ()
    exits = _check(service, [_aaa("84.64", entry_price_today=fill), BBB])
    assert _summary(exits) == [("AAA", STOCK_DROP)]


def test_fill_price_above_the_previous_close_is_still_the_reference() -> None:
    """spec「今天开仓成交价高于前一交易日收盘价时同样从成交均价算」。"""
    aaa = _aaa("96.6", entry_price_today=Decimal(105))

    assert _summary(_check(emergency_service(), [aaa, BBB])) == [("AAA", STOCK_DROP)]


def test_no_quotes_no_stock_drop() -> None:
    """spec「没有行情时不判定个股跌幅」。"""
    service = emergency_service()

    assert _check(service, [replace(AAA, last_price=None), BBB]) == ()
    assert _check(service, [_aaa(80, previous_close=None), BBB]) == ()
    aaa = _aaa(84, previous_close=None, entry_price_today=Decimal(92))
    assert _summary(_check(service, [aaa, BBB])) == [("AAA", STOCK_DROP)]


# 组合熔断


def test_daily_loss_equal_to_the_breaker_line_trips() -> None:
    """spec「单日亏损等于熔断线时触发」。"""
    service = emergency_service()

    exits = _check(service, equity=97_000)

    assert _summary(exits) == [("AAA", BREAKER), ("BBB", BREAKER)]
    assert [e.plan for e in exits] == [
        close_plan("AAA", "科技", BASELINE_DAY),
        close_plan("BBB", "医疗", BASELINE_DAY),
    ]
    assert service.breaker_tripped()


def test_daily_loss_below_the_breaker_line_does_not_trip() -> None:
    """spec「单日亏损小于熔断线时不触发」：差一分钱，以及盈利。"""
    for equity in ("97000.01", 105_000):
        service = emergency_service()

        assert _check(service, equity=equity) == ()
        assert not service.breaker_tripped()


def test_a_stock_that_also_dropped_appears_once() -> None:
    """spec「同时触及个股跌幅的股票只出现一次」。"""
    exits = _check(emergency_service(), [_aaa(90), BBB], equity=97_000)

    assert _summary(exits) == [("AAA", BREAKER), ("BBB", BREAKER)]


def test_holdings_without_quotes_are_closed_on_the_breaker() -> None:
    """spec「熔断时没有行情的持仓照样清仓」。"""
    bbb = replace(BBB, previous_close=None, last_price=None)

    exits = _check(emergency_service(), [AAA, bbb], equity=97_000)

    assert _summary(exits) == [("AAA", BREAKER), ("BBB", BREAKER)]


def test_breaker_trips_without_holdings() -> None:
    """spec「没有持仓时熔断照样触发」。"""
    service = emergency_service()

    assert _check(service, [], equity=97_000) == ()
    assert service.breaker_tripped()


# 熔断状态


def test_breaker_holds_until_the_close() -> None:
    """spec「触发以后到收盘一直保持」。"""
    clock = FakeClock(BASELINE_NOW)
    service = emergency_service(clock)
    _check(service, equity=97_000)

    _at(clock, 16)
    exits = _check(service, [BBB], equity=99_500)

    assert _summary(exits) == [("BBB", BREAKER)]
    assert service.breaker_tripped()
    _at(clock, 19, 59)
    assert service.breaker_tripped()


def test_breaker_clears_after_the_close() -> None:
    """spec「收盘后解除」。"""
    clock = FakeClock(BASELINE_NOW)
    service = emergency_service(clock)
    _check(service, equity=97_000)

    _at(clock, 20)
    assert not service.breaker_tripped()
    _at(clock, 12, day=15)
    assert not service.breaker_tripped()

    _at(clock, 15, day=15)
    exits = _check(service, equity=96_500, previous_close_equity=97_000)

    assert exits == ()
    assert not service.breaker_tripped()


def test_breaker_is_not_tripped_before_any_check() -> None:
    """spec「还没有判定过」。"""
    assert not emergency_service().breaker_tripped()


# 代码触发的清仓方案


def test_close_plan_contents() -> None:
    """spec「清仓方案的内容」。"""
    (exit_,) = _check(emergency_service(), [_aaa(92), BBB])

    plan = exit_.plan
    assert (plan.symbol, plan.sector, plan.action) == ("AAA", "科技", TradeAction.CLOSE)
    assert plan.quantity is None
    assert plan.order_type is OrderType.MARKET
    assert plan.limit_price is None
    assert plan.stop_price is None
    assert plan.emergency_threshold is None
    assert plan.expires_at is None
    assert plan.max_price_deviation is None


def test_repeated_checks_on_the_same_day_give_the_same_plan() -> None:
    """spec「同一交易日重复判定得到同一个方案」。"""
    clock = FakeClock(BASELINE_NOW)
    service = emergency_service(clock)
    (first,) = _check(service, [_aaa(92), BBB])

    _at(clock, 15, 5)
    (second,) = _check(service, [_aaa(91), BBB])

    assert second.plan == first.plan
    assert second.plan.plan_id == first.plan.plan_id


def test_plan_ids_differ_by_stock_or_day() -> None:
    """spec「股票或交易日不同时方案 ID 不同」：经由两天的判定生成三个方案。"""
    clock = FakeClock(BASELINE_NOW)
    service = emergency_service(clock)
    bbb = replace(BBB, last_price=Decimal(45))
    aaa_today, bbb_today = _check(service, [_aaa(92), bbb])

    _at(clock, 15, day=15)
    (aaa_next_day,) = _check(service, [_aaa(92), BBB])

    ids = {e.plan.plan_id for e in (aaa_today, aaa_next_day, bbb_today)}
    assert len(ids) == 3


def test_backup_stop_gets_the_same_plan_as_the_emergency_exit() -> None:
    """spec「本地备用止损和紧急退出得到同一个方案」。"""
    (exit_,) = _check(emergency_service(), [_aaa(92), BBB])

    assert close_plan("AAA", "科技", BASELINE_DAY) == exit_.plan


# 紧急退出配置


def test_changed_breaker_ratio() -> None:
    """spec「改熔断比例」。"""
    service = emergency_service(daily_loss_breaker_ratio=Decimal("0.05"))

    assert _check(service, equity=97_000) == ()
    assert not service.breaker_tripped()
    assert _summary(_check(service, equity=95_000)) == [("AAA", BREAKER), ("BBB", BREAKER)]
