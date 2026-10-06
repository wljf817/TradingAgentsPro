"""止损单的 Alpaca 适配器：实现执行模块的 StopBroker，见 alpaca-broker spec 和 design D4 到 D6。"""

from decimal import Decimal

from tradingagentspro.integrations.alpaca.client import AlpacaClient, Chain, broker_status
from tradingagentspro.modules.execution import StopOrder, StopReport, WorkingOrder


class AlpacaStopBroker:
    """用 Alpaca 实现 StopBroker：止损单是 GTC 的止损市价卖单（type=stop），只在常规交易时段触发。

    Alpaca 改单会生成新订单替换旧单，查询时沿替换找到现在的那一笔，累计成交包括替换之前的，
    见 design D4。commission 是每笔佣金，止损单连同替换只收一笔。
    """

    def __init__(self, client: AlpacaClient, commission: Decimal) -> None:
        self._client = client
        self._commission = commission

    def find(self, stop: StopOrder) -> StopReport | None:
        if stop.broker_order_id is not None:
            found = self._client.order(stop.broker_order_id)
        else:
            found = self._client.order_by_client_id(stop.client_order_id)
        return None if found is None else self._report(self._client.chain(found))

    def place(self, stop: StopOrder) -> StopReport:
        request = {
            "client_order_id": stop.client_order_id,
            "symbol": stop.symbol,
            "qty": str(stop.quantity),
            "side": "sell",
            "type": "stop",
            "stop_price": f"{stop.stop_price:f}",
            "time_in_force": "gtc",
        }
        return self._report(Chain.of((self._client.submit(request),)))

    def modify(self, stop: StopOrder, quantity: int, stop_price: Decimal) -> None:
        assert stop.broker_order_id is not None  # 只改券商受理过的止损单
        self._client.replace(
            stop.broker_order_id, {"qty": str(quantity), "stop_price": f"{stop_price:f}"}
        )

    def cancel(self, stop: StopOrder) -> None:
        assert stop.broker_order_id is not None  # 只撤券商受理过的止损单
        self._client.cancel(stop.broker_order_id)

    def working_stops(self) -> tuple[WorkingOrder, ...]:
        """未结束的止损单；改单还在进行中时新旧两笔都未结束，去掉被替换的旧单。"""
        stops = [o for o in self._client.open_orders() if o.type == "stop"]
        replaced = {o.replaces for o in stops}
        return tuple(
            WorkingOrder(o.symbol, o.client_order_id, o.id) for o in stops if o.id not in replaced
        )

    def _report(self, chain: Chain) -> StopReport:
        current = chain.current
        assert current.qty is not None and current.stop_price is not None  # 止损单都有
        return StopReport(
            broker_order_id=current.id,
            status=broker_status(current.status),
            quantity=int(current.qty - current.filled_qty),
            stop_price=current.stop_price,
            filled=chain.filled,
            average_price=chain.average_price,
            fees=chain.fees(self._commission),
        )
