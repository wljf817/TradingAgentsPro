# Design

## Context

现状：

- 股票池模块（`modules/stockpool`）有 domain（`PoolEntry` 和状态变化的方法、`rules.py` 的检查）、配置、存储端口 `StockPoolStore`（`get`、`in_pool`、`changed_since`、`save`）和用例 `StockPoolService`；只有 `tests/fakes/` 的内存实现，没有 `repo.py`。运行中的程序还没有地方调用它
- `PoolEntry.thesis` 是关注论点；持仓没有论点。`StockPoolService` 的说明写着"同一个实例由调用方串行调用，做法 M4 再定"
- 定时心跳的 `ThesisUpdate` 要原来的论点和"上次复查的那一天"；`last_review_day_after` 给出论点生效以后的那一天（新设和续期是今天，平时调整不变）。add-review-dates 的 design D7：开仓方案的投资论点在买入成交时生效，升级和关注论点更新经股票池保存，清仓转为关注的关注论点在清仓完成时生效；"今天"在 PM 提交结论时算一次
- 状态库的 `VersionedStore`：`save`、`get`、`latest(**where)`（筛选列等于给定值）、`history(since)`。列出只支持相等的条件
- 程序日志模块（add-program-log 的 design D3）：以后的模块在 service 的构造参数里拿一个 `ProgramLog`，行为者和动作加进 `modules/programlog/domain/entry.py`
- 风控和执行模块的调用由流程拿着 bootstrap 建的一把 `RLock` 串行；程序是单进程，DBOS 工作流、盘中巡检和 Discord 的处理在不同线程里
- 迁移到 v0004；跟进存储、任务和费用存储两个变更同时在写 proposal，分别用 v0006、v0007

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/stockpool
  domain/entry.py   PoolEntry：thesis 改名 watch_thesis，加 investment_thesis、last_review_day；
                    状态变化的方法带上论点和上次复查的那一天；加两个论点更新的方法
  service.py        构造参数加 ProgramLog；PM 发起的操作加案件 ID；两个论点更新；
                    买入成交带投资论点；PM 发起的清仓分成转为关注和移出两个操作；锁；写程序日志
  repo.py           新：表 stockpool_entries，PostgresStockPoolStore
  ports.py          接口不改，只改说明
  __init__.py       多导出 PostgresStockPoolStore
modules/statestore
  repo.py           VersionedStore 加只读的 latest_since
modules/programlog
  domain/entry.py   Actor 加股票池，Action 加股票池的十个动作
bootstrap/migrations/versions/v0005_create_stock_pool.py    新
```

跨模块传递的 Pydantic 模型：传入 `WatchThesis`、`InvestmentThesis`（`contracts/thesis.py`）和 `Listing`；传出的记录里有 `StockStatus`。记录 `PoolEntry` 仍是本模块的 frozen dataclass，由包根导出。contracts 和 shared 不改。股票池写程序日志时传给 `ProgramLog.write` 的是字符串、枚举和案件 ID。

## Goals / Non-Goals

**Goals:**

- 记录存在状态库里，重启后接着用；检查的规则（`rules.py`）不因为存储改变
- 论点和上次复查的那一天由调用方按 review-dates 的规则算好，股票池只保存，不重复日期的规则
- 每次改变记录、每次被拒绝的请求各写一条程序日志

**Non-Goals:**

按「编程风格」省略的情况：

- 几个进程同时运行股票池：程序是单进程，锁只管一个进程里的线程
- 同一个操作被重复调用（比如 DBOS 重做一个 step）：第二次按当时的记录检查，比如加入变成"已在池子里"被拒绝，程序日志多一条
- 买入成交传入的论点和开仓方案附的不是同一份：不检查，调用方保证
- 上次复查的那一天晚于今天、不是交易日：不检查，由 review-dates 算出
- 加仓单挂着时持仓被止损清掉、随后加仓单成交：股票池是候选、账户里有持仓，股票池不知道；止损成交和加仓单成交挤在同一段下跌里，少见
- 程序日志的说明里写论点全文：只写日期；论点全文在记录里，结论里的那一份在案件记录里
- 被拒绝的请求里理由是空白（缺少理由）时说明里的理由：原样写，不另外处理

## Decisions

### D1 记录：两种论点和上次复查的那一天

```python
@dataclass(frozen=True, slots=True)
class PoolEntry:
    symbol: str
    status: StockStatus
    source: PoolSource
    reason: str
    sector: str
    added_at: datetime
    expires_at: datetime | None
    watch_thesis: WatchThesis | None = None  # 只有关注有
    investment_thesis: InvestmentThesis | None = None  # 只有持仓有
    last_review_day: date | None = None  # 只有关注和持仓有
    score: int | None = None
    reviewed_at: datetime | None = None
    removal: Removal | None = None
```

不变量（池子里的和移出的记录都满足；移出的记录状态停在移出前）：

| 状态 | 有 | 没有 |
| --- | --- | --- |
| 候选 | 过期时间 | 关注论点、投资论点、上次复查的那一天 |
| 关注 | 关注论点、上次复查的那一天 | 过期时间、投资论点 |
| 持仓 | 投资论点、上次复查的那一天 | 过期时间、关注论点 |

状态变化的方法（返回新的记录）：

| 方法 | 之后 |
| --- | --- |
| `promoted(thesis, last_review_day)` | 关注，关注论点和上次复查的那一天是给出的，没有过期时间 |
| `kept(expires_at)` | 不改 |
| `demoted(expires_at)` | 候选，去掉关注论点和上次复查的那一天 |
| `bought(thesis, last_review_day)` | 不在池子里抛 `ValueError`；持仓返回自己；否则持仓，投资论点和上次复查的那一天是给出的，去掉关注论点和过期时间 |
| `closed_to_candidate(expires_at)` | 要求池子里的持仓；候选，去掉投资论点和上次复查的那一天 |
| `closed_to_watch(thesis, last_review_day)` | 要求池子里的持仓；关注，去掉投资论点 |
| `watch_thesis_updated(thesis, last_review_day)` | 新：换关注论点和上次复查的那一天 |
| `investment_thesis_updated(thesis, last_review_day)` | 新：换投资论点和上次复查的那一天 |
| `scored`、`removed` | 不改 |

- 两个论点更新的方法和 `promoted` 一样不检查状态：PM 发起的变化先由 `rules.py` 的检查给出拒绝原因（`status_rejection(entry, WATCH)`、`status_rejection(entry, HOLDING)`），已有的写法
- `thesis` 改名 `watch_thesis`：记录里有了两种论点，字段名会存进表里的 JSON。现在表里还没有数据，是改名代价最小的时候；名字和结论里的 `watch_thesis` 一致
- 上次复查的那一天只有一个字段：一只股票同时最多有一种论点，它跟着现在的论点走
- 考虑过：一个字段 `thesis: WatchThesis | InvestmentThesis | None`。收盘心跳和 `ThesisUpdate` 读"现在的论点"时少一次按状态选，但读入场条件（只有关注论点有）、目标价和证伪条件（只有投资论点有）的地方都要先判断类型，从 JSON 读回时也要靠 Pydantic 在两种模型里挑。没有采用
- 考虑过：论点和上次复查的那一天合成一个小类型。没有采用：为两个字段多一个类型

### D2 用例

```python
class StockPoolService:
    def __init__(
        self,
        store: StockPoolStore,
        clock: Clock,
        calendar: TradingCalendar,
        config: StockPoolConfig,
        log: ProgramLog,
    ) -> None: ...

    def add(
        self,
        symbol: str,
        source: PoolSource,
        reason: str,
        listing: Listing | None,
        *,
        case_id: UUID | None,
    ) -> PoolEntry | Rejection: ...

    def remove(self, symbol: str, reason: str, *, case_id: UUID) -> PoolEntry | Rejection: ...

    def promote(
        self, symbol: str, thesis: WatchThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection: ...

    def demote(self, symbol: str, *, case_id: UUID) -> PoolEntry | Rejection: ...

    def keep(self, symbol: str, *, case_id: UUID) -> PoolEntry | Rejection: ...

    def record_score(self, symbol: str, score: int, *, case_id: UUID) -> PoolEntry | Rejection: ...

    def update_watch_thesis(
        self, symbol: str, thesis: WatchThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection: ...

    def update_investment_thesis(
        self, symbol: str, thesis: InvestmentThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection: ...

    def expire_candidates(self) -> tuple[PoolEntry, ...]: ...

    def bought(self, symbol: str, thesis: InvestmentThesis, last_review_day: date) -> PoolEntry: ...

    def closed_by_code(self, symbol: str) -> PoolEntry: ...

    def closed_to_watch(
        self, symbol: str, thesis: WatchThesis, last_review_day: date
    ) -> PoolEntry: ...

    def closed_and_removed(self, symbol: str) -> PoolEntry: ...
```

`entry`、`entries` 不变。

- 案件 ID 是只能按名字传的参数，没有默认值：PM 发起的操作都在案件里调用；只有加入可以是 `None`（手动添加）。只用来写程序日志
- 上次复查的那一天总由调用方给出，升级时也是：调用方按 `last_review_day_after` 算，股票池只保存。升级的那一天股票池自己也算得出来，但买入成交和清仓转为关注发生在成交时，那一天得用给出结论的那天，只能由调用方给；四个保存论点的操作用同一种写法
- `closed_by_pm(symbol, thesis | None)` 分成 `closed_to_watch` 和 `closed_and_removed`：转为关注要论点和上次复查的那一天，移出两个都不要；一个方法带两个要么都有、要么都没有的参数容易传错。结论里本来就分开（`AfterClose`）
- `bought` 对持仓不改记录，不保存，也不写程序日志：同一笔开仓单后续的成交、加仓单的成交（调用方不必为加仓调用）
- 论点更新的检查用已有的 `status_rejection`：不在池子里是"不在池子里"，状态不对是"状态不允许"
- 代码发起的操作（买入成交、清仓）遇到不对的状态照旧抛 `ValueError`

### D3 存储

```python
ENTRIES = versioned_table(
    "stockpool_entries",
    Column("symbol", String, nullable=False),
    Column("in_pool", Boolean, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("removed_at", DateTime(timezone=True), nullable=True),
)


class PostgresStockPoolStore:
    """满足 StockPoolStore：记录存在状态库的 stockpool_entries 表里，键是股票代码。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, ENTRIES, PoolEntry)

    def get(self, symbol: str) -> PoolEntry | None:
        return self._versions.get(symbol)

    def in_pool(self) -> tuple[PoolEntry, ...]:
        return self._versions.latest(in_pool=True)

    def changed_since(self, moment: datetime) -> tuple[PoolEntry, ...]:
        return self._versions.latest_since(moment, "added_at", "removed_at")

    def save(self, entry: PoolEntry) -> None:
        removal = entry.removal
        self._versions.save(
            entry.symbol,
            entry,
            in_pool=entry.in_pool,
            added_at=entry.added_at,
            removed_at=None if removal is None else removal.at,
        )
```

筛选列：`in_pool` 给列出池子里的股票用；`added_at`、`removed_at` 给 `changed_since` 用（加入时刻或移出时刻不早于给定时刻）。记录整份存 JSON，论点里的 Decimal、日期和时刻经 `TypeAdapter` 原样读回（state-store spec「记录原样读回」）。

版本表加一种只读的列出，和 `latest` 共用"每个键的最新版本"那个子查询：

```python
class VersionedStore[T]:
    def latest_since(self, since: datetime, *columns: str) -> tuple[T, ...]:
        """每个键的最新版本里，columns 这几个时刻列至少有一列不早于 since 的那些，按键排序。

        为空的时刻列不算。
        """
        newest = self._newest()
        query = (
            select(newest.c.data)
            .where(or_(*(newest.c[name] >= since for name in columns)))
            .order_by(newest.c[self._key.name])
        )
        return self._records(query)
```

- `_newest()`、`_records()` 从现在的 `latest` 里抽出来，`latest` 的行为不变
- 仍是"只看每个键的最新版本"的列出，在 state-store spec「读取最新版本」说的范围里，不改 state-store spec；它的行为由 stock-pool spec「股票池的存储」的场景和状态库模块自己的测试管
- 跟进存储的 `requested_since`（获批时刻不早于某个时刻的跟进）是同一种列出，可以直接用
- 考虑过：`changed_since` 读出全部股票的最新版本，在 Python 里筛。最简单，但每次加入和删除都要读出所有加入过的股票，越用越多。没有采用
- 考虑过：在股票池的 `repo.py` 里自己写这条查询。要把"每个键的最新版本"的查询和读回的写法再写一遍，跟进存储还要再写一遍。没有采用
- 考虑过：筛选列存加入、移出那天所属的交易日，按相等列出。存储要拿交易日历，端口的语义也要改成交易日。没有采用

迁移 `v0005_create_stock_pool.py`（`revision = "0005"`，`down_revision = "0004"`）：用 `op.create_table` 写明每一列——`symbol`（text）、`version`、`recorded_at`、`data`（jsonb）、`in_pool`（boolean）、`added_at`（timestamptz），都不能为空；`removed_at`（timestamptz）可以为空；主键（`symbol`、`version`）。不 import 业务代码。表很小，不另建索引。

### D4 同时进行的操作：用例里的一把锁

`StockPoolService` 持有一把 `threading.Lock`。改变记录的操作（加入、删除、升级、降为候选、保持、记录评分、两个论点更新、移出到期的候选、买入成交、三种清仓）从读时钟、读记录、检查、保存到写程序日志，全程拿着它；被拒绝的请求也一样。`entry`、`entries` 只读，不拿锁。

```python
class StockPoolService:
    def promote(
        self, symbol: str, thesis: WatchThesis, last_review_day: date, *, case_id: UUID
    ) -> PoolEntry | Rejection:
        with self._lock:
            entry = self._store.get(symbol)
            rejection = promote_rejection(entry, self._store.in_pool(), self._config)
            if rejection is not None:
                self._write(Action.POOL_PROMOTE, symbol, _rejected(rejection), case_id)
                return rejection
            assert entry is not None  # 检查保证在池子里
            promoted = self._save(entry.promoted(thesis, last_review_day))
            self._write(Action.POOL_PROMOTE, symbol, _thesis_dates(promoted), case_id)
            return promoted
```

其他改变记录的操作同样写。

- 锁放在用例里：调用方有好几个（最多 3 个案件、收盘心跳、成交的处理），先读后写的检查都在用例里，放在这里一处就够，调用方不会忘了拿
- 普通的 `Lock`：公开的操作拿着锁时不调用别的公开操作（`record_score` 找池子里的记录改用私有方法）
- 不会死锁：股票池拿着锁时只用存储、时钟、交易日历和程序日志，不拿别的锁。调用方可能拿着交易侧的 `RLock` 调用股票池（比如巡检在锁里处理成交），顺序总是先交易侧的锁、再股票池的锁
- 考虑过：和风控、执行模块一样，由调用方拿 bootstrap 建的锁。每个调用方都要记得拿，漏了就会多放进一只。没有采用
- 考虑过：数据库事务加行锁。一次检查要读好几次（这只股票、池子里的全部、当天的增删），`VersionedStore` 每次保存一个事务，要把事务拉到端口外面。没有采用
- 考虑过：并发数为 1 的 DBOS 队列。股票池的操作是在别的工作流的 step 里调用的，为它再开工作流太重。没有采用

### D5 程序日志

```python
class Actor(StrEnum):
    STOCK_POOL = "stock_pool"  # 股票池


class Action(StrEnum):
    POOL_ADD = "pool_add"  # 加入
    POOL_REMOVE = "pool_remove"  # 删除
    POOL_PROMOTE = "pool_promote"  # 升级
    POOL_DEMOTE = "pool_demote"  # 降为候选
    POOL_KEEP = "pool_keep"  # 保持
    POOL_SCORE = "pool_score"  # 记录评分
    POOL_THESIS = "pool_thesis"  # 论点更新
    POOL_EXPIRE = "pool_expire"  # 超期移出
    POOL_BOUGHT = "pool_bought"  # 买入成交
    POOL_CLOSED = "pool_closed"  # 清仓
```

（只列新加的成员，加在已有的枚举里。）

- 动作都以 `pool_` 开头：`action like 'pool_%'` 一次查出股票池的全部条目，以后别的行为者（调度器的审批、拒绝）也不会和它们重名
- 对象是股票代码，没有引用；案件 ID 见 D2
- 说明的写法和已有的条目一样：时刻写成 `2026-10-28 20:00 UTC`，日期写成 `2026-10-21`。被拒绝的原因用中文（"已在池子里"），`Rejection` 到中文的对照放在 `service.py`

| 动作 | 说明的例子 |
| --- | --- |
| 加入 | `通过：PM 探索，理由：放量突破，论点草稿：订单回暖；板块 科技，过期时间 2026-10-28 20:00 UTC`；`被拒绝：已在池子里；PM 探索，理由：……` |
| 删除 | `通过：候选移出，理由：论点不再成立`；`被拒绝：持仓不能删除；理由：……` |
| 升级 | `通过：复查日 2026-10-21，催化剂截止日期 2026-10-29，上次复查的那一天 2026-10-14` |
| 降为候选、保持 | `通过：过期时间 2026-10-28 20:00 UTC` |
| 记录评分 | `评分 1`；`被拒绝：不在池子里` |
| 论点更新 | `投资论点，通过：复查日 2026-10-23，催化剂截止日期 2026-10-29，上次复查的那一天 2026-10-07` |
| 超期移出 | `过期时间 2026-10-27 20:00 UTC` |
| 买入成交 | `关注变为持仓：复查日 2026-10-21，催化剂截止日期 2026-10-29，上次复查的那一天 2026-10-14` |
| 清仓 | `代码触发，转为候选：过期时间 2026-10-28 20:00 UTC`；`PM 发起，转为关注：复查日……`；`PM 发起，移出` |

- 通过的操作先保存、再写条目；被拒绝的只写条目。写条目出错时原样抛出，已经保存的记录不撤回（program-log spec「写入失败」）
- 移出到期的候选按股票代码的顺序，一只一只保存、写条目
- 记录没变的不写：`bought` 对持仓直接返回（D2）
- 抛 `ValueError` 的（代码发起的操作遇到不对的状态、评分超出范围）在写条目之前就抛出，不写

### D6 包根、分层和装配

- 包根多导出 `PostgresStockPoolStore`，以后由 bootstrap 装配
- `service.py` import 程序日志模块的包根（add-program-log 的 design D3），`repo.py` import 状态库的包根，和风控、执行模块的 `repo.py` 一样；`threading` 只在 `service.py` 里用，它不在纯区域
- `domain/entry.py` 多用 `datetime.date` 和 contracts 的 `InvestmentThesis`，都在纯区域的白名单里，不改 `tests/architecture/rules.py`
- 不装配：还没有调用方，bootstrap 不建股票池。迁移照常随启动执行，建出一张空表

### D7 测试

```
tests/modules/stockpool/builders.py           CCC 带基准投资论点，BBB、CCC 的上次复查的那一天
                                              2026-10-07；基准投资论点；用例带内存的程序日志；
                                              案件 ID K1
tests/modules/stockpool/domain/test_entry.py  新字段、状态变化的方法和不变量
tests/modules/stockpool/test_service.py       stock-pool spec 改了和新增的场景，存储和同时进行的除外
tests/modules/stockpool/test_program_log.py   program-log spec「股票池的动作」
tests/modules/stockpool/test_concurrency.py   stock-pool spec「同时进行的操作」
tests/modules/stockpool/test_repo.py          stock-pool spec「股票池的存储」
tests/modules/stockpool/test_state_machine.py 新的操作和不变量，每一步的条目数
tests/modules/statestore/test_repo.py         latest_since
tests/bootstrap/test_database.py、test_app.py 表的清单加 stockpool_entries
```

- 基准投资论点和 case-types spec 的相同，从 `tests/modules/scheduler` 的 builders 复制，不跨测试目录 import
- 同时进行的测试：存储包一层，`in_pool`、`changed_since` 被调用时最多等 0.5 秒，等另一个线程也来读。没有锁时两个线程读到同样的池子，两次都通过，测试失败；有锁时第二个线程要等第一个保存完才读到。每个场景用两个线程各发一个请求，断言恰好一个通过
- `test_repo.py`：列出池子里的股票、`changed_since` 只看最新版本、移出时刻为空的不算，内存实现和数据库实现都测；原样读回、重启（用同一个数据库新建 `PostgresStockPoolStore` 和用例）、数据库连不上只测数据库实现，要开着 Docker
- 状态机性质测试：加上两个论点更新、带论点的买入成交、两种 PM 发起的清仓；每一步之后另外检查：每条记录满足 D1 的不变量；通过并改变了记录的、被拒绝的请求各多一条条目，没改变的、报错的不多
- 每个测试的说明写明对应的 spec 场景

## Risks / Trade-offs

- [保存以后写程序日志出错：记录改了，条目没有] → 照 program-log spec「写入失败」；本机数据库，少见
- [锁只管一个进程] → 程序是单进程；以后拆进程时改成数据库里的锁
- [锁里写程序日志，拿锁的时间长一点] → 每个操作是几次本机数据库读写；股票池每天几十次操作
- [`thesis` 改名] → 只影响股票池模块和它的测试；表还没有数据
- [新候选的判断还缺"成为候选的时刻"] → proposal「需要人决定」第 3 条
- [跟进存储同时在写 proposal，也要按时刻列出最新版本] → 本变更先 apply，`latest_since` 它可以直接用；它的 proposal 里如果写了别的做法，apply 时统一

## Migration Plan

- 迁移 v0005 建一张空表，随启动执行（bootstrap spec「数据库」）；本变更以后还没有程序写这张表，等装配股票池的变更
- 按 `deploy/README.md`「更新」部署；带新迁移，更新前照常手动备份
- 回退：回到上一个提交，用更新前的备份恢复状态库（deployment spec「更新」）
