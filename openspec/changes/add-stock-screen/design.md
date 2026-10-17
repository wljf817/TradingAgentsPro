# Design

## Context

现状：

- `contracts/listing.py` 的 `Listing`（交易所、证券类型、日均成交额、板块）由调用方查好传给 `StockPoolService.add`；股票池按 `StockPoolConfig` 的允许交易所、允许证券类型和流动性门槛检查。项目里没有地方能查到这些信息
- 冷却期的规则在股票池：`cooling_down(entry, moment, calendar, config)` 要用移出以后的记录，`StockPoolService` 只给出池子里的记录（`entry`、`entries`），移出的记录只有它的存储能读
- 外部数据源的写法：模块在 `ports.py` 定义端口和「不可用」异常，适配器放 `integrations/<名字>/`，只 import 模块的包根。moomoo 适配器把 OpenD 的调用失败一律报为不可用（`integrations/moomoo/quotes.py`）；HTTP 适配器用同步 httpx，测试传 `httpx.MockTransport`，超时、连不上、429、5xx 是不可用，其余原样抛出
- 端口也可以由进程里别的部件满足：通知模块的 `PlanIntake` 由 workflows 的 `OrderPlanIntake` 实现，bootstrap 装配
- 模块之间的 import：业务模块只依赖 `statestore`、`programlog` 的包根；规则允许 import 别的模块的包根，跨模块的流程放 workflows
- 状态库：`statestore` 的 `versioned_table` 和 `VersionedStore`，按键保存版本，内容相同不写新版本
- 另外两个数据变更：「新闻和公告」已经 apply（v0008，`integrations/alpaca/news.py` 有 `DATA_URL`）；「财报日历与板块」还没有 apply（v0009，导出 `CompanyDataService.sector(symbol) -> SectorEtf | None`，`SectorEtf` 是值为 ETF 代码的 `StrEnum`，数据源不可用抛 `CompanyDataUnavailableError`，每次 1 个 Finnhub 请求，免费档每分钟 60 次；没有板块的股票不进股票池、粗筛跳过它，已经定了）
- 等待：time 模块全项目禁用，适配器里用 `threading.Event().wait`（`integrations/sec/edgar.py` 的 `_wait`）

数据源（2026-10-17 读本机 moomoo-api 10.11.7108 的源码和 openapi.moomoo.com、docs.alpaca.markets）：

- moomoo `get_stock_filter(market, filter_list, plate_code=None, begin=0, num=200)`：返回 `(RET_OK, (last_page, all_count, ret_list))`，`ret_list` 是 `FilterStockData`，有 `stock_code`（如 `US.AAPL`）、`stock_name` 和请求的字段值，没有交易所和证券类型。`SimpleFilter` 要设 `is_no_filter = False` 才带上 `filter_min`。文档：每 30 秒最多 10 次，每页最多 200 只；按会变的字段排序时翻页之间顺序可能变；只用常规时段的数据；市值的单位没写
- moomoo `get_stock_basicinfo(market, stock_type, code_list)`：给了 `code_list` 时忽略市场和类型，只查这些代码；每行有 `stock_type`（STOCK、ETF、WARRANT 等，不区分 ADR）、`exchange_type`（US_NYSE、US_NASDAQ、US_AMEX、US_PINK 等）、`delisting`。文档：认不出的代码（退市或不存在）照样返回一行，名字是 "unknown stock"，用 delisting 表示是否存在；没写频率限制和 code_list 的上限
- moomoo 还有条件选股 V2（`get_stock_screen`），有交易所筛选，但交易所的取值没有枚举、文档少，不用
- Alpaca `GET /v2/stocks/bars`：参数 symbols、timeframe、start、end（RFC 3339 或 YYYY-MM-DD）、limit（最多 10000，按全部股票合计）、adjustment（raw、split、dividend、all）、feed、page_token、sort；end 的默认值是"有实时权限时为现在，否则为 15 分钟以前"；回复 `{"bars": {"AAPL": [{t, o, h, l, c, v, n, vw}]}, "next_page_token": ...}`，先按股票再按时间排，一页可能只有一只股票

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/screener/                           (新，寻股粗筛)
  __init__.py           导出 Condition、Security、DailyBar、Metrics、Candidate、ScreenRun、
                        SecuritySource、BarSource、SectorSource、ScreenStore、
                        ScreenDataUnavailableError、PostgresScreenStore、ScreenerConfig、
                        ScreenerService
  domain/screen.py      类型；截止交易日和交易日窗口；范围、合格和排序（evaluate）
  domain/indicators.py  日均成交额、相对强度、上升趋势和回调幅度、量比
  ports.py              SecuritySource、BarSource、SectorSource、ScreenStore、
                        ScreenDataUnavailableError
  repo.py               表 screener_runs；PostgresScreenStore
  config.py             ScreenerConfig
  service.py            ScreenerService
modules/stockpool/service.py                (改) StockPoolService.unavailable()
integrations/moomoo/securities.py           (新) MoomooSecuritiesConfig、SecuritiesContext、
                                            MoomooSecuritySource
integrations/alpaca/bars.py                 (新) AlpacaBarsConfig、AlpacaBarSource
bootstrap/migrations/versions/v0010_create_screener_runs.py   (新)
```

- 跨模块传递的 Pydantic 模型：只有 contracts 的 `Listing`。粗筛模块生成它（名单里每只股票带一份、`listing()` 给一份），以后的寻股案件和手动添加把它交给 `StockPoolService.add`。不新增 contracts
- 粗筛模块的类型是 domain 的 frozen dataclass，从包根导出，给以后的寻股案件和状态库快照读
- 粗筛模块依赖 `statestore` 的包根（存储）、`stockpool` 的包根（只用 `StockPoolConfig`，见 D4）、contracts 的 `Listing`、shared 的 `Clock` 和 `TradingCalendar`；不 import 公司数据模块（板块走端口，见 D3）
- 两个适配器只依赖粗筛模块的包根和 contracts
- 股票池只加一个读取方法，表和存储不变

## Goals / Non-Goals

**Goals:**

- 一次调用得到一份可复现的名单：PM 看到的名单、每只股票的指标和上市信息都存进状态库
- 条件、阈值、排序全部是纯函数，按 spec 的场景直接测
- 每个数据源的请求次数可预估：moomoo 约 2 × 股票数 ÷ 200 次，Alpaca 约 2 × 范围内股票数 ÷ 100 次，Finnhub 约 N 次加上跳过的只数

**Non-Goals:**

- 全部合格股票的指标不保存，只存名单
- 板块不缓存：每次粗筛按名次重新查；加入股票池时板块写进记录，之后不变
- 粗筛不拿锁，也不防止同时粗筛：Proposal 规定寻股同一时间只跑一个

按「编程风格」省略的情况：

- moomoo 不区分 ADR，ADR 记为普通股；杠杆 ETF 记为 ETF、杠杆 ETN 不一定识别得出：只有把 ADR 移出允许范围、或者允许 ETF 时才有影响，改配置前先补识别
- NYSE American（US_AMEX）算其他交易所，不在允许范围里
- 盘中粗筛时条件选股按市值排序，翻页之间顺序会变：重复的去掉，漏掉的不补；寻股在收盘后跑
- 优先股、权证等 moomoo 归为 STOCK 的代码：Alpaca 的写法不同、取不到日线，历史不满，不合格
- 只按拆股调整，不按分红调整：高分红股的相对强度略低估
- 基期平均成交量为 0：没有成交就没有日线，这样的股票历史不满，到不了量比
- 名单里的指标是粗筛那一刻的，之后行情变了不更新
- 不在股票池里的持仓（/plan 录入的）不排除：M2 的测试持仓在案件流程开始前清掉
- 两个调用同时用 moomoo 适配器时请求间隔可能不够：只有寻股案件和手动添加两个调用方；超限时报不可用，以后再取
- 两次粗筛同时保存同一个截止交易日：后完成的违反主键报错（state-store spec）
- Alpaca 日线的 v 是带小数的数：按 spec 原样报错（回复格式不对）
- 股票改代码：按 moomoo 现在的代码，Alpaca 的 asof 参数不传

## Decisions

### D1 新建寻股粗筛模块

模块叫 `screener`（寻股粗筛）。股票列表、日线、板块三个端口和粗筛结果的存储都在这里。

- 不把日线端口放进 `marketdata`：那个模块是交易侧的实时行情，没有存储；日线现在只有粗筛用。M6 算信号收益时如果也要日线，由那时的变更决定放在哪里，同一个 Alpaca 适配器可以同时满足两个模块的端口
- 不放进 `stockpool`：股票池管记录和规则，粗筛要连三个数据源、存自己的结果；放进去以后股票池的用例会依赖外部数据源
- 组装上市信息放在这里：交易所、证券类型和日均成交额都来自粗筛用的数据源，算法和粗筛同一个（spec「日均成交额」）

### D2 数据的组合

| 数据 | 来源 | 理由 |
| --- | --- | --- |
| 股票范围 | moomoo 条件选股：美股、市值不低于下限 | Alpaca 没有市值；全市场 1 万多只全取日线太重 |
| 交易所、证券类型 | moomoo 静态信息，按代码分批查 | 条件选股不给这两项；按代码查，股票池允许 ETF 时 ETF 也能正确识别 |
| 全部指标、日均成交额 | Alpaca 日线 | 定义完全由我们控制、能测、能复现；moomoo 历史 K 线额度只有每 7 天 100 只 |
| 板块 | 公司数据模块 | 已经定了，每次 1 个 Finnhub 请求 |

考虑过：用 moomoo 条件选股直接算相对强度、均线位置、量比。少取一大块日线，但这些指标的口径（N 日涨跌幅含不含当天、量比按什么基期）文档没写，只能靠冒烟测试猜，单元测试也测不了规则；美股覆盖也没核实。

### D3 端口

```python
class ScreenDataUnavailableError(RuntimeError):
    """股票列表或日线的数据源超时、断线、限频或出错：这次没有取到，以后再取。"""


class SecuritySource(Protocol):
    """股票列表的数据源。每次调用失败时抛 ScreenDataUnavailableError，不重试。"""

    def universe(self, min_market_cap: Decimal) -> Sequence[Security]:
        """美股里市值不低于 min_market_cap（美元）的股票，不重复，数据源认不出的不在里面。"""
        ...

    def security(self, symbol: str) -> Security | None:
        """一只股票的交易所和证券类型；数据源认不出时为 None。"""
        ...


class BarSource(Protocol):
    """日线的数据源。symbols 非空、不重复，由模块保证。失败时抛 ScreenDataUnavailableError。"""

    def daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> Mapping[str, Sequence[DailyBar]]:
        """start 到 end（含两端）的日线，按交易日从早到晚；没有日线的股票不在结果里。"""
        ...


class SectorSource(Protocol):
    """板块的来源。装配时交进公司数据模块的 CompanyDataService，它的错误原样传出。"""

    def sector(self, symbol: str) -> str | None:
        """板块 ETF 的代码；没有板块时为 None。"""
        ...


class ScreenStore(Protocol):
    """粗筛结果的存储，数据库实现在 repo.py。错误原样抛出，不当作没有结果。"""

    def save(self, run: ScreenRun) -> None: ...

    def get(self, day: date) -> ScreenRun | None:
        """这个截止交易日最新的粗筛结果；没有时为 None。"""
        ...
```

- 板块走端口而不直接 import 公司数据模块：粗筛模块不依赖另一个业务模块的用例，测试用替身；`CompanyDataService.sector` 返回 `SectorEtf | None`，`SectorEtf` 是 `str` 的子类，结构上满足这个端口，mypy 能检查。公司数据模块的不可用异常不在这里分类，原样传出（spec「数据源不可用」）
- 股票列表和日线共用一个不可用异常：调用方的处理一样（以后再粗筛），错误信息里带着请求
- 两个外部数据源分两个端口：来源不同、适配器不同；以后换其中一个只换一个适配器

### D4 允许的范围和流动性门槛取股票池的配置

`ScreenerService` 构造时接收 `StockPoolConfig`（import 股票池的包根），把允许的交易所、证券类型和流动性门槛交给 domain：

```python
@dataclass(frozen=True, slots=True)
class Scope:
    """允许的范围和流动性门槛，取自股票池的配置。"""

    exchanges: frozenset[Exchange]
    security_types: frozenset[SecurityType]
    min_average_dollar_volume: Decimal
```

- spec 要求范围和加入股票池时的检查一致：装配时传同一个配置对象，不会出现两份配置改了一份
- 只依赖股票池的配置类型，不依赖它的用例和存储；股票池不 import 粗筛，没有循环
- 考虑过：在粗筛配置里另设三项、装配时抄一遍。少一个模块间的 import，但两份配置可以不一致，spec 就保证不了

### D5 排除由调用方给出，股票池加「现在不能加入的股票」

`screen(excluded: Collection[str])`：调用方（以后的寻股案件）传 `StockPoolService.unavailable()`。

```python
class StockPoolService:
    def unavailable(self) -> frozenset[str]:
        """现在加入会被拒绝为已在池子里或冷却期内的股票，见 stock-pool spec。不拿锁。"""
```

实现：池子里的全部股票，加上 `store.changed_since(start)` 里 `cooling_down(entry, now, ...)` 为真的。`start` 是今天归属的交易日往前数「冷却期」个交易日那天的收盘时刻：冷却期内的移出，移出时刻都不早于它（归属的交易日晚于那天）。

- 排除用参数不用端口：池子的状态是调用方在这一刻读的，一个集合传进来最直接；和板块不同，板块要按名次逐只查（D8）
- 股票池的这个读取放在本变更：冷却期的规则和移出的记录都在股票池里，别的地方算不出来；寻股要排除冷却期内的股票是本变更的要求
- 考虑过：粗筛模块直接读股票池的存储。违反"不读别的模块的表"，冷却期的算法也会写两份

### D6 截止交易日和交易日窗口

```python
def cutoff(calendar: TradingCalendar, now: datetime, delay: timedelta) -> date:
    """截止交易日：收盘时刻不晚于 now - delay 的最后一个交易日。"""
    return calendar.last_session_before(now - delay).day


def sessions(calendar: TradingCalendar, last: date, count: int) -> tuple[date, ...]:
    """last 及之前共 count 个交易日，从早到晚。"""
    ...


def history_sessions(config: ScreenerConfig) -> int:
    """H：粗筛要取的交易日数，见 stock-screen spec「截止交易日」。"""
    return max(
        config.relative_strength_sessions + 1,
        config.long_ma_sessions,
        config.volume_recent_sessions + config.volume_base_sessions,
        config.pullback_sessions,
        config.dollar_volume_sessions,
    )
```

- 日线延迟默认 15 分钟：Alpaca 免费档只给 15 分钟以前的 SIP 数据，收盘后不满 15 分钟时当天的日线还没收齐。截止交易日之后的日线由适配器去掉，所以盘中运行也只用到前一天
- 窗口按交易日历数，不按日线条数数：合格要求窗口里每个交易日都有日线（spec「粗筛的股票范围」第 3 条），指标就可以直接按位置取（最后一根是截止交易日，倒数第 k+1 根是 k 个交易日以前）
- 交易日历从 2020 年加载（`DEFAULT_FIRST_DAY`），200 个交易日的窗口在范围内

### D7 domain 的类型和计算

```python
class Condition(StrEnum):
    """粗筛的三个条件，顺序就是结果里列出的顺序。"""

    RELATIVE_STRENGTH = "relative_strength"  # 相对强度
    PULLBACK = "pullback"  # 趋势中回调
    VOLUME_SURGE = "volume_surge"  # 成交量异常


@dataclass(frozen=True, slots=True)
class Security:
    """股票列表数据源给出的一只股票。数据来自数据源，不加约束。"""

    symbol: str
    exchange: Exchange
    security_type: SecurityType


@dataclass(frozen=True, slots=True)
class DailyBar:
    """一只股票一个交易日的日线，按拆股调整。数据来自数据源，不加约束。"""

    day: date  # 美东日期
    close: Decimal
    vwap: Decimal  # 成交均价
    volume: int


@dataclass(frozen=True, slots=True)
class Metrics:
    """一只合格股票在截止交易日的指标。"""

    relative_strength: Decimal
    uptrend: bool
    pullback: Decimal  # 回调幅度
    volume_ratio: Decimal  # 量比


@dataclass(frozen=True, slots=True)
class Candidate:
    """名单里的一只股票。listing 加入股票池时直接用。"""

    symbol: str
    listing: Listing
    metrics: Metrics
    conditions: tuple[Condition, ...]  # 满足的条件，按 Condition 的顺序

    @property
    def score(self) -> int:
        return len(self.conditions)


@dataclass(frozen=True, slots=True)
class ScreenRun:
    """一次粗筛的结果，见 stock-screen spec「粗筛的结果」。"""

    day: date  # 截止交易日
    universe: int  # 数据源给出的股票数
    eligible: int  # 合格的股票数，包括被排除的
    candidates: tuple[Candidate, ...]  # 按名次
```

`domain/indicators.py`：

```python
def average_dollar_volume(bars: Iterable[DailyBar], days: Sequence[date]) -> Decimal:
    """days 这几个交易日每天成交均价 × 成交量的平均；没有日线的那天算 0。"""


def measure(
    bars: Sequence[DailyBar], benchmark: Sequence[DailyBar], config: ScreenerConfig
) -> Metrics:
    """bars 和 benchmark 都是窗口里一天一根的日线，最后一根是截止交易日。"""
```

`domain/screen.py`：

```python
@dataclass(frozen=True, slots=True)
class Ranked:
    """排进去的一只合格股票，还没有查板块。"""

    security: Security
    average_dollar_volume: Decimal
    metrics: Metrics
    conditions: tuple[Condition, ...]

    def candidate(self, sector: str) -> Candidate: ...


@dataclass(frozen=True, slots=True)
class Evaluation:
    eligible: int
    ranked: tuple[Ranked, ...]  # 按 spec「得分和排序」，0 分的不在里面


def evaluate(
    securities: Sequence[Security],
    bars: Mapping[str, Sequence[DailyBar]],
    days: Sequence[date],
    scope: Scope,
    config: ScreenerConfig,
) -> Evaluation:
    """范围、合格、指标、条件和排序。基准缺日线时抛 ValueError，写明基准和缺的交易日。"""
```

- 计算全部用 `Decimal`，不四舍五入：存下的值和 spec 的场景逐位相等；成交量是 `int`，平均时转成 `Decimal`
- 相对强度的「前百分比」按"比它高的少于合格数 × 百分比"判断：并列的股票结果相同，和排序的先后无关
- 相对强度的比较包括调用方要排除的股票：名次不随股票池的状态变，同一天的名单只是跳过的不同（spec「跳过要排除的股票」）
- 分数是条件数，存条件不存分数：分数由条件推出，存两份可能不一致
- 范围、合格、排序都在 domain 的 `evaluate` 里，service 只做 I/O 和按名次查板块；spec 的大部分场景在 domain 测

### D8 按名次逐只查板块

service 按 `evaluate` 给出的名次从前往后：在 `excluded` 里的跳过；其余调用 `SectorSource.sector`；没有板块的跳过；取够 N 只就停。

- 只查需要的：给全部合格股票查板块是一千多次 Finnhub 请求；按名次查，一般是 N 次加上跳过的几次
- 先排序再查板块，所以排序不能用板块（相对强度对比 SPY，不对比板块 ETF，见 proposal「需要人决定」第 1 条）
- 查到一半板块数据源不可用：整次报错不保存，已经查到的板块丢掉（spec「数据源不可用」）

### D9 存储：按截止交易日的版本表

```python
RUNS = versioned_table("screener_runs", Column("day", Date, nullable=False))
```

`PostgresScreenStore(engine, clock)` 用一个 `VersionedStore[ScreenRun]`：`save` 是 `save(run.day, run)`，`get` 是 `get(day)`。

- 粗筛结果是「当前市场事实」里的指标，Proposal 要求带版本号、只追加；以截止交易日为键：同一天重跑、结果不变时不多写版本（寻股案件失败重来不会堆版本），排除的股票变了时写新版本，旧版本留着
- 不用一次一行、带运行 ID 的表：还没有调用方要引用某一次运行；快照按 M2 定的做法复制内容
- 只存名单：PM 只看名单；M6 的探索命中率对比的也是"代码选出的"那几只
- 没有筛选列：现在只按截止交易日读

### D10 moomoo 适配器

```python
class MoomooSecuritiesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 条件选股每 30 秒最多 10 次；每个请求之后等这么久
    request_interval: timedelta = Field(default=timedelta(seconds=3), gt=timedelta(0))


class SecuritiesContext(Protocol):
    """适配器用到的 OpenD 行情连接的那部分：moomoo 的 OpenQuoteContext 满足它。"""

    def get_stock_filter(
        self, market: str, filter_list: list[Any], begin: int, num: int
    ) -> tuple[int, Any]: ...

    def get_stock_basicinfo(
        self, market: str, stock_type: str, code_list: list[str]
    ) -> tuple[int, Any]: ...


class MoomooSecuritySource:
    """满足 SecuritySource。"""

    def __init__(
        self,
        context: SecuritiesContext,
        config: MoomooSecuritiesConfig,
        pause: Callable[[timedelta], None] = _wait,
    ) -> None:
        """pause 只给测试用：记下等待而不真的等。"""
```

- 条件选股：一个 `SimpleFilter`：`stock_field = MARKET_VAL`、`filter_min = float(min_market_cap)`、`is_no_filter = False`、`sort = SortDir.DESCEND`；`begin` 每次加 200，直到 `last_page`。按市值排序：收盘后排序稳定，翻页不漏
- 静态信息：代码按每批 200 个调用 `get_stock_basicinfo(Market.US, SecurityType.STOCK, code_list=批)`（有 code_list 时市场和类型被忽略），一批的上限文档没写，冒烟测试用真实的股票范围核对
- 映射见 moomoo-securities spec「静态信息的映射」；`delisting` 为 True 的行去掉
- 每个请求之后调用 `pause(config.request_interval)`，成功失败都等：相邻两个请求、包括相邻两次调用的请求都隔开 3 秒。静态信息的频率限制没写，一律按条件选股的限制等
- 和行情适配器共用一个 OpenD 行情连接（装配时决定），配置只有请求间隔；连接的地址、端口、超时用 `MoomooConfig`
- 失败：`ret != RET_OK` 抛 `ScreenDataUnavailableError`，带上 OpenD 的错误信息，和行情适配器一样（OpenD 只给文字，分不出原因）

### D11 Alpaca 日线适配器

```python
class AlpacaBarsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    secret_key: SecretStr
    base_url: str = DATA_URL
    # 每次请求等多久没有响应就算超时
    timeout: timedelta = Field(default=timedelta(seconds=10), gt=timedelta(0))


class AlpacaBarSource:
    """满足 BarSource。"""

    def __init__(
        self, config: AlpacaBarsConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        """transport 只给测试用：传入 httpx.MockTransport 代替真实的 Alpaca。"""
```

- 每批 100 只：URL 不会太长；100 只 × 200 根 = 2 万根，每批两页
- 不传 end：免费档 end 晚于 15 分钟以前会被拒绝，不传时 Alpaca 自己取到允许的最新时刻；结束日期以后的日线按美东日期去掉。start 写成日期：Alpaca 的日线时间戳是美东零点（UTC 04:00 或 05:00），按 UTC 零点或美东零点理解都包含第一天
- `adjustment=split`：拆股前后价格和成交量连续；成交额（成交均价 × 成交量）在拆股调整下不变。不按分红调整：成交量不跟着调整，成交额会变
- 回复用 `extra="ignore"` 的 Pydantic 模型读，`model_validate_json` 直接读 JSON 文本，价格按十进制精确读取（和 Finnhub 适配器一样）；`t` 是 `AwareDatetime`，`astimezone(ZoneInfo("America/New_York")).date()` 得到交易日
- 失败分类和「新闻和公告」的 Alpaca 新闻源相同；403 原样报错：免费档取了最近 15 分钟的数据才会 403，是程序的问题
- 单独一个配置，不复用 `AlpacaNewsConfig`：名字是新闻的；地址的默认值用同一个 `DATA_URL`，装配时 key 从同样的两个环境变量读
- 不限速：一次粗筛约 30 个请求，远低于每分钟 200 次；和新闻轮询同时撞上 429 时报不可用

### D12 组装上市信息

`listing(symbol)` 依次：`security(symbol)`，为 None 时返回 None；`sector(symbol)`，为 None 时返回 None；按截止交易日取最近「日均成交额的交易日数」个交易日的日线算日均成交额。

- 先查便宜的、会让结果为空的：查不到股票和没有板块都不用再取日线
- 不检查范围和流动性：那是股票池加入时的检查，原因（不在允许范围、流动性不达标）由股票池给出
- 查不到股票和没有板块都返回 None，股票池给出的原因是"股票代码不存在"；手动添加需要分开说明时由那个变更处理（proposal「对后续变更的影响」）

### D13 迁移和测试

迁移 `v0010_create_screener_runs.py`：`revision = "0010"`，`down_revision = "0009"`，写明每一列（day 是 `Date`），不 import 业务代码，`downgrade` 抛 `NotImplementedError`，和 v0005 一样。

测试：

```
tests/fakes/screen.py                          FakeSecuritySource、FakeBarSource、FakeSectorSource、
                                               InMemoryScreenStore（新）
tests/modules/screener/baseline.py             spec 的场景配置和基准数据（新）
tests/modules/screener/domain/test_indicators.py
tests/modules/screener/domain/test_screen.py
tests/modules/screener/test_service.py
tests/modules/screener/test_config.py
tests/modules/screener/test_repo.py            要开着 OrbStack
tests/modules/stockpool/test_service.py        现在不能加入的股票（改）
tests/modules/stockpool/test_repo.py           数据库连不上（改）
tests/integrations/moomoo/securities_payloads.py、test_securities.py
tests/integrations/moomoo/test_securities_smoke.py      moomoo_opend 标记
tests/integrations/alpaca/bars_payloads.py、test_bars.py
tests/integrations/alpaca/test_bars_smoke.py            alpaca_bars 标记
tests/integrations/test_stock_screen_smoke.py           两个标记都带
tests/bootstrap/test_database.py、test_app.py           表的清单、归属、最新版本（改）
```

- 替身：股票列表和日线按安排好的数据给出，记下每次请求（方法和参数），可以安排下一次抛 `ScreenDataUnavailableError`；日线替身按请求的日期范围筛选；板块替身按股票给出，可以安排对某只股票抛错；内存存储按截止交易日保存每个版本（内容不同才追加）
- 基准数据写成一个函数，场景按需覆盖某只股票的收盘价、成交量、成交均价或缺哪天
- moomoo 适配器测试：假的 `SecuritiesContext` 按安排返回 `(RET_OK, (last_page, all_count, items))` 和静态信息的 DataFrame（照 SDK 的列），记下每次调用；`pause` 换成记录函数，等待的时长从配置读
- Alpaca 适配器测试：`httpx.MockTransport` 记录请求、按 page_token 和 symbols 返回；超时由 transport 抛 `httpx.ReadTimeout`，连不上抛 `httpx.ConnectError`
- `source: SecuritySource = MoomooSecuritySource(...)`、`source: BarSource = AlpacaBarSource(...)` 让 mypy 检查适配器满足端口；`sectors: SectorSource = CompanyDataService(...)` 检查公司数据模块满足板块端口（「财报日历与板块」已经 apply）
- 冒烟测试，默认不运行：
  - moomoo（`moomoo_opend`，本机 OpenD 没开时跳过）：市值不低于 20 亿美元的范围超过 1,000 只（美股覆盖）；AAPL 是纳斯达克普通股，JPM、BRK.B 是纽交所普通股，TSM（ADR）是纽交所普通股；市值下限 10 万亿美元时没有 AAPL、1 万亿时有（单位是美元）；SPY、TQQQ 是 ETF；ZZZZQQ 查不到（delisting 为 True）；记下范围里有几只 ETF 和其他交易所的股票，打印出来
  - Alpaca（`alpaca_bars`，没有 `APCA_API_KEY_ID`、`APCA_API_SECRET_KEY` 时跳过）：AAPL、SPY 在一段过去的日期里的日线，交易日和纽交所日历逐日相同、都有 vw；冬令时的一段同样；不带 end 时没有 403；NVDA 2024-06-07 的收盘价按拆股调整后在 100 到 150 之间；60 只股票 200 个交易日超过一页，全部取到；ZZZZQQ 不在结果里、不报错
  - 完整粗筛（两个标记都带，缺一个就跳过）：真的 moomoo 和 Alpaca 适配器、默认配置、板块替身（全部给 XLK）、内存存储：合格超过 500 只，名单 10 只、每只至少 1 分，整次不超过 5 分钟；打印各数据源的请求次数和耗时
- pytest 标记 `alpaca_bars` 登记在 `pyproject.toml`，`addopts` 加 `and not alpaca_bars`
- 每个测试的说明写明对应的 spec 场景；配置的默认值不写测试（测试约定）

## Risks / Trade-offs

- [moomoo 条件选股的美股覆盖、市值单位、静态信息一批的上限都没有核实] → 冒烟测试核对；对不上时先改 spec 和 design，再改适配器
- [moomoo 不区分 ADR 和杠杆 ETN] → 默认配置下没有影响（ADR 允许、ETF 不允许）；改允许范围前要先补识别，写在省略清单里
- [一次粗筛一两分钟，主要等 moomoo 的请求间隔] → 寻股案件在 DBOS 的 step 里跑，排在最低优先级，不挡别的案件
- [Alpaca 每分钟 200 次和新闻轮询共用] → 撞上 429 时整次报不可用，以后再粗筛；不在适配器里排队
- [Alpaca 免费档改了 15 分钟的规则或 SIP 的权限] → 返回 403 原样报错，人能看到
- [相对强度对比 SPY，热门板块的股票会扎堆] → PM 挑选时看板块；风控有板块集中度上限

## Migration Plan

- 新增迁移 v0010，建一张空表；启动时随其他迁移一起升级
- 没有新依赖；没有装配，部署机器上不需要新的环境变量
- 回滚：revert 代码；表留在数据库里没有影响（迁移只升级，见 add-state-store 的 design D6）
