"""事件的用例：记下事件、写程序日志的检测到事件和丢弃事件、读事件。

见 event-detection spec「事件的存储」「读取事件」、program-log spec「事件检测层的动作」和
add-event-detection 的 design D3、D14。事件检测层的程序日志由本模块自己写。
"""

from datetime import datetime

from tradingagentspro.contracts.event import Event
from tradingagentspro.contracts.stock_pool import StockStatus
from tradingagentspro.modules.events.domain.entries import detected_text, discarded_text
from tradingagentspro.modules.events.domain.news import DiscardReason
from tradingagentspro.modules.events.ports import EventStore
from tradingagentspro.modules.programlog import Action, Actor, ProgramLog


class EventService:
    """事件的入口。存储和程序日志的错误原样抛出：记下以后写日志出错时，事件不撤回。"""

    def __init__(self, store: EventStore, log: ProgramLog) -> None:
        self._store = store
        self._log = log

    def record(self, event: Event, status: StockStatus) -> bool:
        """记下这个事件；已经有这个事件 ID 时什么都不做，给出 False。新事件写一条检测到事件，
        说明里写这只股票这时的状态 status。
        """
        if not self._store.add(event):
            return False
        self._log.write(
            Actor.EVENT_DETECTION,
            Action.DETECT_EVENT,
            event.event_id,
            detected_text(event, status),
            inputs=event.refs,
        )
        return True

    def discard(self, symbol: str, ref: str, item: str, reason: DiscardReason) -> None:
        """一条新闻或一份公告对这只股票没有成为事件：写一条丢弃事件。ref 是新闻 ID 或 accession
        number，item 是新闻的标题或公告的表单和 item。
        """
        self._log.write(
            Actor.EVENT_DETECTION,
            Action.DISCARD_EVENT,
            symbol,
            discarded_text(item, reason),
            inputs=(ref,),
        )

    def event(self, event_id: str) -> Event | None:
        """这个事件 ID 的事件；没有时为 None。"""
        return self._store.get(event_id)

    def events(self, symbol: str, since: datetime) -> tuple[Event, ...]:
        """这只股票检测时刻不早于 since 的事件，按检测时刻、再按事件 ID。"""
        return self._store.of_symbol(symbol, since)
