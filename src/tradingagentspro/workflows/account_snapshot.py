"""风控用的账户数据：执行模块的账户数据加上行情的最新价。

见 order-flow spec「风控用的账户数据」和 add-order-flow 的 design D7。只读，不拿锁。
"""

import logging
from collections.abc import Iterable, Mapping
from decimal import Decimal

from tradingagentspro.contracts.account import AccountSnapshot, Position
from tradingagentspro.modules.execution import Account, ExecutionService, HeldPosition
from tradingagentspro.modules.marketdata import MarketDataService, Quote, QuoteUnavailableError

_log = logging.getLogger(__name__)


def price_of(position: HeldPosition, quote: Quote | None) -> Decimal:
    """持仓的最新价：行情的最新价，没有时用前一交易日收盘价，取不到行情时用均价。"""
    if quote is None:
        return position.average_cost
    if quote.last_price is None:
        return quote.previous_close
    return quote.last_price


def account_snapshot(account: Account, quotes: Mapping[str, Quote]) -> AccountSnapshot:
    """风控用的账户数据；quotes 里没有的持仓按均价。"""
    return AccountSnapshot(
        equity=account.equity,
        settled_cash=account.settled_cash,
        positions=tuple(
            Position(
                symbol=p.symbol,
                sector=p.sector,
                quantity=p.quantity,
                average_cost=p.average_cost,
                last_price=price_of(p, quotes.get(p.symbol)),
                stop_price=p.stop_price,
            )
            for p in account.positions
        ),
        last_trade_days=account.last_trade_days,
    )


def read_snapshot(
    execution: ExecutionService, market_data: MarketDataService, extra: Iterable[str] = ()
) -> tuple[AccountSnapshot, dict[str, Quote]]:
    """读执行模块的账户数据，一次取持仓股票加上 extra 的行情，拼成风控用的账户数据。

    返回账户数据和取到的行情。读券商账户失败时 BrokerUnavailableError 原样抛出。行情不可用时
    当作一只都没取到；取不到行情的持仓按均价，各写一条 WARNING。
    """
    account = execution.account()
    try:
        quotes = market_data.quotes([*(p.symbol for p in account.positions), *extra])
        reason = "行情源没有它的数据"
    except QuoteUnavailableError as error:
        quotes = {}
        reason = f"行情不可用：{error}"
    for p in account.positions:
        if p.symbol not in quotes:
            cost = p.average_cost
            _log.warning(
                "取不到 %s 的行情（%s），风控按均价 %s 估算它的市值", p.symbol, reason, cost
            )
    return account_snapshot(account, quotes), quotes
