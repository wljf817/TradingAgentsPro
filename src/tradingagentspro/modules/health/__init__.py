"""健康监控：根据模型 API、券商连接、数据延迟、调用预算和调度器心跳判定是否进入降级模式，
全部恢复并完成一次对账后退出，并给出恢复后的第一个交易日。

规则见 openspec 的 degraded-mode spec。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.health.config import HealthConfig
from tradingagentspro.modules.health.domain.health import (
    HealthCheck,
    HealthMode,
    HealthSignals,
    HealthStatus,
    abnormal_checks,
    recovery_day,
)
from tradingagentspro.modules.health.service import HealthService

__all__ = [
    "HealthCheck",
    "HealthConfig",
    "HealthMode",
    "HealthService",
    "HealthSignals",
    "HealthStatus",
    "abnormal_checks",
    "recovery_day",
]
