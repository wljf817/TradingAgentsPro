"""事件模块需要的外部接口：事件的存储、行情源的到价提醒。"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from tradingagentspro.contracts.event import Event
from tradingagentspro.modules.events.domain.alerts import AlertChanges, PriceAlert


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


class PriceAlertsUnavailableError(RuntimeError):
    """读不到系统设的到价提醒（行情源超时、断线或出错）：这次没有同步。"""


class PriceAlerts(Protocol):
    """行情源的到价提醒，见 price-alerts spec「同步」「推送」。"""

    def sync(self, wanted: frozenset[PriceAlert]) -> AlertChanges:
        """把行情源上系统设的提醒同步成 wanted：多的删、少的设、一样的不动；不是系统设的不碰。

        读不到系统设的提醒时抛 PriceAlertsUnavailableError，什么都不做；一个提醒设不上或删不掉时
        记进结果，其余照做。
        """
        ...

    def listen(self, on_push: Callable[[], None]) -> None:
        """行情源推送任何一个到价提醒触发时调用 on_push；它在行情源自己的线程里调用。"""
        ...
