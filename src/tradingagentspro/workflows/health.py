"""健康监控接线：盘中巡检每一轮让健康监控判定一次，进入和离开降级模式时告警，各项恢复以后对账。

见 health-monitoring spec 和 add-health-monitoring 的 design。取信号、判定和推送告警都不拿锁；
恢复后的对账拿着全程序共用的那把锁。
"""

import logging
import threading
from collections.abc import Collection
from datetime import UTC, datetime

from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    ExecutionService,
    Reconciliation,
)
from tradingagentspro.modules.health import (
    HealthCheck,
    HealthMode,
    HealthService,
    HealthSignals,
    HealthStatus,
)
from tradingagentspro.modules.marketdata import MarketDataService, QuoteUnavailableError
from tradingagentspro.modules.notification import NotificationService
from tradingagentspro.shared.clock import Clock
from tradingagentspro.workflows.reconciliation import PAUSED, inconsistency_lines

_log = logging.getLogger(__name__)

# 五项检查的中文名，见 degraded-mode spec「健康检查」
_CHECK_NAMES = {
    HealthCheck.MODEL_API: "模型 API",
    HealthCheck.BROKER: "券商连接",
    HealthCheck.DATA_DELAY: "数据延迟",
    HealthCheck.BUDGET: "调用预算",
    HealthCheck.SCHEDULER: "调度器心跳",
}
_AWAITING = HealthMode.AWAITING_RECONCILIATION
_NOTICE = (
    "已暂停开仓，已经确认、还没下单的开仓方案下单时作废；止损单照常保留。"
    "各项恢复并和券商对账以后恢复开仓"
)


class HealthMonitor:
    """健康监控接线，见 health-monitoring spec。

    signals 拼一次健康信号，启动时的第一次健康判定和巡检共用；judge 是巡检每一轮的「健康判定」。
    只在启动的线程和巡检的线程里调用，同一时刻只有一个，见 design D1。
    """

    def __init__(
        self,
        health: HealthService,
        execution: ExecutionService,
        market_data: MarketDataService,
        notification: NotificationService,
        lock: threading.RLock,
        clock: Clock,
    ) -> None:
        self._health = health
        self._execution = execution
        self._market_data = market_data
        self._notification = notification
        self._lock = lock
        self._clock = clock
        # 程序启动以后最近一次取到的参考股票更新时刻；还没取到过时为 None，见 design D3
        self._last_quote_at: datetime | None = None

    def signals(self, broker_connected: bool) -> HealthSignals:
        """一次判定的健康信号，见 health-monitoring spec「健康信号」和 design D3、D4。

        取不到行情时沿用最近一次取到的更新时刻，并写一条 WARNING。
        """
        try:
            self._last_quote_at = self._market_data.last_update()
        except QuoteUnavailableError as error:
            last = self._last_quote_at
            kept = (
                f"沿用最近一次取到的 {last.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
                if last
                else "还没有取到过，按没有处理"
            )
            _log.warning("取不到行情（%s），行情最近一次更新的时刻%s", error, kept)
        return HealthSignals(
            pm_model_available=True,
            critic_model_available=True,
            broker_connected=broker_connected,
            last_quote_at=self._last_quote_at,
            reserve_budget_exhausted=False,
            scheduler_heartbeat_at=self._clock.now(),
        )

    def judge(self, broker_connected: bool) -> None:
        """巡检每一轮的健康判定，见 health-monitoring spec「巡检里的健康判定」。

        broker_connected 是这一轮第 3 步读券商全部持仓的结果。判定，按状态变化写日志和告警；
        判定之前和之后都是等待对账时（已经等了一轮）对账，见 design D5。对账读取券商超时或断线
        时写 WARNING，别的异常往外抛，由巡检写 ERROR。
        """
        before = self._health.status
        after = self._health.check(self.signals(broker_connected))
        self._report(before, after)
        if before.mode is _AWAITING and after.mode is _AWAITING:
            self._recover(after)

    def _report(self, before: HealthStatus, after: HealthStatus) -> None:
        """按 health-monitoring spec「状态变化的告警和日志」的表写日志、推送告警，见 design D6。"""
        match before.mode, after.mode:
            case HealthMode.NORMAL, HealthMode.DEGRADED:
                text = degraded_text(after, at_startup=False)
                _log.warning("%s", text)
                self._notification.alert(text)
            case HealthMode.DEGRADED, HealthMode.DEGRADED if before.abnormal != after.abnormal:
                _log.warning("降级模式：现在异常的检查是%s", checks_text(after.abnormal))
            case HealthMode.DEGRADED, HealthMode.AWAITING_RECONCILIATION:
                _log.info("各项检查已恢复，下一轮和券商对账")
            case HealthMode.AWAITING_RECONCILIATION, HealthMode.DEGRADED:
                _log.warning("等待对账时又有检查异常：%s", checks_text(after.abnormal))

    def _recover(self, status: HealthStatus) -> None:
        """拿着锁对账、告诉健康监控对账完成；放开锁以后推送降级模式结束，见 design D5。

        status 是对账以前的状态（等待对账），给告警写进入降级模式的时刻。
        """
        try:
            with self._lock:
                result = self._execution.reconcile()
                self._health.reconciled()
        except BrokerUnavailableError as error:
            _log.warning("恢复后的对账没做成：券商不可用（%s），留在等待对账，下一轮再来", error)
            return
        text = recovered_text(status, result)
        if result.clean:
            _log.info("%s", text)
        else:
            _log.warning("%s", text)
        self._notification.alert(text)


def checks_text(checks: Collection[HealthCheck]) -> str:
    """异常的检查的中文名，按 degraded-mode spec「健康检查」表的顺序，用顿号连接。"""
    return "、".join(_CHECK_NAMES[check] for check in HealthCheck if check in checks)


def degraded_text(status: HealthStatus, *, at_startup: bool) -> str:
    """进入降级模式的告警，见 health-monitoring spec「状态变化的告警和日志」和 design D7。

    at_startup 时第一行是启动时处于降级模式，见 bootstrap spec「第一次健康判定」。
    """
    head = "启动时处于降级模式" if at_startup else "进入降级模式"
    return f"{head}（{_since(status)} 起），异常的检查：{checks_text(status.abnormal)}\n{_NOTICE}"


def recovered_text(status: HealthStatus, result: Reconciliation) -> str:
    """降级模式结束的告警：status 是对账以前的状态，result 是这次对账的结果，见 design D7。"""
    head = f"降级模式结束（{_since(status)} 起）：各项检查已恢复，和券商对账"
    if result.clean:
        return f"{head}没有不一致，已恢复开仓"
    return "\n".join([f"{head}有不一致：", *inconsistency_lines(result), PAUSED])


def _since(status: HealthStatus) -> str:
    """进入降级模式的时刻，UTC，到分钟。"""
    assert status.degraded_since is not None  # 处于降级模式时总有进入时刻
    return f"{status.degraded_since.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
