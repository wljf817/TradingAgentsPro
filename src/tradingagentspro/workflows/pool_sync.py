"""股票池跟上持仓：盘中巡检每一轮按券商持仓和订单，把买入成交和清仓交给股票池，状态变了再交给
跟进审批。

见 pool-position-sync spec 和 update-order-flow-case-plans 的 design D7。按状态比对：股票池的状态和
券商有没有持仓对不上的股票才处理，漏掉的一轮、停机以后下一轮照样补上。投资论点、清仓后的去向和
关注论点取订单的来源案件在案件记录里的结论。/plan 录入的方案没有来源案件：它的买入不进股票池，
股票池里的持仓不论被谁卖完，只要不是案件的清仓方案全部成交，都按代码触发的清仓转为候选。
股票池和跟进审批各自写程序日志，这里不另写。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from tradingagentspro.contracts.conclusion import (
    AfterClose,
    Decision,
    HoldingConclusion,
    WatchConclusion,
)
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.caserecords import CaseConclusion, CaseRecordService
from tradingagentspro.modules.execution import (
    BrokerUnavailableError,
    ExecutionService,
    Order,
    OrderSide,
    OrderStatus,
)
from tradingagentspro.modules.followups import FollowUpService
from tradingagentspro.modules.stockpool import PoolEntry, StockPoolService
from tradingagentspro.shared.trading_calendar import TradingCalendar

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Bought:
    """候选或关注有了持仓，最近创建的买单来自案件 case_id。"""

    case_id: UUID


@dataclass(frozen=True, slots=True)
class SoldByCase:
    """持仓卖完，最近创建的订单是案件 case_id 的卖单并且全部成交；是不是清仓看那个案件的结论。"""

    case_id: UUID


@dataclass(frozen=True, slots=True)
class ClosedByCode:
    """持仓卖完，不是案件的卖单全部成交：转为候选。"""


type PoolChange = Bought | SoldByCase | ClosedByCode


def pool_change(status: StockStatus, held: bool, orders: Sequence[Order]) -> PoolChange | None:
    """按 pool-position-sync spec「股票池跟上持仓」的表，orders 是这只股票的全部订单。

    None 是不动：没有变化、/plan 的买入，或者持仓卖完了但还有没结束的订单。
    """
    if status is StockStatus.HOLDING:
        if held or any(not o.status.is_terminal for o in orders):
            return None
        latest = _latest(orders)
        if (
            latest is not None
            and latest.side is OrderSide.SELL
            and latest.status is OrderStatus.FILLED
            and latest.case_id is not None
        ):
            return SoldByCase(latest.case_id)
        return ClosedByCode()
    if not held:
        return None
    buy = _latest([o for o in orders if o.side is OrderSide.BUY])
    if buy is None or buy.case_id is None:
        return None
    return Bought(buy.case_id)


def _latest(orders: Sequence[Order]) -> Order | None:
    return max(orders, key=lambda o: o.created_at, default=None)


class PoolSync:
    """股票池跟上持仓，见 pool-position-sync spec。只在巡检的线程里调用，不拿全程序共用的那把锁：
    股票池和跟进各有自己的锁，执行模块只读。
    """

    def __init__(
        self,
        execution: ExecutionService,
        pool: StockPoolService,
        follow_ups: FollowUpService,
        case_records: CaseRecordService,
        calendar: TradingCalendar,
    ) -> None:
        self._execution = execution
        self._pool = pool
        self._follow_ups = follow_ups
        self._case_records = case_records
        self._calendar = calendar

    def targets(self) -> tuple[tuple[PoolEntry, bool], ...]:
        """状态和券商持仓对不上的股票和它有没有持仓，按股票代码排序。

        股票池为空时不读券商；读券商的全部持仓超时或断线时写 WARNING，给出空：这一轮不做。
        """
        entries = self._pool.entries()
        if not entries:
            return ()
        try:
            held = self._execution.held_symbols()
        except BrokerUnavailableError as error:
            _log.warning("股票池跟上持仓：读取券商持仓失败（%s），这一轮不做", error)
            return ()
        return tuple(
            (e, e.symbol in held)
            for e in sorted(entries, key=lambda e: e.symbol)
            if (e.status is StockStatus.HOLDING) != (e.symbol in held)
        )

    def follow(self, entry: PoolEntry, held: bool) -> None:
        """一只股票：按它的订单决定买入成交、清仓或者不动；状态变了以后交给跟进审批。"""
        symbol = entry.symbol
        match pool_change(entry.status, held, self._execution.orders(symbol)):
            case None:
                return
            case Bought(case_id=case_id):
                concluded = self._conclusion(case_id)
                conclusion = concluded.conclusion
                assert isinstance(conclusion, WatchConclusion)  # 开仓方案只来自关注的结论
                assert conclusion.thesis is not None  # 开仓方案都附投资论点
                after = self._pool.bought(symbol, conclusion.thesis, self._day(concluded.at))
            case SoldByCase(case_id=case_id):
                after = self._sold(symbol, self._conclusion(case_id))
            case ClosedByCode():
                after = self._pool.closed_by_code(symbol)
        self._follow_ups.status_changed(
            symbol, entry.status, after.status if after.in_pool else None
        )

    def _sold(self, symbol: str, concluded: CaseConclusion) -> PoolEntry:
        """案件的卖单卖完了持仓：清仓的结论按去向，减仓卖完的按代码触发的清仓。"""
        conclusion = concluded.conclusion
        if (
            not isinstance(conclusion, HoldingConclusion)
            or conclusion.decision is not Decision.CLOSE
        ):
            return self._pool.closed_by_code(symbol)
        if conclusion.after_close is AfterClose.REMOVE:
            return self._pool.closed_and_removed(symbol)
        assert conclusion.watch_thesis is not None  # 转为关注的清仓都附关注论点
        return self._pool.closed_to_watch(symbol, conclusion.watch_thesis, self._day(concluded.at))

    def _conclusion(self, case_id: UUID) -> CaseConclusion:
        """订单的来源案件的结论：有方案的案件都写过结论。"""
        conclusion = self._case_records.case(case_id).conclusion
        assert conclusion is not None
        return conclusion

    def _day(self, moment: datetime) -> date:
        """结论时刻归属的交易日：盘后给出的结论归下一个交易日。"""
        return self._calendar.session_closing_after(moment).day
