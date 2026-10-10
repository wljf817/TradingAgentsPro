"""收盘对账：什么时候做、找出这一天的差异、算连续没有差异的交易日数。

见 close-reconciliation spec 和 add-close-reconciliation 的 design D3 到 D6。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from tradingagentspro.modules.execution.domain.order import Order, OrderSide, OrderStatus
from tradingagentspro.modules.execution.domain.protection import Protection
from tradingagentspro.modules.execution.domain.reconciliation import Reconciliation
from tradingagentspro.shared.trading_calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class LeftoverOrder:
    """收盘对账开始时还没结束的订单；status 是对账以后的状态。"""

    plan_id: UUID
    symbol: str
    side: OrderSide
    status: OrderStatus


@dataclass(frozen=True, slots=True)
class NewStop:
    """收盘对账补挂或重挂了止损单的股票；client_order_id 是对账以后的当前止损单。"""

    symbol: str
    client_order_id: str


@dataclass(frozen=True, slots=True)
class UnprotectedSpell:
    """一段无保护：股票和无保护开始的时刻。"""

    symbol: str
    since: datetime


@dataclass(frozen=True, slots=True)
class CloseReconciliation:
    """一个交易日的收盘对账记录，见 close-reconciliation spec「收盘对账记录」。

    - finished_at：对账完成的时刻
    - result：这次对账的三类不一致
    - leftover_orders、new_stops、unprotected：另外三类差异，见 spec「差异」
    - holdings：对账以前读到的、券商那边有持仓的股票数
    - resumed：对账以前最近一次完成的对账有不一致，这次没有
    - streak：连续天数，见 spec「连续天数」
    """

    day: date
    finished_at: datetime
    result: Reconciliation
    leftover_orders: tuple[LeftoverOrder, ...]
    new_stops: tuple[NewStop, ...]
    unprotected: tuple[UnprotectedSpell, ...]
    holdings: int
    resumed: bool
    streak: int

    @property
    def clean(self) -> bool:
        """没有差异：三类不一致和另外三类都为空。"""
        return self.result.clean and not (
            self.leftover_orders or self.new_stops or self.unprotected
        )

    @property
    def counted(self) -> bool:
        """这一天计入连续天数：没有差异，收盘时有持仓。"""
        return self.clean and self.holdings > 0


def close_day(calendar: TradingCalendar, now: datetime, delay: timedelta) -> date | None:
    """now 该做哪个交易日的收盘对账，不看做没做过，见 close-reconciliation spec「收盘对账的时机」。

    收盘时刻不晚于 now 的最后一个交易日 D：now 不在盘中、并且不早于 D 收盘加 delay 时为 D，
    否则为 None。不在盘中就一定在 D 收盘和下一个交易日开盘之间。
    """
    if calendar.session_at(now) is not None:
        return None
    last = calendar.last_session_before(now)
    return last.day if now >= last.close + delay else None


def close_window_start(calendar: TradingCalendar, day: date, delay: timedelta) -> datetime:
    """day 的「当天出现过的无保护仓位」从这个时刻算起：上一个交易日收盘加 delay。"""
    return calendar.previous_session(day).close + delay


def leftover_orders(
    before: Iterable[Order], after: Mapping[UUID, Order]
) -> tuple[LeftoverOrder, ...]:
    """对账以前还没结束的每一笔订单，带上对账以后的状态，按创建的先后。

    before 是对账以前的未结束订单，after 是按方案 ID 读到的对账以后的订单。
    """
    ordered = sorted(before, key=lambda o: o.created_at)
    return tuple(
        LeftoverOrder(o.plan_id, o.symbol, o.side, after.get(o.plan_id, o).status) for o in ordered
    )


def new_stops(used: Mapping[str, int], after: Iterable[Protection]) -> tuple[NewStop, ...]:
    """对账以后用过的止损单序号比 used 里大的股票（没有的算 0），按代码排序。

    挂新的止损单一定让序号加 1，改单不变，见 design D4。
    """
    grown = sorted((p for p in after if p.used > used.get(p.symbol, 0)), key=lambda p: p.symbol)
    return tuple(NewStop(p.symbol, p.stop.client_order_id) for p in grown if p.stop is not None)


def unprotected_spells(history: Iterable[Protection]) -> tuple[UnprotectedSpell, ...]:
    """历史版本里出现过的每一段无保护，按股票和开始的时刻排序。

    同一段无保护的开始时刻不变（stop-protection spec「挂单或改单被拒」），所以按（股票, 开始的
    时刻）去重就是每段一条。
    """
    spells = {(p.symbol, p.unprotected_since) for p in history if p.unprotected_since is not None}
    return tuple(UnprotectedSpell(symbol, since) for symbol, since in sorted(spells))


def close_streak(record: CloseReconciliation, previous: CloseReconciliation | None) -> int:
    """这个交易日的连续天数，见 close-reconciliation spec「连续天数」。

    previous 是上一个交易日的记录，没有时为 None。
    """
    if not record.counted:
        return 0
    return 1 if previous is None else previous.streak + 1
