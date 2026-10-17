"""跟进审批的用例：审批跟进申请、给出这只股票待执行的跟进、到点触发和股票状态往下走时作废。

四个公开方法一次只进行一个；每个动作在保存以后写一条程序日志，行为者是调度器。见 follow-ups spec
和 program-log spec「调度器的动作」。
"""

import threading
from datetime import datetime
from uuid import UUID

from tradingagentspro.contracts.conclusion import FollowUpRequest
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.contracts.thesis import Catalyst
from tradingagentspro.modules.followups.config import FollowUpConfig
from tradingagentspro.modules.followups.domain.entries import (
    content,
    rejection_detail,
    void_detail,
)
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
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator
from tradingagentspro.shared.trading_calendar import TradingCalendar


class FollowUpService:
    """跟进审批的入口。

    审批通过时返回新的跟进，不通过时返回原因，不改变任何跟进的记录。每个动作在保存以后写一条
    程序日志，每保存一个写一条。

    四个公开方法拿同一把可重入锁，从读时钟、读存储到保存、写条目都在锁里，一次只进行一个：上限和
    次数的检查是先读后写，几个调用方（案件、定时任务、股票池的变化）同时调用也不会超出，同一个
    跟进的状态只变化一次（spec「同时进行的调用」）。拿着锁时只读写存储、写程序日志，不拿别的锁。
    """

    def __init__(
        self,
        store: FollowUpStore,
        clock: Clock,
        calendar: TradingCalendar,
        ids: IdGenerator,
        config: FollowUpConfig,
        log: ProgramLog,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._ids = ids
        self._config = config
        self._program_log = log
        self._lock = threading.RLock()  # status_changed 里调用 pending，同一个线程再次拿锁

    def pending(self, symbol: str) -> tuple[FollowUp, ...]:
        """这只股票待执行的跟进，按触发时间、再按获批的先后。"""
        with self._lock:
            return tuple(in_trigger_order(self._pending(symbol)))

    def request(
        self, symbol: str, request: FollowUpRequest, case_id: UUID, catalyst: Catalyst
    ) -> FollowUp | FollowUpRejection:
        """审批案件 case_id 的结论附的跟进申请。

        调用方在结论的其他检查都通过以后调用：获批即保存。catalyst 是这份结论生效以后这只股票
        论点里的催化剂。获批时这只股票原因相同的待执行跟进被替换。
        """
        with self._lock:
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
                self._program_log.write(
                    Actor.SCHEDULER,
                    Action.REJECT_FOLLOW_UP,
                    symbol,
                    rejection_detail(rejection, symbol, request),
                    case_id=case_id,
                )
                return rejection
            old = same_reason(request, pending)
            if old is not None:
                self._store.save(old.replaced())
            follow_up = FollowUp(self._ids.new_id(), symbol, request, case_id, requested_at=now)
            self._store.save(follow_up)
            new_id = str(follow_up.follow_up_id)
            self._program_log.write(
                Actor.SCHEDULER,
                Action.APPROVE_FOLLOW_UP,
                new_id,
                content(symbol, request),
                case_id=case_id,
            )
            if old is not None:
                self._program_log.write(
                    Actor.SCHEDULER,
                    Action.REPLACE_FOLLOW_UP,
                    str(old.follow_up_id),
                    content(old.symbol, old.request),
                    inputs=[new_id],
                    case_id=case_id,
                )
            return follow_up

    def trigger_due(self) -> tuple[FollowUp, ...]:
        """把触发时间不晚于现在的待执行跟进记为已触发，按触发时间、再按获批的先后返回它们。

        返回的就是这次记为已触发的，下一次不会再给：跟进只触发一次。调用方定时调用，每个转成一个
        跟进的触发入队；错过的话，下一次调用照样给出所有已经到点的跟进。
        """
        with self._lock:
            triggered: list[FollowUp] = []
            for follow_up in due(self._store.pending(), self._clock.now()):
                done = follow_up.triggered()
                self._store.save(done)
                self._program_log.write(
                    Actor.SCHEDULER,
                    Action.TRIGGER_FOLLOW_UP,
                    str(done.follow_up_id),
                    content(done.symbol, done.request),
                )
                triggered.append(done)
            return tuple(triggered)

    def status_changed(
        self, symbol: str, before: StockStatus | None, after: StockStatus | None
    ) -> tuple[FollowUp, ...]:
        """这只股票的状态从 before 变为 after（不在股票池也不是持仓时为 None）。

        变到更低的状态时把这只股票待执行的跟进都记为已作废，按触发时间、再按获批的先后返回它们；
        其他变化返回空。
        """
        with self._lock:
            if not voids_follow_ups(before, after):
                return ()
            voided: list[FollowUp] = []
            for follow_up in self.pending(symbol):
                done = follow_up.voided()
                self._store.save(done)
                self._program_log.write(
                    Actor.SCHEDULER,
                    Action.VOID_FOLLOW_UP,
                    str(done.follow_up_id),
                    void_detail(before, after, done),
                )
                voided.append(done)
            return tuple(voided)

    def _pending(self, symbol: str) -> list[FollowUp]:
        return [f for f in self._store.pending() if f.symbol == symbol]

    def _approved_today(self, now: datetime) -> int:
        """now 归属的交易日里已经获批的申请次数。这个交易日从上一个交易日的收盘时刻开始。"""
        start = self._calendar.last_session_before(now).close
        return len(self._store.requested_since(start))
