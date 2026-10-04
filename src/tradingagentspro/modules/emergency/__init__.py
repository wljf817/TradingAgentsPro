"""紧急退出：在常规交易时段内判定紧急风险退出是否触发，给出要清仓的股票和代码触发的清仓方案，
并提供当天的单日亏损熔断状态。

规则见 openspec 的 emergency-exit spec。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.emergency.config import EmergencyConfig
from tradingagentspro.modules.emergency.domain.exit import (
    EmergencyExit,
    ExitReason,
    Holding,
    close_plan,
    daily_loss_tripped,
    drop_triggered,
    emergency_exits,
    reference_price,
)
from tradingagentspro.modules.emergency.service import EmergencyService

__all__ = [
    "EmergencyConfig",
    "EmergencyExit",
    "EmergencyService",
    "ExitReason",
    "Holding",
    "close_plan",
    "daily_loss_tripped",
    "drop_triggered",
    "emergency_exits",
    "reference_price",
]
