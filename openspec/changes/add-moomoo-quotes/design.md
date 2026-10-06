# Design

## Context

现状：

- 用到行情的地方都由调用方传入数值，项目里没有行情接口：
  - `RiskService.recheck(..., last_price)`：下单前再校验的实时最新价
  - `ExecutionService.backup_stop_due(symbol, last_price)`：本地备用止损
  - 紧急退出的 `Holding.previous_close`、`Holding.last_price`：没有行情时为 None
  - 健康监控的 `HealthSignals.last_quote_at`：行情最近一次更新的时刻；数据延迟只在常规交易时段内检查，今天开盘以来还没有更新时从开盘起算（degraded-mode spec「健康检查」）
- `integrations` 里有 Alpaca、纽交所日历和系统时钟，还没有 moomoo
- 执行模块和 M2 的 DBOS 工作流都是同步的

moomoo（2026-10-06 查阅文档、读 moomoo-api 10.11.7108 源码，并在本机 OpenD 上实际调用过）：

- 用户账户的 OpenAPI 美股股票行情权限是 LV3，订阅额度 100，美股指数没有权限
- 市场快照 `get_market_snapshot(code_list)`：按需拉取，不需要订阅、不占订阅额度；每 30 秒最多 60 次，每次最多 400 只；返回 `(ret, DataFrame)`，`ret` 不是 `RET_OK` 时第二项是错误信息
- 快照的 `update_time` 是美东时间的字符串，文档写 `yyyy-MM-dd HH:mm:ss`，实际带毫秒（`2026-10-06 17:53:59.407`）。盘后它还在更新
- `last_price`、`volume` 只算常规时段：盘前、盘后、夜盘的价格和成交量在 `pre_*`、`after_*`、`overnight_*` 里。盘后实测 `last_price` 是当天收盘价，`after_price` 另有其值
- 不存在的代码不报错：这一行 `update_time` 为空、价格全为 0、`sec_status` 是 N/A。代码缺 `US.` 前缀、或者列表为空，整次调用报错
- SDK 用自己的网络线程和回调线程，没有用 asyncio；所有线程默认不是 daemon 线程
- `OpenQuoteContext(...)` 默认在构造函数里循环重连，直到连上 OpenD 为止；`is_async_connect=True` 时构造立即返回，在后台连接。同步调用在没连上时默认无限等待，`set_sync_query_connect_timeout(秒)` 设上限，超时返回错误 `Connect timeout`
- SDK 依赖 pandas、numpy、protobuf、pycryptodome、simplejson；没有类型标注（没有 py.typed）。import 时会在用户目录下建 `~/.com.moomoo.OpenD/Log`，并往控制台打 INFO 日志

动机见 proposal.md 的 Why，要求见 specs/market-data/spec.md 和 specs/moomoo-quotes/spec.md。

### 模块边界

```
modules/marketdata/              (新，对应架构图的「数据源」)
  __init__.py   导出 Quote、QuoteSource、QuoteUnavailableError、MarketDataConfig、MarketDataService
  domain/quote.py   Quote
  ports.py      QuoteSource、QuoteUnavailableError
  service.py    MarketDataService
  config.py     MarketDataConfig

integrations/moomoo/             (新)
  __init__.py   导出 MoomooConfig、MoomooQuoteSource、open_quote_context
  quotes.py     MoomooConfig、SnapshotContext、MoomooQuoteSource、open_quote_context
```

- 行情模块不依赖别的模块；integrations/moomoo 只依赖行情模块的包根
- 不新增、也不修改跨模块传递的 Pydantic 模型。`Quote` 是行情模块 domain 里的 frozen dataclass，从包根导出，由之后的工作流读出数值，再按各模块现有的参数传进去（紧急退出的 `Holding` 也是这样传的）
- 不改风控、执行、紧急退出和健康监控

## Goals / Non-Goals

**Goals:**

- 交易侧能一次取到一组股票的实时行情，开盘那一刻不会拿到前一天的价格
- 健康监控能拿到一个能反映行情源是否及时的更新时刻，行情源退化成延时行情时能被发现
- OpenD 没开、断线、限频时只报行情不可用，不卡住调用方

**Non-Goals:**

按「编程风格」省略的情况：

- 一次超过 400 只股票：持仓和方案涉及的股票远少于这个数
- 停牌：最新价停在停牌前、更新时刻不动，只影响这一只股票；参考股票 SPY 不会单独停牌
- 美东时间秋季回拨那一小时的歧义（凌晨 1 点到 2 点）：不在交易时段，按 zoneinfo 的默认取法
- 股票改名或退市后 OpenD 查不到：按「查不到的股票」处理，结果里没有
- 前一交易日收盘价有没有按除权除息调整：不核对，只在除息日影响跌幅的计算
- 不核对 OpenD 返回的代码是不是都在请求里：按代码对应，多出来的忽略
- 快照响应的超时用 SDK 内部的值（12 秒），不另外配置
- SDK 自己的日志文件和控制台输出不调整，「启动与装配」需要时再定

## Decisions

### D1 新建行情模块，端口放在它里面

行情是架构图「数据源」节点的一部分，也是共享状态库「当前市场事实」里「数据层」写的那一类。新建 `modules/marketdata/`，定义行情源端口，integrations/moomoo 实现它，之后的工作流通过 `MarketDataService` 取行情。

没有 `repo.py`：本变更不写状态库（proposal 的 Non-goals）。

考虑过：

- 把端口分别放进风控、执行、紧急退出、健康监控：四份几乎一样的端口，也不是这些模块的职责。它们现在接收数值的写法正好，不用改
- 放进 `shared`：shared 是纯区域，只放基础类型和协议；行情源是外部系统的接口，按约定属于某个模块的 ports

### D2 行情的类型

```python
@dataclass(frozen=True, slots=True)
class Quote:
    """一只股票的行情，见 market-data spec「取行情」。数据来自行情源，不加约束。

    - last_price：当前交易日常规交易时段的最新成交价；开盘后还没有成交时为 None
    - previous_close：前一交易日常规交易时段的收盘价
    - updated_at：行情源最近一次更新这只股票行情的时刻，UTC
    """

    symbol: str
    last_price: Decimal | None
    previous_close: Decimal
    updated_at: datetime
```

端口：

```python
class QuoteUnavailableError(RuntimeError):
    """行情源超时、断线、限频或出错：这次没有取到行情。"""


class QuoteSource(Protocol):
    """行情源。symbols 非空、不重复，由行情模块保证。

    结果按股票代码对应；行情源没有数据的股票不在结果里。失败时抛 QuoteUnavailableError。
    """

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Quote]: ...
```

`last_price` 可以为 None，而不是在开盘后还没有成交时给前一天的收盘价：

- Proposal 写的是"开盘后的第一个价格就会触发"紧急退出，以及开盘时由下单前再校验检查跳空；把前一天的收盘价当成今天的价格，会让开盘时的跳空检查看不到跳空
- 紧急退出的 `Holding.last_price` 本来就允许为 None（没有行情时不判定），可以直接用

### D3 用例：取行情和参考股票的更新时刻

```python
class MarketDataService:
    def __init__(self, source: QuoteSource, config: MarketDataConfig) -> None: ...

    def quotes(self, symbols: Iterable[str]) -> dict[str, Quote]:
        """去重后向行情源取；没有股票时直接返回空，不调用行情源。"""

    def last_update(self) -> datetime:
        """参考股票的行情的更新时刻；没有它的行情时抛 QuoteUnavailableError。"""
```

`MarketDataConfig` 只有一项 `reference_symbol: str = Field(default="SPY", min_length=1)`。

「行情最近一次更新的时刻」用参考股票的更新时刻，而不是：

- 我们收到行情的时刻：行情源退化成延时行情（推广期结束，或者权限被别的设备占走）时照样每次都收得到，发现不了
- 最近取到的全部行情里最晚的更新时刻：要在内存里记状态；没有持仓时没有行情可取，开盘 60 秒后就会误报数据延迟
- OpenD 的服务器时间（`get_global_state`）：只说明 OpenD 在线，不说明行情及时

SPY 在常规时段里几乎每秒都有成交，延时行情会让它的更新时刻落后十几分钟。每次调用取一次，健康监控接线时每轮多一次快照请求。

行情源报错时，`MarketDataService` 原样抛出 `QuoteUnavailableError`，不重试：之后怎样处理由各流程决定（proposal「对后续变更的影响」）。

### D4 用市场快照，不订阅

适配器只用 `get_market_snapshot`：

- 不占订阅额度（100），不用维护订阅（订阅以后至少 1 分钟才能退订）
- M2 的使用方都是需要时取一次：下单前再校验一次，紧急退出、备用止损、健康监控每轮一次。每轮把全部持仓放进一次请求，远低于每 30 秒 60 次
- 同步调用，和执行模块、DBOS 工作流的写法一致

考虑过：

- 订阅报价，再用 `get_stock_quote` 读 OpenD 本地缓存：每次读不经过服务器，但要管理订阅：持仓变了要增删订阅，退订有 1 分钟的限制，重连以后要重新订阅
- 订阅推送，在回调里更新内存里的行情：回调在 SDK 的线程里执行，要加锁；还要处理推送中断。M4 的事件检测需要推送时再做

### D5 快照的映射

```python
NEW_YORK = ZoneInfo("America/New_York")


def _quote(symbol: str, row: Mapping[str, Any]) -> Quote | None:
    """快照的一行；update_time 为空（OpenD 查不到这只股票）时为 None。"""
```

- 价格：`Decimal(repr(float(value)))`。SDK 给的是 numpy 的 float64，先转成 Python 的 float，再取最短的十进制写法，333.63 得到 `Decimal("333.63")`；`Decimal(float)` 会得到 333.62999999999999545…
- 最新价：`volume` 为 0 时为 None，否则是 `last_price`。用成交量判断"当天常规时段有没有成交"，因为 `volume` 只算常规时段。开盘前 OpenD 已经把成交量清零，这一点没有文档，由冒烟测试在开盘前验证（D8）
- 更新时刻：`datetime.fromisoformat(text)`，带不带毫秒都能解析；`.replace(tzinfo=NEW_YORK)` 按当天的夏令时或冬令时解读，再 `.astimezone(UTC)`。不依赖本机时区，测试进程在 UTC+14 运行也能验证
- 代码：请求时 `f"US.{symbol}"`；按请求时建的「代码 → 股票」表把返回的行对应回股票

考虑过：

- 用 `open_price` 是否为 0 判断开盘后有没有成交：和成交量一样没有文档，而成交量和"有没有成交"的含义更直接
- 比较 `update_time` 的日期和今天：盘前盘后它也在更新，分不出常规时段有没有成交

### D6 适配器和连接

```python
class MoomooConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=11111, ge=1, le=65535)
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class SnapshotContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。"""

    def get_market_snapshot(self, code_list: list[str]) -> tuple[int, Any]: ...


class MoomooQuoteSource:
    def __init__(self, context: SnapshotContext) -> None: ...


def open_quote_context(config: MoomooConfig) -> OpenQuoteContext:
    """建立 OpenD 行情连接：不等连上就返回；调用方用完以后 close()。"""
```

- `open_quote_context` 先调 `SysConfig.set_all_thread_daemon(True)`，再用 `is_async_connect=True` 构造，然后 `set_sync_query_connect_timeout(config.timeout 的秒数)`：
  - 构造不阻塞：OpenD 没开时主程序照样能启动，行情不可用由数据延迟检查发现、进入降级
  - 没连上时一次调用最多等超时时长，然后返回错误，适配器报行情不可用
  - daemon 线程：忘了 close 时进程也能退出。正常退出由「启动与装配」调用 `close()`
- 适配器只依赖 `SnapshotContext`，测试传入替身，不用真的连 OpenD
- `ret` 不是 `RET_OK` 时抛 `QuoteUnavailableError(f"OpenD 快照：{data}")`。SDK 把超时、断线、限频、参数错误都放在返回值里，不抛异常
- 适配器不重试，理由同 Alpaca：调用方会在下一轮再取

mypy：SDK 没有类型标注，`pyproject.toml` 对 `moomoo`、`moomoo.*` 设 `ignore_missing_imports = true`，按 Any 处理；类型边界是 `SnapshotContext` 和 `Quote`。测试要构造 pandas 的 DataFrame，`pandas`、`pandas.*` 同样处理。

考虑过：适配器自己按 `ret` 和错误信息区分"连不上"和"参数错误"。参数只来自持仓和方案，出错只能是程序的问题，一并报行情不可用，错误信息里能看出原因。

### D7 新增依赖

`uv add "moomoo-api>=10.11.7108"`，和用户 OpenD 的版本一致。SDK 顺带装上 pandas、numpy 等，只在 `integrations/moomoo` 里用；纯区域的白名单不变。

### D8 测试

- `tests/fakes/quotes.py`：`FakeQuoteSource`，按股票保存行情；记录每次被请求的股票；可以安排下一次调用抛 `QuoteUnavailableError`
- `tests/modules/marketdata/`：`test_service.py`、`test_config.py`，覆盖 market-data spec 的全部场景
- `tests/integrations/moomoo/payloads.py`：2026-10-06 从真实 OpenD 录下的快照（moomoo-quotes spec 的基准数据），保留映射用到的字段和盘前盘后的价格；函数按需覆盖字段，组成 pandas 的 DataFrame，类型和 SDK 返回的一致（numpy 的 float64、字符串的 update_time）
- `tests/integrations/moomoo/test_quotes.py`：`FakeOpenD` 满足 `SnapshotContext`，记录收到的代码列表，返回安排好的 DataFrame 或错误
- 「OpenD 没开」用真的 SDK：先绑定一个空闲端口再关掉，得到一个没有服务的端口，超时 1 秒。构造和取行情都放进线程里跑，`join(timeout=5)` 以后线程必须已经结束（`time` 被禁用，不直接计时）；结束后 `close()`
- 每个 spec 场景一个测试，名字或说明里写明场景；`source: QuoteSource = MoomooQuoteSource(...)` 让 mypy 检查适配器满足端口
- 冒烟测试 `tests/integrations/moomoo/test_opend_smoke.py`，加 `moomoo_opend` 标记，默认不运行（`addopts` 改为 `-m "not alpaca_paper and not moomoo_opend"`）；本机 127.0.0.1:11111 连不上时跳过。用系统时钟和纽交所日历判断当前在哪个时段：
  - 任何时候：AAPL、SPY 都有行情，前一交易日收盘价大于 0；ZZZZQQ 不在结果里；`last_update()` 等于 SPY 的更新时刻
  - 常规交易时段内：SPY 有最新价，更新时刻和现在相差不超过 10 秒（证明拿到的是实时行情）
  - 当天开盘前 30 分钟内：SPY 没有最新价（验证 D5 的"开盘前成交量已清零"）
  - 不在对应时段时跳过那一项

## Risks / Trade-offs

- [开盘前 OpenD 的成交量是否已经清零，没有文档] → 冒烟测试在开盘前 30 分钟内运行一次验证；不符时先改 spec 和 design（比如改用开盘价判断），再改代码
- [推广期结束、或者行情权限被别的设备占走以后，OpenD 是报错还是给延时行情，没有验证] → 两种都会被发现：报错时取不到更新时刻，给延时行情时更新时刻落后，数据延迟检查都会在超过上限后进入降级并告警
- [模拟盘的行情来自 moomoo，成交在 Alpaca，两边价格可能差几分钱] → 只影响价格偏离检查和备用止损在边界上的判断；实盘的行情和成交都在 moomoo
- [SDK 没有类型标注、带进 pandas 等依赖、import 时写用户目录] → 只在 `integrations/moomoo` 里用，结果马上转成 `Quote`
- [moomoo-api 和 OpenD 版本不一致可能出问题] → 版本下限和用户的 OpenD 一致；「部署」写明两者一起升级

## Migration Plan

- 新增依赖 moomoo-api；没有数据库迁移
- 回滚：revert 代码即可，其他代码还没有用到这两个包
