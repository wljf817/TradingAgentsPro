"""状态库公共写法的测试：只追加的版本记录、读取最新版本、原样读回、重启和出错。

每个测试对应 openspec 的 state-store spec 里的一个场景。spec 场景里的"订单"在这里换成
测试专用的记录 Note：这些场景只关心版本，和记录是什么无关。三张真实的表各自怎样列出记录，
见 tests/modules/risk/test_repo.py 和 tests/modules/execution/test_repo.py。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from threading import Thread

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Boolean, Column, Engine, String, insert, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from tests.fakes.clock import FakeClock
from tradingagentspro.modules.statestore import VersionedStore, connect, versioned_table


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Leg:
    venue: str
    price: Decimal


@dataclass(frozen=True, slots=True)
class Note:
    """测试专用的记录。status 对应场景里订单的状态；active 是列出时用的筛选条件。"""

    key: str
    status: str
    active: bool = True
    amount: Decimal = Decimal(0)
    due: datetime | None = None
    side: Side = Side.BUY
    legs: tuple[Leg, ...] = ()


NOTES = versioned_table(
    "test_notes",
    Column("key", String, nullable=False),
    Column("active", Boolean, nullable=False),
)
NOTE = TypeAdapter(Note)


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """2026-10-14 的某个 UTC 时刻。"""
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(_at(15))


@pytest.fixture
def engine(empty_database: str) -> Iterator[Engine]:
    """建好测试表的空数据库。"""
    engine = connect(empty_database)
    NOTES.create(engine)
    yield engine
    engine.dispose()


def _store(engine: Engine, clock: FakeClock) -> VersionedStore[Note]:
    return VersionedStore(engine, clock, NOTES, Note)


def _save(store: VersionedStore[Note], note: Note) -> None:
    store.save(note.key, note, active=note.active)


def _versions(engine: Engine, key: str) -> list[tuple[int, datetime, Note]]:
    """直接查表：这个键每个版本的版本号、写入时刻和内容，按版本号排序。"""
    query = (
        select(NOTES.c.version, NOTES.c.recorded_at, NOTES.c.data)
        .where(NOTES.c.key == key)
        .order_by(NOTES.c.version)
    )
    with engine.connect() as connection:
        rows = connection.execute(query).all()
    return [(row.version, row.recorded_at, NOTE.validate_python(row.data)) for row in rows]


# 只追加的版本记录


def test_first_save_writes_version_one(engine: Engine, clock: FakeClock) -> None:
    """spec「第一次保存」。"""
    _save(_store(engine, clock), Note("P1", "pending"))

    assert _versions(engine, "P1") == [(1, _at(15), Note("P1", "pending"))]


def test_a_change_appends_a_new_version(engine: Engine, clock: FakeClock) -> None:
    """spec「内容变化时追加新版本」。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    clock.set(_at(15, 0, 2))
    _save(store, Note("P1", "submitted"))

    assert _versions(engine, "P1") == [
        (1, _at(15), Note("P1", "pending")),
        (2, _at(15, 0, 2), Note("P1", "submitted")),
    ]


def test_an_unchanged_record_is_not_written(engine: Engine, clock: FakeClock) -> None:
    """spec「内容没变时不写入」。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    clock.set(_at(15, 0, 2))
    _save(store, Note("P1", "submitted"))
    clock.set(_at(15, 0, 5))
    _save(store, Note("P1", "submitted"))

    versions = [(version, at) for version, at, _ in _versions(engine, "P1")]
    assert versions == [(1, _at(15)), (2, _at(15, 0, 2))]


def test_each_key_numbers_its_own_versions(engine: Engine, clock: FakeClock) -> None:
    """spec「不同的键各自编号」。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    _save(store, Note("P1", "submitted"))
    _save(store, Note("P2", "pending"))

    assert [version for version, _, _ in _versions(engine, "P2")] == [1]


def test_simultaneous_saves_of_one_key(engine: Engine, clock: FakeClock) -> None:
    """spec「同一个键同时保存」：先完成的那次写入版本 2，后完成的那次报错。

    一个连接开着事务插入版本 2，当作先完成的保存；另一个线程保存同一个键，也要写版本 2，
    在主键上等待。确认它在锁上等待以后，才提交事务。
    """
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    errors: list[Exception] = []

    def save_rejected() -> None:
        try:
            _save(store, Note("P1", "rejected"))
        except Exception as error:
            errors.append(error)

    with engine.begin() as first:
        first.execute(
            insert(NOTES).values(
                key="P1",
                version=2,
                recorded_at=_at(15, 0, 2),
                data=NOTE.dump_python(Note("P1", "submitted"), mode="json"),
                active=True,
            )
        )
        second = Thread(target=save_rejected)
        second.start()
        _wait_for_a_lock_wait(engine)
    second.join()

    assert len(errors) == 1
    assert isinstance(errors[0], IntegrityError)
    statuses = [(version, note.status) for version, _, note in _versions(engine, "P1")]
    assert statuses == [(1, "pending"), (2, "submitted")]


def _wait_for_a_lock_wait(engine: Engine) -> None:
    """等到这个数据库里有一个会话在等锁，最多查 10000 次。

    每次查询单独一个事务：pg_stat_activity 在同一个事务里只取一次快照。
    """
    query = text(
        "select count(*) from pg_stat_activity "
        "where datname = current_database() and wait_event_type = 'Lock'"
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for _ in range(10_000):
            if connection.execute(query).scalar_one() > 0:
                return
    pytest.fail("另一个保存没有在锁上等待")


# 读取最新版本


def test_get_reads_the_latest_version(engine: Engine, clock: FakeClock) -> None:
    """spec「读到最新版本」。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    _save(store, Note("P1", "submitted"))

    assert store.get("P1") == Note("P1", "submitted")


def test_get_without_the_key_is_none(engine: Engine, clock: FakeClock) -> None:
    """spec「没有这个键」。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))

    assert store.get("P9") is None


def test_listing_an_empty_table(engine: Engine, clock: FakeClock) -> None:
    """spec「表里没有记录时列出」。"""
    store = _store(engine, clock)

    assert store.latest(active=True) == ()
    assert store.latest() == ()


def test_listing_looks_only_at_the_latest_version(engine: Engine, clock: FakeClock) -> None:
    """spec「读取最新版本」：旧版本满足、最新版本不满足的键不列出；反过来的列出。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "submitted"))
    _save(store, Note("P1", "filled", active=False))
    _save(store, Note("P2", "ended", active=False))
    _save(store, Note("P2", "reopened"))

    assert store.latest(active=True) == (Note("P2", "reopened"),)
    assert store.latest(active=False) == (Note("P1", "filled", active=False),)


def test_listing_is_sorted_by_key(engine: Engine, clock: FakeClock) -> None:
    store = _store(engine, clock)
    for key in ("P3", "P1", "P2"):
        _save(store, Note(key, "pending"))

    assert [note.key for note in store.latest()] == ["P1", "P2", "P3"]


# 记录原样读回


def test_records_read_back_unchanged(engine: Engine, clock: FakeClock) -> None:
    """spec「记录原样读回」：每一位小数、时刻、没填的字段、嵌套的记录和枚举都不变。"""
    store = _store(engine, clock)
    full = Note(
        "P1",
        "partially_filled",
        amount=Decimal("50.0066666667"),
        due=datetime(2026, 10, 15, 10, tzinfo=timezone(timedelta(hours=14))),
        side=Side.SELL,
        legs=(Leg("IEX", Decimal("45.10")), Leg("NYSE", Decimal("0.0001"))),
    )
    _save(store, full)
    _save(store, Note("P2", "pending"))

    read = store.get("P1")
    assert read == full
    assert read is not None
    assert str(read.amount) == "50.0066666667"
    assert str(read.legs[0].price) == "45.10"
    assert read.due == _at(20)
    assert store.get("P2") == Note("P2", "pending")


# 重启后读到同样的状态


def test_a_new_store_on_the_same_database_continues(
    empty_database: str, engine: Engine, clock: FakeClock
) -> None:
    """spec「重启后继续」：新建 Engine 和存储，读到之前的记录，版本号接着往后写。"""
    store = _store(engine, clock)
    _save(store, Note("P1", "pending"))
    _save(store, Note("P1", "submitted"))

    restarted = connect(empty_database)
    try:
        again = _store(restarted, clock)
        assert again.get("P1") == Note("P1", "submitted")
        _save(again, Note("P1", "filled", active=False))
    finally:
        restarted.dispose()

    statuses = [(version, note.status) for version, _, note in _versions(engine, "P1")]
    assert statuses == [(1, "pending"), (2, "submitted"), (3, "filled")]


# 读写出错时报错


def test_unreachable_database_raises(clock: FakeClock) -> None:
    """spec「数据库连不上」：读取、列出、保存都报错，读取不返回空。"""
    engine = connect("postgresql+psycopg://user:secret@127.0.0.1:1/missing")
    store = _store(engine, clock)

    with pytest.raises(OperationalError):
        store.get("P1")
    with pytest.raises(OperationalError):
        store.latest(active=True)
    with pytest.raises(OperationalError):
        _save(store, Note("P1", "pending"))


def test_missing_table_raises(empty_database: str, clock: FakeClock) -> None:
    """spec「表不存在」：没有建表的空数据库。"""
    engine = connect(empty_database)
    try:
        with pytest.raises(ProgrammingError):
            _store(engine, clock).get("P1")
    finally:
        engine.dispose()


def test_unreadable_record_raises(engine: Engine, clock: FakeClock) -> None:
    """spec「记录读不出来」：读取和列出都报错，列出时不跳过这条记录。"""
    store = _store(engine, clock)
    _save(store, Note("P2", "pending"))
    with engine.begin() as connection:
        connection.execute(
            insert(NOTES).values(
                key="P1", version=1, recorded_at=_at(15), data={"key": "P1"}, active=True
            )
        )

    with pytest.raises(ValidationError):
        store.get("P1")
    with pytest.raises(ValidationError):
        store.latest(active=True)


# 会话时区


def test_sessions_use_utc(engine: Engine) -> None:
    """design D5：connect 建的连接，会话时区是 UTC。"""
    with engine.connect() as connection:
        assert connection.execute(text("show timezone")).scalar_one() == "UTC"
