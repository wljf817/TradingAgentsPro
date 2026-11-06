"""可用额度：把风控规则的不等式解成美元上限，见 case-headroom spec「可用额度的内容」。

和规则是同一批公式的两种写法：规则回答某个股数能否通过，这里回答还能用多少，见 add-case-headroom
的 design D3。
"""

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Final

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.risk import Headroom
from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.modules.risk.domain.exposure import (
    ZERO,
    holding_of,
    holding_risk,
    position_value,
    sector_value,
)
from tradingagentspro.modules.risk.domain.request import OpeningReservation

_CENT: Final = Decimal("0.01")


def _floored(amount: Decimal) -> Decimal:
    """向下舍入到分；负数记 0。"""
    return max(ZERO, amount).quantize(_CENT, rounding=ROUND_FLOOR)


def _available_cash(
    account: AccountSnapshot, reservations: Sequence[OpeningReservation], config: RiskConfig
) -> Decimal:
    reserved = sum((r.cash for r in reservations), ZERO)
    spendable = account.settled_cash - reserved - config.commission_per_order
    return _floored(spendable / (1 + config.cost_buffer_ratio))


def _open_slots(
    symbol: str,
    account: AccountSnapshot,
    reservations: Sequence[OpeningReservation],
    config: RiskConfig,
) -> int | None:
    held = {p.symbol for p in account.positions}
    if symbol in held:
        return None
    return max(0, config.max_positions - len(held | {r.symbol for r in reservations}))


def _position_room(
    symbol: str,
    sector: str,
    account: AccountSnapshot,
    reservations: Sequence[OpeningReservation],
    config: RiskConfig,
) -> Decimal:
    held_value = position_value(holding_of(account, symbol), 0, ZERO)
    stock_room = account.equity * config.max_position_ratio - held_value
    sector_room = account.equity * config.max_sector_ratio - sector_value(
        account, reservations, sector
    )
    return _floored(min(stock_room, sector_room))


def _risk_limit(
    symbol: str,
    account: AccountSnapshot,
    reservations: Sequence[OpeningReservation],
    config: RiskConfig,
) -> Decimal:
    others = sum(
        (holding_risk(p, config) for p in account.positions if p.symbol != symbol),
        ZERO,
    )
    reserved = sum((r.risk for r in reservations), ZERO)
    total_room = account.equity * config.max_total_risk_ratio - others - reserved
    return _floored(min(account.equity * config.max_trade_risk_ratio, total_room))


def headroom(
    symbol: str,
    sector: str,
    account: AccountSnapshot,
    reservations: Sequence[OpeningReservation],
    config: RiskConfig,
) -> Headroom:
    """这只股票的可用额度。reservations 是全部有效的开仓预留。"""
    round_trip = 2 * config.commission_per_order
    min_order_value = round_trip / config.max_round_trip_commission_ratio
    return Headroom(
        available_cash=_available_cash(account, reservations, config),
        open_slots=_open_slots(symbol, account, reservations, config),
        position_room=_position_room(symbol, sector, account, reservations, config),
        risk_limit=_risk_limit(symbol, account, reservations, config),
        min_order_value=min_order_value.quantize(_CENT, rounding=ROUND_CEILING),
    )
