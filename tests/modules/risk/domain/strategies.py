"""Hypothesis 策略：任意的风控输入，交易日历用纽交所样例日历。"""

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import strategies as st

from tests.fakes.calendar import SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY
from tests.modules.risk.builders import NYSE
from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.modules.risk import (
    OpeningReservation,
    RiskConfig,
    RiskRequest,
    SellReservation,
)

SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")
SECTORS = ("科技", "医疗", "能源")

_symbols = st.sampled_from(SYMBOLS)
_sectors = st.sampled_from(SECTORS)


def _money(max_value: int | str, min_value: str = "0.01") -> st.SearchStrategy[Decimal]:
    return st.decimals(min_value=Decimal(min_value), max_value=Decimal(max_value), places=2)


@st.composite
def _positions(draw: st.DrawFn) -> tuple[Position, ...]:
    """0 到 4 只持仓，股票不重复；止损价可能高于均价。"""
    symbols = draw(st.lists(_symbols, max_size=4, unique=True))
    return tuple(
        Position(
            symbol=symbol,
            sector=draw(_sectors),
            quantity=draw(st.integers(1, 500)),
            average_cost=draw(_money(500)),
            last_price=draw(_money(500)),
            stop_price=draw(_money(500)),
        )
        for symbol in symbols
    )


@st.composite
def _plans(draw: st.DrawFn) -> TradePlan:
    """四种动作的合法方案；开仓方案的止损价可能不低于限价。"""
    action = draw(st.sampled_from(TradeAction))
    symbol, sector = draw(_symbols), draw(_sectors)
    if action.is_opening:
        return TradePlan(
            plan_id=draw(st.uuids()),
            symbol=symbol,
            sector=sector,
            action=action,
            quantity=draw(st.integers(1, 2000)),
            order_type=OrderType.LIMIT,
            limit_price=draw(_money(500)),
            stop_price=draw(_money(600)),
            emergency_threshold=draw(st.none() | _money("0.99")),
        )
    order_type = draw(st.sampled_from(OrderType))
    return TradePlan(
        plan_id=draw(st.uuids()),
        symbol=symbol,
        sector=sector,
        action=action,
        quantity=None if action is TradeAction.CLOSE else draw(st.integers(1, 2000)),
        order_type=order_type,
        limit_price=draw(_money(500)) if order_type is OrderType.LIMIT else None,
    )


@st.composite
def risk_requests(draw: st.DrawFn) -> RiskRequest:
    """任意的风控输入：账户、方案、预留、熔断状态、样例日历范围内的当前时刻和阈值。"""
    account = AccountSnapshot(
        equity=draw(_money(1_000_000)),
        settled_cash=draw(_money(500_000, min_value="0")),
        positions=draw(_positions()),
        last_trade_days=draw(
            st.dictionaries(_symbols, st.dates(SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY), max_size=3)
        ),
    )
    opening_reservations = draw(
        st.lists(
            st.builds(
                OpeningReservation, _symbols, _sectors, _money(50_000), _money(50_000), _money(5000)
            ),
            max_size=3,
        )
    )
    sell_reservations = draw(
        st.lists(st.builds(SellReservation, _symbols, st.integers(1, 300)), max_size=2)
    )
    # Hypothesis 要求上下限不带时区，生成的时间由 timezones 加上 UTC。
    first, last = datetime(2026, 9, 2), datetime(2027, 1, 27)  # noqa: DTZ001
    now = draw(st.datetimes(first, last, timezones=st.just(UTC)))
    config = RiskConfig(
        max_positions=draw(st.integers(1, 8)),
        min_trade_interval_sessions=draw(st.integers(1, 3)),
    )
    return RiskRequest(
        plan=draw(_plans()),
        account=account,
        opening_reservations=tuple(opening_reservations),
        sell_reservations=tuple(sell_reservations),
        daily_loss_breaker_tripped=draw(st.booleans()),
        now=now,
        calendar=NYSE,
        config=config,
    )
