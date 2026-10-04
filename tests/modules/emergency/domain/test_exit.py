"""紧急退出纯逻辑的测试，直接构造持仓。

场景见 openspec 的 emergency-exit spec「个股跌幅」「组合熔断」「代码触发的清仓方案」，
以及「判定的时机和输入」里的判定结果表。
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from tests.fakes.calendar import SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY
from tests.modules.emergency.builders import (
    AAA,
    BASELINE_DAY,
    BBB,
    NEXT_DAY,
    PREVIOUS_CLOSE_EQUITY,
)
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction
from tradingagentspro.modules.emergency import (
    EmergencyExit,
    ExitReason,
    Holding,
    close_plan,
    daily_loss_tripped,
    drop_triggered,
    emergency_exits,
    reference_price,
)

RATIO = Decimal("0.03")


# 参考价


def test_reference_is_the_previous_close_without_an_opening_fill_today() -> None:
    assert reference_price(AAA) == Decimal(100)


def test_reference_is_the_fill_price_with_an_opening_fill_today() -> None:
    assert reference_price(replace(AAA, entry_price_today=Decimal(92))) == Decimal(92)
    assert reference_price(replace(AAA, entry_price_today=Decimal(105))) == Decimal(105)


def test_fill_price_is_the_reference_even_without_a_previous_close() -> None:
    holding = replace(AAA, previous_close=None, entry_price_today=Decimal(92))

    assert reference_price(holding) == Decimal(92)


def test_no_reference_without_a_previous_close_or_an_opening_fill() -> None:
    assert reference_price(replace(AAA, previous_close=None)) is None


# 个股跌幅


def test_drop_equal_to_the_threshold_triggers() -> None:
    """跌幅 0.08 等于 AAA 的紧急阈值。"""
    assert drop_triggered(replace(AAA, last_price=Decimal(92)))


def test_drop_one_cent_short_of_the_threshold_does_not_trigger() -> None:
    """跌幅 0.0799。"""
    assert not drop_triggered(replace(AAA, last_price=Decimal("92.01")))


def test_price_above_the_reference_does_not_trigger() -> None:
    assert not drop_triggered(replace(AAA, last_price=Decimal(101)))


def test_missing_last_price_does_not_trigger() -> None:
    assert not drop_triggered(replace(AAA, last_price=None))


def test_missing_reference_does_not_trigger() -> None:
    assert not drop_triggered(replace(AAA, previous_close=None, last_price=Decimal(80)))


def test_drop_from_a_fill_price_below_the_previous_close() -> None:
    """今天开仓成交 92：从成交均价算，85 的跌幅约 0.076，84.64 的跌幅是 0.08。"""
    bought = replace(AAA, entry_price_today=Decimal(92))

    assert not drop_triggered(replace(bought, last_price=Decimal(85)))
    assert drop_triggered(replace(bought, last_price=Decimal("84.64")))


def test_drop_from_a_fill_price_above_the_previous_close() -> None:
    """今天开仓成交 105：从成交均价算，96.6 的跌幅是 0.08；从前一交易日收盘价算只有 0.034。"""
    bought = replace(AAA, entry_price_today=Decimal(105), last_price=Decimal("96.6"))

    assert drop_triggered(bought)


# 单日亏损


def test_daily_loss_equal_to_the_breaker_line_trips() -> None:
    assert daily_loss_tripped(Decimal(97_000), PREVIOUS_CLOSE_EQUITY, RATIO)


def test_daily_loss_one_cent_short_of_the_breaker_line_does_not_trip() -> None:
    assert not daily_loss_tripped(Decimal("97000.01"), PREVIOUS_CLOSE_EQUITY, RATIO)


def test_daily_gain_does_not_trip() -> None:
    assert not daily_loss_tripped(Decimal(105_000), PREVIOUS_CLOSE_EQUITY, RATIO)


# 代码触发的清仓方案


def test_close_plan_fields() -> None:
    """spec「代码触发的清仓方案」字段表的每一行。"""
    plan = close_plan("AAA", "科技", BASELINE_DAY)

    assert plan.symbol == "AAA"
    assert plan.sector == "科技"
    assert plan.action is TradeAction.CLOSE
    assert plan.quantity is None
    assert plan.order_type is OrderType.MARKET
    assert plan.limit_price is None
    assert plan.stop_price is None
    assert plan.emergency_threshold is None
    assert plan.expires_at is None
    assert plan.max_price_deviation is None


def test_close_plan_id_is_the_same_for_the_same_stock_and_day() -> None:
    assert close_plan("AAA", "科技", BASELINE_DAY) == close_plan("AAA", "科技", BASELINE_DAY)


def test_close_plan_id_differs_by_stock_or_day() -> None:
    ids = {
        close_plan("AAA", "科技", BASELINE_DAY).plan_id,
        close_plan("AAA", "科技", NEXT_DAY).plan_id,
        close_plan("BBB", "医疗", BASELINE_DAY).plan_id,
    }

    assert len(ids) == 3


# 判定结果


def _exit(holding: Holding, reason: ExitReason) -> EmergencyExit:
    return EmergencyExit(close_plan(holding.symbol, holding.sector, BASELINE_DAY), reason)


def test_without_the_breaker_only_dropped_stocks_exit() -> None:
    """判定结果表"没有触发"一行。"""
    dropped = replace(AAA, last_price=Decimal(92))

    exits = emergency_exits([dropped, BBB], breaker_tripped=False, day=BASELINE_DAY)

    assert exits == (_exit(dropped, ExitReason.STOCK_DROP),)


def test_with_the_breaker_every_holding_exits() -> None:
    """判定结果表"已触发"一行。"""
    exits = emergency_exits([AAA, BBB], breaker_tripped=True, day=BASELINE_DAY)

    assert exits == (
        _exit(AAA, ExitReason.DAILY_LOSS_BREAKER),
        _exit(BBB, ExitReason.DAILY_LOSS_BREAKER),
    )


def test_with_the_breaker_a_dropped_stock_exits_once() -> None:
    dropped = replace(AAA, last_price=Decimal(90))

    exits = emergency_exits([dropped, BBB], breaker_tripped=True, day=BASELINE_DAY)

    assert exits == (
        _exit(dropped, ExitReason.DAILY_LOSS_BREAKER),
        _exit(BBB, ExitReason.DAILY_LOSS_BREAKER),
    )


def test_with_the_breaker_a_holding_without_quotes_exits() -> None:
    no_quotes = replace(BBB, previous_close=None, last_price=None)

    exits = emergency_exits([AAA, no_quotes], breaker_tripped=True, day=BASELINE_DAY)

    assert [e.plan.symbol for e in exits] == ["AAA", "BBB"]


def test_no_holdings_no_exits() -> None:
    assert emergency_exits([], breaker_tripped=True, day=BASELINE_DAY) == ()
    assert emergency_exits([], breaker_tripped=False, day=BASELINE_DAY) == ()


# 性质测试

_SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")
_SECTORS = ("科技", "医疗", "能源")
_days = st.dates(SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY)
_prices = st.none() | st.decimals(min_value=Decimal("0.01"), max_value=Decimal(500), places=2)
_thresholds = st.decimals(min_value=Decimal("0.03"), max_value=Decimal("0.20"), places=2)


@st.composite
def _holdings(draw: st.DrawFn) -> list[Holding]:
    """0 到 5 只持仓，股票不重复；行情和今天开仓成交的均价都可能为空。"""
    symbols = draw(st.lists(st.sampled_from(_SYMBOLS), max_size=5, unique=True))
    return [
        Holding(
            symbol=symbol,
            sector=draw(st.sampled_from(_SECTORS)),
            emergency_threshold=draw(_thresholds),
            previous_close=draw(_prices),
            last_price=draw(_prices),
            entry_price_today=draw(_prices),
        )
        for symbol in symbols
    ]


@given(holdings=_holdings(), breaker_tripped=st.booleans(), day=_days)
def test_exits_follow_the_result_table(
    holdings: list[Holding], breaker_tripped: bool, day: date
) -> None:
    exits = emergency_exits(holdings, breaker_tripped, day)

    symbols = [e.plan.symbol for e in exits]
    assert len(symbols) == len(set(symbols))
    if breaker_tripped:
        assert symbols == [h.symbol for h in holdings]
        assert all(e.reason is ExitReason.DAILY_LOSS_BREAKER for e in exits)
    else:
        assert symbols == [h.symbol for h in holdings if drop_triggered(h)]
        assert all(e.reason is ExitReason.STOCK_DROP for e in exits)
    by_symbol = {h.symbol: h for h in holdings}
    for e in exits:
        holding = by_symbol[e.plan.symbol]
        assert e.plan == close_plan(holding.symbol, holding.sector, day)


@given(
    first=st.tuples(st.sampled_from(_SYMBOLS), _days, st.sampled_from(_SECTORS)),
    second=st.tuples(st.sampled_from(_SYMBOLS), _days, st.sampled_from(_SECTORS)),
)
def test_close_plan_id_depends_only_on_stock_and_day(
    first: tuple[str, date, str], second: tuple[str, date, str]
) -> None:
    (symbol_a, day_a, sector_a), (symbol_b, day_b, sector_b) = first, second

    same_id = (
        close_plan(symbol_a, sector_a, day_a).plan_id
        == close_plan(symbol_b, sector_b, day_b).plan_id
    )

    assert same_id == ((symbol_a, day_a) == (symbol_b, day_b))
