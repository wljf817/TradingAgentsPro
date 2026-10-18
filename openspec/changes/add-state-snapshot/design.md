# Design

## Context

现状：

- `modules/statestore` 只有 `repo.py`：`connect`、`versioned_table`、`VersionedStore`（按键保存版本，内容和最新版本相同时不写），各模块的 repo 用它定义自己的表。它不 import 任何业务模块，所有带表的模块都 import 它
- 各来源给出的事实和类型：
  - 执行模块 `ExecutionService.account() -> Account`：权益、已结算现金、前一交易日收盘权益（Alpaca 的 `last_equity`）、持仓（`HeldPosition`：股票、板块、股数、均价、止损价……）；读券商失败抛 `BrokerUnavailableError`；只读，不拿锁（`workflows/account_snapshot.py` 也这样读）。当日盈亏没有现成的字段，紧急退出用"前一交易日收盘权益减权益"判断单日亏损
  - 行情模块 `MarketDataService.quotes(symbols) -> dict[str, Quote]`：一次取、去重；行情源没有的股票不在结果里；失败抛 `QuoteUnavailableError`。`Quote` 是 domain 的 frozen dataclass
  - 股票池 `StockPoolService.entries()`（池子里的全部，按代码）和 `unavailable()`（现在不能加入的股票）；`PoolEntry` 是 domain 的 frozen dataclass，带论点、评分、时间
  - 新闻和公告 `NewsService.articles(symbol, since)`、`filings(symbol, since)`：只读状态库；`NewsArticle`、`Filing` 已经在 contracts（当时就是给快照和网关读的）
  - 公司数据 `CompanyDataService.releases(symbol)`、`surprises(symbol)`：只读状态库；`EarningsRelease`、`SurpriseHistory`、`ReleaseTime` 是 domain 的类型。`sector(symbol)` 每次向 Finnhub 请求
  - 寻股粗筛 `ScreenerService.screen(excluded) -> ScreenRun`：取数据源、存 `screener_runs`、返回结果；数据源失败抛 `ScreenDataUnavailableError`，板块数据源的错误原样抛出；`ScreenRun`、`Candidate`、`Condition` 是 domain 的类型
- 调度器的 `define_case(trigger, status) -> CaseDefinition` 给出案件类型和股票（寻股和每周复盘没有股票，卖出复盘有）
- `contracts/signal.py` 的 `EntryId` 是 `Annotated[str, Field(min_length=1)]`，注释写着"格式 M4 再定"；`TaskRequest`、`Task` 的 `inputs` 和信号的 `cited_entries` 都用它
- 程序日志：模块在 service 里拿一个 `ProgramLog` 写自己的条目；跟进审批模块以「调度器」的身份写
- 交易日的写法：`StockPoolService.unavailable()` 用 `session_closing_after(now)` 再 `previous_session` 往前数 N 次、取那天的收盘时刻
- workflow 的配置写在 workflow 文件里（`workflows/patrol.py` 的 `PatrolConfig`）

动机见 proposal.md，要求见 specs/。

### 模块边界

```
contracts/snapshot.py                 (新) EntryKind、entry_id、entry_version、check_reason；
                                      SnapshotEntry 和各种条目、Snapshot
modules/statestore/
  ports.py                            (新) SnapshotStore
  repo.py                             (改) 加 SNAPSHOTS 表和 PostgresSnapshotStore
  service.py                          (新) SnapshotService
  __init__.py                         (改) 说明写上快照表；导出上面三个
modules/programlog/domain/entry.py    (改) Action.TAKE_SNAPSHOT
workflows/snapshot_flow.py            (新) SnapshotConfig、SnapshotFlow
contracts/signal.py                   (改) EntryId 的注释
bootstrap/migrations/versions/v0011_create_snapshots.py   (新)
```

- 跨模块传递的 Pydantic 模型：`contracts/snapshot.py` 的 `Snapshot` 和各种条目。`SnapshotFlow` 生成它，`SnapshotService` 存和读它；以后的「替身」「案件工作流」「案件记录」、M5 的网关和 M6 的回放读它。条目里直接用已有的 contracts：`NewsArticle`、`Filing`、`Listing`、`StockStatus`、`CaseType`
- 状态库模块只依赖 contracts、shared 和 `programlog` 的包根，不 import 业务模块：所有带表的模块都 import 状态库，反过来 import 就成环
- `SnapshotFlow` 依赖执行、行情、股票池、新闻和公告、公司数据、寻股粗筛、状态库这几个模块的包根
- 读这几个模块不改它们：都用已有的公开方法

## Goals / Non-Goals

**Goals:**

- 一次调用得到一版完整、可复现的快照：存下以后不再依赖来源表的内容，M6 只凭快照就能回放
- 给出以后几个变更直接用的接口（D6、D7），不用再改快照
- 条目 ID 凭案件 ID 就能查回，不用另外记"这个 Result 读的是哪一版"

**Non-Goals:**

- 不做全局的条目编号，不让别的表引用快照里的条目
- 不缓存、不去重：两版快照内容一样也各存一份
- 不对快照做摘要或裁剪：怎样放进 prompt 由 M5 定

按「编程风格」省略的情况：

- 寻股、每周复盘带了股票：不检查（由调度器的 `define_case` 保证）；针对单只股票的案件没给股票时报错
- 第 n 版要求第 n-1 版已经存在：不检查
- 同一版再生成时给出的案件类型、股票和存下的不同：返回存下的那一版
- 两个调用同时生成同一个案件的同一版：后保存的违反主键报错
- 保存成功、写程序日志出错以后重做：返回存下的那一版，这一版少一条程序日志
- 寻股的粗筛要几分钟：行情在粗筛之后取，比生成时刻晚几分钟
- 新闻的发布时刻晚于生成时刻（时钟回退）：照样放进快照
- 不在股票池里的持仓（/plan 录入的）：在账户里、有行情条目，股票池里没有它
- 生成时刻超出交易日历的覆盖范围：交易日历报错，原样抛出
- 新闻很多时快照很大：不截断

## Decisions

### D1 快照的表归状态库模块，组装放 workflows

- 快照表 `statestore_snapshots` 只由状态库模块写：Proposal 把快照写成共享状态库自己的东西（「共享状态库」读写规则），也没有别的模块适合；状态库模块从"各表共用的写法"变成"共用的写法加上快照"
- 组装要读七个模块，按架构规则放 workflows；状态库模块只负责存、读和查条目，不知道快照里的事实从哪来
- 考虑过：新建 `modules/snapshot`。多一个模块，表名前缀和状态库的关系反而说不清；状态库模块现在没有 service，正好加上
- 考虑过：组装放进状态库的 service。它要 import 七个业务模块，而它们都 import 状态库，成环

### D2 快照的格式在 contracts 另定

```python
type EntryId = Annotated[str, Field(min_length=1)]  # 已有，在 contracts/signal.py


class EntryKind(StrEnum):
    """条目的类别：条目 ID 里版本后面的那一段。"""

    ACCOUNT = "account"  # 账户
    MARKET = "market"  # 市场状态
    POOL = "pool"  # 股票池
    QUOTE = "quote"  # 一只股票的行情
    NEWS = "news"  # 一条新闻
    FILING = "filing"  # 一份公告
    EARNINGS = "earnings"  # 一次财报发布
    SURPRISES = "surprises"  # 一只股票的 surprise
    SCREEN = "screen"  # 粗筛结果


class SnapshotEntry(BaseModel):
    """快照里的一个条目。entry_id 在案件的全部版本里不重复，见 spec「条目和条目 ID」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: EntryId


class HeldStock(BaseModel):
    """账户里的一只持仓。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    sector: str
    quantity: int
    average_cost: Decimal
    stop_price: Decimal


class AccountEntry(SnapshotEntry):
    """账户：执行模块的账户数据。day_pnl 是 equity 减 previous_close_equity。"""

    equity: Decimal
    previous_close_equity: Decimal
    day_pnl: Decimal
    settled_cash: Decimal
    positions: tuple[HeldStock, ...]  # 按股票代码


class MarketEntry(SnapshotEntry):
    """市场状态：生成时刻归属的交易日，以及生成时刻是否在它的常规交易时段里。"""

    trading_day: date
    in_session: bool
    session_open: AwareDatetime
    session_close: AwareDatetime


class PoolMember(BaseModel):
    """股票池里的一只股票。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    status: StockStatus
    sector: str


class StockPoolEntry(SnapshotEntry):
    """股票池。"""

    members: tuple[PoolMember, ...]  # 按股票代码


class QuoteEntry(SnapshotEntry):
    """一只股票的行情。last_price：开盘后还没有成交时为 None。"""

    symbol: str
    last_price: Decimal | None
    previous_close: Decimal
    updated_at: AwareDatetime


class NewsEntry(SnapshotEntry):
    """一条新闻，原样复制。"""

    article: NewsArticle


class FilingEntry(SnapshotEntry):
    """一份公告，原样复制。"""

    filing: Filing


class EarningsTime(StrEnum):
    """财报的发布时段，取值和公司数据模块的 ReleaseTime 相同。"""

    BEFORE_OPEN = "before_open"  # 盘前
    AFTER_CLOSE = "after_close"  # 盘后
    DURING_MARKET = "during_market"  # 盘中


class EarningsEntry(SnapshotEntry):
    """一次财报发布的最新版本。没有的项为 None。"""

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    day: date  # 发布日，美东日期
    time: EarningsTime | None
    eps_estimate: Decimal | None
    eps_actual: Decimal | None
    revenue_estimate: Decimal | None
    revenue_actual: Decimal | None


class SurpriseQuarter(BaseModel):
    """一个季度的 EPS surprise。没有的项为 None。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: date  # 报告期
    fiscal_year: int
    fiscal_quarter: int
    eps_actual: Decimal | None
    eps_estimate: Decimal | None
    surprise: Decimal | None
    surprise_percent: Decimal | None


class SurprisesEntry(SnapshotEntry):
    """一只股票最近一次保存的 surprise；数据源没有时 quarters 为空。"""

    symbol: str
    quarters: tuple[SurpriseQuarter, ...]  # 按报告期从新到旧


class ScreenCondition(StrEnum):
    """粗筛的条件，取值和寻股粗筛模块的 Condition 相同。"""

    RELATIVE_STRENGTH = "relative_strength"  # 相对强度
    PULLBACK = "pullback"  # 回调
    VOLUME_SURGE = "volume_surge"  # 成交量异常


class ScreenCandidate(BaseModel):
    """粗筛名单里的一只股票。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    listing: Listing
    relative_strength: Decimal
    pullback: Decimal  # 回调幅度
    volume_ratio: Decimal  # 量比
    conditions: tuple[ScreenCondition, ...]  # 满足的条件

    @property
    def score(self) -> int:
        return len(self.conditions)


class ScreenEntry(SnapshotEntry):
    """寻股的粗筛结果。"""

    day: date  # 截止交易日
    universe: int  # 数据源给出的股票数
    eligible: int  # 合格的股票数
    trending: int  # 在上升趋势里的股票数
    candidates: tuple[ScreenCandidate, ...]  # 按名次


class Snapshot(BaseModel):
    """一个案件的一版快照，见 state-snapshot spec。没有的部分：元组为空，单个条目为 None。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    case_id: UUID
    version: int = Field(ge=1)
    case_type: CaseType
    symbol: str | None  # 寻股和每周复盘没有
    taken_at: AwareDatetime  # 生成时刻
    reason: str | None  # 生成的原因；第 1 版没有
    account: AccountEntry
    market: MarketEntry
    pool: StockPoolEntry
    quotes: tuple[QuoteEntry, ...]  # 按股票代码
    news: tuple[NewsEntry, ...]  # 按发布时刻、新闻 ID
    filings: tuple[FilingEntry, ...]  # 按受理时刻、accession number
    earnings: tuple[EarningsEntry, ...]  # 按发布日
    surprises: SurprisesEntry | None
    screen: ScreenEntry | None

    def entries(self) -> tuple[SnapshotEntry, ...]:
        """全部条目，按 spec「条目和条目 ID」的先后。"""
        optional = tuple(e for e in (self.surprises, self.screen) if e is not None)
        listed = (*self.quotes, *self.news, *self.filings, *self.earnings)
        return (self.account, self.market, self.pool, *listed, *optional)

    def entry(self, entry_id: str) -> SnapshotEntry | None:
        """这一版里 ID 是 entry_id 的条目；没有时为 None。"""
        return next((e for e in self.entries() if e.entry_id == entry_id), None)

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_reason(self.version, self.reason)
        ids = [e.entry_id for e in self.entries()]
        prefix = f"s{self.version}/"
        if len(set(ids)) != len(ids) or not all(i.startswith(prefix) for i in ids):
            raise ValueError(f"条目 ID 必须不重复，并以 {prefix} 开头")
        return self
```

- 不直接用各模块 domain 的 dataclass（`Quote`、`PoolEntry`、`EarningsRelease`、`SurpriseHistory`、`ScreenRun`）：它们是模块内部的类型，快照是要长期存着、给别的模块和回放读的格式；字段跟着模块改，旧快照就读不回来了。复制一遍字段，转换写在 `SnapshotFlow` 里，测试逐项核对
- `ReleaseTime`、`Condition` 两个枚举也复制一份，取值相同，转换按值；考虑过把它们挪进 contracts 让模块 import，但那要改公司数据和寻股粗筛两个模块，本变更不碰它们
- 新闻和公告直接用 contracts 的 `NewsArticle`、`Filing`：那两个就是为快照和网关定的格式
- `PoolMember` 只有代码、状态、板块：论点属于案件上下文（「案件工作流」从股票池读），评分属于已确认的结论
- 每个条目自己带 `entry_id`，存下的就是 ID 本身：以后改了写法，旧快照和引用它们的 Result 照样对得上
- 一个 `Snapshot` 用具名字段，不用一张"条目列表"：替身和网关按种类取（`snapshot.quotes`、`snapshot.screen`）更直接；按 ID 找用 `entry()`
- 账户里的持仓不带最新价：最新价在行情条目里，取不到行情时不用均价顶替（风控的 `account_snapshot` 那样顶替是为了估市值，给 agent 看会误导）

### D3 放哪些事实，日线和指标不放

放进来的就是 spec 的三张表；不放的见 proposal 的 Non-goals。日线和指标是本变更自己的选择：

- M4 没有人读：替身不看事实，按案件类型给固定的结论
- 放哪些还定不下来：Proposal 只说 Quant 解读"代码算好的行情和指标"，没列指标；M5 接 Quant 时按它的任务类型（价格反应、趋势和支撑阻力、波动情况）定，到时候加一种条目，已有的条目不变
- 日线要另外取（Alpaca），每个案件多一个外部请求、多一个会失败的来源

### D4 条目 ID 带版本，按案件查

```python
_PREFIX = re.compile(r"s([1-9][0-9]*)/")


def entry_id(version: int, kind: EntryKind, key: str | None = None) -> str:
    """条目 ID：s<版本>/<类别>，有键时 s<版本>/<类别>/<键>。"""
    head = f"s{version}/{kind}"
    return head if key is None else f"{head}/{key}"


def entry_version(text: str) -> int | None:
    """条目 ID 前缀里的版本；不是 s<版本>/ 开头时为 None。"""
    match = _PREFIX.match(text)
    return None if match is None else int(match.group(1))
```

- 带版本：Critic 的补充研究会生成第 2 版，同一只股票的行情在两版里不同；ID 带版本，就在整个案件里不重复，Result 和信号引用的 ID 只凭案件 ID 就能找回（Result 经它的 Task 知道案件），不用另外记"这次调用读的是哪一版"
- 只在一个案件里不重复，不做全局唯一：引用条目的东西（Task、Result、结论）都属于一个案件；全局唯一要把案件 ID 也写进去，模型读着、抄着都费劲
- 写法可读：`s1/quote/BBB`、`s1/news/24518832`，模型看得懂、照抄不容易错；键用来源自己的标识（股票代码、新闻 ID、accession number、财报发布的 `AAPL:2026Q4`）
- 只解析前缀：键里可以有任何字符（accession number 有横线，财报发布有冒号），查条目时按整个 ID 比较
- 考虑过：每一版里重新从 E-1 开始编号。短，但同一个 ID 在两版里是不同的条目，要查回就得知道版本
- 考虑过：版本相同的条目沿用旧 ID、只有变了的换新 ID。Task 去重能多复用一些，但规则复杂，"变没变"还要逐条比较

### D5 版本由调用方给出，同一版再生成返回存下的

```python
def check_reason(version: int, reason: str | None) -> None:
    """第 1 版没有原因，之后的版本必须有，去掉首尾空白后不为空。不符合时抛 ValueError。"""
```

- 「案件工作流」把生成快照放进 DBOS 的 step：step 做完、还没记下结果时进程崩溃，恢复后会再做一次。由调用方给出版本号，"已经有这一版就返回它"就是幂等的；如果版本号由快照自己数（最新的一版加 1），重做会多出一版
- 再生成时不重新读数据、不写程序日志：重做的结果和第一次相同
- 先检查原因再看存没存：第 1 版带了原因、第 2 版没带原因，不管存没存都报错，不会因为碰巧存在而放过

### D6 状态库模块：存储和用例

```python
class SnapshotStore(Protocol):
    """快照的存储，数据库实现在 repo.py。错误原样抛出，不当作没有快照。"""

    def add(self, snapshot: Snapshot) -> None:
        """写入一版；这个案件已经有这一版时报错，已有的不变。"""
        ...

    def get(self, case_id: UUID, version: int) -> Snapshot | None:
        """这个案件的这一版；没有时为 None。"""
        ...

    def latest(self, case_id: UUID) -> Snapshot | None:
        """这个案件版本号最大的一版；还没有快照时为 None。"""
        ...


class SnapshotService:
    """快照的入口：保存、读取、按条目 ID 查条目。不拿锁。"""

    def __init__(self, store: SnapshotStore, log: ProgramLog) -> None:
        self._store = store
        self._log = log

    def save(self, snapshot: Snapshot) -> None:
        """写入一版，再写一条生成快照的程序日志。这一版已经存在时报错，不写程序日志。"""

    def snapshot(self, case_id: UUID, version: int) -> Snapshot | None:
        """这个案件的这一版；没有时为 None。"""

    def latest(self, case_id: UUID) -> Snapshot | None:
        """这个案件版本号最大的一版；还没有快照时为 None。"""

    def resolve(self, case_id: UUID, entry_id: str) -> SnapshotEntry | None:
        """按条目 ID 的前缀找到那一版，在里面找这个条目；找不到时为 None。"""
        version = entry_version(entry_id)
        if version is None:
            return None
        found = self._store.get(case_id, version)
        return None if found is None else found.entry(entry_id)
```

表和数据库实现：

```python
SNAPSHOTS = versioned_table("statestore_snapshots", Column("case_id", Uuid, nullable=False))


class PostgresSnapshotStore:
    """满足 SnapshotStore：键是案件 ID，版本号是快照自己的版本，整份快照存成 JSONB。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
```

- 表的形状和别的版本表相同（键、版本号、写入时刻、内容），用 `versioned_table` 定义；但不用 `VersionedStore.save`：它的版本号是"最新的加 1"，快照的版本号要和条目 ID 的前缀一致，必须用快照自己的。`add` 直接插入，主键（案件 ID、版本）冲突就报错；`get` 按主键读，`latest` 按版本号倒序取一行
- 写入时刻从时钟取，和别的版本表一样；快照里另有生成时刻（开始生成的时刻），两者差几秒到几分钟
- 内容用 `model_dump(mode="json")` 存、`model_validate` 读：Decimal 存成文字，位数不丢；读不出来时抛 pydantic 的 `ValidationError`
- 没有别的索引：只按案件 ID 读，主键的第一列就是它
- `resolve` 读出整版再找条目：一版几十个条目，查一个 ID 的代价是读一行 JSONB，现在只有零星的调用

### D7 生成快照的 workflow

```python
class SnapshotConfig(BaseModel):
    """快照的配置和默认值，含义见 state-snapshot spec「快照配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每份快照都带这些指数的行情
    indexes: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        default=("SPY", "QQQ"), min_length=1
    )
    # 新闻回看的交易日数，含当前交易日
    news_sessions: int = Field(default=5, gt=0)
    # 公告回看的交易日数，含当前交易日；约一个季度
    filing_sessions: int = Field(default=65, gt=0)


class SnapshotFlow:
    """生成快照：读各模块的事实，组成一版快照存下，见 state-snapshot spec。

    只读执行模块、股票池、新闻和公告、公司数据，不拿锁；寻股的粗筛写的是粗筛模块自己的表。
    """

    def __init__(
        self,
        execution: ExecutionService,
        market_data: MarketDataService,
        pool: StockPoolService,
        news: NewsService,
        company_data: CompanyDataService,
        screener: ScreenerService,
        snapshots: SnapshotService,
        clock: Clock,
        calendar: TradingCalendar,
        config: SnapshotConfig,
    ) -> None: ...

    def take(
        self,
        case_id: UUID,
        case_type: CaseType,
        symbol: str | None,
        *,
        version: int = 1,
        reason: str | None = None,
    ) -> Snapshot:
        """生成并保存这个案件的第 version 版；这一版已经存在时返回存下的，不读数据。

        原因不对、针对单只股票的案件没给股票、案件股票不在股票池里时抛 ValueError；
        数据读不到时原样抛出，什么都不保存。
        """
```

`take` 的步骤：

1. `check_reason(version, reason)`；`snapshots.snapshot(case_id, version)` 有就返回
2. `now = clock.now()`，交易日 `calendar.session_closing_after(now)`，是否盘中 `calendar.session_at(now) is not None`
3. 读股票池 `pool.entries()`；针对单只股票的案件找出案件股票的记录，没有就报错
4. 读账户 `execution.account()`
5. 针对单只股票的案件：读状态库里的新闻、公告（回看起点见下）、财报发布和 surprise
6. 寻股：`screener.screen(pool.unavailable())`
7. 一次取行情：配置的指数、持仓、案件股票和它的板块 ETF（板块取股票池记录），`MarketDataService.quotes` 去重
8. 转换成条目、组出 `Snapshot`，`snapshots.save(snapshot)`

```python
def lookback_start(calendar: TradingCalendar, now: datetime, sessions: int) -> datetime:
    """从 now 归属的交易日往前数 sessions 个交易日，那一天的收盘时刻。"""
    session = calendar.session_closing_after(now)
    for _ in range(sessions):
        session = calendar.previous_session(session.day)
    return session.close
```

- 先读本地的股票池、再读券商和行情：案件股票不在股票池里时不白读券商
- 行情一次取：一个案件一个行情请求，股票不超过十几只；行情源没有的股票没有条目
- 只给案件股票的板块 ETF 取行情，不给持仓的：信号比较的是案件股票相对它的板块，持仓的板块和本案件无关
- 回看起点和股票池「现在不能加入的股票」的写法相同：按交易日历数交易日，不按 UTC 日期
- 针对单只股票的案件：`case_type` 不是寻股、每周复盘、卖出复盘的 11 种，用一个 frozenset 写在 workflow 里
- 案件类型和股票都由调用方给出，不读调度器：「案件工作流」从 `define_case` 的 `CaseDefinition` 取出这两项传进来

### D8 寻股的名单当场粗筛

- 寻股流程是"代码粗筛出前 N 只 → PM 挑选"，粗筛就是寻股案件要读的事实；在生成快照时粗筛，名单和其余事实是同一时刻的，排除的股票也是这一刻股票池给出的
- 每种案件要什么事实都写在 `SnapshotFlow` 一处，「案件工作流」不用为寻股另开一步
- 粗筛自己把结果存进 `screener_runs`，快照再复制一份；两份内容相同，快照这份给回放
- 考虑过：先由别处跑粗筛，快照读当天的粗筛结果。要另找时机和调用方，粗筛结果和股票池的状态也可能不是同一时刻的

### D9 数据读不到时原样抛出

- 账户、行情、粗筛任何一个读不到，`take` 原样抛出它们的异常，`save` 不会被调用，程序日志也就没有条目
- 不重试、不留空：怎样处理（案件按系统故障结束、或者等一会儿再生成）由「案件工作流」定；见 proposal「需要人决定」第 1 条
- 新闻、公告、财报只读状态库，不会因为外部数据源出错；状态库读写出错照样抛出

### D10 程序日志

- 状态库模块的 `SnapshotService.save` 写：行为者调度器，动作生成快照（`Action.TAKE_SNAPSHOT = "take_snapshot"`），对象 `s<版本>`，案件 ID 是快照的案件，输出的引用是 `entries()` 的全部 ID，说明是"<股票>，<原因>，<n> 个条目"（不针对单只股票的写"不针对单只股票"，第 1 版的原因写"案件开始"）
- 行为者用调度器：Proposal 把"每个案件开始时生成共享状态库快照"写在调度器的运行约束里，行为者表里也没有状态库；和跟进审批模块以调度器身份写一样
- 输出的引用列出全部条目 ID：审计时直接看到这一版有哪些条目；一版几十个 ID，数组列放得下
- 说明不写案件类型：「案件工作流」开案件时写的调度器条目会写案件类型；快照的条目只写和快照有关的

### D11 测试

- `tests/fakes/snapshots.py`：`InMemorySnapshotStore`，`add` 遇到同一版时报错，和数据库实现一样
- `tests/contracts/test_snapshot.py`：条目 ID 的写法和解析（各种类别、键里有冒号和横线、`E-1`、`s0/`、`quote/BBB`）、`check_reason`、`Snapshot` 的条目先后、`entry()`、条目 ID 重复或前缀不对时报错
- `tests/modules/statestore/test_service.py`：用内存存储和程序日志，跑 spec「读取快照和查条目」、program-log 的生成快照几个场景
- `tests/modules/statestore/test_snapshot_repo.py`（要开着 OrbStack）：spec「快照的存储」的全部场景，和"同一版保存两次"用真的主键
- `tests/workflows/test_snapshot_flow.py`：用各模块的真用例加 `tests/fakes/` 的替身（券商、行情源、新闻和公司数据的存储、股票池存储、粗筛的数据源），跑 spec 的其余场景；寻股用 stock-screen 的场景配置和基准数据（`tests/modules/screener/baseline.py`），断言快照里的粗筛结果和 `ScreenerService.screen` 返回的逐项相同、排除的股票是股票池给出的
- 转换逐项核对：每种条目至少一个测试把源对象的每一项和条目比较，防止复制字段时漏了一项

## Risks / Trade-offs

- [快照的体积] 一份几十到一百多 KB，主要是新闻正文；按每天几十个案件，一年几百 MB 到 1 GB，PostgreSQL 会压缩 JSONB → 现在不处理；占地方了再定是否只存新闻的摘要
- [字段复制两份] 模块的 domain 类型加了字段，快照不会自动跟上 → 转换的测试逐项核对；要不要加由用到的变更决定
- [寻股的快照慢] 生成要做一次完整的粗筛（冒烟测试约 4 分钟） → 寻股在收盘后跑，不占盘中的时间；DBOS 的 step 不设超时
- [条目 ID 写法以后要改] → ID 存在每个条目里，查条目按整个 ID 比较；只有前缀的解析依赖写法
- [新闻、公告、财报可能是旧的] 快照只读状态库，取得及时不及时看「事件检测」和刷新财报的调用方 → proposal「需要人决定」第 7 条；M4 的替身不看这些事实

## Migration Plan

- 迁移 v0011（`down_revision = "0010"`）建 `statestore_snapshots`：`case_id` UUID、`version` 整数、`recorded_at` 带时区的时刻、`data` JSONB，主键（`case_id`、`version`）；和别的迁移一样不写降级
- 部署主机下次升级时一起建表；表是空的，没有数据要迁移
- 「案件记录」（v0012）在本变更之后 apply，它的 `down_revision` 是 0011
