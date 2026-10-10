"""订单状态、买卖方向和止损单状态的中文名：巡检和对账的日志、告警共用。"""

from tradingagentspro.modules.execution import OrderSide, OrderStatus, StopStatus

SIDES = {OrderSide.BUY: "买入", OrderSide.SELL: "卖出"}
ORDER_STATUSES = {
    OrderStatus.PENDING: "待提交",
    OrderStatus.SUBMITTED: "已提交",
    OrderStatus.PARTIALLY_FILLED: "部分成交",
    OrderStatus.FILLED: "全部成交",
    OrderStatus.CANCELLED: "已撤销",
    OrderStatus.EXPIRED: "已过期",
    OrderStatus.REJECTED: "已拒绝",
    OrderStatus.UNKNOWN: "状态未知",
}
STOP_STATUSES = {
    StopStatus.PENDING: "还没挂出去",
    StopStatus.WORKING: "挂着",
    StopStatus.UNKNOWN: "状态未知",
    StopStatus.ENDED: "已经结束",
}
