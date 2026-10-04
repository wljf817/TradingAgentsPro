"""健康监控的用例：判定健康信号，记下对账完成，给出当前状态。

见 degraded-mode spec。
"""

from tradingagentspro.modules.health.config import HealthConfig
from tradingagentspro.modules.health.domain.health import (
    HealthSignals,
    HealthStatus,
    abnormal_checks,
)
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.trading_calendar import TradingCalendar


class HealthService:
    """健康监控的入口。

    状态放在内存里，新建以后是正常，进程重启以后由下一次判定重新算，见 add-degraded-mode 的
    design D4。同一个实例一次只能有一个调用，由调用方保证。
    """

    def __init__(self, clock: Clock, calendar: TradingCalendar, config: HealthConfig) -> None:
        self._clock = clock
        self._calendar = calendar
        self._config = config
        self._status = HealthStatus()

    @property
    def status(self) -> HealthStatus:
        """当前状态。"""
        return self._status

    def check(self, signals: HealthSignals) -> HealthStatus:
        """在当前时刻判定一次，返回之后的状态。有异常的检查就进入或留在降级，降级时全部恢复就等待对账。"""
        now = self._clock.now()
        abnormal = abnormal_checks(signals, now, self._calendar.session_at(now), self._config)
        self._status = self._status.judged(abnormal, now)
        return self._status

    def reconciled(self) -> HealthStatus:
        """调用方刚完成一次对账，返回之后的状态：只有等待对账时回到正常。"""
        self._status = self._status.reconciled()
        return self._status
