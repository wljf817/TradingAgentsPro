"""只追加的版本表：状态库所有表共用的写法，见 add-state-store 的 design D1、D2、D4、D5。

每张表按键保存记录的各个版本：内容有变化时写入一个新版本，读取时取最新版本。
记录整份存成 JSONB；筛选列在保存时从记录算出，列出记录时按最新版本筛选。
"""

from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, distinct_on

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
        table = self._table
        newest = (
            select(table)
            .ext(distinct_on(self._key))
            .order_by(self._key, table.c.version.desc())
            .subquery()
        )
        query = (
            select(newest.c.data)
            .where(*(newest.c[name] == value for name, value in where.items()))
            .order_by(newest.c[self._key.name])
        )
        with self._engine.connect() as connection:
            rows: Sequence[Any] = connection.execute(query).scalars().all()
        return tuple(self._adapter.validate_python(data) for data in rows)
