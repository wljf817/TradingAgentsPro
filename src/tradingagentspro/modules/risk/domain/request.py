"""风控的输入：方案、账户数据、其他方案的预留、熔断和降级模式的状态、当前时刻、交易日历和阈值。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradingagentspro.contracts.account import AccountSnapshot
from tradingagentspro.contracts.trade_plan import TradePlan
from tradingagentspro.modules.risk.config import RiskConfig
from tradingagentspro.shared.trading_calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class OpeningReservation:
    """一个已通过、尚未结束的开仓方案占用的额度。"""

    symbol: str
    sector: str
    cash: Decimal
    market_value: Decimal
    risk: Decimal


@dataclass(frozen=True, slots=True)
class SellReservation:
    """一个已通过、尚未结束的退出方案预留的卖出股数。"""

    symbol: str
    quantity: int


@dataclass(frozen=True, slots=True)
class RiskRequest:
    """检查一个方案需要的全部输入。now 是带时区的 UTC 时间。

    degraded 是系统是否处于降级模式，见 degraded-mode spec，由调用方从健康监控取来。
    """

    plan: TradePlan
    account: AccountSnapshot
    opening_reservations: tuple[OpeningReservation, ...]
    sell_reservations: tuple[SellReservation, ...]
    daily_loss_breaker_tripped: bool
    degraded: bool
    now: datetime
    calendar: TradingCalendar
    config: RiskConfig
