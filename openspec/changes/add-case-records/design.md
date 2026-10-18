# Design

## Context

现状：

- 调度器 `modules/scheduler` 只有纯逻辑：案件状态 `CaseState`（阶段、方案的动作、有结论、重交次数、质询轮次、Critic 审核的结果、结束原因）"不读写存储"，存在哪里由 M4 的工作流定；队列的决定只根据调用方给的快照算，排队的案件 `QueuedCase` 带触发 `Trigger` 和触发时刻，`QueueDecision.drop` 是不再运行的排队案件。触发的四个类型定义在调度器的 domain 里
- contracts 已经有案件记录要存的模型：`CaseType`、`CaseEndReason`（`case.py`），四种结论和 `Conclusion` 联合类型（`conclusion.py`），`TradePlan`（`trade_plan.py`），`CriticReview`、`PlanResponse`（`review.py`），`RiskDecision`（`risk.py`），`ModelUse`（`model.py`），`Signal`（`signal.py`）
- Agent 网关把 Task 和 Result 按案件 ID 存在 `gateway_tasks`、`gateway_results`（`TaskStore.case_records(case_id)`，Result 带实际使用的模型），每次尝试的费用记录带案件 ID 存在 `gateway_costs`；`CostStore` 只有按时刻读
- 股票池把每只股票的记录（含现在生效的关注论点、投资论点）存在 `stockpool_entries` 的版本里；版本不带案件 ID，PM 发起的操作写程序日志时带案件 ID
- 状态库的 `VersionedStore`：`save`（内容和最新版本不同才写新版本）、`get`、`latest(**where)`（按键排序）、`latest_since`、`history`；没有"按时刻取最近的 n 个"
- 纯区域的 domain 只能 import shared、contracts 和本模块的 domain、config，不能 import 别的模块
- 迁移到 v0010；「状态库快照」（v0011）把快照按案件 ID 和版本存，`SnapshotService.latest(case_id)` 给出最后一版，凭案件 ID 就能找到案件的快照
- 试过：`Conclusion` 联合类型（持仓、关注、候选、寻股的顺序）写成 JSON 再读回时，候选决定移出的结论读成关注的结论。候选的结论的字段是关注的结论的子集，两种都能通过，pydantic 2.13 的 smart 模式取左边的

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/caserecords                新模块
  domain/record.py    CaseRecord、CaseTrigger、CaseConclusion、ProposalRecord、Review、
                      Response、Finding 和三个枚举；每一步的写入规则（D5）
  ports.py            CaseRecordStore
  repo.py             表 caserecords_cases、caserecords_proposals、caserecords_findings；
                      PostgresCaseRecordStore
  service.py          CaseRecordService：写入和读取（D5、D6）
  __init__.py         导出
modules/statestore/repo.py         VersionedStore.most_recent（D6）
contracts/conclusion.py            Conclusion 的顺序（D7）
bootstrap/migrations/versions/v0012_create_case_records.py
```

跨模块传递的模型：写入时调用方传进 contracts 的 `CaseType`、`CaseEndReason`、四种结论（Pydantic）、`ModelUse`、`TradePlan`、`CriticReview`、`PlanResponse`、`RiskDecision`、`Signal`（在 `Finding` 里），以及本模块的 `CaseTrigger`、`Finding`（frozen dataclass）；读取给出本模块的 `CaseRecord`、`ProposalRecord`、`Finding`。本模块不 import 调度器、Agent 网关和股票池：触发的种类、股票池不通过的原因由调用方换成本模块的值（D8、D4）。别的模块、workflows、bootstrap 的装配、shared 不改；程序日志不加行为者和动作（D10）。

## Goals / Non-Goals

**Goals:**

- 「案件工作流」在每个事实发生时调一次写入就能记下它；DBOS 重新执行同一步时再写一次也不出错、不多出一项
- 运行中案件的阶段只有一份：在调度器的案件状态里，案件记录不记（D3）
- 读取覆盖接下来的变更要的：按案件 ID（跟进案件的原案件结论、确认消息里的双方意见）、一只股票最近结束的案件（案件上下文的近期结论和上一个案件的结束原因）、最近一次得出结论的案件（收盘心跳的上一次寻股和新候选）

**Non-Goals:**

- 三张表之间的外键、一次写多张表的事务：每张表各自只追加，写入的顺序由调用方和 D5 的规则保证
- 索引：每天几十个案件，按股票、按类型读直接扫最新版本；M5、M6 的查询多起来时再加
- 数据库层面禁止修改和删除（触发器、权限）：和别的表一样，程序只有追加和读取
- 按方案 ID、按交易读案件：M6 的评估需要时再加

按「编程风格」省略的情况：

- 同一个案件的两次写入同时进行（比如紧急退出中止和案件工作流同时写结束）：调用方保证依次写；同时写同一张表时后完成的那次因为版本号冲突报错（state-store spec「只追加的版本记录」）
- 写过结论以后再写的结论内容不同：当作重复写入，留第一次的；案件状态保证一个案件只有一次结论通过
- 结束以后再写审核、回应或风控结果：不检查，案件状态保证结束以后没有这些事件
- 给退出方案写回应、修改的回应换了动作：不检查，案件状态和回应的格式保证
- 传入的交易方案和结论里的方案对不上：不检查，调用方用结论的 `trade_plan()` 得出
- 寻股的结论里同一只股票写了两次：照样写两项发现，股票池对第二次给出不通过
- 股票池增删执行到一半崩溃、重新执行时股票池给出"已在池子里"：发现照重新执行时股票池给的结果写；股票池操作的重复执行由「案件工作流」处理
- Task 数、调用次数是负数：调用方从计数得出，不检查
- 存着的记录的格式和代码对不上（以后改了 contracts）：读的时候报错，由改 contracts 的变更迁移数据
- 表一直增长：不清理

## Decisions

### D1 模块和三张表

新模块 `modules/caserecords`：案件记录是 Proposal 四部分记录之一，读写都和调度器、网关、股票池不同，单独一个模块，表名以 `caserecords_` 开头（state-store spec「表的归属和迁移」）。没有 `config.py`：没有阈值，读几个由调用方给。

`repo.py` 用 SQLAlchemy Core 定义，迁移 v0012 写明每一列：

```python
CASES = versioned_table(
    "caserecords_cases",
    Column("case_id", Uuid, nullable=False),
    Column("symbol", String, nullable=True),
    Column("case_type", String, nullable=True),
    Column("status", String, nullable=False),
    Column("concluded", Boolean, nullable=False),
    Column("concluded_at", DateTime(timezone=True), nullable=True),
    Column("ended_at", DateTime(timezone=True), nullable=True),
)

PROPOSALS = versioned_table("caserecords_proposals", Column("case_id", Uuid, nullable=False))

FINDINGS = Table(
    "caserecords_findings",
    MetaData(),
    Column("case_id", Uuid, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("symbol", String, nullable=False),
    Column("data", JSONB, nullable=False),
    PrimaryKeyConstraint("case_id", "seq"),
)
```

- cases、proposals 用 `VersionedStore`，键是案件 ID：一个案件的记录随案件进行变化（开始、结论、结束；审核、回应、风控结果），每一步一个新版本，旧版本留着。记录整份存成 JSON
- cases 的筛选列只放读取要用的（D6）：股票、案件类型、状态按值筛，`concluded` 筛出有结论的，`concluded_at`、`ended_at` 排序。不用 `concluded_at` 是否为空代替 `concluded`：`VersionedStore` 的筛选只有等于
- findings 一项一行、写入以后不变，不用版本表（和 `gateway_tasks` 一样）。主键是案件 ID 和第几项（从 1 起，按写入的顺序），读回按它排序；`symbol` 单独一列，M5 的 `query_history` 按股票查（Proposal「查询工具」）
- 三张表没有外键，每张表各用一个 `MetaData`

### D2 记录的模型

`domain/record.py`（frozen dataclass，`VersionedStore` 用 pydantic 的 `TypeAdapter` 存取）：

```python
class TriggerKind(StrEnum):
    """触发的种类，就是 case-types spec「触发」的四种。"""

    EVENT = "event"  # 事件
    HEARTBEAT = "heartbeat"  # 定时心跳的检查项
    FOLLOW_UP = "follow_up"  # 跟进
    SELL = "sell"  # 卖出成交


class CaseStatus(StrEnum):
    RUNNING = "running"  # 进行中
    ENDED = "ended"  # 已结束
    NOT_RUN = "not_run"  # 不再运行


@dataclass(frozen=True, slots=True)
class CaseTrigger:
    """案件的触发，开始运行和不再运行都带。"""

    kind: TriggerKind
    reason: str  # 触发原因：调用方写的中文说明
    symbol: str | None  # 寻股和每周复盘没有
    at: datetime  # 触发时刻


@dataclass(frozen=True, slots=True)
class CaseConclusion:
    """通过校验的结论、给出它的模型和结论时刻。"""

    conclusion: Conclusion
    model: ModelUse
    at: datetime


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """一个案件的记录，各项见 case-records spec「案件记录的内容」。"""

    case_id: UUID
    trigger: CaseTrigger
    case_type: CaseType | None  # 不再运行的没有
    started_at: datetime | None  # 不再运行的没有
    conclusion: CaseConclusion | None = None
    end_reason: CaseEndReason | None = None  # 只有已结束的有
    ended_at: datetime | None = None  # 已结束和不再运行的有
    tasks: int | None = None  # Task 数：结束时写；不再运行的是 0
    calls: int | None = None  # 调用次数：同上

    @property
    def status(self) -> CaseStatus:
        if self.started_at is None:
            return CaseStatus.NOT_RUN
        return CaseStatus.RUNNING if self.ended_at is None else CaseStatus.ENDED

    @property
    def duration(self) -> timedelta | None:
        """耗时：只有已结束的有。"""
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass(frozen=True, slots=True)
class Review:
    review: CriticReview
    model: ModelUse  # Critic 这次用的模型


@dataclass(frozen=True, slots=True)
class Response:
    response: PlanResponse
    model: ModelUse  # PM 这次用的模型


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """一个案件的方案记录，各项见 case-records spec「方案记录」。"""

    case_id: UUID
    plan: TradePlan  # 结论通过时的交易方案；修改的条款在那一轮回应里
    reviews: tuple[Review, ...] = ()
    responses: tuple[Response, ...] = ()
    critic_unavailable: bool = False
    risk: RiskDecision | None = None


class FindingKind(StrEnum):
    ADD = "add"  # 加入候选
    REMOVE = "remove"  # 删除


@dataclass(frozen=True, slots=True)
class Finding:
    """一项股票池增删和股票池的结果。"""

    kind: FindingKind
    symbol: str
    reason: str  # 加入的理由与论点草稿；删除的理由
    signal: Signal | None  # 加入的候选的信号；删除没有
    rejection: str | None  # 股票池不通过的原因：stockpool 的 Rejection 的值；通过为 None
```

- 状态和耗时由记录推出，不另存：开始时刻为空就是不再运行，结束时刻为空就是进行中
- 不存快照 ID：快照按案件 ID 和版本存（「状态库快照」），读案件的快照用案件 ID
- 方案记录只存结论通过时的方案，不维护"现在的方案"：修改的条款在 `PlanResponse.plan` 里，现在的方案由「案件工作流」自己的状态带着（D3），读记录的人按最后一次修改的条款得出
- Critic 不可用不放进审核的列表：它不是 Critic 的判断（case-stages spec「Critic 不可用」）
- 股票池不通过的原因存 `Rejection` 的英文值，和程序日志的动作一样存代码；本模块不 import 股票池
- `case_type`、`started_at` 不给默认值：存着的记录缺了这几项时读回报错，不当作不再运行

### D3 和调度器的案件状态的边界

| 东西 | 在哪里 | 谁写 | 谁读 |
| --- | --- | --- | --- |
| 排队的案件、它的触发和触发时刻 | 调度器的队列；存在哪里由「排队运行」定 | 排队运行 | 队列的决定 |
| 运行中案件的阶段、方案的动作、重交次数、质询轮次、Critic 审核的结果、现在的方案 | 调度器的案件状态；存在哪里由「案件工作流」定（比如 DBOS 工作流的状态） | 案件工作流 | 案件工作流、队列的快照 |
| 开始运行、结论、方案和审核、风控结果、发现、结束、不再运行 | 案件记录 | 案件工作流；不再运行由排队运行 | 案件上下文、收盘心跳、M5 的查询、M6 的评估 |

- 案件记录不记阶段、重交次数和质询轮次，所以阶段只有一份
- 两边都有的只有"有结论"和结束原因：案件状态是来源，调用方在案件状态变化以后写进案件记录（结束原因就是 `CaseState.end_reason`），之后只追加；不拿案件记录推进案件、排队或者恢复崩溃的案件，崩溃以后从案件状态（DBOS）接着走
- 案件记录的"进行中"只说明"开始运行写了、结束还没写"：队列、并发上限、方案作废都不读它
- 写入的时刻（spec「写入的时刻」）对应案件状态的事件：开始运行是 `new_case`，结论是 `accept_conclusion`，审核、Critic 不可用、回应、风控结果是 `CaseState` 的 `review`、`critic_unavailable`、`respond`、`risk_checked`，结束是案件状态变成已结束以后；不再运行是队列决定里的 `drop`

不选的做法：

- 案件记录也存阶段（每次阶段变化写一个版本）：两份阶段，崩溃时可能对不上，要定哪份为准
- 结束时才把整个案件整理进案件记录：中途的审核、回应要另找地方存，崩溃时丢；Proposal 也要求"在案件进行、结束时写入"

### D4 不重复存的：Result 和论点

- **Result**：Agent 网关按案件 ID 存了 Task 和 Result（`TaskStore.case_records`），Result 里有实际使用的模型和是不是备用模型，Task 里有对应的发起者、任务类型和轮次。案件记录只在结束时记 Task 数，不另存一份。Proposal 的 results 表里的 prompt 版本随 M5 的 prompt 一起加
- **论点**：PM 在案件里写的论点（开仓方案附的投资论点、论点更新、升级和清仓转为关注时的关注论点）就是结论的一部分，存在 cases 的结论里。Proposal theses 表的"每次复查时的状态变化和理由"就是这次结论的决定和理由，"复查日和催化剂截止日期的调整"就是附的论点里的日期；现在生效的论点在股票池里。案件记录在这上面加的是：给出结论的模型和结论时刻（谁、什么时候写的），以及案件的类型和触发（为什么复查）。不另建 theses 表
- **平仓时的论点结果**：放到 M6（proposal「需要人决定」第 2 条）。标注要用的事实（交易记录的来源案件、案件类型和触发、结论）都已经存下
- **发现要另存的原因**：结论里只有 PM 的请求，股票池常常不通过（流动性、冷却期、容量、每天的增删次数），探索命中率要只算真正加进去的候选，所以每一项记下股票池的结果

### D5 写入的接口和每一步只写一次

`CaseRecordService(store: CaseRecordStore, clock: Clock)`，「案件工作流」「排队运行」建在它上面：

```python
class CaseRecordService:
    """案件记录的写入和读取，见 case-records spec。每一步只写一次，顺序不对时抛 ValueError。"""

    def start(self, case_id: UUID, trigger: CaseTrigger, case_type: CaseType) -> None:
        """案件开始运行：开始时刻是现在。"""

    def not_run(self, case_id: UUID, trigger: CaseTrigger) -> None:
        """排队的案件不再运行：结束时刻是现在，Task 数和调用次数是 0。"""

    def conclude(
        self, case_id: UUID, conclusion: Conclusion, model: ModelUse, plan: TradePlan | None
    ) -> None:
        """结论通过校验：结论时刻是现在；有交易方案时 plan 是它补上方案 ID、股票和板块以后的。"""

    def review(self, case_id: UUID, review: CriticReview, model: ModelUse) -> None:
        """Critic 给出一次审核。"""

    def critic_unavailable(self, case_id: UUID) -> None:
        """Critic 不可用，方案没有经过审核。"""

    def respond(self, case_id: UUID, response: PlanResponse, model: ModelUse) -> None:
        """PM 回应一轮质询。"""

    def risk_checked(self, case_id: UUID, decision: RiskDecision) -> None:
        """风控给出结果。"""

    def add_findings(self, case_id: UUID, findings: Sequence[Finding]) -> None:
        """结论里的股票池增删执行以后，按顺序写每一项。"""

    def end(self, case_id: UUID, reason: CaseEndReason, tasks: int, calls: int) -> None:
        """案件结束：结束时刻是现在。"""
```

- 每个写入读出这个案件现在的记录，交给 domain 的规则得出新的记录，再保存。规则是纯函数，"现在"作为参数传入：
  - 这一步已经写过（开始时刻、结论、结束时刻、Critic 不可用、风控结果已经有了，发现已经有了）：原样返回，`VersionedStore` 因为内容相同不写新版本
  - 审核和回应按 spec「重复写入和写入的顺序」的表交替：上一次审核还没有回应时再写审核、上一轮回应以后还没有新的审核时再写回应，都是重复写入，原样返回
  - 前面的步骤没写（没有记录、不再运行的案件、没有方案记录、还没有审核就写回应），或者开始和不再运行互相冲突：抛 `ValueError`，不保存
- 为什么要"只写一次"：这些写入放在 DBOS 的步骤里，步骤写完数据库、还没记下完成就崩溃时会再执行一次（「外部写操作必须幂等」）。按"这一步写过没有"判断不用调用方传序号：审核和回应本来就交替，案件状态保证不会连着两次审核或两次回应
- 重复写入时时钟读到的"现在"更晚，所以不能靠"内容相同就不写"：开始时刻、结论时刻、结束时刻会不同
- `conclude` 写两张表（先方案记录、再结论），各自按"写过没有"判断：中间崩溃时，重新执行补上没写的那张
- 发现：同一个事务里先看这个案件有没有发现，没有才按顺序插入；`add_findings(case_id, [])` 什么都不写
- 不加锁：同一个案件的写入由调用方依次进行（一个案件一次只在一个阶段），不同案件的键不同

### D6 读取的接口

```python
class CaseRecordService:
    def case(self, case_id: UUID) -> CaseRecord:
        """这个案件的记录；没有时抛 KeyError。"""

    def proposal(self, case_id: UUID) -> ProposalRecord | None:
        """这个案件的方案记录；没有交易方案的案件为 None。"""

    def findings(self, case_id: UUID) -> tuple[Finding, ...]:
        """这个案件的发现，按写入的顺序。"""

    def recent_ended(self, symbol: str, limit: int) -> tuple[CaseRecord, ...]:
        """这只股票已结束的案件，按结束时刻从晚到早，最多 limit 个。"""

    def last_concluded(self, case_type: CaseType, symbol: str | None) -> CaseRecord | None:
        """这种案件类型（寻股的 symbol 为 None）结论时刻最晚的有结论的案件；没有时为 None。"""
```

- `recent_ended`：案件上下文的"该股票的近期结论"和"上一个案件的结束原因"（case-stages spec「结束原因」）。取几个、展示哪些由「案件工作流」定；复盘案件也在里面，由调用方挑
- `last_concluded`：「收盘心跳」的"上一次寻股的那一天"（寻股、`symbol=None`）和"新候选"（这只股票的候选评估的结论时刻早于成为候选的时刻，或者没有）。进行中的也算：有结论就是有结论（case-stages spec「结束原因」）
- 跟进案件的上下文要原案件的结论：跟进的记录有来源案件 ID，用 `case`
- 历史案件的数量索引、按时间和类型的查询：M5 的 `query_history` 一起加

`VersionedStore` 加一个读取，两个查询都用它：

```python
def most_recent(self, column: str, limit: int, **where: object) -> tuple[T, ...]:
    """每个键的最新版本里，筛选列等于给定值的那些，按时刻列 column 从晚到早，最多 limit 个。

    时刻相同的按键排序。
    """
    newest = self._newest()
    return self._records(
        select(newest.c.data)
        .where(*(newest.c[name] == value for name, value in where.items()))
        .order_by(newest.c[column].desc(), newest.c[self._key.name])
        .limit(limit)
    )
```

- `recent_ended` 是 `most_recent("ended_at", limit, symbol=symbol, status="ended")`；`last_concluded` 是 `most_recent("concluded_at", 1, case_type=..., symbol=symbol, concluded=True)`，`symbol=None` 时 SQLAlchemy 生成 `IS NULL`
- 放在 `VersionedStore` 而不是在 repo 里另写 SQL："每个键的最新版本"只在一处写（和股票池存储加 `latest_since` 一样）

### D7 结论的联合类型的顺序

`contracts/conclusion.py`：

```python
# 候选的结论排在关注的结论前面：候选决定移出的结论的字段是关注的结论的子集，按 JSON 读回时两种都
# 能通过，pydantic 取左边的。其余的决定和字段各不相同，只有一种能通过
type Conclusion = HoldingConclusion | CandidateConclusion | WatchConclusion | ExplorationConclusion
```

- 现在的顺序把候选的移出读成关注的移出：结论种类错了，`last_concluded`、M6 按案件类型的统计都会错
- 不选的做法：给四种结论加一个种类字段做判别（改变 PM 的输出格式和 case-types spec）；按案件类型选模型读回（`VersionedStore` 用一个 `TypeAdapter` 读整份记录，要另写读法，还要在本模块再放一份案件类型到结论种类的表）
- 用往返测试锁住：四种结论的每种决定写成 JSON 再读回，种类和内容都不变

### D8 触发怎样记

案件记录只记触发的种类（四种）、调用方写的触发原因、股票和触发时刻（`CaseTrigger`），调用方从调度器的 `Trigger` 换过来：

| 调度器的触发 | 种类 | 触发原因的写法 |
| --- | --- | --- |
| `EventTrigger` | 事件 | 股票、事件的类别，持仓风险级别的写明 |
| `HeartbeatTrigger` | 定时心跳的检查项 | 检查项，比如"CCC 的持仓复查日到期""股票池候选不足" |
| `FollowUpTrigger` | 跟进 | 股票和申请时写下的问题 |
| `SellTrigger` | 卖出成交 | 股票和怎样卖出的（止损单成交、紧急风险退出、PM 发起的减仓或清仓） |

- domain 不能 import 调度器；把触发的类型挪进 contracts 要连带调度器的案件类型表（`HeartbeatTrigger` 的检查依赖它）
- 在本模块再定义一份同样结构的触发：两份要一起改，现在也没有读者要按事件类别或检查项筛选，按案件类型筛就够了
- 事件 ID、跟进 ID 有了以后（「事件检测」「跟进到点」），由那些变更给 `CaseTrigger` 加引用

### D9 Task 数和调用次数从哪里来

都由调用方在结束时给出，案件记录只存：

- Task 数：Agent 网关给出的本案件用量里 PM 的任务数加 Critic 用掉的补充研究额度（task-dispatch spec「本案件的任务和用量」），就是派发出去的任务数
- 调用次数：Agent 网关为本案件记下的费用记录的条数（call-budget spec「费用的记录」）。重试、切换备用模型的每次尝试都在这里，和费用对得上；拿不到用量的尝试不记费用，也不算。`CostStore` 现在只有按时刻读，「案件工作流」给网关加按案件读的接口
- 不选的做法：工作流自己数调用（重试和切换备用模型在网关里，工作流看不到）；数程序日志（模型调用的条目 M5 才有）
- M4 的替身不调用模型，调用次数都是 0；不再运行的由本模块记 0

### D10 不写程序日志

案件记录本身就是记录，写它不另写程序日志条目，程序日志不加行为者和动作。程序日志里调度器的动作（开案件及案件类型、阶段变化、唤醒 PM 和 Critic、质询轮次、排队、方案作废）由做这些动作的流程写：「案件工作流」「排队运行」在它们的变更里加 `Action`。

### D11 迁移和不装配

- 迁移 `v0012_create_case_records.py`：`down_revision = "0011"`（「状态库快照」），写明三张表的每一列和主键，不 import 业务代码；只升级（add-state-store 的 design D6）
- bootstrap 不建 `CaseRecordService` 和 `PostgresCaseRecordStore`：现在没有写入方，「案件工作流」装配。表随启动时的迁移建好，一直是空的
- `PostgresCaseRecordStore(engine, clock)`：cases、proposals 各一个 `VersionedStore`；findings 直接用 SQLAlchemy Core。数据库的错误、读不出来的记录（pydantic 的 `ValidationError`）原样抛出，不当作没有记录
- 单元测试用 `tests/fakes/case_records.py` 的内存实现；domain 的规则单独测；repo 用真的数据库（要开着 OrbStack）

## Risks / Trade-offs

- [结论的读回依赖 pydantic 在两种都能通过时取左边的] → D7 的往返测试锁住，升级 pydantic 时测试会报
- [触发原因是文字，不能按事件类别筛选] → 现在按案件类型筛；事件 ID 有了以后加引用（D8）
- [调用次数要等网关加按案件读的接口] → 「案件工作流」加；在那之前替身的调用次数本来就是 0
- [被人取消、不再恢复的工作流的案件一直是进行中] → 不影响队列（D3）；以后要清理时由人按程序日志处理
- [state-store「表的归属和迁移」和「状态库快照」同时改] → 本变更后归档，apply 时按快照归档后的主 spec 重写 delta，只加本变更的三张表

## Migration Plan

- 迁移 v0012 在启动时随别的迁移升级（bootstrap spec），建三张空表；没有数据要搬
- 回滚：迁移只升级；不用的表留着是空的，不影响别的模块
