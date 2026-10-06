"""普通订单的 Alpaca 适配器：实现执行模块的 Broker，见 alpaca-broker spec 和 design D5、D6。"""

from collections.abc import Mapping
from decimal import Decimal

from tradingagentspro.integrations.alpaca.client import AlpacaClient, Chain, broker_status
from tradingagentspro.modules.execution import (
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    Order,
    WorkingOrder,
)


class AlpacaBroker:
    """用 Alpaca 实现 Broker：订单都是当日有效单，客户端订单 ID 是方案 ID。

    commission 是每笔佣金，和风控估算用的是同一个配置值：模拟盘不收费，按它估算费用。
    """

    def __init__(self, client: AlpacaClient, commission: Decimal) -> None:
        self._client = client
        self._commission = commission

    def find(self, order: Order) -> BrokerOrder | None:
        found = self._client.order_by_client_id(order.client_order_id)
        return None if found is None else self._report(self._client.chain(found))

    def place(self, order: Order) -> BrokerOrder:
        request = {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "side": order.side.value,
            "type": order.order_type.value,
            "time_in_force": "day",
        }
        if order.limit_price is not None:
            request["limit_price"] = f"{order.limit_price:f}"
        return self._report(Chain.of((self._client.submit(request),)))

    def cancel(self, order: Order) -> None:
        assert order.broker_order_id is not None  # 只撤券商受理过的订单
        self._client.cancel(order.broker_order_id)

    def position(self, symbol: str) -> int:
        held = self._client.position(symbol)
        return 0 if held is None else int(held.qty)

    def working_orders(self) -> tuple[WorkingOrder, ...]:
        return tuple(
            WorkingOrder(o.symbol, o.client_order_id, o.id)
            for o in self._client.open_orders()
            if o.type != "stop"
        )

    def all_positions(self) -> Mapping[str, int]:
        return {p.symbol: int(p.qty) for p in self._client.positions() if p.qty > 0}

    def account(self) -> BrokerAccount:
        account = self._client.account()
        return BrokerAccount(
            equity=account.equity,
            cash=account.cash,
            previous_close_equity=account.last_equity,
            positions=tuple(
                BrokerPosition(p.symbol, int(p.qty), p.avg_entry_price)
                for p in self._client.positions()
                if p.qty > 0
            ),
        )

    def _report(self, chain: Chain) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=chain.current.id,
            status=broker_status(chain.current.status),
            filled=chain.filled,
            average_price=chain.average_price,
            fees=chain.fees(self._commission),
        )
