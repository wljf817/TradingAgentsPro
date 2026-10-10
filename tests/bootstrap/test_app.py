"""装配、启动和停止的测试：真实的数据库（testcontainers）、真实的装配和 DBOS，外部系统用替身。

场景见 openspec 的 bootstrap spec，做法见 add-bootstrap-wiring 的 design D1、D5、D6、D8、
D10、D11、D12。
正常启动的测试先设置好 stop 再调用 run：启动完成以后立即停止，同步跑完，不用线程。
"""

import asyncio
import io
import itertools
import re
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import discord
import httpx
import pytest
from dbos import DBOS
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, inspect, make_url, text

from tests.bootstrap.externals import (
    APCA_SECRET,
    DISCORD_TOKEN,
    NOW,
    World,
    environment,
)
from tests.fakes.clock import FakeClock
from tests.integrations.alpaca import payloads as alpaca_payloads
from tests.integrations.alpaca.server import FakeAlpaca
from tests.integrations.discord.calls import FakeCall
from tests.integrations.discord.gateway import FakeGateway, FakePress
from tests.integrations.moomoo import payloads as moomoo_payloads
from tests.modules.notification.builders import USER_ID
from tradingagentspro.bootstrap import app as app_module
from tradingagentspro.bootstrap.app import (
    BootstrapConfig,
    paper_execution,
    run,
    start,
)
from tradingagentspro.bootstrap.database import migrate
from tradingagentspro.contracts.risk import RiskDecision, RiskOutcome
from tradingagentspro.contracts.trade_plan import OrderType, TradeAction, TradePlan
from tradingagentspro.integrations.alpaca import AlpacaClient, AlpacaConfig
from tradingagentspro.integrations.discord import DiscordConfig
from tradingagentspro.integrations.discord.notifier import PressHandler
from tradingagentspro.integrations.discord.plans import PlanEntry
from tradingagentspro.modules.execution import (
    Order,
    OrderSide,
    OrderStatus,
    PostgresCloseReconciliationStore,
    PostgresOrderStore,
    StopOrder,
    StopStatus,
)
from tradingagentspro.modules.health import HealthCheck, HealthMode
from tradingagentspro.modules.notification import NotificationConfig, NotificationUnavailableError
from tradingagentspro.modules.risk import PostgresReservationStore, Reservation, RiskConfig
from tradingagentspro.modules.statestore import connect
from tradingagentspro.workflows.patrol import PatrolConfig, SessionPatrol

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
    "盘中巡检已开始，间隔 15 秒",
    "启动完成",
    "收到停止信号，开始停止",
    "已关闭：盘中巡检",
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
    """每个测试结束时，没有留下运行中的非守护线程（DBOS、Discord 都已关闭），巡检也已停下。"""
    before = set(threading.enumerate())
    yield
    assert [t for t in threading.enumerate() if t.name == "session-patrol"] == []
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
        "execution_close_reconciliations",
    }
    assert "dbos" in _schemas(database_url)
    engine = _connect(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("select version_num from alembic_version"))
            assert revision.scalar_one() == "0003"
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


def _alerts(world: World) -> list[str]:
    """Discord 收到的消息的文字，去掉开头 @ 用户的部分。"""
    return [sent.content.split(" ", 1)[1] for sent in world.gateway.sent]


def test_no_alert_without_mismatch(log_stream: io.StringIO, database_url: str) -> None:
    """spec「没有不一致」：Discord 没有收到告警。"""
    world = World()

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert world.gateway.sent == []
    assert "启动对账：没有不一致" in _messages(log_stream)


def test_unexplained_position_at_startup(log_stream: io.StringIO, database_url: str) -> None:
    """spec「来历不明的持仓」：启动完成，WARNING 写明 CCC 100 股，结果保留下来；
    Discord 连上以后、DBOS 启动以前推送告警。
    """
    world = World()
    world.alpaca.hold("CCC", "100")

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.reconciliation.unexplained == (("CCC", 100),)
        [alert] = _alerts(world)
    [warning] = _messages(log_stream, "WARNING")
    assert "启动对账有不一致" in warning and "CCC" in warning
    assert alert.split("\n") == [
        "启动对账有不一致：",
        "- 来历不明的持仓：CCC 100 股",
        "已暂停开仓，之后的对账没有不一致时恢复",
    ]
    assert _in_order(
        _messages(log_stream), ["Discord 已连接", "推送告警：启动对账有不一致", "DBOS 已启动"]
    )


def test_stray_order_at_startup(log_stream: io.StringIO, database_url: str) -> None:
    """spec「不认得的挂单」：告警写明 DDD、X1，挂单没有被撤销。"""
    world = World()
    world.alpaca.add_order(id="O9", client_order_id="X1", symbol="DDD", status="new")

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        [alert] = _alerts(world)
    assert "- 不认得的挂单：DDD，客户端订单 ID X1，券商订单号 O9" in alert.split("\n")
    assert ("DELETE", "/v2/orders/O9") not in world.alpaca_requests()


def test_startup_alert_that_cannot_be_sent(log_stream: io.StringIO, database_url: str) -> None:
    """spec「告警发不出去」：错误日志含告警的文字和原因，照常启动完成。"""
    world = World()
    world.alpaca.hold("CCC", "100")
    world.gateway.fail_next_send(NotificationUnavailableError("Discord：503 Service Unavailable"))

    assert _run(environment(database_url), world) == 0

    [error] = _messages(log_stream, "ERROR")
    assert "启动对账有不一致" in error and "503 Service Unavailable" in error
    assert "启动完成" in _messages(log_stream)


def test_close_reconciliation_after_starting_past_the_close(
    log_stream: io.StringIO, database_url: str
) -> None:
    """close-reconciliation spec「开盘以前重启时补做」：收盘加延迟以后启动，巡检的第一轮做这一天的
    收盘对账，记录存进状态库，Discord 收到结果。
    """
    world = World(clock=FakeClock(datetime(2026, 10, 14, 20, 15, tzinfo=UTC)))
    day = date(2026, 10, 14)

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)
        for _ in range(100):  # 最多等 10 秒
            if world.gateway.sent:
                break
            threading.Event().wait(0.1)

    engine = _connect(database_url)
    try:
        record = PostgresCloseReconciliationStore(engine, world.clock).get(day)
    finally:
        engine.dispose()
    assert record is not None
    assert (record.clean, record.holdings, record.streak) == (True, 0, 0)
    assert _alerts(world) == ["收盘对账 2026-10-14：没有差异，收盘时没有持仓，连续天数清零"]
    assert _messages(log_stream, "ERROR") == []


HEALTH_NOTICE = (
    "已暂停开仓，已经确认、还没下单的开仓方案下单时作废；止损单照常保留。"
    "各项恢复并和券商对账以后恢复开仓"
)


def _health_alerts(world: World) -> list[str]:
    """Discord 收到的健康告警（进入降级模式、启动时处于降级模式、降级模式结束）。"""
    return [a for a in _alerts(world) if "降级模式" in a.split("\n")[0]]


def test_opend_down_in_session(log_stream: io.StringIO, database_url: str) -> None:
    """spec「盘中 OpenD 没开」：写取不到行情和降级的 WARNING；Discord 连上以后、DBOS 启动以前
    推送启动时处于降级模式的告警；照常启动完成。
    """
    world = World(opend_error="Connection refused")

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.health.status.mode is HealthMode.DEGRADED
        assert app.health.status.abnormal == {HealthCheck.DATA_DELAY}
        [alert] = _health_alerts(world)
    warnings = _messages(log_stream, "WARNING")
    assert any("取不到行情" in w and "Connection refused" in w for w in warnings)
    assert any("降级" in w and "数据延迟" in w for w in warnings)
    assert alert == (
        f"启动时处于降级模式（2026-10-14 14:00 UTC 起），异常的检查：数据延迟\n{HEALTH_NOTICE}"
    )
    assert _in_order(
        _messages(log_stream), ["Discord 已连接", "推送告警：启动时处于降级模式", "DBOS 已启动"]
    )


def test_opend_down_before_open(log_stream: io.StringIO, database_url: str) -> None:
    """spec「盘外 OpenD 没开」：健康是正常，Discord 没有收到健康的告警。"""
    world = World(
        clock=FakeClock(datetime(2026, 10, 14, 12, tzinfo=UTC)), opend_error="Connection refused"
    )

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.health.status.mode is HealthMode.NORMAL
        assert _health_alerts(world) == []
    assert any("取不到行情" in w for w in _messages(log_stream, "WARNING"))


def test_quote_stuck_at_previous_close(log_stream: io.StringIO, database_url: str) -> None:
    """spec「开盘后行情一直没有更新」：进入降级模式，Discord 收到启动时处于降级模式的告警。"""
    world = World(opend_updated_at=datetime(2026, 10, 13, 20, tzinfo=UTC))

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        assert app.health.status.abnormal == {HealthCheck.DATA_DELAY}
        [alert] = _health_alerts(world)
    assert alert.startswith("启动时处于降级模式（2026-10-14 14:00 UTC 起），异常的检查：数据延迟")


def test_reconciliation_and_health_alerts_in_order(
    log_stream: io.StringIO, database_url: str
) -> None:
    """spec「启动对账和健康都要告警」：先启动对账的告警，再健康的告警，之后 DBOS 才启动。"""
    world = World(opend_error="Connection refused")
    world.alpaca.hold("CCC", "100")

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)

        first, second = _alerts(world)[:2]
    assert first.startswith("启动对账有不一致")
    assert second.startswith("启动时处于降级模式")
    assert _in_order(
        _messages(log_stream),
        [
            "Discord 已连接",
            "推送告警：启动对账有不一致",
            "推送告警：启动时处于降级模式",
            "DBOS 已启动",
        ],
    )


def test_health_alert_that_cannot_be_sent(log_stream: io.StringIO, database_url: str) -> None:
    """spec「健康的告警发不出去」：错误日志含告警的文字和原因，照常启动完成。"""
    world = World(opend_error="Connection refused")
    world.gateway.fail_next_send(NotificationUnavailableError("Discord：503 Service Unavailable"))

    assert _run(environment(database_url), world) == 0

    [error] = _messages(log_stream, "ERROR")
    assert "启动时处于降级模式" in error and "503 Service Unavailable" in error
    assert "启动完成" in _messages(log_stream)


def test_no_second_health_alert_after_the_first_round(
    log_stream: io.StringIO, database_url: str
) -> None:
    """spec「巡检第一轮仍是降级时不再告警」：巡检做完第一轮的健康判定，仍只有启动时的那一条。"""
    world = World(opend_error="Connection refused")

    with ExitStack() as stack:
        app = start(environment(database_url), world.externals(), BootstrapConfig(), stack)
        assert world.opend is not None
        for _ in range(100):  # 最多等 10 秒：巡检第一轮的健康判定取了行情
            if world.opend.calls >= 2:
                break
            threading.Event().wait(0.1)
        assert world.opend.calls >= 2
    # 停止时巡检做完正在做的那一项，健康判定已经做完

    assert app.health.status.mode is HealthMode.DEGRADED
    assert len(_health_alerts(world)) == 1


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


# 盘中巡检（add-session-patrol）

P1 = UUID("00000000-0000-4000-8000-000000000001")


def _p1_plan() -> TradePlan:
    """买入 BBB（医疗）100 股，限价 50，止损价 45。"""
    return TradePlan(
        plan_id=P1,
        symbol="BBB",
        sector="医疗",
        action=TradeAction.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(50),
        stop_price=Decimal(45),
    )


def _submitted_p1(url: str) -> None:
    """停机前的状态库：P1 的买单已提交（券商订单号 O1），风控为它预留了 100 股。"""
    engine = _connect(url)
    try:
        migrate(engine)
        clock = FakeClock(NOW)
        plan = _p1_plan()
        decision = RiskDecision(
            outcome=RiskOutcome.PASSED,
            quantity=100,
            emergency_threshold=Decimal("0.08"),
            expires_at=NOW + timedelta(hours=6),
            violations=(),
        )
        PostgresReservationStore(engine, clock).save(Reservation(plan=plan, decision=decision))
        PostgresOrderStore(engine, clock).save(
            Order(
                plan_id=P1,
                symbol="BBB",
                sector="医疗",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=Decimal(50),
                quantity=100,
                deadline=NOW + timedelta(hours=6),
                created_at=NOW - timedelta(minutes=5),
                stop_price=Decimal(45),
                emergency_threshold=Decimal("0.08"),
                status=OrderStatus.SUBMITTED,
                broker_order_id="O1",
            )
        )
    finally:
        engine.dispose()


def _reservation_active(url: str) -> bool:
    engine = _connect(url)
    try:
        reservation = PostgresReservationStore(engine, FakeClock(NOW)).get(P1)
        return reservation is not None and reservation.is_active
    finally:
        engine.dispose()


def _p1_status(url: str) -> OrderStatus:
    engine = _connect(url)
    try:
        order = PostgresOrderStore(engine, FakeClock(NOW)).get(P1)
        assert order is not None
        return order.status
    finally:
        engine.dispose()


def test_patrol_reports_fills_of_orders_submitted_before_the_restart(
    log_stream: io.StringIO, database_url: str
) -> None:
    """spec「启动以后巡检跟进停机前提交的订单」：启动对账跟进 P1、挂上止损单，第一轮上报成交。"""
    _submitted_p1(database_url)
    world = World()
    world.alpaca.add_order(
        id="O1",
        client_order_id=str(P1),
        symbol="BBB",
        qty="100",
        status="filled",
        filled_qty="100",
        filled_avg_price="49.95",
    )
    world.alpaca.hold("BBB", "100", "49.95")

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)
        assert any(o["client_order_id"] == "BBB-STOP-1" for o in world.alpaca.orders.values())
        for _ in range(100):  # 最多等 10 秒
            if not _reservation_active(database_url):
                break
            threading.Event().wait(0.1)

    assert _p1_status(database_url) is OrderStatus.FILLED
    assert not _reservation_active(database_url)
    assert _messages(log_stream, "ERROR") == []


def test_patrol_and_later_flows_share_one_lock(
    log_stream: io.StringIO, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec「巡检和以后的流程共用一把锁」：间隔 15 秒；TradingApp.lock 就是巡检用的那一把。"""
    patrols: list[tuple[object, PatrolConfig]] = []

    class RecordingPatrol(SessionPatrol):
        def __init__(self, *args: Any) -> None:
            super().__init__(*args)
            patrols.append((args[4], args[5]))  # 锁和巡检配置

    monkeypatch.setattr(app_module, "SessionPatrol", RecordingPatrol)

    with ExitStack() as stack:
        app = start(environment(database_url), World().externals(), BootstrapConfig(), stack)

    [(lock, config)] = patrols
    assert lock is app.lock
    assert config.interval == timedelta(seconds=15)


class _SlowAlpaca(FakeAlpaca):
    """按 P1 第 nth 次查询时停住，直到测试放行。"""

    def __init__(self, nth: int) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._left = nth

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("client_order_id") == str(P1):
            self._left -= 1
            if self._left == 0:
                self.entered.set()
                self.release.wait(10)
        return super()._handle(request)


def test_stop_waits_for_the_patrol_item_in_progress(
    log_stream: io.StringIO, database_url: str
) -> None:
    """spec「停止时巡检正在跟进订单」：这笔订单保存以后才写已关闭盘中巡检，之后才关 DBOS 等。"""
    _submitted_p1(database_url)
    alpaca = _SlowAlpaca(nth=2)  # 第 1 次是启动对账，第 2 次是巡检的第一轮
    alpaca.add_order(id="O1", client_order_id=str(P1), symbol="BBB", qty="100", status="new")
    world = World(alpaca=alpaca)
    stop = threading.Event()
    result: list[int] = []
    runner = threading.Thread(
        target=lambda: result.append(run(environment(database_url), stop, world.externals()))
    )
    runner.start()

    try:
        assert alpaca.entered.wait(30)
        stop.set()
        threading.Event().wait(0.3)
        assert "已关闭：盘中巡检" not in _messages(log_stream)
        alpaca.orders["O1"] |= {"status": "partially_filled", "filled_qty": "30"}
    finally:
        stop.set()
        alpaca.release.set()
        runner.join(60)

    assert result == [0]
    assert _p1_status(database_url) is OrderStatus.PARTIALLY_FILLED
    assert _in_order(
        _messages(log_stream),
        [
            "收到停止信号",
            "已关闭：盘中巡检",
            "已关闭：DBOS",
            "已关闭：Discord",
            "已关闭：OpenD 行情连接",
            "已关闭：数据库连接",
            "已停止",
        ],
    )


# 下单流程的接线


def _plan_world(clock_at: datetime = NOW) -> World:
    """账户 10,000、OpenD 有 BBB 行情（最新价 50）的世界。"""
    world = World(clock=FakeClock(clock_at))
    world.alpaca.account = alpaca_payloads.account(
        equity="10000", cash="10000", last_equity="10000"
    )
    world.opend_rows.append(
        moomoo_payloads.row(
            moomoo_payloads.SPY,
            code="US.BBB",
            name="BBB",
            last_price=50.0,
            prev_close_price=49.5,
            volume=1000.0,
        )
    )
    return world


def _until(condition: Callable[[], bool]) -> None:
    for _ in range(500):  # 最多等 10 秒
        if condition():
            return
        threading.Event().wait(0.02)
    raise AssertionError("等不到条件成立")


def _buy_bbb(world: World) -> str:
    """本人运行 /plan buy symbol:BBB sector:医疗 quantity:20 limit:50 stop:45，返回回复。"""
    assert world.plans is not None
    call = FakeCall(USER_ID)
    entry = PlanEntry(TradeAction.BUY, "BBB", sector="医疗", quantity=20, limit="50", stop="45")
    asyncio.run(world.plans.handle(call, entry))
    [reply] = call.replies
    return reply


def _press_confirm(world: World) -> None:
    """等确认消息发出来，按它的「确认」。"""
    _until(lambda: any(s.view is not None for s in world.gateway.sent))
    message = next(s for s in world.gateway.sent if s.view is not None)
    button = message.view.children[0]  # type: ignore[union-attr]
    assert isinstance(button, discord.ui.Button) and button.label == "确认"
    assert button.custom_id is not None
    world.gateway.press(FakePress(USER_ID, button.custom_id, message.message_id))


def _until_flow_ends(plan_id: UUID) -> None:
    """等这个方案的下单流程记下结果：关闭 DBOS 和数据库以前，工作流线程已经不再写库。"""

    def ended() -> bool:
        status = DBOS.get_workflow_status(str(plan_id))
        return status is not None and status.status == "SUCCESS"

    _until(ended)


def _bought(world: World) -> list[dict[str, Any]]:
    """Alpaca 收到的买入 BBB 的订单。"""
    return [o for o in world.alpaca.orders.values() if o["symbol"] == "BBB" and o["side"] == "buy"]


def test_enter_and_confirm_a_buy(log_stream: io.StringIO, database_url: str) -> None:
    """spec「录入并确认一个买入方案」：回复风控通过，按确认以后 Alpaca 收到买单。"""
    world = _plan_world()

    with ExitStack() as stack:
        start(environment(database_url), world.externals(), BootstrapConfig(), stack)
        reply = _buy_bbb(world)
        assert reply.startswith("风控通过：买入 BBB 20 股")
        _press_confirm(world)
        _until(lambda: bool(_bought(world)))
        _until_flow_ends(UUID(int=1))

    [order] = _bought(world)
    assert (order["client_order_id"], order["qty"], order["limit_price"]) == (
        str(UUID(int=1)),
        "20",
        "50",
    )
    assert _messages(log_stream, "ERROR") == []


@pytest.mark.parametrize("updated", [False, True], ids=["restart", "update"])
def test_recover_a_plan_waiting_for_the_open(
    log_stream: io.StringIO, database_url: str, updated: bool
) -> None:
    """spec「启动时恢复等开盘的方案」「程序更新以后恢复」：盘后确认，进程在等开盘时退出；
    下一个交易日盘中启动以后下单，没有再请求确认。update 时两次启动之间多登记一个工作流。"""
    first = _plan_world(datetime(2026, 10, 14, 21, tzinfo=UTC))
    first.sleep.crash = True
    with ExitStack() as stack:
        start(environment(database_url), first.externals(), BootstrapConfig(), stack)
        assert _buy_bbb(first).startswith("风控通过")
        _press_confirm(first)
        assert first.sleep.crashed.wait(10)
    assert _bought(first) == []
    if updated:
        _register_another_workflow()

    second = _plan_world(datetime(2026, 10, 15, 14, tzinfo=UTC))
    second.sleep.hold = True
    with ExitStack() as stack:
        start(environment(database_url), second.externals(), BootstrapConfig(), stack)
        _until(lambda: bool(_bought(second)))
        _until_flow_ends(UUID(int=1))

    assert [s for s in second.gateway.sent if s.view is not None] == []
    assert _bought(second)[0]["client_order_id"] == str(UUID(int=1))


_extra_workflows = itertools.count(1)


def _register_another_workflow() -> None:
    """程序更新：多登记一个工作流，工作流源码的哈希就变了。"""

    def extra() -> None:
        """测试「程序更新以后恢复」时多登记的工作流。"""

    DBOS.workflow(name=f"bootstrap_test.extra.{next(_extra_workflows)}")(extra)


def test_stop_while_a_plan_waits_for_confirmation(
    log_stream: io.StringIO, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec「停止时有方案在等确认」：按顺序关闭、写出已停止、退出码 0，不等截止时刻。

    确认超时改成 2 秒，让留下的工作流线程在测试结束以前醒来、结束。
    """
    monkeypatch.setattr(
        app_module,
        "NotificationConfig",
        lambda: NotificationConfig(confirmation_timeout=timedelta(seconds=2)),
    )
    world = _plan_world()
    stop = threading.Event()
    result: list[int] = []
    runner = threading.Thread(
        target=lambda: result.append(run(environment(database_url), stop, world.externals()))
    )
    runner.start()
    _until(lambda: "启动完成" in _messages(log_stream))
    assert _buy_bbb(world).startswith("风控通过")
    _until(lambda: any(s.view is not None for s in world.gateway.sent))

    stop.set()
    runner.join(10)

    assert not runner.is_alive() and result == [0]
    assert _in_order(_messages(log_stream), ["收到停止信号", "已关闭：DBOS", "已停止"])
    _until(
        lambda: (
            not any(
                t.name.startswith("dbos-executor") and t.is_alive() for t in threading.enumerate()
            )
        )
    )
