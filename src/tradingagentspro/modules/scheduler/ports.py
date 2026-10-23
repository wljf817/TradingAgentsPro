"""调度器需要的外部接口：排队的案件的存储。"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from tradingagentspro.modules.scheduler.domain.queue import QueuedCase, QueueExit


class QueueStore(Protocol):
    """排队的案件的存储，见 queue-runner spec「排队的案件的存储」。

    只追加：案件排队时记一笔，出队时另记一笔，写入以后不改不删。数据库的错误原样抛出，不当作
    队列是空的。
    """

    def add(self, case: QueuedCase, enqueued_at: datetime) -> None:
        """记下一个排队的案件；入队的先后就是记下的先后。"""
        ...

    def queued(self) -> tuple[QueuedCase, ...]:
        """排了队、还没出队的案件，按入队的先后。"""
        ...

    def leave(self, case_id: UUID, outcome: QueueExit, at: datetime) -> None:
        """这个案件出队；同一个案件第二次出队报错。"""
        ...

    def sales(self) -> frozenset[str]:
        """排过的卖出成交的客户端订单 ID：全部排过的，出没出队都算。"""
        ...
