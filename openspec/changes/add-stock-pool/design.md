# Design

## Context

现状（M1 完成，M2 进行中）：

- 交易方案（`TradePlan`）和持仓（`Position`）都带板块，风控按板块算集中度；板块现在由录入方案的人填写，来源留给股票池（风控规则的 design D7）
- 交易日历有 `session_at`（所在的常规时段）、`next_session_after`（开盘晚于某个时刻的第一个时段）、`last_session_before`（收盘不晚于某个时刻的最后一个时段）。健康监控的 `recovery_day` 用前两个拼出"收盘晚于某个时刻的第一个交易日"，日历本身没有这项查询
- 已有模块的模式：domain 是 frozen dataclass 和纯函数；存储在 `ports.py` 定义 Protocol，service 注入 Clock、交易日历、存储和配置；测试用 `tests/fakes/` 的内存实现（见额度预留）
- M3 和 M1 一样只写纯逻辑和测试：不建表、不加迁移、不写 `repo.py`

动机见 proposal.md 的 Why，要求见 specs/stock-pool/spec.md 和 specs/trading-calendar/spec.md。

### 模块边界

```
contracts（Pydantic，跨模块传递）
  stock_pool.py  StockStatus                          -> 调度器、收盘检查、跟进审批（以后）
  thesis.py      Condition、Catalyst、WatchThesis      <- PM 的结论（下一个变更）；投资论点共用
  listing.py     Exchange、SecurityType、Listing       <- 数据源（M4）

shared
  trading_calendar.py  TradingCalendar.session_closing_after：时刻归属的交易日

modules/stockpool
  domain/entry.py  PoolSource、RemovalKind、Removal、PoolEntry，以及状态变化的方法
  domain/rules.py  Rejection；过期时间、冷却期、当天的增删次数、加入和删除的检查
  config.py        StockPoolConfig
  ports.py         StockPoolStore
  service.py       StockPoolService
  __init__.py      导出公开的名字
```

本次只涉及股票池一个模块，不改其他模块；health 的 `recovery_day` 不改。跨模块传递的 Pydantic 模型：

- 传入：加入时的 `Listing`（M4 由数据源给出），升级和清仓后转为关注时的 `WatchThesis`（以后由 PM 的结论给出）
- 传出：记录里的 `StockStatus`。记录本身（`PoolEntry`）是本模块的 frozen dataclass，由包根导出，和健康监控的 `HealthStatus` 一样。收盘检查以后要把记录交给别的模块时，再决定要不要放进 contracts

## Goals / Non-Goals

**Goals:**

- 规则都在 domain 里，是纯函数；service 只负责读当前时刻、读写存储
- 状态变化只有 spec 里的一张表，每一行都有场景和测试；性质测试拿随机的操作序列检查不变量

**Non-Goals:**

按「编程风格」省略的情况：

- 股票代码的大小写和空白：不规范化，调用方传标准代码
- 上市信息本身不合理（日均成交额为负、板块为空）：不检查，数据来自数据源
- 加入以后流动性下降、板块或上市信息变化：不重新检查，板块一直用加入时的
- 关注股挂着买单时被移出、随后成交：买入成交报错，M4 接线时处理；买单最多挂到收盘
- PM 发起的清仓还没成交，止损单先把仓位清掉：按代码触发的清仓转为候选，PM 选的去向不再用
- M2 手工录入方案买进的、不在池子里的股票：买入成交报错；M4 接线时给已有持仓建记录
- 理由、论点文字的长度：不限制
- 两个操作同时进行：调用方串行调用，和风控一样，做法 M4 再定
- 超出上限以后：不主动处理，等它随删除、超期自然降回

## Decisions

### D1 contracts 里的三个文件

股票状态只有 Proposal 的三种，移出不是一种状态：

```python
class StockStatus(StrEnum):
    """股票在股票池里的状态；允许的结论只由它决定。"""

    CANDIDATE = "candidate"
    WATCH = "watch"
    HOLDING = "holding"
```

- 移出的股票不在池子里，调度器不会为它开案件；别的模块拿到的状态永远是这三种之一
- 考虑过：加一个"已移出"状态。没有采用：调度器、收盘检查都要多处理一个永远不是案件输入的值；冷却期只在股票池内部用，记在记录的移出字段里（D3）

关注论点、催化剂和条件放在 `contracts/thesis.py`，文字字段都去掉首尾空白再校验非空：

```python
class ConditionKind(StrEnum):
    PRICE = "price"  # 最新价
    CHANGE = "change"  # 当日涨跌幅：相对前一交易日收盘价的比例，下跌为负
    VOLUME = "volume"  # 当日累计成交量（股）


class Comparison(StrEnum):
    AT_LEAST = "at_least"  # 不低于
    AT_MOST = "at_most"  # 不高于


class Condition(BaseModel):
    """代码能检测的条件。价格和成交量的值大于 0，成交量只能是不低于。"""

    kind: ConditionKind
    comparison: Comparison
    value: Decimal


class ReferenceKind(StrEnum):
    SCHEDULE = "schedule"  # 日程项
    EVENT = "event"  # 事件


class CatalystReference(BaseModel):
    kind: ReferenceKind
    ref_id: str = Field(min_length=1)


class Catalyst(BaseModel):
    description: str = Field(min_length=1)
    deadline: date
    # 没有固定日期的催化剂可以先不引用，事件发生后再关联
    reference: CatalystReference | None = None


class WatchThesis(BaseModel):
    """关注论点。复查日和截止日期取什么值由「复查日与催化剂」校验。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1)  # 看好理由
    # 任一条件满足就算入场条件满足
    entry_conditions: tuple[Condition, ...] = Field(min_length=1)
    catalyst: Catalyst
    invalidation: str = Field(min_length=1)  # 证伪条件
    review_day: date  # 复查日
```

（每个模型都是 `frozen=True, extra="forbid"`，带文字字段的加 `str_strip_whitespace=True`，上面只写了一处。）

- 关注论点是否齐全由 Pydantic 保证：缺字段的关注论点构造不出来，升级时不用再检查。论点来自模型输出，和交易方案一样在 contracts 校验格式；解析失败由解析模型输出的一方处理（Agent 网关、信号格式）
  - 考虑过：升级时由股票池检查各字段，不齐时返回"关注论点不完整"。没有采用：同一份检查要写两遍
- 多个入场条件是"任一满足"：每个条件对应一个到价提醒，各自触发（proposal「需要人决定」第 10 条）
- 条件只有到价提醒能覆盖的三类；技术指标类由 M4 的事件检测加，到时候扩充 `ConditionKind`
- 催化剂只有一个：Proposal 写的是"催化剂及截止日期"，到期后"写新的催化剂和截止日期"（proposal「需要人决定」第 11 条）
- 证伪条件是文字。投资论点里"证伪条件中代码能检测的部分"由下一个变更加，复用 `Condition`
- 事件和日程项的 ID 用 `str`：两者都在 M4 才有，现在不限定格式

上市信息放在 `contracts/listing.py`，因为它由 M4 的数据源给出、交给股票池：

```python
class Exchange(StrEnum):
    NYSE = "nyse"
    NASDAQ = "nasdaq"
    OTHER = "other"  # 其他交易所，包括 OTC


class SecurityType(StrEnum):
    COMMON_STOCK = "common_stock"
    ADR = "adr"
    ETF = "etf"
    LEVERAGED = "leveraged"  # 杠杆和反向产品，包括杠杆 ETF
    OTHER = "other"


class Listing(BaseModel):
    """一只股票的上市信息，由调用方从数据源查好传入；数据来自数据源，不加约束。"""

    exchange: Exchange
    security_type: SecurityType
    average_dollar_volume: Decimal  # 日均成交额，美元；按多少天平均由 M4 定
    sector: str
```

- 允许交易的范围要可配置（proposal「需要人决定」第 3 条），配置项的类型就是这两个枚举。配置在模块层级的最底层，不能 import domain，所以枚举放在 contracts
- 枚举只列 Proposal 和人的决定里提到的值，其余归"其他"。杠杆 ETF 归杠杆产品，这样以后放开 ETF 也不会放进杠杆产品
- 考虑过：交易所和证券类型用字符串，配置写字符串集合。没有采用：配置写错一个字母不会报错；M4 的适配器反正要做映射

### D2 交易日历加一项：时刻归属的交易日

```python
def session_closing_after(self, moment: datetime) -> TradingSession:
    """moment 归属的交易日：收盘时间晚于 moment 的第一个时段。"""
    self._check_moment(moment)
    return self._at(bisect_right(self._closes, moment))
```

- 盘中和盘前是当天；收盘时刻或之后、休市日是下一个交易日（proposal「需要人决定」第 1 条）
- 和 `recovery_day` 的结果相同。`recovery_day` 不改：健康监控已经完成，只改一个节点
- 性质测试检查它等于 `session_at(t) or next_session_after(t)`，也就是 `recovery_day` 的写法
- 考虑过：在股票池的 domain 里用 `session_at` 和 `next_session_after` 拼出来。没有采用：每日预算、每天的候选评估上限以后也要用，放在日历里只写一次

一个交易日开始的时刻是 `last_session_before(now).close`：它等于 `now` 归属的交易日的前一个交易日的收盘时刻，不用再加查询。

### D3 一只股票一条记录

```python
class PoolSource(StrEnum):
    EXPLORATION = "exploration"  # PM 探索
    MANUAL = "manual"  # 手动添加


class RemovalKind(StrEnum):
    DELETED = "deleted"  # PM 删除，计入每天增删次数
    EXPIRED = "expired"  # 候选超期，代码移出
    CLOSED = "closed"  # PM 发起的清仓后选择移出


@dataclass(frozen=True, slots=True)
class Removal:
    kind: RemovalKind
    at: datetime
    reason: str | None  # 删除的理由；超期和清仓后移出为 None


@dataclass(frozen=True, slots=True)
class PoolEntry:
    symbol: str
    status: StockStatus
    source: PoolSource
    reason: str  # 理由与论点草稿
    sector: str
    added_at: datetime
    expires_at: datetime | None  # 只有候选有
    thesis: WatchThesis | None = None  # 只有关注有
    score: int | None = None
    reviewed_at: datetime | None = None
    removal: Removal | None = None  # 移出以后才有

    @property
    def in_pool(self) -> bool:
        return self.removal is None
```

- 状态变化写成返回新记录的方法，对应 spec「状态变化」表的每一行：`promoted(thesis)`、`kept(expires_at)`、`demoted(expires_at)`、`bought()`、`closed_to_candidate(expires_at)`、`closed_to_watch(thesis)`、`scored(score, at)`、`removed(kind, at, reason)`
- 不变量由这些方法维持：候选有过期时间、没有关注论点；关注有关注论点、没有过期时间；持仓两者都没有
- 代码发起的变化在方法里检查状态，不对就抛 `ValueError`：`bought` 要求在池子里，`closed_*` 要求持仓。PM 发起的变化由 D4 的检查先给出拒绝原因，方法本身不再检查
- 记分要求 -2 到 +2 的整数，否则抛 `ValueError`。完整的评分格式在「信号格式」，这里只存这个数
- 移出以后记录保留，带着移出的时刻和原因，用来判断冷却期；重新加入时换成一条新记录，旧的评分、复查时间都不带过来
- 过期时间存时刻（收盘时刻），不存日期：收盘检查在收盘后运行，这时"归属的交易日"已经是下一天；比较时刻就不会差一天

考虑过：移出时从存储里删掉，另存一张"移出记录"表

- 没有采用：多一个端口；M4 的存储按股票只追加，移出就是这只股票的一个新版本

### D4 检查和拒绝原因

```python
class Rejection(StrEnum):
    MISSING_REASON = "missing_reason"  # 缺少理由
    ALREADY_IN_POOL = "already_in_pool"  # 已在池子里
    COOLING_DOWN = "cooling_down"  # 冷却期内
    UNKNOWN_SYMBOL = "unknown_symbol"  # 股票代码不存在
    NOT_ALLOWED = "not_allowed"  # 不在允许交易的范围内
    ILLIQUID = "illiquid"  # 流动性不达标
    POOL_FULL = "pool_full"  # 池子已满
    WATCH_FULL = "watch_full"  # 关注名额已满
    DAILY_LIMIT = "daily_limit"  # 增删次数已用完
    NOT_IN_POOL = "not_in_pool"  # 不在池子里
    HOLDING = "holding"  # 持仓不能删除
    WRONG_STATUS = "wrong_status"  # 状态不允许
```

`domain/rules.py` 的检查都返回 `Rejection | None`，按 spec 表里的顺序给出第一项不通过的原因：

- `add_rejection`：加入的八项
- `remove_rejection`：删除的四项
- `status_rejection(entry, required)`：升级、保持要求候选，降为候选要求关注；不在池子里时是"不在池子里"
- 升级的关注名额在 `status_rejection` 之后检查

PM 和手动发起的操作返回 `PoolEntry | Rejection`：通过时是新的记录，不通过时是原因，不改变任何记录。

- 拒绝是 PM 提议的正常结果，M4 的工作流要拿原因告诉 PM、写日志，所以作为返回值，不抛异常
- 只给第一项原因：PM 改正以后再提交，下一项自然会被检查到；多项原因要处理检查之间的依赖（没有上市信息就没法查流动性）

### D5 过期时间、冷却期和当天的增删次数

```python
def candidate_expiry(
    moment: datetime, calendar: TradingCalendar, config: StockPoolConfig
) -> datetime:
    """moment 归属的交易日之后，第 candidate_ttl_sessions 个交易日的收盘时刻。"""
    day = calendar.session_closing_after(moment).day
    return calendar.add_sessions(day, config.candidate_ttl_sessions).close


def cooling_down(
    entry: PoolEntry | None, moment: datetime, calendar: TradingCalendar, config: StockPoolConfig
) -> bool:
    """移出时刻归属的交易日到 moment 归属的交易日，不满冷却期。"""
    if entry is None or entry.removal is None:
        return False
    removed = calendar.session_closing_after(entry.removal.at).day
    today = calendar.session_closing_after(moment).day
    return calendar.count_sessions(removed, today) < config.readd_cooldown_sessions


def changes_since(entries: Iterable[PoolEntry], start: datetime) -> int:
    """start 以后通过的加入和删除：加入时刻不早于 start 的记录，加上这之后删除的记录。"""
```

当天的增删次数 = `changes_since(store.changed_since(start), start)`，`start` 是 D2 的交易日开始时刻。

- 次数从记录推出，不另存日志：每次加入都会新建记录，删除会记下时刻和原因（`DELETED`）
- 这要求同一只股票在同一个交易日里不能删除以后又加入，否则新记录会盖掉删除。冷却期至少 1 个交易日（配置要求大于 0），正好保证这一点
- 超期和清仓后移出的原因不是 `DELETED`，自然不计入；升降级、保持、记分、买入成交都不改加入时刻，也不计入
- 考虑过：单独的增删日志端口。没有采用：多一个端口和一张表；增删的历史 M4 写进程序日志

### D6 存储端口

```python
class StockPoolStore(Protocol):
    def get(self, symbol: str) -> PoolEntry | None: ...  # 最新的记录，包括已经移出的

    def in_pool(self) -> tuple[PoolEntry, ...]: ...  # 没有移出的记录

    def changed_since(self, moment: datetime) -> tuple[PoolEntry, ...]: ...

    def save(self, entry: PoolEntry) -> None: ...  # 保存这只股票最新的记录
```

- `changed_since`：加入时刻或移出时刻不早于 `moment` 的记录，只用来数当天的增删次数
- `tests/fakes/stock_pool.py`：`InMemoryStockPoolStore`，按股票代码存在 dict 里
- M4 的 `repo.py` 按状态库的写法实现：按股票只追加，读取时取最新版本

### D7 用例

```python
class StockPoolService:
    def __init__(
        self,
        store: StockPoolStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: StockPoolConfig,
    ) -> None: ...

    def entry(self, symbol: str) -> PoolEntry | None: ...  # 池子里的记录

    def entries(self) -> tuple[PoolEntry, ...]: ...  # 池子里的全部股票

    def add(
        self, symbol: str, source: PoolSource, reason: str, listing: Listing | None
    ) -> PoolEntry | Rejection: ...

    def remove(self, symbol: str, reason: str) -> PoolEntry | Rejection: ...

    def promote(self, symbol: str, thesis: WatchThesis) -> PoolEntry | Rejection: ...

    def demote(self, symbol: str) -> PoolEntry | Rejection: ...

    def keep(self, symbol: str) -> PoolEntry | Rejection: ...

    def record_score(self, symbol: str, score: int) -> PoolEntry | Rejection: ...

    def expire_candidates(self) -> tuple[PoolEntry, ...]: ...  # 这次移出的股票

    def bought(self, symbol: str) -> PoolEntry: ...

    def closed_by_code(self, symbol: str) -> PoolEntry: ...

    def closed_by_pm(self, symbol: str, thesis: WatchThesis | None) -> PoolEntry: ...
```

`add` 的流程，其他操作类似：

```
now = clock.now()
previous = store.get(symbol)
in_pool = store.in_pool()
start = calendar.last_session_before(now).close
changes = changes_since(store.changed_since(start), start)
rejection = add_rejection(reason, listing, previous, in_pool, changes, now, calendar, config)
  | 有原因 --> 返回原因
  v
entry = PoolEntry(..., status=候选, sector=listing.sector, added_at=now,
                  expires_at=candidate_expiry(now, calendar, config))
store.save(entry)，返回 entry
```

- "现在"由 service 从 Clock 读取，再作为参数传给 domain
- `closed_by_pm` 的关注论点为空表示 PM 选择移出。PM 怎么在结论里写这个选择，由「案件类型与结论」定
- `expire_candidates` 不依赖调用时刻是不是刚收盘：移出全部过期时间不晚于现在的候选，错过一次下次补上
- 代码发起的三个操作找不到池子里的记录时抛 `ValueError`（D3）
- 同一个实例由调用方串行调用。容量和次数的检查是"先读后写"，并发时会多放进一个；M4 和风控一样选定串行的做法

### D8 配置和默认值

```python
class StockPoolConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 候选和关注合计不超过这么多只，持仓不计入
    max_pool_size: int = Field(default=30, gt=0)
    max_watch: int = Field(default=8, gt=0)
    # 每个交易日通过的加入和删除合计
    max_daily_changes: int = Field(default=10, gt=0)
    candidate_ttl_sessions: int = Field(default=10, gt=0)
    readd_cooldown_sessions: int = Field(default=5, gt=0)
    # 日均成交额，美元
    min_average_dollar_volume: Decimal = Field(default=Decimal(10_000_000), gt=0)
    allowed_exchanges: frozenset[Exchange] = Field(
        default=frozenset({Exchange.NYSE, Exchange.NASDAQ}), min_length=1
    )
    allowed_security_types: frozenset[SecurityType] = Field(
        default=frozenset({SecurityType.COMMON_STOCK, SecurityType.ADR}), min_length=1
    )
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 池子容量 | 30 | moomoo 最低档有 100 个订阅额度：池子 30 只加最多 8 只持仓，每只订两类数据还有余量；也够一次寻股补一批候选 |
| 关注名额 | 8 | 和风控的最多持仓数一样；关注股要设到价提醒、按复查日开案件，比候选费预算 |
| 每天增删次数上限 | 10 | 一次寻股最多补 10 只候选；也挡住 PM 一天里来回增删 |
| 候选过期期限 | 10 个交易日 | 两周：候选加入当天就开候选评估，两周里既没升级也没选保持，说明不急 |
| 重新加入的冷却期 | 5 个交易日 | 一周：挡住来回增删和超期后马上被寻股加回来，又不会错过一周以后的新变化 |
| 流动性门槛 | 日均成交额 1,000 万美元 | 账户 2,000 美元，每笔最多一千多美元，成交不是问题；门槛是为了挡掉价差大、容易跳空的小盘股 |
| 允许的范围 | 纽交所、纳斯达克的普通股和 ADR | 人决定（proposal「需要人决定」第 3 条） |

只加"大于 0"和"至少一项"的约束，不校验配置项之间的关系。M4 由 bootstrap 从配置文件读取，测试里直接构造。

### D9 包根导出

`StockPoolConfig`、`StockPoolService`、`StockPoolStore`、`PoolEntry`、`PoolSource`、`Removal`、`RemovalKind`、`Rejection`，以及 domain 的 `candidate_expiry`、`cooling_down`、`changes_since`、`add_rejection`、`remove_rejection`、`status_rejection`。纯函数导出是为了测试只从包根 import。

domain 只用 `dataclasses`、`datetime`、`decimal`、`enum`、`typing`、`collections.abc`，都在纯区域白名单里，不改 `tests/architecture/rules.py`。

### D10 测试

```
tests/shared/test_trading_calendar.py          trading-calendar spec「时刻归属的交易日」，含性质测试
tests/contracts/test_thesis.py                 stock-pool spec「关注论点的格式」
tests/fakes/stock_pool.py                      股票池存储的内存实现
tests/modules/stockpool/builders.py            基准数据：纽交所样例日历、2026-10-14 15:00Z、
                                               AAA/BBB/CCC 三条记录、基准上市信息、基准关注论点；
                                               新建 StockPoolService 的辅助函数
tests/modules/stockpool/test_config.py         spec「股票池配置」
tests/modules/stockpool/domain/test_entry.py   状态变化方法和不变量
tests/modules/stockpool/domain/test_rules.py   过期时间、冷却期、当天的增删次数、检查的顺序
tests/modules/stockpool/test_service.py        spec 其余的场景
tests/modules/stockpool/test_state_machine.py  状态机性质测试
```

- service 测试用 `FakeClock`、纽交所样例日历和内存存储；基准数据里的三条记录直接存进内存存储
- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：随机交错加入（合格或不合格的上市信息、有没有理由）、删除、升级、降为候选、保持、记分、移出到期的候选、买入成交、两种清仓和拨动时间（跨收盘、跨周末），每一步之后检查：
  - 每条记录的状态和字段满足 D3 的不变量
  - 被拒绝的操作没有改变任何记录
  - 通过的加入发生时，候选和关注合计低于池子容量；通过的升级发生时，关注低于关注名额
  - 每个交易日通过的加入和删除不超过上限
  - 在冷却期内的加入都被拒绝
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [当天的增删次数从记录推出，依赖冷却期至少 1 个交易日] → 配置要求冷却期大于 0；性质测试检查每个交易日的次数不超过上限
- [成交后的状态变化可能超出容量或名额] → 超出的数量最多是持仓数；超出期间加入和升级被拒绝，随删除、超期降回（proposal「需要人决定」第 7 条）
- [板块一直用加入时的，公司换板块以后不准] → 很少见；需要时删除再加入
- [归属的交易日在日历和 `recovery_day` 里各写了一份] → 结果相同，由日历的性质测试保证；以后改健康监控时可以换成调用日历
- [容量和次数的检查是先读后写，并发时会多放进一个] → M4 选定串行的做法时一起处理

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
