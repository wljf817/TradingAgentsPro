"""启动对账和第一次健康判定：启动时、Discord 和 DBOS 之前各做一次，这里只写日志。

见 bootstrap spec「启动对账」「第一次健康判定」和 add-bootstrap-wiring 的 design D7。
启动对账有不一致时的告警由 app.py 在 Discord 连上以后发出（add-close-reconciliation 的
design D10）；健康的告警由「健康监控接线」补上。
"""

import logging

from tradingagentspro.modules.execution import ExecutionService, Reconciliation
from tradingagentspro.modules.health import HealthService, HealthSignals, HealthStatus
from tradingagentspro.modules.marketdata import MarketDataService, QuoteUnavailableError
from tradingagentspro.shared.clock import Clock

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


def judge_health_at_startup(
    health: HealthService, market_data: MarketDataService, clock: Clock
) -> HealthStatus:
    """按 bootstrap spec「第一次健康判定」的信号判定一次；取不到行情、降级时写 WARNING。

    模型、预算、调度器还没有接入，按正常传入；券商连接按正常：启动对账刚读过券商。
    """
    try:
        last_quote_at = market_data.last_update()
    except QuoteUnavailableError as error:
        _log.warning("启动时取不到行情（%s），行情最近一次更新的时刻按没有处理", error)
        last_quote_at = None
    status = health.check(
        HealthSignals(
            pm_model_available=True,
            critic_model_available=True,
            broker_connected=True,
            last_quote_at=last_quote_at,
            reserve_budget_exhausted=False,
            scheduler_heartbeat_at=clock.now(),
        )
    )
    if status.degraded:
        abnormal = "、".join(sorted(status.abnormal))
        _log.warning("启动时处于降级模式，暂停开仓，异常的检查：%s", abnormal)
    else:
        _log.info("健康：正常")
    return status
