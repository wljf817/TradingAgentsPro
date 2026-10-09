"""跟进审批的用例：审批跟进申请、给出这只股票待执行的跟进、到点触发和股票状态往下走时作废。

见 follow-ups spec。
"""

from datetime import datetime
from uuid import UUID

from tradingagentspro.contracts.conclusion import FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import Catalyst
from tradingagentspro.modules.followups.config import FollowUpConfig
from tradingagentspro.modules.followups.domain.follow_up import FollowUp
from tradingagentspro.modules.followups.domain.rules import (
    FollowUpRejection,
    due,
    in_trigger_order,
    request_rejection,
    same_reason,
    voids_follow_ups,
)
from tradingagentspro.modules.followups.ports import FollowUpStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar


class FollowUpService:
    """跟进审批的入口。

    审批通过时返回新的跟进，不通过时返回原因，不改变任何记录。同一个实例由调用方串行调用：
    上限和次数的检查是先读后写，做法 M4 和股票池一起定（本变更 design D9）。
    """

    def __init__(
        self,
        store: FollowUpStore,
        clock: Clock,
        calendar: TradingCalendar,
        ids: IdGenerator,
        config: FollowUpConfig,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._ids = ids
        self._config = config

    def pending(self, symbol: str) -> tuple[FollowUp, ...]:
        """这只股票待执行的跟进，按触发时间、再按获批的先后。"""
        return tuple(in_trigger_order(self._pending(symbol)))

    def request(
        self, symbol: str, request: FollowUpRequest, case_id: UUID, catalyst: Catalyst
    ) -> FollowUp | FollowUpRejection:
        """审批案件 case_id 的结论附的跟进申请。

        调用方在结论的其他检查都通过以后调用：获批即保存。catalyst 是这份结论生效以后这只股票
        论点里的催化剂。获批时这只股票原因相同的待执行跟进被替换。
        """
        now = self._clock.now()
        pending = self._pending(symbol)
        rejection = request_rejection(
            request,
            catalyst,
            pending,
            self._approved_today(now),
            now,
            self._calendar,
            self._config,
        )
        if rejection is not None:
            return rejection
        old = same_reason(request, pending)
        if old is not None:
            self._store.save(old.replaced())
        follow_up = FollowUp(self._ids.new_id(), symbol, request, case_id, requested_at=now)
        self._store.save(follow_up)
        return follow_up

    def trigger_due(self) -> tuple[FollowUp, ...]:
        """把触发时间不晚于现在的待执行跟进记为已触发，按触发时间、再按获批的先后返回它们。

        返回的就是这次记为已触发的，下一次不会再给：跟进只触发一次。调用方定时调用，每个转成一个
        跟进的触发入队；错过的话，下一次调用照样给出所有已经到点的跟进。
        """
        triggered = tuple(f.triggered() for f in due(self._store.pending(), self._clock.now()))
        for follow_up in triggered:
            self._store.save(follow_up)
        return triggered

    def status_changed(
        self, symbol: str, before: StockStatus | None, after: StockStatus | None
    ) -> tuple[FollowUp, ...]:
        """这只股票的状态从 before 变为 after（不在股票池也不是持仓时为 None）。

        变到更低的状态时把这只股票待执行的跟进都记为已作废，按触发时间、再按获批的先后返回它们；
        其他变化返回空。
        """
        if not voids_follow_ups(before, after):
            return ()
        voided = tuple(f.voided() for f in self.pending(symbol))
        for follow_up in voided:
            self._store.save(follow_up)
        return voided

    def _pending(self, symbol: str) -> list[FollowUp]:
        return [f for f in self._store.pending() if f.symbol == symbol]

    def _approved_today(self, now: datetime) -> int:
        """now 归属的交易日里已经获批的申请次数。这个交易日从上一个交易日的收盘时刻开始。"""
        start = self._calendar.last_session_before(now).close
        return len(self._store.requested_since(start))
