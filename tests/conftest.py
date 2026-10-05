"""全部测试共用的设置：时区守卫、Hypothesis 配置和数据库。"""

import os
import time
from collections.abc import Iterator, Sequence
from itertools import count

import pytest
from hypothesis import settings
from sqlalchemy import Engine, create_engine, make_url, text
from testcontainers.community.postgres import PostgresContainer

from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.modules.statestore import connect

# 时区守卫：测试进程固定在 UTC+14 运行。美股常规交易时段对应的 UTC 时间换到 UTC+14
# 都已经是第二天，偷偷依赖本机时区的代码在测试里会算错日期。
os.environ["TZ"] = "Pacific/Kiritimati"
time.tzset()

# dev：本地随机探索，失败样例存进 .hypothesis/。
# ci：固定随机种子，同一个提交在 CI 里结果可重现；失败时打印复现所需的数据。
# 纯逻辑不测耗时，两套都关掉 deadline，避免慢机器上的偶发失败。
settings.register_profile("dev", deadline=None)
settings.register_profile(
    "ci",
    deadline=None,
    derandomize=True,
    print_blob=True,
    max_examples=200,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# 数据库：连数据库的测试用 testcontainers 启动的临时 PostgreSQL 18，需要开着 Docker。
# 每次测试运行一个容器，用完即删，测试碰不到正式的数据库。

_database_numbers = count(1)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """整个测试运行共用的 PostgreSQL 容器的地址：第一次用到时启动，测试结束时删除。"""
    with PostgresContainer("postgres:18", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def empty_database(postgres_url: str) -> Iterator[str]:
    """在容器里新建的一个空数据库的地址，测试结束后删除。"""
    name = f"empty_{next(_database_numbers)}"
    admin = create_engine(postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    try:
        yield make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> Iterator[Engine]:
    """容器默认的数据库，整个测试运行只升级一次。"""
    engine = connect(postgres_url)
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def database(migrated_database: Engine) -> Engine:
    """升级到最新版本的数据库：每个测试开始前清空除迁移版本表以外的全部表。"""
    with migrated_database.begin() as connection:
        tables: Sequence[str] = (
            connection.execute(
                text(
                    "select tablename from pg_tables "
                    "where schemaname = 'public' and tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        if tables:
            connection.execute(text(f"TRUNCATE {', '.join(tables)}"))
    return migrated_database
