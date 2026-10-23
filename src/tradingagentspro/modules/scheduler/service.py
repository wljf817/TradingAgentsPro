"""排队的案件的用例：排队（分配案件 ID）、读队列、出队、排过的卖出成交。

规则见 queue-runner spec「排队」「排队的案件的存储」「排过的卖出成交」和 add-queue-runner 的
design D2。排队时不确定
案件类型、不看股票的状态；程序日志由调用方（排队运行）写。
"""

from datetime import datetime
from uuid import UUID

from tradingagentspro.modules.scheduler.domain.case_type import Trigger
from tradingagentspro.modules.scheduler.domain.queue import QueuedCase, QueueExit
from tradingagentspro.modules.scheduler.ports import QueueStore
from tradingagentspro.shared.clock import Clock
from tradingagentspro.shared.ids import IdGenerator


class QueueService:
    """排队的案件的入口。存储出错时原样抛出：排队时没有存下就不交回案件。"""

    def __init__(self, store: QueueStore, clock: Clock, ids: IdGenerator) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> QueuedCase:
        """排进一个案件：新的案件 ID，入队时刻是现在。同样的触发每次都是一个新的案件。"""
        case = QueuedCase(self._ids.new_id(), trigger, triggered_at)
        self._store.add(case, self._clock.now())
        return case

    def queued(self) -> tuple[QueuedCase, ...]:
        """排了队、还没出队的案件，按入队的先后。"""
        return self._store.queued()

    def leave(self, case_id: UUID, outcome: QueueExit) -> None:
        """这个案件出队，出队时刻是现在；同一个案件只出队一次。"""
        self._store.leave(case_id, outcome, self._clock.now())

    def sales(self) -> frozenset[str]:
        """排过的卖出成交的客户端订单 ID，出没出队都算；事件检测用它让每笔卖出只排一次。"""
        return self._store.sales()
