"""订单的状态和交易记录的类型，执行模块和历史查询都用。"""

from enum import StrEnum


class OrderStatus(StrEnum):
    """订单的 8 种状态，含义见 order-lifecycle spec「订单状态和券商回报」。"""

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        """全部成交、已撤销、已过期、已拒绝是终态，进入后不再变化。"""
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        )


class TradeKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    STOP = "stop"
