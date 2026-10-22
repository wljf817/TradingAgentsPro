"""调度器的表：排队表 scheduler_queue 和出队表 scheduler_queue_exits，以及 QueueStore 的数据库实现。

两张表都只追加、一行一条，写入以后不改：案件排队时排队表一行（position 是入队的先后），启动或者
不再运行时出队表一行（主键是案件 ID，所以只出队一次）。触发存成带种类的 JSON，读回时按种类校验，
不让 pydantic 在四种触发之间猜。见 queue-runner spec「排队的案件的存储」和 add-queue-runner 的
design D2。
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Identity,
    MetaData,
    String,
    Table,
    Uuid,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB

from tradingagentspro.modules.scheduler.domain.case_type import (
    EventTrigger,
    FollowUpTrigger,
    HeartbeatTrigger,
    SellTrigger,
    Trigger,
)
from tradingagentspro.modules.scheduler.domain.queue import QueuedCase, QueueExit

QUEUE = Table(
    "scheduler_queue",
    MetaData(),
    Column("case_id", Uuid, primary_key=True),
    Column("position", BigInteger, Identity(always=True), nullable=False),
    Column("trigger", JSONB, nullable=False),
    Column("triggered_at", DateTime(timezone=True), nullable=False),
    Column("enqueued_at", DateTime(timezone=True), nullable=False),
)

EXITS = Table(
    "scheduler_queue_exits",
    MetaData(),
    Column("case_id", Uuid, primary_key=True),
    Column("outcome", String, nullable=False),
    Column("exited_at", DateTime(timezone=True), nullable=False),
)

# 触发的种类和它的 dataclass
_KINDS: Final[dict[str, TypeAdapter[Any]]] = {
    "event": TypeAdapter(EventTrigger),
    "heartbeat": TypeAdapter(HeartbeatTrigger),
    "follow_up": TypeAdapter(FollowUpTrigger),
    "sell": TypeAdapter(SellTrigger),
}


class PostgresQueueStore:
    """满足 QueueStore：每个操作一个事务；数据库的错误、读不出来的触发原样抛出。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, case: QueuedCase, enqueued_at: datetime) -> None:
        values = {
            "case_id": case.case_id,
            "trigger": _encode(case.trigger),
            "triggered_at": case.triggered_at,
            "enqueued_at": enqueued_at,
        }
        with self._engine.begin() as connection:
            connection.execute(insert(QUEUE).values(values))

    def queued(self) -> tuple[QueuedCase, ...]:
        query = (
            select(QUEUE.c.case_id, QUEUE.c.trigger, QUEUE.c.triggered_at)
            .outerjoin(EXITS, EXITS.c.case_id == QUEUE.c.case_id)
            .where(EXITS.c.case_id.is_(None))
            .order_by(QUEUE.c.position)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return tuple(
            QueuedCase(row.case_id, _decode(row.trigger), row.triggered_at) for row in rows
        )

    def leave(self, case_id: UUID, outcome: QueueExit, at: datetime) -> None:
        """同一个案件第二次出队违反主键，IntegrityError 原样抛出。"""
        values = {"case_id": case_id, "outcome": outcome.value, "exited_at": at}
        with self._engine.begin() as connection:
            connection.execute(insert(EXITS).values(values))


def _encode(trigger: Trigger) -> dict[str, Any]:
    """触发的种类加上它的字段。"""
    match trigger:
        case EventTrigger():
            kind = "event"
        case HeartbeatTrigger():
            kind = "heartbeat"
        case FollowUpTrigger():
            kind = "follow_up"
        case SellTrigger():
            kind = "sell"
    fields: dict[str, Any] = _KINDS[kind].dump_python(trigger, mode="json")
    return {"kind": kind, **fields}


def _decode(data: dict[str, Any]) -> Trigger:
    """按种类校验存着的触发。"""
    fields = dict(data)
    kind = fields.pop("kind")
    trigger: Trigger = _KINDS[kind].validate_python(fields)
    return trigger
