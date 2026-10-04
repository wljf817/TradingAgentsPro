"""紧急风险退出的判定：个股跌幅、单日亏损熔断，以及代码触发的清仓方案。

见 emergency-exit spec。判定需要的数据都由调用方传入。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan

# 代码触发的清仓方案的方案 ID 由这个命名空间、股票和交易日算出，见 add-emergency-exit 的 design D5
_CLOSE_PLAN_NAMESPACE = UUID("4fd05e50-25af-4217-978e-9d68b0baa5dd")


class ExitReason(StrEnum):
    """为什么清仓。"""

    STOCK_DROP = "stock_drop"  # 个股跌幅
    DAILY_LOSS_BREAKER = "daily_loss_breaker"  # 组合熔断


@dataclass(frozen=True, slots=True)
class Holding:
    """判定紧急退出用的一只持仓。数据来自行情、券商和执行模块的订单，不加约束。

    - emergency_threshold：这只股票最近一次有成交的开仓方案的生效紧急阈值
    - previous_close、last_price：前一交易日收盘价、最新价；没有行情时为 None
    - entry_price_today：今天开仓成交的均价；今天没有开仓成交时为 None
    """

    symbol: str
    sector: str
    emergency_threshold: Decimal
    previous_close: Decimal | None
    last_price: Decimal | None
    entry_price_today: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EmergencyExit:
    """要清仓的一只股票：代码触发的清仓方案和原因。"""

    plan: TradePlan
    reason: ExitReason


def reference_price(holding: Holding) -> Decimal | None:
    """今天有开仓成交时是成交均价，否则是前一交易日收盘价；都没有时为 None。"""
    if holding.entry_price_today is not None:
        return holding.entry_price_today
    return holding.previous_close


def drop_triggered(holding: Holding) -> bool:
    """参考价和最新价都有，并且最新价不高于参考价乘以（1 减紧急阈值），即跌幅达到紧急阈值。

    用乘法而不是除法算跌幅，Decimal 的结果是精确的，边界值不会因为舍入判错。
    """
    reference = reference_price(holding)
    if reference is None or holding.last_price is None:
        return False
    return holding.last_price <= reference * (1 - holding.emergency_threshold)


def daily_loss_tripped(equity: Decimal, previous_close_equity: Decimal, ratio: Decimal) -> bool:
    """单日亏损达到熔断线。

    单日亏损是前一交易日收盘时的权益减去当前权益，熔断线是前一交易日收盘时的权益乘以 ratio。
    """
    return previous_close_equity - equity >= previous_close_equity * ratio


def close_plan(symbol: str, sector: str, day: date) -> TradePlan:
    """这只股票在这个交易日的代码触发的清仓方案：市价单，卖出全部可卖数量。

    方案 ID 只由股票和交易日决定，同一只股票在同一个交易日总是同一个方案。
    """
    return TradePlan(
        plan_id=uuid5(_CLOSE_PLAN_NAMESPACE, f"{symbol}/{day.isoformat()}"),
        symbol=symbol,
        sector=sector,
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )


def emergency_exits(
    holdings: Iterable[Holding], breaker_tripped: bool, day: date
) -> tuple[EmergencyExit, ...]:
    """要清仓的股票，按传入的顺序：熔断已触发时是全部持仓，原因是组合熔断；
    否则是触及个股跌幅的持仓。day 是判定时所在交易时段的交易日。
    """
    if breaker_tripped:
        return tuple(_exit(h, ExitReason.DAILY_LOSS_BREAKER, day) for h in holdings)
    return tuple(_exit(h, ExitReason.STOCK_DROP, day) for h in holdings if drop_triggered(h))


def _exit(holding: Holding, reason: ExitReason, day: date) -> EmergencyExit:
    return EmergencyExit(close_plan(holding.symbol, holding.sector, day), reason)
