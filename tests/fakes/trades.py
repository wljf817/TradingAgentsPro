"""测试用的交易记录存储：存在内存里，按客户端订单 ID 保存每条交易记录的最新状态。"""

from tradingagentspro.modules.execution import Trade


class InMemoryTradeStore:
    """满足执行模块的 TradeStore。用同一个实例重新创建执行用例，相当于进程重启。"""

    def __init__(self) -> None:
        self._trades: dict[str, Trade] = {}

    def get(self, client_order_id: str) -> Trade | None:
        return self._trades.get(client_order_id)

    def traded(self) -> tuple[Trade, ...]:
        return tuple(t for t in self._trades.values() if t.filled > 0)

    def save(self, trade: Trade) -> None:
        self._trades[trade.client_order_id] = trade
