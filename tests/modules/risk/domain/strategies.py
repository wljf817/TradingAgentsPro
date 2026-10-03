"""Hypothesis 策略：任意的风控输入、方案和账户，交易日历用纽交所样例日历。"""

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
from tradingagentspro.modules.risk.domain.exposure import ZERO, holding_risk

SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")
SECTORS = ("科技", "医疗", "能源")

_symbols = st.sampled_from(SYMBOLS)
_sectors = st.sampled_from(SECTORS)
_last_trade_days = st.dictionaries(
    _symbols, st.dates(SAMPLE_FIRST_DAY, SAMPLE_LAST_DAY), max_size=3
)
# 样例日历范围内的时刻。Hypothesis 要求上下限不带时区，生成的时间由 timezones 加上 UTC。
_moments = st.datetimes(
    datetime(2026, 9, 2),  # noqa: DTZ001
    datetime(2027, 1, 27),  # noqa: DTZ001
    timezones=st.just(UTC),
)


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
def plans(draw: st.DrawFn) -> TradePlan:
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
            expires_at=draw(st.none() | _moments),
            max_price_deviation=draw(st.none() | _money("0.99")),
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
        expires_at=draw(st.none() | _moments),
    )


@st.composite
def risk_requests(draw: st.DrawFn) -> RiskRequest:
    """任意的风控输入：账户、方案、预留、熔断状态、样例日历范围内的当前时刻和阈值。"""
    account = AccountSnapshot(
        equity=draw(_money(1_000_000)),
        settled_cash=draw(_money(500_000, min_value="0")),
        positions=draw(_positions()),
        last_trade_days=draw(_last_trade_days),
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
    now = draw(_moments)
    config = RiskConfig(
        max_positions=draw(st.integers(1, 8)),
        min_trade_interval_sessions=draw(st.integers(1, 3)),
    )
    return RiskRequest(
        plan=draw(plans()),
        account=account,
        opening_reservations=tuple(opening_reservations),
        sell_reservations=tuple(sell_reservations),
        daily_loss_breaker_tripped=draw(st.booleans()),
        now=now,
        calendar=NYSE,
        config=config,
    )


@st.composite
def accounts_within_limits(draw: st.DrawFn) -> AccountSnapshot:
    """持仓一开始就满足默认阈值下的组合合计风险和板块集中度的账户。

    先生成持仓，再按持仓算出满足这两项上限所需的最低权益，权益比它至少多 50,000，
    免得开仓方案大多 1 股都通不过。最多 4 只持仓，不会超过默认的最多持仓数。
    没有最近成交日，免得开仓方案多半被交易间隔挡住。
    """
    config = RiskConfig()
    positions = draw(_positions())
    total_risk = sum((holding_risk(p, config.cost_buffer_ratio) for p in positions), ZERO)
    sector_values = (
        sum((p.quantity * p.last_price for p in positions if p.sector == sector), ZERO)
        for sector in SECTORS
    )
    min_equity = max(
        total_risk / config.max_total_risk_ratio, max(sector_values) / config.max_sector_ratio
    )
    return AccountSnapshot(
        equity=min_equity + draw(_money(1_000_000, min_value="50000")),
        settled_cash=draw(_money(500_000, min_value="0")),
        positions=positions,
    )


@st.composite
def passable_opening_plans(draw: st.DrawFn) -> TradePlan:
    """多半能通过风控的开仓方案：止损价是限价的 50% 到 99%，不填紧急阈值。"""
    limit_price = draw(_money(500, min_value="1"))
    stop_ratio = draw(st.decimals(min_value=Decimal("0.5"), max_value=Decimal("0.99"), places=2))
    return TradePlan(
        plan_id=draw(st.uuids()),
        symbol=draw(_symbols),
        sector=draw(_sectors),
        action=draw(st.sampled_from((TradeAction.BUY, TradeAction.ADD))),
        quantity=draw(st.integers(1, 500)),
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        stop_price=(limit_price * stop_ratio).quantize(Decimal("0.01")),
    )
