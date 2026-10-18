"""只追加的版本表：状态库所有表共用的写法，见 add-state-store 的 design D1、D2、D4、D5。

每张表按键保存记录的各个版本：内容有变化时写入一个新版本，读取时取最新版本；需要时也能读出
一段时间里用过的版本。
记录整份存成 JSONB；筛选列在保存时从记录算出，列出记录时按最新版本筛选：筛选列等于给定值，或者
几个时刻列至少一列不早于给定时刻。

另有这个模块自己的快照表 statestore_snapshots 和 SnapshotStore 的数据库实现，见 state-snapshot
spec「快照的存储」和 add-state-snapshot 的 design D6。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Select,
    Subquery,
    Table,
    Uuid,
    create_engine,
    insert,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB, distinct_on

from tradingagentspro.contracts.snapshot import Snapshot
from tradingagentspro.shared.clock import Clock


def connect(url: str) -> Engine:
    """连接数据库，会话时区固定为 UTC，以后在 SQL 里按日期计算时不受服务器时区影响。"""
    return create_engine(url, connect_args={"options": "-c timezone=UTC"})


def versioned_table(name: str, key: Column[Any], *columns: Column[Any]) -> Table:
    """只追加的版本表：键、版本号、写入时刻、记录内容，再加上列出时用的筛选列。

    主键是键和版本号。每张表各用一个 MetaData，表之间没有外键。
    """
    return Table(
        name,
        MetaData(),
        key,
        Column("version", Integer, nullable=False),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
        Column("data", JSONB, nullable=False),
        *columns,
        PrimaryKeyConstraint(key.name, "version"),
    )


class VersionedStore[T]:
    """按键保存记录的各个版本，读取最新版本。T 是 domain 的 frozen dataclass。

    数据库的错误原样抛出；存着的记录读不出来时抛 pydantic 的 ValidationError。
    两种情况都不当作没有记录。
    """

    def __init__(self, engine: Engine, clock: Clock, table: Table, record: type[T]) -> None:
        self._engine = engine
        self._clock = clock
        self._table = table
        self._key = next(iter(table.primary_key.columns))
        self._adapter = TypeAdapter(record)

    def save(self, key: object, record: T, **columns: object) -> None:
        """内容和最新版本不同时写入新版本；columns 是这个版本的筛选列的值。

        同一个键的两次保存同时进行时，两次都要写同一个版本号，后完成的那次违反主键而报错。
        """
        data = self._adapter.dump_python(record, mode="json")
        table = self._table
        with self._engine.begin() as connection:
            latest = connection.execute(
                select(table.c.version, table.c.data)
                .where(self._key == key)
                .order_by(table.c.version.desc())
                .limit(1)
            ).first()
            if latest is not None and latest.data == data:
                return
            connection.execute(
                insert(table).values(
                    {
                        self._key.name: key,
                        "version": 1 if latest is None else latest.version + 1,
                        "recorded_at": self._clock.now(),
                        "data": data,
                        **columns,
                    }
                )
            )

    def get(self, key: object) -> T | None:
        """这个键的最新版本；没有时为 None。"""
        query = (
            select(self._table.c.data)
            .where(self._key == key)
            .order_by(self._table.c.version.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            data = connection.execute(query).scalar()
        return None if data is None else self._adapter.validate_python(data)

    def latest(self, **where: object) -> tuple[T, ...]:
        """每个键的最新版本里，筛选列等于给定值的那些，按键排序。"""
        newest = self._newest()
        return self._records(
            select(newest.c.data)
            .where(*(newest.c[name] == value for name, value in where.items()))
            .order_by(newest.c[self._key.name])
        )

    def latest_since(self, since: datetime, *columns: str) -> tuple[T, ...]:
        """每个键的最新版本里，columns 这几个时刻列至少有一列不早于 since 的那些，按键排序。

        为空的时刻列不算。
        """
        newest = self._newest()
        return self._records(
            select(newest.c.data)
            .where(or_(*(newest.c[name] >= since for name in columns)))
            .order_by(newest.c[self._key.name])
        )

    def history(self, since: datetime) -> tuple[T, ...]:
        """since 以来用过的版本：每个键在 since 时的版本，加上写入时刻不早于 since 的全部版本。

        每个键在 since 时的版本是写入时刻早于 since 的最新版本。结果按键、版本号排序。
        见 state-store spec「读取一段时间里的版本」。
        """
        table = self._table
        at_since = (
            select(self._key, table.c.version, table.c.data)
            .where(table.c.recorded_at < since)
            .ext(distinct_on(self._key))
            .order_by(self._key, table.c.version.desc())
            .subquery()
        )
        later = select(self._key, table.c.version, table.c.data).where(table.c.recorded_at >= since)
        used = union_all(select(at_since), later).subquery()
        return self._records(select(used.c.data).order_by(used.c[self._key.name], used.c.version))

    def _newest(self) -> Subquery:
        """每个键的最新版本，带全部的列。"""
        table = self._table
        return (
            select(table)
            .ext(distinct_on(self._key))
            .order_by(self._key, table.c.version.desc())
            .subquery()
        )

    def _records(self, query: Select[Any]) -> tuple[T, ...]:
        """执行只选出记录内容的查询，按查询给出的顺序读回记录。"""
        with self._engine.connect() as connection:
            rows: Sequence[Any] = connection.execute(query).scalars().all()
        return tuple(self._adapter.validate_python(data) for data in rows)


SNAPSHOTS = versioned_table("statestore_snapshots", Column("case_id", Uuid, nullable=False))


class PostgresSnapshotStore:
    """满足 SnapshotStore：键是案件 ID，版本号是快照自己的版本，整份快照存成 JSONB。

    不用 VersionedStore.save：它的版本号是最新的加 1，快照的版本号要和条目 ID 的前缀一致。
    同一版写两次违反主键，IntegrityError 原样抛出；存着的快照读不出来时抛 pydantic 的
    ValidationError。
    """

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def add(self, snapshot: Snapshot) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(SNAPSHOTS).values(
                    case_id=snapshot.case_id,
                    version=snapshot.version,
                    recorded_at=self._clock.now(),
                    data=snapshot.model_dump(mode="json"),
                )
            )

    def get(self, case_id: UUID, version: int) -> Snapshot | None:
        query = select(SNAPSHOTS.c.data).where(
            SNAPSHOTS.c.case_id == case_id, SNAPSHOTS.c.version == version
        )
        return self._one(query)

    def latest(self, case_id: UUID) -> Snapshot | None:
        query = (
            select(SNAPSHOTS.c.data)
            .where(SNAPSHOTS.c.case_id == case_id)
            .order_by(SNAPSHOTS.c.version.desc())
            .limit(1)
        )
        return self._one(query)

    def _one(self, query: Select[Any]) -> Snapshot | None:
        with self._engine.connect() as connection:
            data = connection.execute(query).scalar()
        return None if data is None else Snapshot.model_validate(data)
