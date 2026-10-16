"""程序日志的表 programlog_entries，以及 ProgramLogStore 的数据库实现。

一个动作一行，编号自增；只追加，没有修改和删除。见 program-log spec 和 add-program-log 的
design D1。
"""

from collections.abc import Collection
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Identity,
    Index,
    Integer,
    Interval,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY

from tradingagentspro.modules.programlog.domain.entry import Action, Actor, LogEntry

ENTRIES = Table(
    "programlog_entries",
    MetaData(),
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("actor", String, nullable=False),
    Column("action", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("detail", String, nullable=False),
    Column("inputs", ARRAY(String), nullable=False),
    Column("outputs", ARRAY(String), nullable=False),
    Column("case_id", Uuid, nullable=True),
    Column("duration", Interval, nullable=True),
    Column("tokens", Integer, nullable=True),
    Column("cost", Numeric, nullable=True),
    Index("programlog_entries_recorded_at", "recorded_at"),
)


class PostgresProgramLogStore:
    """满足 ProgramLogStore：条目存在状态库的 programlog_entries 表里，追加一条一个事务。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, entry: LogEntry) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(ENTRIES).values(
                    recorded_at=entry.recorded_at,
                    actor=entry.actor.value,
                    action=entry.action.value,
                    subject=entry.subject,
                    detail=entry.detail,
                    inputs=list(entry.inputs),
                    outputs=list(entry.outputs),
                    case_id=entry.case_id,
                    duration=entry.duration,
                    tokens=entry.tokens,
                    cost=entry.cost,
                )
            )

    def latest(self, actions: Collection[Action]) -> LogEntry | None:
        query = (
            select(ENTRIES)
            .where(ENTRIES.c.action.in_([action.value for action in actions]))
            .order_by(ENTRIES.c.id.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(query).first()
        return None if row is None else _entry(row)


def _entry(row: Any) -> LogEntry:
    return LogEntry(
        recorded_at=row.recorded_at,
        actor=Actor(row.actor),
        action=Action(row.action),
        subject=row.subject,
        detail=row.detail,
        inputs=tuple(row.inputs),
        outputs=tuple(row.outputs),
        case_id=row.case_id,
        duration=row.duration,
        tokens=row.tokens,
        cost=row.cost,
    )
