"""对账：拿券商的全部挂单和持仓，和执行模块的记录比对，找出不一致。

见 reconciliation spec。账户只给系统用，所以券商那边系统解释不了的挂单和持仓都算不一致。
"""

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from tradingagentspro.modules.execution.domain.order import Order, OrderSide


class NotReconciledError(RuntimeError):
    """执行模块新建以后还没完成过对账，不能为方案新建订单。"""


class ReconcileMismatchError(RuntimeError):
    """最近一次对账有不一致，暂停开仓：不能为买入、加仓方案新建订单。"""


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    """券商那边一笔挂着（已受理、未结束）的订单或止损单。"""

    symbol: str
    client_order_id: str
    broker_order_id: str


@dataclass(frozen=True, slots=True)
class Listing:
    """对账第 1 步读到的券商挂单：挂着的普通订单的券商订单号，挂着止损单的股票。

    跟进和对齐时，受理过却查不到的订单或止损单，挂单里也没有就当作已经结束。
    """

    order_ids: frozenset[str]
    stop_symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """一次对账的结果：三类不一致。

    - stray_orders：不认得的挂单
    - extra_stops：多出来的止损单，即不一致的股票挂着的全部止损单
    - unexplained：来历不明的持仓，每项是（股票, 券商持仓股数）
    """

    stray_orders: tuple[WorkingOrder, ...] = ()
    extra_stops: tuple[WorkingOrder, ...] = ()
    unexplained: tuple[tuple[str, int], ...] = ()

    @property
    def clean(self) -> bool:
        """没有不一致：三类都为空。"""
        return not (self.stray_orders or self.extra_stops or self.unexplained)


def stray_orders(
    working: Iterable[WorkingOrder], orders: Iterable[Order]
) -> tuple[WorkingOrder, ...]:
    """券商挂着、不是执行模块的普通订单，见 reconciliation spec「不认得的挂单」。

    是执行模块的：有一笔未结束的订单，客户端订单 ID 相同，并且它还没有券商订单号、
    或者券商订单号相同。
    """
    open_orders = {o.client_order_id: o for o in orders if not o.status.is_terminal}

    def ours(w: WorkingOrder) -> bool:
        order = open_orders.get(w.client_order_id)
        return order is not None and order.broker_order_id in (None, w.broker_order_id)

    return tuple(w for w in working if not ours(w))


def extra_stops(
    working_stops: Iterable[WorkingOrder], in_use: Collection[str]
) -> tuple[WorkingOrder, ...]:
    """券商挂着的止损单里不一致的那些，见 reconciliation spec「多出来的止损单」。

    一只股票挂着多于一张，或者不在 in_use 里却挂着一张，就返回这只股票挂着的全部止损单。
    只比张数不比订单号：Alpaca 改单会生成订单号不同的新订单。
    in_use 是当前止损单还没挂出去、挂着或状态未知的股票。
    """
    working_stops = tuple(working_stops)
    counts = Counter(s.symbol for s in working_stops)
    return tuple(s for s in working_stops if counts[s.symbol] > 1 or s.symbol not in in_use)


def position_explained(orders: Iterable[Order]) -> bool:
    """这只股票的持仓有来历：有累计成交大于 0 的买单，或者有未结束的买单。

    未结束的买单可能已经成交、只是还没查到。见 reconciliation spec「来历不明的持仓」。
    """
    return any(
        o.side is OrderSide.BUY and (o.filled > 0 or not o.status.is_terminal) for o in orders
    )
