# Design

## Context

现状（M2 进行中，M3 完成了股票池、案件类型与结论、案件队列、案件阶段、复查日与催化剂、收盘检查）：

- contracts 已经有跟进申请 `FollowUpRequest`（原因类型 `FollowUpReason` 三种；引用 `CatalystReference | Condition | None`；问题；可能采取的动作；带时区的触发时间）、催化剂 `Catalyst`（引用 `CatalystReference | None`，带 `ReferenceKind` 日程项或事件和 ID）、代码能检测的条件 `Condition`、股票状态 `StockStatus`。格式由 Pydantic 校验，原因和引用是否对应、触发时间的范围留给本变更
- 调度器有 `FollowUpTrigger(symbol, question)`（`modules/scheduler/domain/case_type.py`）：`define_case` 按开始时的状态定为持仓跟进或关注跟进，候选和不在股票池时不运行；结论校验第 2 项已经拒绝跟进案件附跟进申请
- 股票池的写法：domain 是 frozen dataclass 和纯函数，拒绝原因是 `StrEnum`、作为返回值；`ports.py` 定义存储 Protocol；service 注入存储、Clock、交易日历和配置；测试用 `tests/fakes/` 的内存实现和 Hypothesis 状态机
- 交易日历有 `session_closing_after`（时刻归属的交易日）、`last_session_before`（一个交易日从上一个交易日的收盘开始）、`add_sessions`；shared 有 `Clock` 和 `IdGenerator`，测试替身是 `FakeClock` 和 `FakeIds`（依次给出 `UUID(int=1)`、`UUID(int=2)`……）
- M3 只写纯逻辑和测试：不接外部系统，不建表、不加迁移、不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/follow-ups/spec.md。

### 模块边界

```
contracts（不改）
  conclusion.py   FollowUpRequest、FollowUpReason          <- PM 结论里的跟进申请
  thesis.py       Catalyst、CatalystReference、ReferenceKind、Condition
  stock_pool.py   StockStatus                               <- 状态变化的之前和之后

shared（不改）
  trading_calendar.py  session_closing_after、last_session_before、add_sessions
  clock.py、ids.py     Clock、IdGenerator（注入 service）

modules/followups（新增）
  domain/follow_up.py  FollowUpStatus、FollowUp 和状态变化的方法
  domain/rules.py      FollowUpRejection；审批的检查、触发时间的上限、原因相同、到点、作废的规则
  config.py            FollowUpConfig
  ports.py             FollowUpStore
  service.py           FollowUpService
  __init__.py          导出公开的名字
```

本次只涉及跟进一个模块，不改其他模块。跨模块传递的模型：

- 传入：`FollowUpRequest`（PM 的持仓或关注的结论里的）和 `Catalyst`（结论生效以后的论点里的），都是 contracts 的 Pydantic 模型；`StockStatus`（contracts 的 StrEnum），M4 从股票池的状态变化取得
- 传出：`FollowUp`，本模块的 frozen dataclass，由包根导出，里面带着原样的 `FollowUpRequest`。M4 用 `FollowUpTrigger(f.symbol, f.request.question)` 构造调度器的触发
- 没有新的 Pydantic 模型：记录只有本模块和 M4 的工作流读写，和股票池的 `PoolEntry` 一样放在模块里

## Goals / Non-Goals

**Goals:**

- 规则都在 domain 里，是纯函数；service 只负责读当前时刻、生成 ID、读写存储
- spec 每张表的每一行都有测试；状态机性质测试拿随机的申请、拨动时间、到点触发和状态变化检查不变量

**Non-Goals:**

按「编程风格」省略的情况：

- 案件运行中股票状态往下走（比如止损成交）以后才提交结论：结论里的跟进照样获批；到点时案件按那时的状态不运行，之后再升级为关注的话按关注跟进运行
- 论点更新以后催化剂改为引用某个待执行跟进引用的对象：不作废这个跟进，到点照常触发
- 已触发的跟进开出的案件还在排队时，又获批了原因相同的跟进：当作新的跟进，两个案件依次运行
- 进程长时间不在，跟进到点很久以后才触发：照常触发，不判断过时
- 运行中改了配置：已经获批的跟进不受影响（调低上限以后已有的保留，调低最远间隔以后远期的照常触发）
- 今天之后第"触发时间最远间隔"个交易日超出交易日历的覆盖范围：日历报覆盖范围错误，按系统故障处理
- 来源案件是否存在、是不是这只股票的案件：不检查，调用方保证
- 状态变化传入的之前和之后与股票池对不上：不检查，调用方保证
- 股票代码的大小写和空白：不规范化，调用方传标准代码
- 触发时间的时区：不换算，带时区的时刻直接比较
- 两个审批同时进行：调用方串行调用，和股票池一样，做法 M4 再定

## Decisions

### D1 存储端口加用例，不用纯函数加快照

跟进的记录是本模块自己的数据，写入者只有跟进审批；审批、到点触发和作废都要先读这只股票待执行的跟进，再写回新的状态。所以照股票池的写法：domain 是纯函数，service 读 Clock、生成 ID、读写存储端口。

- 考虑过：像队列和收盘检查那样，输入一份快照、输出一个决定。没有采用：那两个的输入来自好几个模块（队列、案件、股票池、论点），决定由别人执行；跟进的话，替换时旧的记为被替换、到点记为已触发、作废，这些写回都会落到 M4，规则就不在本模块的测试里了
- 考虑过：放进调度器模块。没有采用：一个变更只改架构图的一个节点，自定义跟进是「触发」下的节点；调度器的 domain 都是无状态的决定

### D2 记录和状态

```python
class FollowUpStatus(StrEnum):
    """跟进的状态。只有待执行的会变化，其余三种不再变化。"""

    PENDING = "pending"  # 待执行
    TRIGGERED = "triggered"  # 已触发
    REPLACED = "replaced"  # 被替换：这只股票原因相同的新申请获批
    VOIDED = "voided"  # 已作废：股票的状态往下走


@dataclass(frozen=True, slots=True)
class FollowUp:
    """一个获批的跟进。request 是申请的原样内容；获批的申请都引用了具体对象。"""

    follow_up_id: UUID
    symbol: str
    request: FollowUpRequest
    case_id: UUID  # 来源案件：提出申请的案件
    requested_at: datetime  # 获批时刻
    status: FollowUpStatus = FollowUpStatus.PENDING

    def triggered(self) -> Self:
        """到了触发时间。"""
        return self._settled(FollowUpStatus.TRIGGERED)

    def replaced(self) -> Self:
        """这只股票原因相同的新申请获批。"""
        return self._settled(FollowUpStatus.REPLACED)

    def voided(self) -> Self:
        """这只股票的状态往下走。"""
        return self._settled(FollowUpStatus.VOIDED)

    def _settled(self, status: FollowUpStatus) -> Self:
        if self.status is not FollowUpStatus.PENDING:
            raise ValueError(f"跟进 {self.follow_up_id} 已经是 {self.status}，不能再变化")
        return replace(self, status=status)
```

- 申请原样保存，不拆成字段：问题、可能采取的动作和引用都是 PM 申请时写下的，M4 放进案件上下文时原样给出
- 替换时新建一条记录，不改旧的：旧的留着（被替换），复盘看得到 PM 改过什么；每天的申请次数也直接从记录数出来（D5）
- 状态只从待执行变一次，方法里检查，不是待执行就抛 `ValueError`：service 只对待执行的跟进调用，抛出说明有错
- 不记状态变化的时刻：规则用不到；M4 的存储按版本追加，版本本身带时刻，程序日志也会记
- 来源案件是 `UUID`，和调度器的 `case_id` 一样

### D3 审批的检查和拒绝原因

```python
class FollowUpRejection(StrEnum):
    """跟进申请不通过的原因，按 spec「审批的顺序」。"""

    NO_REFERENCE = "no_reference"  # 没有引用具体对象：提示 PM 改为调整复查日
    REFERENCE_MISMATCH = "reference_mismatch"  # 原因和引用的对象不符
    CATALYST_COVERED = "catalyst_covered"  # 催化剂已经覆盖
    TRIGGER_PASSED = "trigger_passed"  # 触发时间已过
    TRIGGER_TOO_FAR = "trigger_too_far"  # 触发时间太远
    STOCK_FULL = "stock_full"  # 待执行的跟进已满
    DAILY_LIMIT = "daily_limit"  # 申请次数已用完


# 引用事件或日程项的两种原因各自要求的引用种类；价格或指标观察引用条件
_REFERENCE_KINDS: Final = {
    FollowUpReason.EVENT_REACTION: ReferenceKind.EVENT,
    FollowUpReason.EXTERNAL_SCHEDULE: ReferenceKind.SCHEDULE,
}


def request_rejection(
    request: FollowUpRequest,
    catalyst: Catalyst,
    pending: Sequence[FollowUp],
    approved_today: int,
    now: datetime,
    calendar: TradingCalendar,
    config: FollowUpConfig,
) -> FollowUpRejection | None:
    """审批一个跟进申请，给出第一项不通过的原因，见 spec「审批的顺序」。

    - catalyst：这份结论生效以后这只股票论点里的催化剂
    - pending：这只股票待执行的跟进
    - approved_today：今天已经获批的申请次数
    """
    reference = request.reference
    if reference is None:
        return FollowUpRejection.NO_REFERENCE
    if not _matches(request.reason, reference):
        return FollowUpRejection.REFERENCE_MISMATCH
    if reference == catalyst.reference:
        return FollowUpRejection.CATALYST_COVERED
    if request.trigger_at <= now:
        return FollowUpRejection.TRIGGER_PASSED
    if request.trigger_at >= trigger_limit(now, calendar, config):
        return FollowUpRejection.TRIGGER_TOO_FAR
    if same_reason(request, pending) is None and len(pending) >= config.max_pending_per_stock:
        return FollowUpRejection.STOCK_FULL
    if approved_today >= config.max_daily_requests:
        return FollowUpRejection.DAILY_LIMIT
    return None


def same_reason(request: FollowUpRequest, pending: Iterable[FollowUp]) -> FollowUp | None:
    """原因相同（原因类型相同，且引用同一个对象）的待执行跟进；没有时为 None，最多一个。"""
    for follow_up in pending:
        old = follow_up.request
        if old.reason is request.reason and old.reference == request.reference:
            return follow_up
    return None


def _matches(reason: FollowUpReason, reference: CatalystReference | Condition) -> bool:
    """事件后反应引用事件，外部日程引用日程项，价格或指标观察引用代码能检测的条件。"""
    if isinstance(reference, Condition):
        return reason is FollowUpReason.OBSERVATION
    return _REFERENCE_KINDS.get(reason) is reference.kind
```

- 拒绝是 PM 提议的正常结果，作为返回值，不抛异常（同股票池 D4）。调用方当作结论校验不通过退回 PM（proposal 第 2 条）
- 两种引用用类型分：`CatalystReference` 带种类，`Condition` 是另一个类型，字段不重叠（contracts 的 `FollowUpRequest` 已经这样说明）
- 和催化剂、和旧跟进比较都用 Pydantic 模型的相等：日程项和事件要种类和 ID 都相同；条件要类型、比较和值都相同；条件和日程项、事件永远不等，催化剂不引用（None）时也不等
- 原因相同按 Proposal 比原因类型和引用两项。有了第 2 项的对应关系，引用相同其实就意味着原因类型相同，比两项不多花什么
- 替换时不受每只股票的上限限制：先看有没有原因相同的，再看上限。每只股票原因相同的待执行跟进最多一个，由替换维持
- 不看股票状态：只有持仓和关注的结论能附跟进申请，格式已经保证；不检查引用的事件或日程项是否存在（proposal 第 7 条）
- 只给第一项原因：和股票池、复查日的检查一样，PM 改正以后再提交，下一项自然会被检查到

### D4 触发时间的上限

```python
def trigger_limit(now: datetime, calendar: TradingCalendar, config: FollowUpConfig) -> datetime:
    """触发时间要早于它：现在归属的交易日之后，第 max_trigger_sessions 个交易日的收盘时刻。"""
    today = calendar.session_closing_after(now).day
    return calendar.add_sessions(today, config.max_trigger_sessions).close
```

- 今天从现在归属的交易日算（proposal 第 5 条），和 review-dates 的"今天"相同：收盘后审批的从下一个交易日算起
- 和收盘时刻比，等价于"触发时间归属的交易日不晚于那一天"，又不用查触发时间本身归属哪天：PM 写一个几十年后的时刻，查归属会超出日历的覆盖范围而报错，比较时刻只会被拒绝
- 先比"在现在之后"：过去的时刻不查日历

### D5 每天的申请次数

```python
def _approved_today(self, now: datetime) -> int:
    """now 归属的交易日里已经获批的申请次数。这个交易日从上一个交易日的收盘时刻开始。"""
    start = self._calendar.last_session_before(now).close
    return len(self._store.requested_since(start))
```

- 从记录推出，不另存计数：每次获批（新增或替换）都新建一条记录，获批时刻不早于这个交易日开始的记录个数就是今天获批的次数，不论之后触发、被替换还是作废（proposal 第 4 条）
- 被拒绝的申请不保存，自然不计
- 交易日的开始和股票池的每天增删次数相同（股票池 design D2）
- 考虑过：只算新增、不算替换。没有采用：要另外记哪些记录是替换出来的；默认上限下替换很少

### D6 到点触发

```python
def due(pending: Iterable[FollowUp], now: datetime) -> list[FollowUp]:
    """触发时间不晚于 now 的待执行跟进，按触发时间、再按获批的先后。"""
    return sorted(
        (f for f in pending if f.request.trigger_at <= now),
        key=lambda f: (f.request.trigger_at, f.requested_at),
    )
```

- service 的 `trigger_due` 把 `due` 给出的跟进都记为已触发、保存，再返回它们：给出的就是这次记为已触发的，下一次不会再给，跟进只触发一次（proposal 第 6 条）
- 排序决定 M4 入队的先后：同一优先级、同一触发时刻的案件按入队的先后启动（case-queue spec「启动的顺序」）
- 不看股票状态、降级模式和调用预算：往下走的已经作废；降级或预算用完时案件由队列留着；开始运行时由 `define_case` 按那时的状态定类型
- 错过的不补判断，下一次照样给出全部已经到点的
- 输出本模块的 `FollowUp`，不输出调度器的 `FollowUpTrigger`：模块之间不能 import，M4 一行就能转换
- 考虑过：把 `FollowUpTrigger` 移到 contracts，两边共用。没有采用：要改调度器的代码，本变更只改一个节点；和收盘检查的 `CheckItem`（close-check design D2）同样处理

### D7 状态变化时作废

```python
# 状态从高到低；不在股票池也不是持仓（None）最低
_RANKS: Final = {StockStatus.HOLDING: 3, StockStatus.WATCH: 2, StockStatus.CANDIDATE: 1}


def voids_follow_ups(before: StockStatus | None, after: StockStatus | None) -> bool:
    """状态变到更低时作废这只股票待执行的跟进：清仓、关注降为候选、移出股票池。"""
    return _rank(after) < _rank(before)


def _rank(status: StockStatus | None) -> int:
    return 0 if status is None else _RANKS[status]
```

- 一条规则覆盖 spec 表里每一行（已决定第 1 条）：清仓的三种去向、降为候选、删除都变低；关注股买入成交变高，保留；候选没有待执行的跟进，变低时作废的是空的
- 规则在本模块：M4 每次状态变化都交过来，不由 M4 判断哪些变化要作废，规则只有一处、有表格测试
- 考虑过：只提供"作废这只股票的跟进"，由 M4 在清仓、降为候选和移出时调用。没有采用：哪些变化作废就落到了 M4，"买入成交保留"容易漏
- 考虑过：本模块读股票池判断。不能：模块之间不能 import

### D8 存储端口

```python
class FollowUpStore(Protocol):
    """跟进的存储：按跟进 ID 保存每个跟进最新的记录。

    数据库实现（M4）只追加不覆盖：状态有变化时写入一个新版本，读取时取每个跟进的最新版本，
    见 state-store spec。
    """

    def pending(self) -> tuple[FollowUp, ...]:
        """全部待执行的跟进。"""
        ...

    def requested_since(self, moment: datetime) -> tuple[FollowUp, ...]:
        """获批时刻不早于 moment 的跟进，不论现在的状态；用来数当天的申请次数。"""
        ...

    def save(self, follow_up: FollowUp) -> None:
        """保存这个跟进最新的记录：新的跟进新增，状态变了的取代原来的版本。"""
        ...
```

- `pending` 返回全部待执行的，service 按股票筛：最多 16 只股票、每只 2 个，不需要按股票查的接口
- `tests/fakes/follow_ups.py`：`InMemoryFollowUpStore`，按跟进 ID 存在 dict 里
- M4 的 `repo.py` 按状态库的写法实现：按跟进 ID 只追加，读取时取最新版本

### D9 用例

```python
class FollowUpService:
    def __init__(
        self,
        store: FollowUpStore,
        clock: Clock,
        calendar: TradingCalendar,
        ids: IdGenerator,
        config: FollowUpConfig,
    ) -> None: ...

    def pending(self, symbol: str) -> tuple[FollowUp, ...]: ...

    def request(
        self, symbol: str, request: FollowUpRequest, case_id: UUID, catalyst: Catalyst
    ) -> FollowUp | FollowUpRejection: ...

    def trigger_due(self) -> tuple[FollowUp, ...]: ...

    def status_changed(
        self, symbol: str, before: StockStatus | None, after: StockStatus | None
    ) -> tuple[FollowUp, ...]: ...
```

- `pending`：这只股票待执行的跟进，按 `due` 的顺序（触发时间、获批的先后）
- `trigger_due`：这次记为已触发的跟进（D6）
- `status_changed`：`voids_follow_ups` 为真时把这只股票待执行的跟进都记为已作废，返回它们；否则返回空

`request` 的流程：

```
now = clock.now()
pending = 这只股票待执行的跟进（store.pending() 按股票筛）
approved_today = len(store.requested_since(calendar.last_session_before(now).close))
rejection = request_rejection(request, catalyst, pending, approved_today, now, calendar, config)
  | 有原因 --> 返回原因，不写存储
  v
old = same_reason(request, pending)；有的话 store.save(old.replaced())
follow_up = FollowUp(ids.new_id(), symbol, request, case_id, requested_at=now)
store.save(follow_up)，返回 follow_up
```

- "现在"由 service 从 Clock 读取，再作为参数传给 domain；ID 从 `IdGenerator` 取，回放时能重现
- 同一个实例由调用方串行调用：上限和次数的检查是先读后写，做法 M4 和股票池一起定

### D10 配置和默认值

```python
class FollowUpConfig(BaseModel):
    """跟进审批的阈值，含义见 follow-ups spec「跟进的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 每只股票待执行的跟进不超过这么多个，替换不算新增
    max_pending_per_stock: int = Field(default=2, ge=1)
    # 触发时间早于今天之后第这么多个交易日的收盘时刻
    max_trigger_sessions: int = Field(default=60, ge=1)
    # 每个交易日获批的申请（新增和替换）不超过这么多次
    max_daily_requests: int = Field(default=5, ge=1)
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 每只股票的待执行跟进上限 | 2 个 | Proposal 规定（proposal 第 8 条） |
| 触发时间最远间隔 | 60 个交易日 | Proposal 规定；和不引用日程项的催化剂截止日期最长间隔相同，约一个财报季 |
| 每天的申请次数上限 | 5 次 | 持仓和关注最多 16 只，财报季一天几只股票发财报，每只一个"看盘后反应"够用；跟进开出的案件压在每天 5 个左右，和每天 3 个候选评估同一量级（proposal 第 4 条） |

都只要求至少 1：0 的话跟进就没法申请；配置项之间没有必须满足的关系，不加校验。M4 由 bootstrap 从配置文件读取，测试里直接构造。

### D11 M4 的接法（给 M4 的对照）

本变更不调用其他模块，下表是 M4 工作流的接法：

| 什么时候 | 做什么 |
| --- | --- |
| PM 的结论附了跟进申请 | case-types 的结论校验、review-dates 的检查都通过以后，最后调用 `request(symbol, request, case_id, catalyst)`；`catalyst` 取结论里论点更新的催化剂，没有论点更新时取现在的论点的。返回 `FollowUpRejection` 时当作结论校验不通过，带着原因退回 PM |
| 定时，或用 DBOS 的持久化休眠睡到最早的触发时间 | `trigger_due()`；每个跟进构造 `FollowUpTrigger(f.symbol, f.request.question)` 入队，触发时刻用 `f.request.trigger_at`；记为已触发和入队放在同一个步骤里 |
| 股票池的每次状态变化以后（加入、升降级、删除、超期移出、买入成交、两种清仓） | `status_changed(symbol, before, after)`，移出时 `after` 为 None |
| 组案件上下文 | 这只股票待执行的跟进：`pending(symbol)`。跟进案件要带上原案件的结论和申请原因：`FollowUpTrigger` 现在只有股票和问题，M4 接线时给它加上跟进 ID（「修改：调度器」），再用 `FollowUp.case_id` 和 `request.reason` 取 |

- 审批获批即保存，所以必须是结论的最后一项检查：放在前面的话，后面的检查不通过时跟进已经存了
- 程序日志记审批、拒绝、替换、作废和触发（Proposal「程序日志」的调度器一行）

### D12 包根导出和纯区域

包根导出 `FollowUp`、`FollowUpConfig`、`FollowUpRejection`、`FollowUpService`、`FollowUpStatus`、`FollowUpStore`，以及 domain 的 `request_rejection`、`same_reason`、`trigger_limit`、`due`、`voids_follow_ups`。纯函数导出是为了测试只从包根 import。拒绝原因叫 `FollowUpRejection`，不叫 `Rejection`：M4 的工作流会同时 import 股票池的 `Rejection`。

- domain 只用 `collections.abc`、`dataclasses`、`datetime`、`enum`、`typing`、`uuid`，以及 contracts 的 `conclusion`、`thesis`、`stock_pool`，shared 的 `trading_calendar` 和本模块的 config；都在白名单里，不改 `tests/architecture/rules.py`
- 模块里只有允许的几种文件（service、ports、domain、config），module-layers 契约自动覆盖新模块，不用改 import-linter 的配置

### D13 测试

```
tests/fakes/follow_ups.py                        跟进存储的内存实现
tests/modules/followups/builders.py              基准数据：纽交所样例日历、2026-10-14 15:00Z、
                                                 三种申请、CCC 和 BBB 的催化剂、来源案件；
                                                 新建 FollowUpService 的辅助函数
tests/modules/followups/test_config.py           spec「跟进的配置」
tests/modules/followups/domain/test_follow_up.py 状态变化方法
tests/modules/followups/domain/test_rules.py     审批的检查、触发时间的上限、原因相同、到点、作废的规则
tests/modules/followups/test_service.py          spec 其余场景
tests/modules/followups/test_state_machine.py    状态机性质测试
```

- service 测试用 `FakeClock`、`FakeIds`（获批的跟进依次是 `UUID(int=1)`、`UUID(int=2)`……，对应 spec 的 F-1、F-2）、纽交所样例日历（覆盖到 2027-01-29，场景最晚用到 2027-01-12）和内存存储；来源案件 K-1、K-2 是固定的 UUID
- builders 用 `model_copy(update=...)` 写"触发时间改为""引用改为"
- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：几只股票各有一个模型里的状态，随机交错：
  - 申请：只对持仓和关注；随机原因、引用（不写、少量事件和日程项 ID、少量条件，有时就是这只股票催化剂的引用）、触发时间（过去、近处、上限附近）；催化剂按股票固定，有时引用对象
  - 拨动时间（跨收盘、跨周末），现在不晚于 2027-01-04，最远间隔配置不超过 10 个交易日，按交易日推算的日期都在样例日历之内
  - 到点触发
  - 状态变化：按 stock-pool spec「状态变化」表从模型里的状态随机选一个合法的变化
  - 配置用较小的上限（每只股票 2 个、每天 3 次），让上限经常被碰到

  每一步之后检查：
  - 每只股票待执行的跟进不超过上限，原因相同的最多一个
  - 每个交易日获批的申请不超过上限
  - 被拒绝的申请没有改变任何记录
  - 获批的跟进：引用和原因对应、不等于催化剂的引用、触发时间在获批时刻之后并且早于那时的上限
  - 到点触发以后没有触发时间不晚于现在的待执行跟进；每个跟进最多被给出一次
  - 已触发、被替换、已作废的跟进不再变化
  - 状态往下走以后这只股票没有待执行的跟进；候选和不在股票池的股票没有待执行的跟进
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [获批即保存，审批必须是结论的最后一项检查] → spec「审批的输入和结果」写明，D11 的接法照做
- [记为已触发以后、入队以前进程崩溃，这个跟进就丢了] → M4 把两步放进同一个 DBOS 步骤
- [跟进只触发一次，跟进案件因系统故障没有结论就没了] → 持仓和关注有复查日兜底，PM 在之后的案件里可以再申请（proposal 第 6 条）
- [不检查引用是否存在，编造的引用能绕过"不允许两周后再看看"] → 受每只股票 2 个、每天 5 次的上限约束；复盘按案件类型统计跟进案件；M4 定了数据源以后可以加（proposal 第 7 条）
- [申请被拒绝要退回 PM，占一次重交] → 和复查日的检查一样；M5 在 PM 的提示里写明规则，减少被拒绝（proposal 第 2 条）
- [每天的上限按获批次数，替换也算] → 默认 5 次够用，不够时改配置

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
