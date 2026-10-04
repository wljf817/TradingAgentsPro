"""紧急退出的用例：在常规交易时段内判定，记下当天的单日亏损熔断。

见 emergency-exit spec。
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from tradingagentspro.modules.emergency.config import EmergencyConfig
from tradingagentspro.modules.emergency.domain.exit import (
    EmergencyExit,
    Holding,
    daily_loss_tripped,
    emergency_exits,
)
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar


class EmergencyService:
    """紧急退出的入口。

    熔断状态放在内存里，进程重启以后由下一次判定重新算，见 add-emergency-exit 的 design D4。
    同一个实例一次只能有一个调用，由调用方保证。
    """

    def __init__(self, clock: Clock, calendar: TradingCalendar, config: EmergencyConfig) -> None:
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._tripped_on: date | None = None  # 熔断触发的交易日；新建以后还没触发过时为 None

    def check(
        self, holdings: Iterable[Holding], equity: Decimal, previous_close_equity: Decimal
    ) -> tuple[EmergencyExit, ...]:
        """判定一次，给出要清仓的股票。holdings 是全部持仓。

        只在常规交易时段内判定，时段之外什么都不返回，熔断也不会因此触发。单日亏损达到熔断线时，
        记下熔断在这个交易日触发；之后到收盘之前，每次判定都清仓全部持仓。可以重复调用，
        同一交易日里同一只股票每次给出同一个方案。
        """
        session = self._calendar.session_at(self._clock.now())
        if session is None:
            return ()
        ratio = self._config.daily_loss_breaker_ratio
        if daily_loss_tripped(equity, previous_close_equity, ratio):
            self._tripped_on = session.day
        return emergency_exits(holdings, self._tripped_on == session.day, session.day)

    def breaker_tripped(self) -> bool:
        """当前时刻单日亏损熔断是否已经触发：只在触发的那个交易时段收盘之前为真。

        风控检查和下单前再校验的熔断输入用它。
        """
        session = self._calendar.session_at(self._clock.now())
        return session is not None and session.day == self._tripped_on
