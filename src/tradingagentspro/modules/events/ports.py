"""事件模块需要的外部接口：事件的存储。"""

from datetime import datetime
from typing import Protocol

from tradingagentspro.contracts.event import Event


class EventStore(Protocol):
    """事件的存储，见 event-detection spec「事件的存储」。

    只追加：一个事件一行，事件 ID 不重复，写入以后不改不删。数据库的错误和读不出来的事件原样抛出，
    不当作没有事件，也不当作已经记下。
    """

    def add(self, event: Event) -> bool:
        """写入这个事件；已经有这个事件 ID 时不写，给出 False。同时写同一个事件 ID 时只有一个
        给出 True。
        """
        ...

    def get(self, event_id: str) -> Event | None:
        """这个事件 ID 的事件；没有时为 None。"""
        ...

    def of_symbol(self, symbol: str, since: datetime) -> tuple[Event, ...]:
        """这只股票检测时刻不早于 since 的事件，按检测时刻、再按事件 ID。"""
        ...
