"""事件表 events_detected：一个事件一行，主键是事件 ID；只追加，没有修改和删除。

见 event-detection spec「事件的存储」和 add-event-detection 的 design D3、D16。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Engine, Index, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tradingagentspro.contracts.event import Event

EVENTS = Table(
    "events_detected",
    MetaData(),
    Column("event_id", String, primary_key=True),
    Column("symbol", String, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    Column("event", JSONB, nullable=False),
    Index("events_detected_symbol", "symbol", "detected_at"),
)


class PostgresEventStore:
    """满足 EventStore：事件整份存成 JSON。每个操作一个事务；数据库的错误、读不出来的事件原样
    抛出。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, event: Event) -> bool:
        """另一个事务正在写同一个事件 ID 时等它结束，然后什么都不做。"""
        write = (
            pg_insert(EVENTS)
            .values(
                event_id=event.event_id,
                symbol=event.symbol,
                detected_at=event.detected_at,
                event=event.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=[EVENTS.c.event_id])
            .returning(EVENTS.c.event_id)
        )
        with self._engine.begin() as connection:
            return connection.execute(write).first() is not None

    def get(self, event_id: str) -> Event | None:
        query = select(EVENTS.c.event).where(EVENTS.c.event_id == event_id)
        with self._engine.connect() as connection:
            data = connection.execute(query).scalar_one_or_none()
        return None if data is None else Event.model_validate(data)

    def of_symbol(self, symbol: str, since: datetime) -> tuple[Event, ...]:
        query = (
            select(EVENTS.c.event)
            .where(EVENTS.c.symbol == symbol, EVENTS.c.detected_at >= since)
            .order_by(EVENTS.c.detected_at, EVENTS.c.event_id)
        )
        with self._engine.connect() as connection:
            rows: Sequence[Any] = connection.execute(query).scalars().all()
        return tuple(Event.model_validate(data) for data in rows)
