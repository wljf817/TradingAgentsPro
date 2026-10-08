"""装配、启动和停止的测试：真实的数据库（testcontainers）、真实的装配和 DBOS，外部系统用替身。

场景见 openspec 的 bootstrap spec，做法见 add-bootstrap-wiring 的 design D1、D5、D6、D8、
D10、D11、D12。
正常启动的测试先设置好 stop 再调用 run：启动完成以后立即停止，同步跑完，不用线程。
"""

import io
import re
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, inspect, make_url, text

from tests.bootstrap.externals import (
    APCA_SECRET,
    DISCORD_TOKEN,
    NOW,
    World,
    environment,
)
from tests.integrations.discord.gateway import FakeGateway
from tradingagentspro.bootstrap.app import (
    BootstrapConfig,
    paper_execution,
    run,
    start,
)
from tradingagentspro.contracts.trade_plan import OrderType
from tradingagentspro.integrations.alpaca import AlpacaClient, AlpacaConfig
from tradingagentspro.integrations.discord import DiscordConfig
from tradingagentspro.integrations.discord.notifier import PressHandler
from tradingagentspro.modules.execution import (
    Order,
    OrderSide,
    OrderStatus,
    PostgresOrderStore,
    StopOrder,
    StopStatus,
)
from tradingagentspro.modules.health import HealthCheck, HealthMode
from tradingagentspro.modules.notification import NotificationUnavailableError
from tradingagentspro.modules.risk import RiskConfig
from tradingagentspro.modules.statestore import connect

DB_PASSWORD = "db-pass-k3v9"  # 测试里建的数据库用户的密码，只用来检查它不外露
_LINE = re.compile(r"^\S+ (?P<level>[A-Z]+) \[[^\]]*\] (?P<name>\S+): (?P<message>.*)$")

NORMAL_ORDER = [
    "启动 tradingagentspro ",
    "数据库已升级到最新版本：",
    "交易日历：2020-01-02 到 2028-10-13",
    "Alpaca 账户设置完成",
    "启动对账：没有不一致",
    "健康：正常",
    "Discord 已连接",
    "DBOS 已启动",
    "启动完成",
    "收到停止信号，开始停止",
    "已关闭：DBOS",
    "已关闭：Discord",
    "已关闭：OpenD 行情连接",
    "已关闭：数据库连接",
    "已停止",
]


@pytest.fixture
def database_url(postgres_url: str, empty_database: str) -> str:
    """空数据库的地址，用户是测试里建的 trader，密码是 DB_PASSWORD。

    地址不写驱动（postgresql://），覆盖 spec「数据库地址不写驱动」。
    """
    admin = create_engine(postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(text("select 1 from pg_roles where rolname = 'trader'"))
        if exists.first() is None:
            connection.execute(text(f"CREATE ROLE trader LOGIN SUPERUSER PASSWORD '{DB_PASSWORD}'"))
    admin.dispose()
    url = make_url(empty_database).set(
        drivername="postgresql", username="trader", password=DB_PASSWORD
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture(autouse=True)
def no_threads_left_behind() -> Iterator[None]:
    """每个测试结束时，没有留下运行中的非守护线程（DBOS、Discord 都已经关闭）。"""
    before = set(threading.enumerate())
    yield
    left = [t for t in threading.enumerate() if t not in before and not t.daemon and t.is_alive()]
    for thread in left:
        thread.join(5)
    assert [t.name for t in left if t.is_alive()] == []


def _messages(stream: io.StringIO, level: str | None = None) -> list[str]:
    """日志每一行的内容部分；给出 level 时只要这个级别的。"""
    found = []
    for line in stream.getvalue().splitlines():
        match = _LINE.match(line)
        if match and (level is None or match["level"] == level):
            found.append(match["message"])
    return found


def _in_order(messages: list[str], expected: list[str]) -> bool:
    """expected 里的每一项都依次出现在某条日志的开头。"""
    position = 0
    for prefix in expected:
        while position < len(messages) and not messages[position].startswith(prefix):
            position += 1
        if position == len(messages):
            return False
        position += 1
    return True


def _run(environ: dict[str, str], world: World, config: BootstrapConfig | None = None) -> int:
    """启动、启动完成以后立即停止，返回退出码。"""
    stop = threading.Event()
    stop.set()
    if config is None:
        return run(environ, stop, world.externals())
    return run(environ, stop, world.externals(), config)


def _connect(url: str) -> Engine:
    """测试自己连数据库：地址不写驱动，这里换成 psycopg。"""
    psycopg = make_url(url).set(drivername="postgresql+psycopg")
    return connect(psycopg.render_as_string(hide_password=False))


def _schemas(url: str) -> set[str]:
    engine = _connect(url)
    try:
        return set(inspect(engine).get_schema_names())
    finally:
        engine.dispose()


def _tables(url: str) -> set[str]:
    engine = _connect(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _leaks(text_: str) -> bool:
    return any(secret in text_ for secret in (APCA_SECRET, DISCORD_TOKEN, DB_PASSWORD))


# 启动顺序


def test_normal_start_and_stop(log_stream: io.StringIO, database_url: str) -> None:
    """spec「正常启动的顺序」「SIGTERM」「启动和停止 DBOS」「正常停止的退出码」「DBOS 的日志」。"""
    world = World()

    code = _run(environment(database_url), world)

    assert code == 0
    assert _in_order(_messages(log_stream), NORMAL_ORDER), log_stream.getvalue()
    requests = world.alpaca_requests()
    assert requests[0] == ("PATCH", "/v2/account/configurations")
    assert world.alpaca.body("PATCH", "/v2/account/configurations") == {
        "no_shorting": True,
        "max_margin_multiplier": "1",
    }
    assert ("GET", "/v2/orders") in requests[1:]
    assert ("GET", "/v2/positions") in requests[1:]
    assert world.opend is not None and world.opend.closed
    assert _messages(log_stream).count("DBOS launched!") == 1
    assert _messages(log_stream, "WARNING") == []
    assert _messages(log_stream, "ERROR") == []


def test_all_variables_are_used(log_stream: io.StringIO, database_url: str) -> None:
    """spec「全部给出」：数据库、Alpaca、Discord 的变量都用上了。"""
    world = World()

    assert _run(environment(database_url), world) == 0

    alpaca = next(c for c in world.configs if isinstance(c, AlpacaConfig))
    assert alpaca.key_id == "PK-TEST-ID"
    assert alpaca.secret_key.get_secret_value() == APCA_SECRET
    discord = next(c for c in world.configs if isinstance(c, DiscordConfig))
    assert discord.token.get_secret_value() == DISCORD_TOKEN
    assert (discord.guild_id, discord.channel_id, discord.user_id) == (3003, 2002, 1001)
    assert "risk_reservations" in _tables(database_url)


def test_empty_database_start_does_not_touch_the_broker(
    log_stream: io.StringIO, database_url: str
) -> None:
    """spec「空数据库启动不改动券商」：有不认得的挂单和持仓，写请求只有账户设置一个。"""
    world = World()
    world.alpaca.add_order(id="O9", client_order_id="X1", symbol="DDD", status="new")
    world.alpaca.hold("CCC", "100")

    assert _run(environment(database_url), world) == 0

    assert world.alpaca_writes() == [("PATCH", "/v2/account/configurations")]
    assert "启动完成" in _messages(log_stream)


# 数据库


def test_empty_database(log_stream: io.StringIO, database_url: str) -> None:
    """spec「空数据库」：全部表、最新的迁移版本，以及 dbos schema。"""
    assert _run(environment(database_url), World()) == 0

    assert _tables(database_url) == {
        "alembic_version",
        "risk_reservations",
        "execution_orders",
        "execution_protections",
        "execution_trades",
    }
    assert "dbos" in _schemas(database_url)
    engine = _connect(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("select version_num from alembic_version"))
            assert revision.scalar_one() == "0002"
            dbos_tables = inspect(engine).get_table_names(schema="dbos")
    finally:
        engine.dispose()
    assert "workflow_status" in dbos_tables


def test_second_start_keeps_the_data(log_stream: io.StringIO, database_url: str) -> None:
    """spec「再次启动」：表和已有的订单记录都不变。"""
    assert _run(environment(database_url), World()) == 0
    order = Order(
        plan_id=UUID(int=1),
        symbol="BBB",
        sector="半导体",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(50),
        quantity=100,
        deadline=datetime(2026, 10, 13, 20, tzinfo=UTC),
        created_at=datetime(2026, 10, 13, 15, tzinfo=UTC),
        status=OrderStatus.CANCELLED,
    )
    engine = _connect(database_url)
    try:
        PostgresOrderStore(engine, World().clock).save(order)
        tables = _tables(database_url)

        assert _run(environment(database_url), World()) == 0

        assert PostgresOrderStore(engine, World().clock).get(order.plan_id) == order
        with engine.connect() as connection:
            count = connection.execute(text("select count(*) from execution_orders"))
            assert count.scalar_one() == 1
    finally:
        engine.dispose()
    assert _tables(database_url) == tables


# 交易日历、装配、启动对账、第一次健康判定：直接调用 start，检查装配好的程序


def test_default_calendar_range(log_stream: io.StringIO, database_url: str) -> None:
    """spec「默认的加载范围」：2026-10-14T14:00:00Z 启动，2020-01-02 到 2028-10-13。"""
    with ExitStack() as stack:
        app = start(environment(database_url), World().externals(), BootstrapConfig(), stack)

        assert app.calendar.first_session.day == date(2020, 1, 2)
        assert app.calendar.last_session.day == date(2028, 10, 13)
    assert "交易日历：2020-01-02 到 2028-10-13" in _messages(log_stream)


def test_configured_calendar_range(log_stream: io.StringIO, database_url: str) -> None:
    """spec「改了加载范围」：起点 2026-01-01、往后 30 天，2026-01-02 到 2026-11-13。"""
    config = BootstrapConfig(
        calendar_first_day=date(2026, 1, 1), calendar_lookahead=timedelta(days=30)
    )
    with ExitStack() as stack:
        app = start(environment(database_url), World().externals(), config, stack)

        assert app.calendar.first_session.day == date(2026, 1, 2)
        assert app.calendar.last_session.day == date(2026, 11, 13)


def test_paper_execution_staggers_buys_and_stops() -> None:
    """spec「打开买单和止损单错开」。"""
    client = AlpacaClient(AlpacaConfig(key_id="K1", secret_key=SecretStr("S1")))

    _, _, config = paper_execution(client, RiskConfig())

    assert config.stagger_buys_and_stops


@pytest.mark.parametrize("commission", [None, Decimal(3)])
def test_paper_fees_use_the_risk_commission(commission: Decimal | None) -> None:
    """spec「估算费用用风控的每笔佣金」：默认 2；改了风控的每笔佣金，估算跟着变。"""
    world = World()
    world.alpaca.add_order(
        id="O1",
        client_order_id=str(UUID(int=1)),
        symbol="BBB",
        qty="100",
        status="filled",
        filled_qty="100",
        filled_avg_price="49.95",
    )
    world.alpaca.add_order(
        id="S1",
        client_order_id="BBB-STOP-1",
        symbol="BBB",
        side="sell",
        type="stop",
        qty="100",
        stop_price="47",
        status="filled",
        filled_qty="100",
        filled_avg_price="46.9",
    )
    risk = RiskConfig() if commission is None else RiskConfig(commission_per_order=commission)
    client = AlpacaClient(
        AlpacaConfig(key_id="K1", secret_key=SecretStr("S1")),
        transport=world.alpaca.transport(),
    )
    broker, stops, _ = paper_execution(client, risk)
    order = Order(
        plan_id=UUID(int=1),
        symbol="BBB",
        sector="半导体",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(50),
        quantity=100,
        deadline=NOW + timedelta(hours=6),
        created_at=NOW,
        status=OrderStatus.SUBMITTED,
        broker_order_id="O1",
    )
    stop = StopOrder(
        symbol="BBB",
        client_order_id="BBB-STOP-1",
        quantity=100,
        stop_price=Decimal(47),
        created_at=NOW,
        status=StopStatus.WORKING,
        broker_order_id="S1",
    )

    order_report = broker.find(order)
    stop_report = stops.find(stop)

    expected = Decimal(2) if commission is None else commission
    assert order_report is not None and order_report.fees == expected
    assert stop_report is not None and stop_report.fees == expected


def test_unexplained_position_at_startup(log_stream: io.StringIO, database_url: str) -> None:
    """spec「来历不明的持仓」：启动完成，WARNING 写明 CCC 100 股，结果保留下来。"""
    world = World()
    world.alpaca.hold("CCC", "100")

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.reconciliation.unexplained == (("CCC", 100),)
    [warning] = _messages(log_stream, "WARNING")
    assert "启动对账有不一致" in warning and "CCC" in warning


def test_opend_down_in_session(log_stream: io.StringIO, database_url: str) -> None:
    """spec「盘中 OpenD 没开」：写取不到行情和降级的 WARNING，照常启动完成。"""
    world = World(opend_error="Connection refused")

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.health.status.mode is HealthMode.DEGRADED
        assert app.health.status.abnormal == {HealthCheck.DATA_DELAY}
    warnings = _messages(log_stream, "WARNING")
    assert any("取不到行情" in w and "Connection refused" in w for w in warnings)
    assert any("降级" in w and "data_delay" in w for w in warnings)
    assert "DBOS 已启动" in _messages(log_stream)


# 启动失败


def _failed(log_stream: io.StringIO) -> str:
    [error] = _messages(log_stream, "ERROR")
    assert error.startswith("启动失败：")
    return error


def test_missing_variables(log_stream: io.StringIO) -> None:
    """spec「一次列出全部缺少的变量」「启动失败的退出码」：退出码 1，什么都没有连接。"""
    world = World()
    environ = environment("postgresql://trader@127.0.0.1:1/x") | {"DISCORD_USER_ID": ""}
    del environ["APCA_API_SECRET_KEY"]

    assert _run(environ, world) == 1

    error = _failed(log_stream)
    assert "读取环境变量失败" in error
    assert "APCA_API_SECRET_KEY" in error and "DISCORD_USER_ID" in error
    assert world.configs == []
    assert "数据库已升级到最新版本" not in log_stream.getvalue()


def test_discord_id_not_an_integer(log_stream: io.StringIO) -> None:
    """spec「Discord 的 ID 不是正整数」。"""
    environ = environment("postgresql://trader@127.0.0.1:1/x") | {"DISCORD_CHANNEL_ID": "general"}

    assert _run(environ, World()) == 1

    error = _failed(log_stream)
    assert "DISCORD_CHANNEL_ID" in error and "general" not in error


def test_database_address_is_wrong(log_stream: io.StringIO) -> None:
    """spec「数据库地址写错」：日志里没有地址的任何部分。"""
    environ = environment("pw-9f3k@db")

    assert _run(environ, World()) == 1

    assert "TRADINGAGENTSPRO_DATABASE_URL" in _failed(log_stream)
    assert "pw-9f3k" not in log_stream.getvalue()


def test_database_unreachable(log_stream: io.StringIO) -> None:
    """spec「数据库连不上」：写明数据库这一步，没有连接 Alpaca、OpenD、Discord。"""
    world = World()
    url = f"postgresql://trader:{DB_PASSWORD}@127.0.0.1:{_unused_port()}/trading"

    assert _run(environment(url), world) == 1

    assert "连接和升级数据库失败" in _failed(log_stream)
    assert world.configs == []
    assert world.gateway.loop_thread is None
    assert not _leaks(log_stream.getvalue())
    assert "已关闭：数据库连接" in _messages(log_stream)


def test_migration_fails(log_stream: io.StringIO, database_url: str) -> None:
    """spec「数据库升级失败」：数据库不变，没有连接外部系统。"""
    engine = _connect(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE risk_reservations (id integer)"))
    finally:
        engine.dispose()
    world = World()

    assert _run(environment(database_url), world) == 1

    assert "连接和升级数据库失败" in _failed(log_stream)
    assert _tables(database_url) == {"risk_reservations"}
    assert world.configs == []


def test_alpaca_authentication_fails(log_stream: io.StringIO, database_url: str) -> None:
    """spec「Alpaca 认证失败」：后面的步骤没有做，OpenD 和数据库按相反顺序关闭。"""
    world = World()
    world.alpaca.respond_next("PATCH", "/v2/account/configurations", 401, "unauthorized.")

    assert _run(environment(database_url), world) == 1

    error = _failed(log_stream)
    assert "Alpaca 账户设置失败" in error and "401" in error
    assert world.alpaca_requests() == [("PATCH", "/v2/account/configurations")]
    assert world.gateway.loop_thread is None
    assert "dbos" not in _schemas(database_url)
    assert world.opend is not None and world.opend.closed
    assert _in_order(_messages(log_stream), ["已关闭：OpenD 行情连接", "已关闭：数据库连接"])
    assert not _leaks(log_stream.getvalue())


def test_alpaca_times_out_on_account_settings(log_stream: io.StringIO, database_url: str) -> None:
    """spec「账户设置时 Alpaca 超时」：没有对账，Discord 没有连接。"""
    world = World()
    world.alpaca.raise_next("PATCH", "/v2/account/configurations", httpx.ConnectTimeout)

    assert _run(environment(database_url), world) == 1

    error = _failed(log_stream)
    assert "Alpaca 账户设置失败" in error and "BrokerUnavailableError" in error
    assert world.alpaca_requests() == [("PATCH", "/v2/account/configurations")]
    assert world.gateway.loop_thread is None


def test_alpaca_unavailable_at_reconcile(log_stream: io.StringIO, database_url: str) -> None:
    """spec「对账时 Alpaca 不可用」：Discord 没有连接，DBOS 没有启动。"""
    world = World()
    world.alpaca.raise_next("GET", "/v2/orders", httpx.ConnectError)

    assert _run(environment(database_url), world) == 1

    error = _failed(log_stream)
    assert "启动对账失败" in error and "BrokerUnavailableError" in error
    assert world.gateway.loop_thread is None
    assert "dbos" not in _schemas(database_url)


def test_discord_fails(log_stream: io.StringIO, database_url: str) -> None:
    """spec「Discord 启动失败」：DBOS 没有启动，OpenD 和数据库已关闭。"""
    world = World()
    world.gateway.run_error = NotificationUnavailableError("Discord 登录失败：token 无效")

    assert _run(environment(database_url), world) == 1

    error = _failed(log_stream)
    assert "启动 Discord失败" in error and "Discord 登录失败" in error
    assert "dbos" not in _schemas(database_url)
    assert world.opend is not None and world.opend.closed
    assert _in_order(_messages(log_stream), ["已关闭：OpenD 行情连接", "已关闭：数据库连接"])
    assert "已关闭：Discord" not in _messages(log_stream)


# 停止


def test_a_close_that_fails(log_stream: io.StringIO, database_url: str) -> None:
    """spec「某一项关闭出错」：写 ERROR，数据库照样关闭，退出码 0。"""
    world = World(opend_close_error=RuntimeError("OpenD 关闭出错"))

    assert _run(environment(database_url), world) == 0

    [error] = _messages(log_stream, "ERROR")
    assert error == "关闭时出错：OpenD 行情连接"
    assert "RuntimeError: OpenD 关闭出错" in log_stream.getvalue()
    assert _in_order(_messages(log_stream), ["已关闭：数据库连接", "已停止"])


class StoppingGateway(FakeGateway):
    """连接 Discord 的时候收到停止信号。"""

    def __init__(self, stop: threading.Event) -> None:
        super().__init__()
        self.stop = stop

    async def run(self, on_press: PressHandler, ready: Callable[[], None]) -> None:
        self.stop.set()
        await super().run(on_press, ready)


def test_stop_during_startup(log_stream: io.StringIO, database_url: str) -> None:
    """spec「启动过程中收到停止信号」：做完启动以后立即停止，退出码 0。"""
    stop = threading.Event()
    world = World()
    world.gateway = StoppingGateway(stop)
    result: list[int] = []

    thread = threading.Thread(
        target=lambda: result.append(run(environment(database_url), stop, world.externals()))
    )
    thread.start()
    thread.join(60)

    assert result == [0]
    assert _in_order(_messages(log_stream), ["DBOS 已启动", "启动完成", "收到停止信号", "已停止"])


# 日志里没有密钥


def test_no_secrets_in_the_logs(log_stream: io.StringIO, database_url: str) -> None:
    """spec「日志里没有密钥」：正常启停、Alpaca 拒绝账户设置、数据库连不上，三次都没有密钥。"""
    assert _run(environment(database_url), World()) == 0
    rejected = World()
    rejected.alpaca.respond_next("PATCH", "/v2/account/configurations", 401, "unauthorized.")
    assert _run(environment(database_url), rejected) == 1
    unreachable = f"postgresql://trader:{DB_PASSWORD}@127.0.0.1:{_unused_port()}/trading"
    assert _run(environment(unreachable), World()) == 1

    output = log_stream.getvalue()
    assert not _leaks(output)
    assert "postgresql+psycopg://trader:***@" in output
