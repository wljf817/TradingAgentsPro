"""启动对账、第一次健康判定，以及程序日志的启动、崩溃恢复和停止。

启动对账和第一次健康判定在启动时、Discord 和 DBOS 之前各做一次，见 bootstrap spec「启动对账」
「第一次健康判定」和 add-bootstrap-wiring 的 design D7。两者的告警都由 app.py 在 Discord 连上以后
发出（add-close-reconciliation 的 design D10、add-health-monitoring 的 design D8）。启动、崩溃恢复
和停止见 program-log spec「系统的动作」和 add-program-log 的 design D7。
"""

import logging
from datetime import UTC

from tradingagentspro.modules.execution import ExecutionService, Reconciliation
from tradingagentspro.modules.health import HealthService, HealthStatus
from tradingagentspro.modules.programlog import (
    ACCOUNT,
    WHOLE_SYSTEM,
    Action,
    Actor,
    ProgramLog,
)
from tradingagentspro.workflows.entries import order_changes
from tradingagentspro.workflows.health import HealthMonitor, checks_text, degraded_line
from tradingagentspro.workflows.reconciliation import reconciliation_text

_log = logging.getLogger(__name__)


def record_startup(log: ProgramLog, text: str) -> None:
    """写程序日志的启动，说明是 text（版本号和模式）。

    最近的一条启动或停止是启动时，上一次运行没有走到停止：先写一条崩溃恢复，写明上一次启动的
    时刻。
    """
    last = log.latest({Action.STARTUP, Action.SHUTDOWN})
    if last is not None and last.action is Action.STARTUP:
        started = f"{last.recorded_at.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
        log.write(
            Actor.SYSTEM,
            Action.CRASH_RECOVERY,
            WHOLE_SYSTEM,
            f"上一次运行 {started} 启动，没有正常停止",
        )
    log.write(Actor.SYSTEM, Action.STARTUP, WHOLE_SYSTEM, text)


def record_shutdown(log: ProgramLog) -> None:
    """写程序日志的停止：关闭数据库连接以前的最后一条。"""
    log.write(Actor.SYSTEM, Action.SHUTDOWN, WHOLE_SYSTEM, "")


def reconcile_at_startup(execution: ExecutionService, log: ProgramLog) -> Reconciliation:
    """对账一次。没有不一致写 INFO；有不一致写 WARNING（带三类不一致的清单）。

    对账跟进过的订单有变化的写程序日志的订单变化，然后写对账。读取券商失败时
    BrokerUnavailableError 原样抛出，由启动顺序转成启动失败。
    """
    with order_changes(execution, log):
        result = execution.reconcile()
    if result.clean:
        _log.info("启动对账：没有不一致")
    else:
        _log.warning("启动对账有不一致，暂停开仓：%r", result)
    log.write(Actor.EXECUTION, Action.RECONCILE, ACCOUNT, reconciliation_text("启动对账", result))
    return result


def judge_health_at_startup(
    health: HealthService, monitor: HealthMonitor, log: ProgramLog
) -> HealthStatus:
    """按 bootstrap spec「第一次健康判定」的信号判定一次；降级时写 WARNING 和程序日志的进入
    降级模式。

    信号和巡检的取法相同（health-monitoring spec「健康信号」），券商连接按正常：启动对账刚读过
    券商。取不到行情的 WARNING 由 monitor 写，取到的时刻由它记下给巡检接着用。
    """
    status = health.check(monitor.signals(broker_connected=True))
    if status.degraded:
        _log.warning("启动时处于降级模式，暂停开仓，异常的检查：%s", checks_text(status.abnormal))
        log.write(
            Actor.SYSTEM,
            Action.ENTER_DEGRADED,
            WHOLE_SYSTEM,
            degraded_line(status, at_startup=True),
        )
    else:
        _log.info("健康：正常")
    return status
