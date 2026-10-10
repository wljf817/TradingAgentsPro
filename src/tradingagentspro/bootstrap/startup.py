"""启动对账和第一次健康判定：启动时、Discord 和 DBOS 之前各做一次，这里只写日志。

见 bootstrap spec「启动对账」「第一次健康判定」和 add-bootstrap-wiring 的 design D7。
两者的告警都由 app.py 在 Discord 连上以后发出（add-close-reconciliation 的 design D10、
add-health-monitoring 的 design D8）。
"""

import logging

from tradingagentspro.modules.execution import ExecutionService, Reconciliation
from tradingagentspro.modules.health import HealthService, HealthStatus
from tradingagentspro.workflows.health import HealthMonitor, checks_text

_log = logging.getLogger(__name__)


def reconcile_at_startup(execution: ExecutionService) -> Reconciliation:
    """对账一次。没有不一致写 INFO；有不一致写 WARNING（带三类不一致的清单）。

    读取券商失败时 BrokerUnavailableError 原样抛出，由启动顺序转成启动失败。
    """
    result = execution.reconcile()
    if result.clean:
        _log.info("启动对账：没有不一致")
    else:
        _log.warning("启动对账有不一致，暂停开仓：%r", result)
    return result


def judge_health_at_startup(health: HealthService, monitor: HealthMonitor) -> HealthStatus:
    """按 bootstrap spec「第一次健康判定」的信号判定一次；降级时写 WARNING。

    信号和巡检的取法相同（health-monitoring spec「健康信号」），券商连接按正常：启动对账刚读过
    券商。取不到行情的 WARNING 由 monitor 写，取到的时刻由它记下给巡检接着用。
    """
    status = health.check(monitor.signals(broker_connected=True))
    if status.degraded:
        _log.warning("启动时处于降级模式，暂停开仓，异常的检查：%s", checks_text(status.abnormal))
    else:
        _log.info("健康：正常")
    return status
