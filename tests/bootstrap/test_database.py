"""数据库迁移的测试：每个测试对应 openspec 的 state-store spec「表的归属和迁移」里的一个场景。

每个测试用一个新建的空数据库，看到的就是迁移建出来的样子。
"""

import pkgutil
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, MetaData, Table, insert, inspect, select, text

import tradingagentspro.modules
from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.modules.statestore import connect


@pytest.fixture
def engine(empty_database: str) -> Iterator[Engine]:
    engine = connect(empty_database)
    yield engine
    engine.dispose()


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _columns(engine: Engine, table: str) -> dict[str, tuple[str, bool]]:
    """表的每一列：类型和能不能为空。"""
    return {
        column["name"]: (str(column["type"]), column["nullable"])
        for column in inspect(engine).get_columns(table)
    }


def _version_columns(key: str, key_type: str) -> dict[str, tuple[str, bool]]:
    """design D1：每张版本表都有的列，都不能为空。"""
    return {
        key: (key_type, False),
        "version": ("INTEGER", False),
        "recorded_at": ("TIMESTAMP", False),
        "data": ("JSONB", False),
    }


def test_upgrading_an_empty_database_creates_the_tables(engine: Engine) -> None:
    """spec「空数据库升级到最新版本」：表的列和主键见 add-state-store 和 add-trade-records 的
    design D1、add-close-reconciliation 的 design D6。
    """
    migrate(engine)

    assert _tables(engine) == {
        "alembic_version",
        "risk_reservations",
        "execution_orders",
        "execution_protections",
        "execution_trades",
        "execution_close_reconciliations",
    }
    assert _columns(engine, "risk_reservations") == _version_columns("plan_id", "UUID") | {
        "active": ("BOOLEAN", False)
    }
    assert _columns(engine, "execution_orders") == _version_columns("plan_id", "UUID") | {
        "symbol": ("VARCHAR", False),
        "active": ("BOOLEAN", False),
    }
    assert _columns(engine, "execution_protections") == _version_columns("symbol", "VARCHAR") | {
        "active": ("BOOLEAN", False)
    }
    assert _columns(engine, "execution_trades") == _version_columns(
        "client_order_id", "VARCHAR"
    ) | {"traded": ("BOOLEAN", False)}
    assert _columns(engine, "execution_close_reconciliations") == _version_columns("day", "DATE")
    keys = {
        table: inspect(engine).get_pk_constraint(table)["constrained_columns"]
        for table in _tables(engine) - {"alembic_version"}
    }
    assert keys == {
        "risk_reservations": ["plan_id", "version"],
        "execution_orders": ["plan_id", "version"],
        "execution_protections": ["symbol", "version"],
        "execution_trades": ["client_order_id", "version"],
        "execution_close_reconciliations": ["day", "version"],
    }
    with engine.connect() as connection:
        timestamp: str = connection.execute(
            text(
                "select data_type from information_schema.columns "
                "where table_name = 'execution_orders' and column_name = 'recorded_at'"
            )
        ).scalar_one()
    assert timestamp == "timestamp with time zone"


def test_upgrading_again_changes_nothing(engine: Engine) -> None:
    """spec「已经是最新版本时再升级」：表和已有的行都不变。"""
    migrate(engine)
    orders = Table("execution_orders", MetaData(), autoload_with=engine)
    at = datetime(2026, 10, 14, 15, tzinfo=UTC)
    with engine.begin() as connection:
        for version, status in ((1, "pending"), (2, "submitted")):
            connection.execute(
                insert(orders).values(
                    plan_id=UUID(int=1),
                    version=version,
                    recorded_at=at,
                    data={"status": status},
                    symbol="BBB",
                    active=True,
                )
            )

    def snapshot() -> tuple[set[str], str, list[tuple[int, str]]]:
        with engine.connect() as connection:
            revision = connection.execute(text("select version_num from alembic_version"))
            rows = connection.execute(
                select(orders.c.version, orders.c.data["status"].astext).order_by(orders.c.version)
            )
            return _tables(engine), revision.scalar_one(), [tuple(row) for row in rows]

    before = snapshot()
    migrate(engine)

    assert snapshot() == before
    assert before[2] == [(1, "pending"), (2, "submitted")]


def test_table_names_start_with_their_module(engine: Engine) -> None:
    """spec「表名以写入它的模块开头」。"""
    migrate(engine)
    modules = {info.name for info in pkgutil.iter_modules(tradingagentspro.modules.__path__)}
    owners = {
        table: [module for module in modules if table.startswith(f"{module}_")]
        for table in _tables(engine) - {"alembic_version"}
    }

    assert owners == {
        "risk_reservations": ["risk"],
        "execution_orders": ["execution"],
        "execution_protections": ["execution"],
        "execution_trades": ["execution"],
        "execution_close_reconciliations": ["execution"],
    }
