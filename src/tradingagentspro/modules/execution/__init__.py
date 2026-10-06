"""执行模块：按通过下单前再校验的方案提交订单，把订单跟踪到终态。

下单权限只在这个模块里。订单的状态和处理见 openspec 的 order-lifecycle spec。
订单和止损保护存在状态库里，见 state-store spec。每笔买单、卖单和止损单记一条交易记录，并据此给出
账户数据，见 trade-records spec。模块之外的代码只能 import 这里导出的名字。
"""

from tradingagentspro.modules.execution.config import ExecutionConfig
from tradingagentspro.modules.execution.domain.account import (
    Account,
    BrokerAccount,
    BrokerPosition,
    HeldPosition,
    account_of,
    held_position,
    last_trade_days,
    unsettled_proceeds,
)
from tradingagentspro.modules.execution.domain.order import (
    BrokerOrder,
    BrokerOrderStatus,
    Order,
    OrderPausedError,
    OrderSide,
    OrderStatus,
    new_order,
    opening_order,
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
from tradingagentspro.modules.execution.domain.trade import (
    Trade,
    TradeKind,
    trade_of_order,
    trade_of_stop,
)
from tradingagentspro.modules.execution.ports import (
    Broker,
    BrokerUnavailableError,
    OrderRejectedError,
    OrderStore,
    ProtectionStore,
    StopBroker,
    TradeStore,
)
from tradingagentspro.modules.execution.repo import (
    PostgresOrderStore,
    PostgresProtectionStore,
    PostgresTradeStore,
)
from tradingagentspro.modules.execution.service import ExecutionService

__all__ = [
    "Account",
    "Broker",
    "BrokerAccount",
    "BrokerOrder",
    "BrokerOrderStatus",
    "BrokerPosition",
    "BrokerUnavailableError",
    "ExecutionConfig",
    "ExecutionService",
    "HeldPosition",
    "Listing",
    "NotReconciledError",
    "Order",
    "OrderPausedError",
    "OrderRejectedError",
    "OrderSide",
    "OrderStatus",
    "OrderStore",
    "PostgresOrderStore",
    "PostgresProtectionStore",
    "PostgresTradeStore",
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
    "Trade",
    "TradeKind",
    "TradeStore",
    "UnprotectedPositionError",
    "WorkingOrder",
    "account_of",
    "backup_stop_due",
    "extra_stops",
    "held_position",
    "last_trade_days",
    "new_order",
    "next_action",
    "opening_order",
    "position_explained",
    "stop_price_of",
    "stop_target",
    "stop_yields",
    "stray_orders",
    "trade_of_order",
    "trade_of_stop",
    "unsettled_proceeds",
]
