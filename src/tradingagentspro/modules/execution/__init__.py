"""执行模块：按通过下单前再校验的方案提交订单，把订单跟踪到终态。

下单权限只在这个模块里。订单的状态和处理见 openspec 的 order-lifecycle spec。
模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.execution.config import ExecutionConfig
from tradingagentspro.modules.execution.domain.order import (
    BrokerOrder,
    BrokerOrderStatus,
    Order,
    OrderPausedError,
    OrderSide,
    OrderStatus,
    new_order,
)
from tradingagentspro.modules.execution.domain.protection import (
    Protection,
    StopAction,
    StopOrder,
    StopReport,
    StopStatus,
    StopTarget,
    UnprotectedPositionError,
    backup_stop_due,
    next_action,
    stop_price_of,
    stop_target,
    stop_yields,
)
from tradingagentspro.modules.execution.domain.reconciliation import (
    Listing,
    NotReconciledError,
    ReconcileMismatchError,
    Reconciliation,
    WorkingOrder,
    extra_stops,
    position_explained,
    stray_orders,
)
from tradingagentspro.modules.execution.ports import (
    Broker,
    BrokerUnavailableError,
    OrderRejectedError,
    OrderStore,
    ProtectionStore,
    StopBroker,
)
from tradingagentspro.modules.execution.service import ExecutionService

__all__ = [
    "Broker",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerUnavailableError",
    "ExecutionConfig",
    "ExecutionService",
    "Listing",
    "NotReconciledError",
    "Order",
    "OrderPausedError",
    "OrderRejectedError",
    "OrderSide",
    "OrderStatus",
    "OrderStore",
    "Protection",
    "ProtectionStore",
    "ReconcileMismatchError",
    "Reconciliation",
    "StopAction",
    "StopBroker",
    "StopOrder",
    "StopReport",
    "StopStatus",
    "StopTarget",
    "UnprotectedPositionError",
    "WorkingOrder",
    "backup_stop_due",
    "extra_stops",
    "new_order",
    "next_action",
    "position_explained",
    "stop_price_of",
    "stop_target",
    "stop_yields",
    "stray_orders",
]
