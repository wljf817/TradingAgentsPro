"""已实现盈亏：把一只股票的交易记录按加权平均成本算成历史里的交易，见 history-query spec
「已实现盈亏」和 add-query-history 的 design D6。
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from tradingagentspro.contracts.history import HistoryTrade
from tradingagentspro.modules.execution.domain.trade import Trade

_CENTS: Final = Decimal("0.01")


def history_trades(trades: Iterable[Trade]) -> tuple[HistoryTrade, ...]:
    """这只股票全部有成交的交易记录变成历史里的交易，从晚到早。"""
    cost = Decimal(0)
    shares = 0
    history: list[HistoryTrade] = []
    for trade in sorted(trades, key=_calculation_order):
        assert trade.average_price is not None  # 累计成交大于 0 的都有成交均价
        value = trade.average_price * trade.filled
        if trade.is_sale:
            basis = cost * trade.filled / shares
            pnl = value - trade.fees - basis
            cost -= basis
            shares -= trade.filled
            history.append(_history(trade, pnl.quantize(_CENTS, ROUND_HALF_UP)))
        else:
            cost += value + trade.fees
            shares += trade.filled
            history.append(_history(trade, None))
    return tuple(reversed(history))


def _calculation_order(trade: Trade) -> tuple[date, bool, datetime, str]:
    """按成交日；同一天先买入、后卖出和止损单；再按创建时刻和客户端订单 ID。"""
    assert trade.traded_on is not None  # 累计成交大于 0 的都有成交日
    return trade.traded_on, trade.is_sale, trade.created_at, trade.client_order_id


def _history(trade: Trade, realized_pnl: Decimal | None) -> HistoryTrade:
    assert trade.average_price is not None and trade.traded_on is not None  # 同上
    return HistoryTrade(
        client_order_id=trade.client_order_id,
        case_id=trade.case_id,
        kind=trade.kind,
        quantity=trade.quantity,
        filled=trade.filled,
        average_price=trade.average_price,
        fees=trade.fees,
        status=trade.status,
        traded_on=trade.traded_on,
        realized_pnl=realized_pnl,
    )
