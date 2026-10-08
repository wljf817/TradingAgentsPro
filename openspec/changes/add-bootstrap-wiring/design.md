# Design

## Context

现状：

- `bootstrap/cli.py` 的 `main()` 只打印版本号；`bootstrap/database.py` 的 `migrate(engine)` 在一个事务里升级到最新版本，现在只有测试调用
- 各部件都只在自己的测试里构造：`statestore.connect`、四个 `Postgres*Store`、`ExecutionService`、`RiskService`、`EmergencyService`、`HealthService`、`MarketDataService`、`NotificationService`；integrations 里的 `AlpacaClient` + `configure_account` + 两个适配器、`open_quote_context` + `MoomooQuoteSource`、`DiscordConfig.from_env` + `PlanCommand` + `DiscordGateway` + `DiscordNotifier`、`SystemClock`、`RandomIdGenerator`、`load_nyse_calendar`
- 两个由 bootstrap 实现、通知适配器调用的接口还没有实现：`PlanIntake`（`held_sector`、`submit`）和 `DecisionHandler`（按钮回调）
- 项目里的 `logging` 都是 `getLogger(__name__)`，没有任何 handler 配置
- 测试有现成的外部替身：`tests/integrations/alpaca/server.py` 的 `FakeAlpaca`（`httpx.MockTransport`），`tests/integrations/discord/gateway.py` 的 `FakeGateway`，`tests/integrations/moomoo/payloads.py` 的快照；testcontainers 的 `empty_database` 夹具

外部库（2026-10-08 读文档和源码）：

- DBOS 3.2.0（2026-09-29 发布，自带类型标注）：`DBOS(config={"name": ..., "system_database_url": ...})`、`DBOS.launch()`、`DBOS.destroy(workflow_completion_timeout_sec=0)`。系统表放在 `dbos_system_schema`（默认 `dbos`）；对 PostgreSQL 一律用 psycopg 驱动；launch 时自己建 schema 和表，并先连 `postgres` 库看系统数据库在不在（连不上只写警告）。3.x 没有管理端口，不装信号处理。`DBOS()` 构造时如果 `dbos` logger 没有 handler，就自己加一个控制台 handler 并关掉向上传递
- moomoo SDK：自己的 logger `FTConsoleLog`（stdout，INFO）和 `FTFileLog`（`~/.com.moomoo.OpenD/Log`，按天轮换，最多 20 个），都不向上传递；行情连接里没有账号和密码
- discord.py：不用 `client.run()` 就不配置日志；INFO 只有登录、连上、重连这类消息；DEBUG 会记下请求地址，交互回调的地址里有那次交互的短期 token，机器人的 token 只在请求头里（add-discord-notify 的 design）
- httpx：每个请求写一条 INFO（方法、地址、状态码），不写请求头
- 在 CPython 3.13 里，主线程停在 `threading.Event.wait()` 上时，信号处理函数照常运行，处理函数里 `set()` 以后 `wait()` 返回（在本机验证过）

要求见 specs/bootstrap/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
bootstrap/
  cli.py          (改) argparse：run、--version；安装 SIGTERM、SIGINT 的处理；调用 run
  settings.py     (新) VARIABLES、Settings、SettingsError、load_settings
  logs.py         (新) configure_logging、UtcFormatter
  startup.py      (新) reconcile_at_startup、judge_health_at_startup
  order_flow.py   (新) AccountPlanIntake、refuse_decision、OrderFlowMissingError（下单流程接上之前）
  app.py          (新) Externals、SYSTEM_EXTERNALS、BootstrapConfig、TradingApp、paper_execution、
                       start、run、on_stop、StartupError
  database.py     不变

integrations/discord/
  __init__.py     (改) 包根多导出 Gateway：装配要给 DiscordNotifier 传它的实现，测试换成替身
```

- 只改 bootstrap，另外 integrations/discord 的包根多导出一个已有的协议 `Gateway`。modules、workflows、contracts、shared 和 integrations 的其余部分都不变，架构测试的白名单不变
- bootstrap 用到的都从包根 import：模块的包根（`execution`、`risk`、`emergency`、`health`、`marketdata`、`notification`、`statestore`），integrations 的 `alpaca`、`moomoo`、`discord` 包根，以及 `nyse_calendar`、`system_clock`、`random_ids`。装配要用的都由包根导出（README 的约定），所以 `Gateway` 也加进 discord 包根的导出，不从 `integrations.discord.notifier` 子模块 import
- 不新增跨模块的 Pydantic 模型。装配时传递的是已有的配置模型（`AlpacaConfig`、`DiscordConfig`、`MoomooConfig`、各模块的 `*Config`）；`/plan` 经 `PlanIntake` 传 `TradePlan`、返回 `RiskDecision`（不变）；`held_sector` 读执行模块的 `Account`（dataclass，不出 bootstrap）
- 新依赖 `dbos` 只在 bootstrap 里用；「下单流程」以后在 workflows 里用

## Goals / Non-Goals

**Goals:**

- 一条命令启动，systemd 能按退出码决定重启
- 启动顺序固定，并且让「对账在前、接受新订单在后」由顺序本身保证
- 以后的流程变更只需要在 `start` 里加几行，不用改启动和停止的框架
- 测试只替换三个外部系统（Alpaca、OpenD、Discord）以及系统时钟和随机 ID，其余的装配、数据库、DBOS 都是真的

**Non-Goals:**

按「编程风格」省略的情况：

- 数据库主机不可达、包被丢弃时，连接可能等到 TCP 超时（约 2 分钟）：本机或同一台机器上的 PostgreSQL 没运行时会立即报错，不另设连接超时
- 进程连续运行两年用完交易日历的加载范围：重启时重新加载
- `AlpacaClient` 的 HTTP 连接停止时不关闭：进程退出时一起释放
- 停止过程中再收到信号：不强制退出；卡住时由 systemd 的停止超时发 SIGKILL
- 运行中 Discord 连接意外中止：适配器写错误日志，程序不退出（add-discord-notify 已定）
- 运行中 OpenD 断开：SDK 自己重连；是否降级由「健康监控接线」判定
- 某一步卡住：每一步都有自己的超时（Alpaca 10 秒、Discord 连接 30 秒、OpenD 10 秒），不另设整个启动的超时
- DBOS 启动失败：按通用的启动失败处理，不写专门的场景
- 两个进程用同一个数据库或同一个 token 同时运行：不加锁，部署只运行一个
- 数据库地址写成别的数据库（比如 `mysql://`）：驱动一律换成 psycopg，连接时报错
- moomoo SDK 在 OpenD 没开时反复写重连日志：不调整 SDK 的日志
- 启动和停止不发 Discord 消息：只写日志

## Decisions

### D1 外部连接的边界

`start` 需要的外部东西集中在一个 `Externals` 里：系统时钟、随机 ID，以及三个外部系统的连接方式。运行时用 `SYSTEM_EXTERNALS`，测试换成替身，其余部分（数据库、存储、用例、适配器、`DiscordNotifier`、`PlanCommand`、DBOS）照常装配。

```python
class QuoteContext(SnapshotContext, Protocol):
    """OpenD 行情连接里装配用到的部分：取快照，退出时关闭。"""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Externals:
    """程序和外界接触的地方。运行时用 SYSTEM_EXTERNALS，测试换成替身，其余照常装配。"""

    clock: Clock
    ids: IdGenerator
    alpaca: Callable[[AlpacaConfig], AlpacaClient]
    opend: Callable[[MoomooConfig], QuoteContext]
    discord: Callable[[DiscordConfig, PlanCommand], Gateway]


SYSTEM_EXTERNALS = Externals(
    clock=SystemClock(),
    ids=RandomIdGenerator(),
    alpaca=AlpacaClient,
    opend=open_quote_context,
    discord=DiscordGateway,
)
```

- 边界选在 integrations 已经留好的替换点上：`AlpacaClient` 的 `transport`（测试传 `FakeAlpaca().transport()`）、`MoomooQuoteSource` 的 `SnapshotContext`、`DiscordNotifier` 的 `Gateway`。这样测试覆盖的是真实的适配器和真实的装配，不是替身之间的装配
- `discord` 要拿到 `PlanCommand` 才能建 `DiscordGateway`，所以是带两个参数的工厂

考虑过：

- `start` 的参数直接列出每个部件：参数有十几个，`run` 和测试都要重复一遍装配
- 测试里 monkeypatch 模块里的构造函数：改名或挪位置时测试悄悄失效
- 依赖注入框架：只有一个装配点，不值得多一个依赖

### D2 命令行

`argparse`：子命令 `run`（`required=True`），选项 `--version`。`main(argv=None) -> int` 返回退出码，入口脚本的包装会用它 `sys.exit`。

- `run` 做三件事：`configure_logging()`、建 `threading.Event` 并把 SIGTERM、SIGINT 的处理设为 `set()`，然后 `return run(os.environ, stop)`
- 不加 `--paper`、`--live` 这样的模式选项：现在只有模拟盘，M7 有了实盘再加
- `--version` 和用法错误由 argparse 处理，在读环境变量之前退出（退出码 0 和 2）

考虑过：

- 不带子命令直接启动：敲错命令就会连上券商；也和已有的「打印版本号」冲突
- click：多一个依赖，只为两个命令不值得

### D3 环境变量

`settings.py` 是全部环境变量的唯一清单，「部署」的模板照它写：

```python
DATABASE_URL = "TRADINGAGENTSPRO_DATABASE_URL"
VARIABLES = (
    DATABASE_URL,
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_GUILD_ID",
    "DISCORD_CHANNEL_ID",
    "DISCORD_USER_ID",
)


class SettingsError(ValueError):
    """环境变量缺少或格式不对。信息里只有变量名，没有值。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """从环境变量读到的配置。repr 里没有密钥：密钥都是 SecretStr。"""

    database_url: SecretStr  # 驱动已经换成 postgresql+psycopg
    alpaca: AlpacaConfig
    discord: DiscordConfig

    @property
    def database_label(self) -> str:
        """写日志用的数据库地址：密码换成 ***。"""
        ...


def load_settings(environ: Mapping[str, str]) -> Settings:
    """先检查 VARIABLES 有没有缺少（没有或为空），一次全部列出；再逐个解析。"""
    ...
```

- 先对整张清单检查缺少，再解析：`DiscordConfig.from_env` 只能列出 Discord 自己缺少的变量，直接调用就做不到「一次列出全部」。检查完以后再调用它解析 ID（它的错误信息只有变量名）
- 数据库变量带项目前缀：通用的 `DATABASE_URL` 可能是本机别的项目留在 shell 里的，程序启动时会在那个数据库里建表
- 数据库地址用 SQLAlchemy 的 `make_url` 解析，驱动换成 `postgresql+psycopg`（写 `postgresql://` 也能用；DBOS 也是这样做的）。解析失败时报 `SettingsError`，只写变量名（SQLAlchemy 2.1 的解析错误本身也不含地址，读过）
- `database_label` 用 `render_as_string(hide_password=True)`
- 不加 OpenD 地址、日志级别、超时的变量：OpenD 和主程序在同一台机器上，默认值就对；超时用各适配器配置的默认值。需要时再加

### D4 日志

```python
FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
QUIET = ("httpx", "httpcore")


class UtcFormatter(logging.Formatter):
    """时间按 UTC 写成 2026-10-14T14:00:00.123Z，不依赖本机时区。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_logging(stream: TextIO | None = None) -> None:
    """根 logger 只留一个写到 stream（默认 stderr）的 handler，级别 INFO。"""
    ...
```

- `configure_logging` 换掉根 logger 的全部 handler（`basicConfig(force=True)`），级别 INFO；`httpx`、`httpcore` 设为 WARNING
- DBOS：给 `dbos` logger 清掉已有的 handler、加一个 `NullHandler`、打开向上传递。这样 `DBOS()` 构造时看到已有 handler，不再加自己的控制台 handler，日志经根 logger 用同一种格式输出，每条一次。在 `DBOS()` 之前调用（`cli` 一开始就调用）
- 时间用 UTC：`logging` 默认按本机时区格式化时间；`time.gmtime` 被 Ruff 禁用，所以覆写 `formatTime`，用 `datetime.fromtimestamp(record.created, UTC)`
- 级别固定 INFO，不设环境变量：DEBUG 才会出现库的请求地址（discord.py 的交互 token），固定 INFO 就不会出现；排查问题时需要再加
- 线程名写进每一行：Discord 的事件循环、按钮和 `/plan` 的工作线程、DBOS 的线程都会写日志
- moomoo SDK 的日志不动：它的控制台输出在 stdout，systemd 一样交给 journald；日志文件有轮换上限；里面没有密钥
- 启动、每一步、启动完成、停止、每一项关闭各写一条，见 spec「日志」。异常用 `exc_info` 带上异常栈；异常栈里只有异常信息和源码，没有局部变量的值

### D5 启动顺序

见 spec「启动顺序」的表。顺序的理由：

1. 环境变量最先：最常见的配置错误，在连接任何东西之前报出来
2. 数据库第二：状态库是事实的唯一来源，表不是最新的就什么都不能做
3. 日历、装配：本地操作。OpenD 的连接在装配时打开（不等连上，SDK 在后台连），到第 7 步取行情时多半已经连上
4. Alpaca 账户设置在对账之前：alpaca-broker spec 的要求，对账可能补挂止损单
5. 对账、健康判定在 Discord 之前：`/plan` 和按钮是人下新订单的入口，DBOS 恢复的工作流是另一个入口；把它们放在后面，「先对账、先判定，再接受新订单」就由顺序保证，不需要额外的标记
6. Discord 在券商之后：券商不可用时启动失败、systemd 反复重启，Discord 在后面就不会每次都重新注册 `/plan`
7. DBOS 最后：恢复的工作流可能马上要请求确认，要 Discord 已经连上

`start` 的骨架（以后的流程变更在这里加几行：DBOS launch 之前注册工作流，launch 之后开始盘中巡检）：

```python
def start(
    environ: Mapping[str, str], externals: Externals, config: BootstrapConfig, stack: ExitStack
) -> TradingApp:
    """按 bootstrap spec「启动顺序」启动；打开的部分都登记到 stack，关闭时按相反顺序。"""
    clock = externals.clock
    with step("读取环境变量"):
        settings = load_settings(environ)
    with step("连接和升级数据库"):
        engine = connect(settings.database_url.get_secret_value())
        on_stop(stack, "数据库连接", engine.dispose)
        migrate(engine)
    with step("加载交易日历"):
        end = (clock.now() + config.calendar_lookahead).date()  # 范围的边界，不是交易日
        calendar = load_nyse_calendar(config.calendar_first_day, end)
    with step("装配"):
        parts = assemble(settings, externals, engine, calendar, stack)
    with step("Alpaca 账户设置"):
        configure_account(parts.alpaca)
    with step("启动对账"):
        reconciliation = reconcile_at_startup(parts.execution)
    with step("第一次健康判定"):
        judge_health_at_startup(parts.health, parts.market_data, clock)
    with step("启动 Discord"):
        parts.notifier.start()
        on_stop(stack, "Discord", parts.notifier.close)
    with step("启动 DBOS"):
        url = settings.database_url.get_secret_value()
        DBOS(config={"name": APP_NAME, "system_database_url": url})
        on_stop(stack, "DBOS", DBOS.destroy)
        DBOS.launch()
    return parts.trading_app(reconciliation)
```

`assemble` 建出 spec「模拟盘的装配」表里的全部部件（D11），返回一个只在 bootstrap 里用的 dataclass，`trading_app` 把装配好的用例和启动对账的结果拼成 `TradingApp`；OpenD 行情连接打开以后的关闭在 `assemble` 里登记。骨架里的名字以实现为准，顺序和登记关闭的位置照此。第一次健康判定不会因为 OpenD 失败（spec），放进 `step` 只是为了万一出了别的异常也按启动失败处理。

### D6 失败处理和关闭：ExitStack

```python
class StartupError(RuntimeError):
    """启动的某一步失败：信息是「<步骤>失败：<原因>」，__cause__ 是原来的异常。"""


@contextmanager
def step(name: str) -> Iterator[None]:
    """把这一步里的任何异常转成 StartupError，写明步骤；成功时写一条 INFO。"""
    ...


def on_stop(stack: ExitStack, name: str, close: Callable[[], object]) -> None:
    """登记一项关闭：出错只写 ERROR 日志，不影响别的项；成功写 INFO。"""

    def call() -> None:
        try:
            close()
        except Exception:
            _log.exception("关闭%s时出错", name)
        else:
            _log.info("已关闭%s", name)

    stack.callback(call)


def run(
    environ: Mapping[str, str],
    stop: threading.Event,
    externals: Externals = SYSTEM_EXTERNALS,
    config: BootstrapConfig = DEFAULT_CONFIG,
) -> int:
    """启动，等到 stop 被设置，停止。返回退出码：0 正常停止，1 启动失败。"""
    _log.info("启动 tradingagentspro %s（模拟盘）", version("tradingagentspro"))
    with ExitStack() as stack:
        try:
            start(environ, externals, config, stack)
        except StartupError as error:
            _log.error("启动失败：%s", error, exc_info=error.__cause__)
            return 1
        _log.info("启动完成")
        stop.wait()
        _log.info("收到停止信号，开始停止")
    _log.info("已停止")
    return 0
```

- 每个资源打开以后立刻登记关闭。启动失败时 `return 1` 离开 `with`，`ExitStack` 按相反顺序关掉已经打开的部分；正常停止时同一套代码按相反顺序关闭全部。启动失败和停止共用一个关闭顺序，不会漏
- `on_stop` 吞掉关闭时的异常：`ExitStack` 本身遇到回调抛错会继续调用后面的回调，但最后把异常抛出来，退出码就不对了
- `DBOS()` 构造完就登记 destroy，再 launch：launch 中途失败时后台线程也能停掉
- `notifier.start()` 成功以后才登记 close：失败时 `DiscordNotifier` 已经自己收拾好了（超时时它自己调用 close，登录失败时线程已经结束）
- 失败就以 1 退出，由 systemd 重启再试，不在进程里重试：重试要等待、计数、告警，都是只为少见情况写的代码；配置错误重试也没用，日志里写明了原因
- `run` 不接 `KeyboardInterrupt`：信号处理装好以后 SIGINT 不会变成异常（D10）

考虑过：

- 券商不可用时不退出，带着「还没对账」的执行模块继续运行：执行模块会拒绝所有新订单，但没有人会再去对账，直到「收盘对账」的定时任务；退出重启更简单，也更早恢复
- 手写 try/finally 一层套一层：步骤一多就容易漏关或关错顺序

### D7 启动对账和第一次健康判定

`startup.py` 两个函数，只写日志、不发告警：

```python
def reconcile_at_startup(execution: ExecutionService) -> Reconciliation:
    """对账一次。没有不一致写 INFO；有不一致写 WARNING（带三类不一致的清单）。

    读取券商失败时 BrokerUnavailableError 原样抛出，由 step 转成启动失败。
    """
    ...


def judge_health_at_startup(
    health: HealthService, market_data: MarketDataService, clock: Clock
) -> HealthStatus:
    """按 bootstrap spec「第一次健康判定」的信号判定一次；取不到行情、降级时写 WARNING。"""
    ...
```

- 券商连接按正常：对账失败就已经启动失败了，能走到这一步说明刚刚读到了券商
- 行情：`market_data.last_update()`，`QuoteUnavailableError` 时为 None。OpenD 没开时这次调用最多等 OpenD 的超时（10 秒）
- 先对账、后判定：判定为降级以后，退出降级还要「全部恢复后再对账一次」（degraded-mode spec），所以启动对账算不算数都不影响；而券商连接的信号要靠对账的结果
- 不发告警：add-discord-notify 已经把启动对账的告警交给「收盘对账」，降级告警交给「健康监控接线」；这时 Discord 也还没启动。两个结果都留着（`TradingApp.reconciliation`、`health.status`），那两个变更在 Discord 启动以后补上告警，告警文字只写一份
- WARNING 里的不一致直接用 `Reconciliation` 的 repr：dataclass 的 repr 已经列出股票、客户端订单 ID 和股数，不为日志另写格式，「收盘对账」写告警文字时再定

考虑过：放进 workflows。它们只在启动时、DBOS 启动之前运行一次，不是持久化的流程；定时的版本由「收盘对账」「健康监控接线」放进 workflows，到时 `start` 改为调用它们

### D8 DBOS 在本变更启动

本变更加入依赖，启动和停止 DBOS，现在还没有工作流。

- 启动和停止 DBOS 属于进程的生命周期：它和对账、Discord 的先后关系（D5）现在就要定，「下单流程」只注册工作流
- 同一个数据库：`system_database_url` 就是状态库的地址，DBOS 的表在 `dbos` schema（3.x 的默认值），本系统的表在 `public`。一个地址、一份备份；测试夹具清表时只清 `public`
- DBOS 自己建连接池（默认 20 个连接，还有一个 LISTEN 连接），不和状态库的 engine 共用：DBOS 的等待和通知会长期占着连接
- 应用名 `tradingagentspro`；应用版本用默认的（按注册的工作流代码算）；destroy 不等工作流结束（默认 0），工作流下次启动时恢复。这两项是否要改，留给有了工作流的「下单流程」
- DBOS 3.x 没有管理端口：程序不监听端口

考虑过：

- 整个留给「下单流程」：那个变更已经最大（工作流、巡检、锁、账户数据的拼装），启动顺序也要在那时再改一次
- DBOS 用单独的数据库：两个地址、两份备份，换来的隔离用 schema 就有

### D9 下单流程接上之前

```python
class OrderFlowMissingError(RuntimeError):
    """下单流程还没接上：方案没有提交，按钮的结果没有送达。"""


class AccountPlanIntake:
    """满足 PlanIntake。持仓的板块从账户数据查；提交在下单流程接上之前一律报错。"""

    def __init__(self, account: Callable[[], Account]) -> None:
        self._account = account

    def held_sector(self, symbol: str) -> str | None:
        positions = self._account().positions
        return next((p.sector for p in positions if p.symbol == symbol), None)

    def submit(self, plan: TradePlan) -> RiskDecision:
        raise OrderFlowMissingError("下单流程还没接上，方案没有提交")


def refuse_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：下单流程接上之前没有方案在等确认，一律报错。"""
    raise OrderFlowMissingError("下单流程还没接上，没有方案在等确认")
```

- 装配时 `AccountPlanIntake(execution.account)`
- `held_sector` 现在就接真的：manual-plans 的「对后续变更的影响」写的是账户数据里 `HeldPosition.sector`，执行模块已经给出账户数据，用不着等下单流程；冒烟运行也能顺带检查 Alpaca 账户的读取。`account()` 只读，现在没有别的写入在并发
- 提交和回调报错而不是假装成功：报错时 `/plan` 回复「提交出错」，按钮提示「没有送达」，人不会以为方案进了流程。回调一律报错也满足 add-discord-notify 的要求「这个方案的工作流不在等确认时要抛错」
- `/plan` 的「提交出错」回复第二行是「方案可能已经提交」：是 manual-plans 的通用文字，在这里不准确，但只到「下单流程」接上为止，不为它改适配器
- 「下单流程」替换 `submit` 和 `refuse_decision`，`held_sector` 保留

### D10 停止和信号

- `cli` 在调用 `run` 之前装信号处理：SIGTERM 和 SIGINT 都只做 `stop.set()`。处理函数里不写日志（它可能打断主线程正在写的那条日志），由 `run` 在 `stop.wait()` 返回以后写
- 启动过程中收到信号：`stop` 已经被设置，启动做完以后 `stop.wait()` 立即返回，直接停止。不在每一步之间检查 `stop`：每一步都有超时，最多多等几十秒，systemd 默认的停止超时是 90 秒
- 关闭顺序就是 `ExitStack` 的相反顺序：DBOS → Discord → OpenD 行情连接 → 数据库连接。先停 DBOS，工作流就不再开始新的步骤，之后再关它们用到的 Discord 和 OpenD
- 退出码 0：收到停止信号是正常的停止，`systemctl stop` 和部署时的重启都是这样

### D11 模拟盘的装配

```python
class BootstrapConfig(BaseModel):
    """启动的配置和默认值，含义见 bootstrap spec「交易日历的加载范围」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calendar_first_day: date = DEFAULT_FIRST_DAY
    calendar_lookahead: timedelta = Field(default=DEFAULT_LOOKAHEAD, gt=timedelta(0))


DEFAULT_CONFIG = BootstrapConfig()


def paper_execution(
    client: AlpacaClient, commission: Decimal
) -> tuple[Broker, StopBroker, ExecutionConfig]:
    """模拟盘的券商：两个 Alpaca 适配器共用一个客户端，打开买单和止损单错开。"""
    return (
        AlpacaBroker(client, commission),
        AlpacaStopBroker(client, commission),
        ExecutionConfig(stagger_buys_and_stops=True),
    )


@dataclass(frozen=True, slots=True)
class TradingApp:
    """启动好的程序：以后的流程变更从这里取用例。"""

    clock: Clock
    ids: IdGenerator
    calendar: TradingCalendar
    execution: ExecutionService
    risk: RiskService
    emergency: EmergencyService
    health: HealthService
    market_data: MarketDataService
    notification: NotificationService
    reconciliation: Reconciliation  # 启动对账的结果
```

- 券商的选择集中在 `paper_execution`：错开开关是「用哪个券商」的一部分，M7 加一个 `live_execution`（moomoo 适配器、开关关闭），业务代码不变
- 每笔佣金：`RiskConfig().commission_per_order`，和风控估算用同一个值（add-trade-records 的 design D5）
- 各模块的配置用默认值，不读配置文件：update-risk-loosen-defaults 定了「直接用这些默认值装配风控和紧急退出，不另外配置」，add-risk-rules 的 design 里「M2 由 bootstrap 从配置文件读取」不再适用；别的模块也一样，要改阈值就改 `config.py` 的默认值
- 日历的加载范围放在 `BootstrapConfig`：trading-calendar spec 要求可以配置，默认值用 `nyse_calendar` 的两个常量
- `TradingApp` 是 `start` 的结果，`run` 现在不用它；测试用它检查装配，以后的流程变更从它取用例
- 股票池不装配：它的存储 M4 才有

### D12 测试

- `tests/bootstrap/test_settings.py`：spec「环境变量」的场景；错误信息里没有值；`repr(settings)` 里没有密钥；驱动换成 psycopg
- `tests/bootstrap/test_logs.py`：用 `StringIO` 作 stream 调用 `configure_logging`，检查一行的格式（构造 `created` 已知的 `LogRecord`，进程在 UTC+14）、DEBUG 不输出、httpx 的 INFO 不输出、`dbos` logger 的一条 INFO 只出现一次。每个测试前后保存和恢复根 logger 与 `dbos`、`httpx`、`httpcore` logger 的 handler、级别和传递设置
- `tests/bootstrap/test_order_flow.py`：spec「下单流程接上之前」的场景。`held_sector` 用返回给定 `Account` 的函数；`/plan` 的回复用真实的 `PlanCommand`、`tests/integrations/discord/calls.py` 的 `FakeCall`；按钮用真实的 `DiscordNotifier`、`FakeGateway`、`FakePress`
- `tests/bootstrap/test_startup.py`：spec「启动对账」「第一次健康判定」的场景，用 `tests/fakes/` 的券商、止损单、存储、行情源、时钟和样例日历，`caplog` 检查日志
- `tests/bootstrap/externals.py`：测试用的外部连接——`FakeAlpaca` 的 transport、带 `close()` 和「报错」开关的 OpenD 替身（快照用 `tests/integrations/moomoo/payloads.py` 的行，更新时刻按测试的时刻改写）、`FakeGateway`、`FakeClock`、`FakeIds`
- `tests/bootstrap/test_app.py`（要开着 Docker）：先用 `StringIO` 调用 `configure_logging`，再用 `empty_database` 和上面的外部替身调用 `run`。正常启动的测试先 `stop.set()` 再调用 `run`，启动完成以后立即停止，同步跑完，不用线程；检查日志的先后、Alpaca 收到的请求、数据库里的表和 `dbos` schema、退出码。覆盖 spec「启动顺序」「启动失败」「数据库」「交易日历的加载范围」「模拟盘的装配」「DBOS」「停止」「日志」（日志里没有密钥）的场景。DBOS 是真的，每个测试结束时都已经 destroy
  - 数据库连不上：地址指向 `127.0.0.1` 上一个没人监听的端口，不用 Docker
  - 升级失败：在空数据库里先手工建一张 `risk_reservations`
  - 「启动过程中收到停止信号」：`FakeGateway.run` 里设置 `stop`
  - 「某一项关闭出错」：OpenD 替身的 `close` 抛错
- `tests/bootstrap/test_cli.py`：`--version`、用法错误（`SystemExit` 的代码是 0 和 2）；`run` 子命令把 `os.environ` 和一个 `Event` 交给 `app.run`（monkeypatch），并且装好了两个信号处理（测试结束恢复原来的处理）；向本进程发 SIGTERM 以后 `Event` 被设置
- 冒烟运行（人在自己的终端里做）：见 tasks 的最后一项。不写成 pytest：要看的是真实的启动日志、Discord 里的回复和 Ctrl-C 的退出

## Risks / Trade-offs

- [Discord 连不上时整个程序起不来，盘中巡检（以后）也跟着停] → add-discord-notify 定的；券商那边的止损单一直挂着；systemd 重启，Discord 恢复后自动起来
- [券商不可用时 systemd 反复重启] → 重启间隔由「部署」定（建议不短于 30 秒）；Discord 在券商之后启动，不会每次都重新注册 `/plan`
- [启动对账有不一致、启动时降级，这一阶段只写日志，人看不到] → 现在还不下单；「收盘对账」「健康监控接线」补上告警。在那之前的冒烟运行由人看日志
- [`dbos` logger 的处理依赖 DBOS「已有 handler 就不加自己的」这个行为] → 行为变了最多是日志重复或格式不同，`test_logs.py` 和启动测试会发现
- [DBOS 3.2 发布不久，更新快] → 版本下限是读过文档和源码的 3.2.0；启动测试用真的 DBOS 和 PostgreSQL 18
- [本机跑冒烟运行和部署机器上的程序同时连着同一个 Alpaca 账户和 Discord 机器人] → 冒烟运行前先停掉部署机器上的程序（和 add-discord-notify 的冒烟测试一样）
- [OpenD 没开时 moomoo SDK 不断写重连日志] → 只在 OpenD 出问题时出现，正好提醒人去看

## Migration Plan

- 没有新的数据库迁移；DBOS 第一次启动时在同一个数据库里建 `dbos` schema
- 新依赖 `dbos`，`uv add` 以后更新 `uv.lock`
- 回滚：revert 代码和依赖；数据库里的 `dbos` schema 可以留着，也可以 `DROP SCHEMA dbos CASCADE`
