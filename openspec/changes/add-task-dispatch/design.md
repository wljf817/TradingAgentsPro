# Design

## Context

现状（M2 进行中，M3 完成了股票池、调度器、触发三组和 Agent 网关的信号格式）：

- Agent 网关模块 `modules/gateway/` 只有纯逻辑和配置：`GatewayConfig`（评估窗口的选项）、`signal_rejection`（对已经构造好的信号检查评估窗口和数据截至时刻，给出 `SignalRejection`）、`conclusion_signal_rejection`。没有 service、ports 和存储
- contracts 有子 agent 的身份 `SubAgent`（`contracts/agent.py`），信号 `Signal`、无信号 `NoSignal`、子 agent 的输出 `SubAgentOutput = Signal | NoSignal` 和条目 ID `EntryId`（`contracts/signal.py`），案件类型 `CaseType`（`contracts/case.py`）。还没有 Task 和 Result 的任何类型
- 调度器的结论校验 `conclusion_rejection(case, conclusion, dispatched, completed, config)` 要调用方传入 `dispatched: Collection[SubAgent]`（派过任务的子 agent）和 `completed: Mapping[str, SubAgent]`（状态是完成的 Result 出自哪个子 agent，键是 Result ID）；结论里的 `cited_results` 是 `tuple[str, ...]`
- 跟进模块的写法：domain 是 frozen dataclass 和纯函数，拒绝原因是 `StrEnum`、作为返回值；`ports.py` 定义存储 Protocol；service 注入存储、Clock、`IdGenerator` 和配置；测试用 `tests/fakes/` 的内存实现、`FakeClock`、`FakeIds`（依次给出 `UUID(int=1)`、`UUID(int=2)`……）和 Hypothesis 状态机
- 纯区域里只有 contracts 和 config 能用 pydantic；service 不在纯区域，可以用
- M3 只写纯逻辑和测试：不接外部系统，不建表、不加迁移、不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/task-dispatch/spec.md 和 specs/case-types/spec.md。

### 模块边界

```
contracts
  task.py         TaskType、Dispatcher、TaskRequest、Task、ResultStatus、TaskRejection、
                  TaskFailure、Result（新增）
                  <- 任务请求：PM 和 Critic 的 dispatch_tasks 工具参数（M5 解析）
                  -> Task、Result：状态库、案件记录、M5 的工具结果、M6 的评估
  agent.py        SubAgent（不改）
  signal.py       Signal、NoSignal、SubAgentOutput、EntryId（不改）
  case.py         CaseType（不改）

modules/gateway
  domain/signal.py    SignalRejection、signal_rejection（不改，Result 的生成调用它）
  domain/dispatch.py  CaseScope、TaskRecord、DispatchAction、Dispatch、TaskUsage；
                      相同的任务、复用、放行或拒绝、用量（新增）
  domain/result.py    Malformed；收到输出、调用失败、超时、被拒绝时的 Result（新增）
  config.py           GatewayConfig 加四个配置项
  ports.py            TaskStore（新增）
  service.py          TaskDispatchService（新增）
  __init__.py         导出公开的名字
```

本次只改 Agent 网关一个模块，加 contracts 的一个文件，不改其他模块的代码。跨模块传递的模型：

- 传入：`TaskRequest`（contracts 的 Pydantic 模型，M5 从工具参数解析）；子 agent 的输出是 JSON 文本，service 用 `SubAgentOutput` 解析成 contracts 的 `Signal` 或 `NoSignal`；`CaseType`（contracts 的 StrEnum），M4 从调度器的案件定义取得
- 传出：`Task`、`Result`（contracts 的 Pydantic 模型）；派过任务的子 agent `frozenset[SubAgent]` 和完成的 Result `dict[str, SubAgent]`，正好是调度器结论校验的两个参数
- 本模块的 frozen dataclass（`CaseScope`、`TaskRecord`、`Dispatch`、`TaskUsage`）只在网关和 M4、M5 的工作流之间传递，由包根导出

## Goals / Non-Goals

**Goals:**

- 规则都在 domain 里，是纯函数；service 只负责读当前时刻、生成 ID、解析输出、读写存储
- 子 agent 的输出只校验一处：结构由 contracts 的模型、评估窗口和数据截至时刻由 signal-format 的 `signal_rejection`，本变更只把结果记成 Result
- spec 每张表的每一行都有测试；状态机性质测试拿随机的派发、输出、失败、超时和拨动时间检查不变量

**Non-Goals:**

按「编程风格」省略的情况：

- 输入和引用的条目是否存在、是否在本案件的快照里：不检查
- 寻股的股票是否在粗筛出的名单里、是否在允许交易的范围：不检查
- 股票代码的大小写：不规范化，PM 写了小写的案件股票时被拒绝为不是案件的股票，看到原因可以改
- 问题措辞不同、意思相同：算不同的任务
- 复盘案件派任务：不检查，复盘案件不唤醒 PM 和 Critic
- Critic 在不审核方案的时候派任务：不检查，M5 只在 Critic 审核时给它工具
- 一次 dispatch_tasks 的请求个数：不限，超出的按任务数被拒绝；没有请求的一次什么都不做，不算一轮
- 案件是否存在、`CaseScope` 的案件类型和股票是否对得上：不检查，调用方保证
- 收到输出、调用失败、记超时的任务不存在：存储抛 `KeyError`，是调用方的错误
- 同一个案件的两次派发同时进行：调用方串行调用
- 运行中改配置：已经生成的 Task 的截止时间不变，已经用掉的轮次和任务数照算
- 截止时间已过、还没记超时的任务被相同的请求复用：照样复用，等它的超时 Result
- 费用的币种和小数位：调用方给出美元的 `Decimal`，不检查
- 派发以后进程崩溃、工作流重新执行时重复派发：由 M4 按 DBOS 的步骤处理
- PM 引用 Result ID 时改了大小写等写法：引用不上，按没有引用处理

## Decisions

### D1 Task 和 Result 放进 contracts，目标由任务类型推出

```python
class TaskType(StrEnum):
    """任务类型，见 task-dispatch spec「任务清单」。每种只属于一个子 agent。"""

    EVENT_IMPACT = "event_impact"  # 事件影响评估
    NEWS_SINCE = "news_since"  # 某日期以来的新闻汇总：起始日期写在问题里
    PRICE_REACTION = "price_reaction"  # 价格反应
    TREND_LEVELS = "trend_levels"  # 趋势和支撑阻力
    VOLATILITY = "volatility"  # 波动情况
    EARNINGS_VS_EXPECTATIONS = "earnings_vs_expectations"  # 财报对比预期
    GUIDANCE_CHANGE = "guidance_change"  # 指引变化
    VALUATION = "valuation"  # 估值位置

    @property
    def agent(self) -> SubAgent:
        """这种任务的目标：所属的子 agent。"""
        return _AGENTS[self]


_AGENTS: Final = {
    TaskType.EVENT_IMPACT: SubAgent.NEWS,
    TaskType.NEWS_SINCE: SubAgent.NEWS,
    TaskType.PRICE_REACTION: SubAgent.QUANT,
    TaskType.TREND_LEVELS: SubAgent.QUANT,
    TaskType.VOLATILITY: SubAgent.QUANT,
    TaskType.EARNINGS_VS_EXPECTATIONS: SubAgent.FUNDAMENTALS,
    TaskType.GUIDANCE_CHANGE: SubAgent.FUNDAMENTALS,
    TaskType.VALUATION: SubAgent.FUNDAMENTALS,
}


class Dispatcher(StrEnum):
    """任务的发起者：只有 PM 和 Critic 能派任务。"""

    PM = "pm"
    CRITIC = "critic"
```

- 放 contracts：Task 和 Result 要写进状态库，案件记录、M5 的工具结果、M6 的评估、复盘都要读，模块之间不能 import；任务请求是 PM 和 Critic 的工具参数，和结论一样来自模型输出，在 contracts 校验格式
- 目标不是字段，是 `TaskType.agent` 推出来的（proposal 第 8 条）：请求只写类型，"目标只能是三个子 agent"由这张表保证，不会出现类型和目标对不上
- 发起者是只有两个值的枚举：子 agent、Reviewer 不能派任务由类型保证，构造时就报错。发起者由 M5 按调用工具的 agent 填写，不是模型写的
- 考虑过：`SubAgent` 和 `Dispatcher` 合成一个 agent 身份的枚举。没有采用：两处都要再检查"只能是其中几个"

### D2 任务请求和 Task

```python
class TaskRequest(BaseModel):
    """发起者写的一个任务请求：dispatch_tasks 工具参数里的一项。不写目标和发起者。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_type: TaskType
    symbol: str = Field(min_length=1)
    question: str = Field(min_length=1)  # 在任务类型之外补充的具体关注点
    inputs: tuple[EntryId, ...] = ()  # 状态库条目引用，可以为空


class Task(BaseModel):
    """网关生成的任务：请求的原样内容，加上网关填写的几项。生成以后不能修改。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_id: UUID
    case_id: UUID
    dispatcher: Dispatcher
    task_type: TaskType
    symbol: str = Field(min_length=1)
    question: str = Field(min_length=1)
    inputs: tuple[EntryId, ...] = ()
    dispatched_at: AwareDatetime  # 派发时刻
    deadline: AwareDatetime  # 截止时间：派发时刻加任务时限
    pm_round: int | None = Field(default=None, ge=1)  # PM 派发出去的任务是第几轮

    @property
    def target(self) -> SubAgent:
        """目标 agent：任务类型所属的子 agent。"""
        return self.task_type.agent

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.deadline <= self.dispatched_at:
            raise ValueError("截止时间必须晚于派发时刻")
        if self.dispatcher is Dispatcher.CRITIC and self.pm_round is not None:
            raise ValueError("Critic 的任务没有 PM 的轮次")
        return self
```

- 股票必填（proposal 第 7 条）：单只股票的案件也写，由派发规则检查是不是案件的股票，不由网关悄悄改掉
- 问题必填：任务类型已经决定了要回答什么，问题写 PM 想让子 agent 特别注意的地方；某日期以来的新闻汇总的起始日期也写在这里（第 11 条）
- 输入可以为空：子 agent 本来就读本案件的快照，输入只指出要特别看的条目
- Task 是平铺的字段，不嵌套 `TaskRequest`：状态库和案件记录读起来直接，复制四个字段的代价很小
- `pm_round` 存在 Task 上：轮次要从记录数出来（D3），被拒绝的任务和 Critic 的任务没有轮次。被拒绝的 PM 任务"没有轮次"不在 validator 里查：Task 生成时网关已经知道是不是被拒绝
- 考虑过：按派发时刻分批数轮次。没有采用：测试的时钟不走，同一时刻可以派两次；轮次直接写在 Task 上，案件记录和复盘也看得到

### D3 Result

```python
class ResultStatus(StrEnum):
    """Result 的状态。"""

    COMPLETED = "completed"  # 完成：输出是信号
    NO_SIGNAL = "no_signal"  # 无信号：输出是无信号
    TIMED_OUT = "timed_out"  # 超时
    FAILED = "failed"  # 失败：格式不合法或调用失败
    REJECTED = "rejected"  # 被拒绝：网关没有派发


class TaskRejection(StrEnum):
    """被拒绝的原因，按 spec「派发的顺序」。"""

    NOT_CASE_STOCK = "not_case_stock"  # 股票不是案件的股票
    EXPLORATION_TARGET = "exploration_target"  # 寻股只能派给 Quant 和 Fundamentals
    ROUNDS_USED_UP = "rounds_used_up"  # 轮次已用完
    PM_TASKS_USED_UP = "pm_tasks_used_up"  # 任务数已用完
    CRITIC_TASKS_USED_UP = "critic_tasks_used_up"  # 补充研究额度已用完


class TaskFailure(StrEnum):
    """失败的原因。"""

    FORMAT_INVALID = "format_invalid"  # 格式不合法：不重新调用，不切换模型
    CALL_FAILED = "call_failed"  # 调用失败：什么时候算由「备用模型」规定


# 有输出的状态和输出的类型；带原因的状态和原因的类型
_OUTPUTS: Final = {ResultStatus.COMPLETED: Signal, ResultStatus.NO_SIGNAL: NoSignal}
_REASONS: Final = {ResultStatus.FAILED: TaskFailure, ResultStatus.REJECTED: TaskRejection}


class Result(BaseModel):
    """一个 Task 的 Result，用 task_id 标识。各状态带什么见 spec「Result 的格式」。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    task_id: UUID
    status: ResultStatus
    output: Signal | NoSignal | None = None
    reason: TaskFailure | TaskRejection | None = None
    detail: str | None = Field(default=None, min_length=1)  # 失败的说明
    elapsed: timedelta = Field(ge=timedelta(0))  # 耗时：生成 Result 的时刻减派发时刻
    cost: Decimal = Field(ge=0)  # 费用：调用方给出，怎么算由「调用预算」定

    @property
    def cited_entries(self) -> tuple[EntryId, ...]:
        """引用的条目：取自输出；没有输出时为空。"""
        return () if self.output is None else self.output.cited_entries

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if not _matches(self.output, _OUTPUTS.get(self.status)):
            raise ValueError("只有完成和无信号有输出：完成是信号，无信号是无信号")
        if not _matches(self.reason, _REASONS.get(self.status)):
            raise ValueError("失败带失败的原因，被拒绝带被拒绝的原因，其他状态不带")
        if (self.status is ResultStatus.FAILED) != (self.detail is not None):
            raise ValueError("失败必须写说明，其他状态不写")
        return self


def _matches(value: object, expected: type | None) -> bool:
    """这个状态不带这一项（expected 为 None）时 value 为空，带时 value 是 expected 的实例。"""
    return value is None if expected is None else isinstance(value, expected)
```

- 没有单独的 Result ID，用 task_id（proposal 第 10 条）：一个 Task 只有一个 Result；调度器要的 Result ID 是 `str(task_id)`
- 引用的条目是属性，不是字段：从输出取，不会和输出不一致；存储时不重复
- 原因是两个枚举的联合：失败和被拒绝的原因不重叠，值也不同，Pydantic 读回时分得清（实现时用 JSON 往返的性质测试确认）
- 说明只给失败：格式不合法时是 signal-format 给出的原因或者 Pydantic 指出的哪一项、为什么；调用失败时是调用方的说明。被拒绝的原因本身就说清楚了
- 不检查被拒绝的耗时和费用是 0：只有网关生成被拒绝的 Result，D5 写死为 0
- 实际使用的模型和是否为备用模型：「备用模型」加字段
- 考虑过：失败和被拒绝各一个原因字段。没有采用：多一个字段、多一条"另一个必须为空"的检查，Proposal 也只说"带原因"

### D4 派发规则

```python
_EXPLORATION_AGENTS: Final = frozenset({SubAgent.QUANT, SubAgent.FUNDAMENTALS})
_REUSABLE: Final = frozenset({ResultStatus.COMPLETED, ResultStatus.NO_SIGNAL})


@dataclass(frozen=True, slots=True)
class CaseScope:
    """派发任务的案件。symbol 是案件的股票，寻股没有。"""

    case_id: UUID
    case_type: CaseType
    symbol: str | None


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """一个任务和它的 Result；还在运行时没有 Result。"""

    task: Task
    result: Result | None = None

    @property
    def dispatched(self) -> bool:
        """派发出去了：不是被拒绝的任务。"""
        return self.result is None or self.result.status is not ResultStatus.REJECTED


def same_task(request: TaskRequest, task: Task) -> bool:
    """任务类型、股票、问题相同，输入不论顺序相同；不论发起者。"""
    return (
        request.task_type is task.task_type
        and request.symbol == task.symbol
        and request.question == task.question
        and set(request.inputs) == set(task.inputs)
    )


def reusable(request: TaskRequest, records: Iterable[TaskRecord]) -> Task | None:
    """本案件可以复用的相同任务：还没有 Result，或者 Result 是完成、无信号。"""
    for record in records:
        if same_task(request, record.task) and (
            record.result is None or record.result.status in _REUSABLE
        ):
            return record.task
    return None


def dispatch_rejection(
    request: TaskRequest,
    dispatcher: Dispatcher,
    scope: CaseScope,
    records: Sequence[TaskRecord],
    batch_round: int,
    config: GatewayConfig,
) -> TaskRejection | None:
    """按 spec「派发的顺序」的表给出第一项不通过的原因；全部通过时为 None。

    - records：本案件已有的任务，包括这一次里前面处理过的
    - batch_round：这一次如果有任务派发出去，是 PM 的第几轮
    """
    if scope.symbol is not None and request.symbol != scope.symbol:
        return TaskRejection.NOT_CASE_STOCK
    if (
        scope.case_type is CaseType.EXPLORATION
        and request.task_type.agent not in _EXPLORATION_AGENTS
    ):
        return TaskRejection.EXPLORATION_TARGET
    used = tasks_used(records, dispatcher)
    if dispatcher is Dispatcher.CRITIC:
        return TaskRejection.CRITIC_TASKS_USED_UP if used >= config.max_critic_tasks else None
    if batch_round > config.max_pm_rounds:
        return TaskRejection.ROUNDS_USED_UP
    if used >= config.max_pm_tasks:
        return TaskRejection.PM_TASKS_USED_UP
    return None


def rounds_used(records: Iterable[TaskRecord]) -> int:
    """PM 用掉的轮次：派发出去的任务里最大的轮次，没有时为 0。"""
    return max((r.task.pm_round for r in records if r.task.pm_round is not None), default=0)


def tasks_used(records: Iterable[TaskRecord], dispatcher: Dispatcher) -> int:
    """这个发起者派发出去的任务数，不论 Result 的状态；被拒绝的不算。"""
    return sum(1 for r in records if r.task.dispatcher is dispatcher and r.dispatched)
```

- 复用先于检查（spec「派发的顺序」）：能复用的请求不花钱，也就不受轮次和任务数限制；它复用的任务当初已经通过了股票和寻股的检查
- 相同和可复用分开（proposal 第 2、3 条）：`same_task` 只比内容；`reusable` 再看 Result，超时、失败、被拒绝的不复用，这样发起者能重试。同一个案件里可复用的相同任务最多一个：有了它，后面相同的请求都复用它，不会再派
- 还没有 Result 的也复用：同一次里的重复请求复用前面刚派发的任务。跨次的任务在 PM 拿到工具结果时都已经有 Result，所以"还在运行"只出现在同一次里
- 轮次在一次开始时算一次（`batch_round = rounds_used(records) + 1`），由 service 传入：一次里第一个任务派发以后，`rounds_used` 就变成这一轮了，不能每个请求重算。一次里没有任务派发出去时，这个轮次没有任何 Task 带着，下一次还是同一个数（proposal 第 4 条）
- 被拒绝的请求也生成 Task 存下来（spec「Task 的格式」）：每个 Result 都能找到对应的请求内容；它们没有轮次，`dispatched` 为假，不计数（第 5 条）
- 股票用 `scope.symbol` 判断：寻股没有股票，就不检查；寻股派给谁按案件类型判断
- Critic 不看轮次（Proposal：补充研究不占 PM 的轮次）
- 只给第一项原因：和股票池、跟进审批一样
- 考虑过：一次里有一个请求超限就整次拒绝。没有采用：Proposal 写的是"超限的任务"被拒绝；PM 已经能拿到的 Result 没必要扔掉

### D5 输出、调用失败和超时变成 Result

```python
@dataclass(frozen=True, slots=True)
class Malformed:
    """不能解析成格式正确的信号或无信号的输出。detail 指出是哪一项、为什么。"""

    detail: str


def received(
    task: Task,
    output: SubAgentOutput | Malformed,
    now: datetime,
    cost: Decimal,
    config: GatewayConfig,
) -> Result:
    """收到子 agent 的输出时的 Result，见 spec「子 agent 的输出变成 Result」「超时」。"""
    if now > task.deadline:
        return _settled(task, ResultStatus.TIMED_OUT, now, cost)
    if isinstance(output, Malformed):
        return _failed(task, TaskFailure.FORMAT_INVALID, output.detail, now, cost)
    rejection = signal_rejection(output, now, config)
    if rejection is not None:
        return _failed(task, TaskFailure.FORMAT_INVALID, rejection.value, now, cost)
    status = ResultStatus.COMPLETED if isinstance(output, Signal) else ResultStatus.NO_SIGNAL
    return _settled(task, status, now, cost, output=output)


def call_failed(task: Task, detail: str, now: datetime, cost: Decimal) -> Result:
    """调用方给出调用失败时的 Result；晚于截止时间是超时。"""
    if now > task.deadline:
        return _settled(task, ResultStatus.TIMED_OUT, now, cost)
    return _failed(task, TaskFailure.CALL_FAILED, detail, now, cost)


def expired(task: Task, now: datetime, cost: Decimal) -> Result:
    """到截止时间仍没有 Result：超时。早于截止时间抛 ValueError。"""
    if now < task.deadline:
        raise ValueError(f"任务 {task.task_id} 还没到截止时间")
    return _settled(task, ResultStatus.TIMED_OUT, now, cost)


def rejected(task: Task, reason: TaskRejection) -> Result:
    """被拒绝的 Result：没有调用子 agent，耗时和费用都是 0。"""
    return Result(
        task_id=task.task_id,
        status=ResultStatus.REJECTED,
        reason=reason,
        elapsed=timedelta(0),
        cost=Decimal(0),
    )
```

（`_settled` 和 `_failed` 用 `now - task.dispatched_at` 作耗时构造 `Result`，上面省略。）

- 截止时间先看：晚到的输出不论合不合法都是超时（proposal 第 6 条），PM 不会在超时以后又收到这个任务的信号。正好在截止时间收到的照常处理，记超时也要不早于截止时间，两边在同一时刻都允许，先到的为准（D7）
- 格式不合法的两个来源合在一起（已决定第 1 条）：结构错误由 service 解析时给出 `Malformed`（domain 不能 import pydantic）；评估窗口和数据截至时刻调用 signal-format 的 `signal_rejection`，说明就是它的原因值。都记为失败、原因是格式不合法，不重新调用
- 早于截止时间记超时抛 `ValueError`：调用方只在截止时间到了才调用，提前调用说明有错
- 调用失败只收说明：什么时候算调用失败、说明写什么由「备用模型」定

### D6 存储端口

```python
class TaskStore(Protocol):
    """任务和 Result 的存储。只追加：Task 生成时写入，Result 出来时写入，写入以后都不再改。"""

    def case_records(self, case_id: UUID) -> tuple[TaskRecord, ...]:
        """本案件的任务和它们的 Result，按 Task 写入的先后。"""
        ...

    def record(self, task_id: UUID) -> TaskRecord:
        """一个任务和它的 Result；没有这个任务时抛 KeyError。"""
        ...

    def add_task(self, task: Task) -> None:
        """写入一个新 Task。"""
        ...

    def add_result(self, result: Result) -> None:
        """写入一个 Task 的 Result；service 保证每个 Task 只写一次。"""
        ...
```

- Task 和 Result 由网关写、只追加，正好是 Proposal 共享状态库的"当前任务状态"和"已产出的结构化结果"，网关是它们唯一的写入者
- 按案件取：派发规则只看本案件的任务，最多十来个
- `tests/fakes/tasks.py`：`InMemoryTaskStore`，Task 按写入的先后存在 list 里，Result 按 task_id 存在 dict 里；重复写入同一个 Task 的 Result 时抛错，让测试发现 service 的问题
- 考虑过：像队列、收盘检查那样输入一份快照、输出一个决定，存储交给 M4。没有采用：同一次里后面的请求要看到前面刚派发的任务（复用、计数），Result 只能写一次也要先读后写；这些都落到 M4，规则就不在本模块的测试里了。和跟进模块同样处理

### D7 用例

```python
class TaskDispatchService:
    def __init__(
        self, store: TaskStore, clock: Clock, ids: IdGenerator, config: GatewayConfig
    ) -> None: ...

    def dispatch(
        self, scope: CaseScope, dispatcher: Dispatcher, requests: Sequence[TaskRequest]
    ) -> tuple[Dispatch, ...]: ...

    def receive(self, task_id: UUID, output: str, cost: Decimal) -> Result: ...

    def fail(self, task_id: UUID, detail: str, cost: Decimal) -> Result: ...

    def expire(self, task_id: UUID, cost: Decimal) -> Result: ...

    def record(self, task_id: UUID) -> TaskRecord: ...

    def records(self, case_id: UUID) -> tuple[TaskRecord, ...]: ...
```

`dispatch` 的流程，返回和请求一一对应的 `Dispatch(task, action)`，`action` 是 `DispatchAction` 的派发、复用或被拒绝：

```
now = clock.now()
records = store.case_records(scope.case_id) 的副本
batch_round = rounds_used(records) + 1
对每个请求：
  task = reusable(request, records)
    | 有 --> Dispatch(task, 复用)，下一个
    v
  rejection = dispatch_rejection(request, dispatcher, scope, records, batch_round, config)
  pm_round = batch_round（PM 并且没有被拒绝），否则 None
  task = new_task(ids.new_id(), scope, dispatcher, request, now, pm_round, config)
  store.add_task(task)
    | rejection 不是 None --> result = rejected(task, rejection)；store.add_result(result)
    |                         records 加上 (task, result)；Dispatch(task, 被拒绝)
    v
  records 加上 (task, 没有 Result)；Dispatch(task, 派发)
```

`receive`、`fail`、`expire` 的流程相同：

```
record = store.record(task_id)
  | 已经有 Result --> 返回它，不写存储（spec「一个任务只有一个 Result」）
  v
receive：SubAgentOutput 解析 JSON 文本；ValidationError --> Malformed(每条错误的位置和说明)
result = received(...) / call_failed(...) / expired(...)，now 从 clock 读
store.add_result(result)，返回 result
```

- 一次派发只读一次时钟：同一次的 Task 派发时刻相同
- `new_task` 是 domain 的纯函数：截止时间是 now 加任务时限，其余照抄请求
- 返回 `Dispatch(task, action)`，不直接返回 Result：派发的还没有 Result；复用的可能还在运行；被拒绝的 Result 已经在存储里，调用方用 `record(task_id)` 取，三种的取法一样
- 已经有 Result 时直接返回：晚到的输出、M4 的步骤重新执行都不会改 Result，也不报错
- 子 agent 的输出收 JSON 文本：模型给出的就是文本或者工具参数，M5 统一转成文本交过来，原样也方便写进程序日志。错误说明的格式是 `Signal.risks: Field required; NoSignal.reason: Field required` 这样每条的位置和说明，位置为空（不是 JSON）时写"输出"
- 同一个案件由调用方串行调用：先读后写，做法 M4 定

### D8 案件的用量和给结论校验的数

```python
@dataclass(frozen=True, slots=True)
class TaskUsage:
    """一个案件用掉的轮次和任务数。"""

    pm_rounds: int
    pm_tasks: int
    critic_tasks: int


def usage(records: Sequence[TaskRecord]) -> TaskUsage:
    """PM 用掉的轮次和任务数、Critic 用掉的补充研究额度。"""
    return TaskUsage(
        rounds_used(records),
        tasks_used(records, Dispatcher.PM),
        tasks_used(records, Dispatcher.CRITIC),
    )


def dispatched_agents(records: Iterable[TaskRecord]) -> frozenset[SubAgent]:
    """派过任务的子 agent：派发出去的任务的目标，不论 Result 的状态；被拒绝的不算。"""
    return frozenset(r.task.target for r in records if r.dispatched)


def completed_results(records: Iterable[TaskRecord]) -> dict[str, SubAgent]:
    """状态是完成的 Result 出自哪个子 agent，键是 Result 的 ID（task_id 的文字）。"""
    return {
        str(r.task.task_id): r.task.target
        for r in records
        if r.result is not None and r.result.status is ResultStatus.COMPLETED
    }
```

- 都是 domain 的纯函数，输入 `records(case_id)`：service 不必为每种数加一个方法
- 两个给结论校验的数正好是 `conclusion_rejection` 的 `dispatched` 和 `completed` 参数，M4 直接传
- 被拒绝的不算派过任务（proposal 第 9 条，case-types delta）；还在运行的算：PM 提交结论时一般都已有 Result，提交得早也不影响"派过"

### D9 配置和默认值

```python
class GatewayConfig(BaseModel):
    """Agent 网关的阈值，含义见 signal-format spec「信号的配置」、task-dispatch spec「派发的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 子 agent 和 PM 出信号时可以选的评估窗口，交易日数；至少一个，否则任何信号都不合法
    signal_horizons: tuple[PositiveInt, ...] = Field(default=(3, 5, 20, 60), min_length=1)
    # 每个案件 PM 派发出去的任务最多这么多个
    max_pm_tasks: PositiveInt = 6
    # 每个案件 PM 最多派这么多轮
    max_pm_rounds: PositiveInt = 2
    # 每个案件 Critic 派发出去的任务最多这么多个，不占 PM 的任务数和轮次
    max_critic_tasks: PositiveInt = 2
    # 截止时间是派发时刻加这么久
    task_timeout: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| PM 的任务数上限 | 6 个 | Proposal 的例子；三个子 agent 一轮各派一两个，开仓的研究要求是 2 个不同子 agent，够用 |
| PM 的轮次上限 | 2 轮 | Proposal 规定 |
| Critic 的补充研究额度 | 2 个 | Proposal 的例子；按案件计，Critic 最多审 3 次 |
| 任务时限 | 5 分钟 | 子 agent 一次调用通常在一分钟以内，留出重试和切换备用模型的余地；PM 两轮最多等 10 分钟，案件侧的决策不需要秒级，盘中风险由止损单和紧急风险退出兜底（proposal 第 6 条） |

- 都是正数：0 个任务、0 轮的话开仓的研究要求永远满足不了；时限为 0 的任务一派发就超时
- 不按任务类型分别设时限：现在没有证据表明哪种任务明显更慢，需要时再加
- 和调度器的"研究要求的子 agent 数"之间不校验：两个模块的配置，调低到互相矛盾属于配置错误
- 寻股和其他案件用同一套上限（proposal 第 13 条）
- M4 由 bootstrap 从配置文件读取，测试里直接构造

### D10 后续的接法（给 M4、M5、备用模型、调用预算的对照）

本变更不调用其他模块，下表是之后的接法：

| 什么时候 | 做什么 |
| --- | --- |
| PM 或 Critic 调用 `dispatch_tasks`（M5） | 把工具参数解析成 `TaskRequest` 的列表，格式错误退回模型；`dispatch(CaseScope(case_id, case_type, symbol), dispatcher, requests)`；派发的并行调用子 agent；按请求的顺序用 `record(task.task_id).result` 组工具结果，复用的还在运行时等它 |
| 子 agent 返回（M5） | `receive(task_id, output_json, cost)` |
| 调用失败（备用模型） | 重试和备用模型都用完以后 `fail(task_id, detail, cost)`；每次调用的超时要放在任务时限以内 |
| 截止时间到了还没有 Result（M4） | 用持久化休眠睡到 `task.deadline`，`expire(task_id, cost)`，费用是已经花掉的 |
| PM 提交结论（M4） | `records = records(case_id)`；`dispatched_agents(records)`、`completed_results(records)` 传给调度器的 `conclusion_rejection` |
| 组 PM 和 Critic 的提示（M5） | `usage(records(case_id))` 告诉它们还剩几个任务、几轮 |
| Critic 审核（M4） | 补充研究的 Result 交给 Critic，PM 回应时一并交给 PM（case-stages spec 已定） |
| 写入（M4） | repo 实现 `TaskStore`，写进状态库；Task 和 Result 写程序日志 |
| 预算（调用预算） | 预算用完时拒绝任务：加一项 `TaskRejection` 和一项检查；`cost` 按它的算法给出 |

- M5 只在唤醒 PM 阶段给 PM `dispatch_tasks`（proposal 第 12 条）；网关不检查阶段，给了也照常计数

### D11 包根导出和纯区域

包根导出 `TaskDispatchService`、`TaskStore`、`CaseScope`、`TaskRecord`、`Dispatch`、`DispatchAction`、`TaskUsage`、`Malformed`，以及 domain 的 `same_task`、`reusable`、`dispatch_rejection`、`rounds_used`、`tasks_used`、`usage`、`dispatched_agents`、`completed_results`、`new_task`、`received`、`call_failed`、`expired`、`rejected`。纯函数导出是为了测试只从包根 import。

- domain 只用 `collections.abc`、`dataclasses`、`datetime`、`decimal`、`enum`、`typing`、`uuid`，以及 contracts 的 `agent`、`case`、`signal`、`task` 和本模块的 config、`domain/signal.py`；`contracts/task.py` 只用 `datetime`、`decimal`、`enum`、`typing`、`uuid` 和 pydantic。都在白名单里，不改 `tests/architecture/rules.py`
- service 用 pydantic 的 `TypeAdapter` 和 `ValidationError` 解析输出：service 不在纯区域
- 模块里只有允许的几种文件（service、ports、domain、config），module-layers 契约自动覆盖，不用改 import-linter 的配置

### D12 测试

```
tests/contracts/test_task.py                    「任务清单」「任务请求的格式」「Task 的格式」
                                                「Result 的格式」的格式部分
tests/fakes/tasks.py                            任务存储的内存实现
tests/modules/gateway/builders.py               补充：三个基准请求、request(**changes)、
                                                K-1 到 K-4 的 CaseScope、task_service()
tests/modules/gateway/test_config.py            「派发的配置」的默认值和不合法
tests/modules/gateway/domain/test_dispatch.py   相同的任务、复用、派发的检查、用量
tests/modules/gateway/domain/test_result.py     收到输出、调用失败、超时、被拒绝的 Result
tests/modules/gateway/test_service.py           spec 其余场景
tests/modules/gateway/test_state_machine.py     状态机性质测试
```

- service 测试用 `FakeClock`（2026-10-14 15:00Z）、`FakeIds`（T-1、T-2……就是 `UUID(int=1)`、`UUID(int=2)`……）和内存存储；案件 K-1 到 K-4 是固定的 UUID
- 子 agent 的输出用基准信号、基准无信号的 `model_dump_json()`，结构错误的输出手写 JSON 文本
- 性质测试：任意合法的 Task 和 Result 按 JSON 写出再读回相等，原因的联合类型读回同样的枚举（M4 存进状态库再读回的前提）
- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：一个单只股票的案件、一个寻股；随机交错 PM 和 Critic 的派发（任务类型、股票、问题、输入从小的集合里取，让重复和超限经常出现）、给还在运行的任务交输出（合法的信号、无信号、结构错误、评估窗口不合法）、调用失败、拨动时间和记超时；配置用较小的上限（4 个任务、2 轮、1 个额度）。每一步之后检查：
  - 每个案件 PM 派发出去的任务不超过上限，轮次是从 1 开始连续的数、不超过上限，Critic 派发出去的不超过额度
  - 寻股没有派发给 News 的任务；单只股票的案件派发出去的任务都是案件的股票
  - 同一个案件里可以复用的相同任务最多一个
  - 被拒绝的任务一生成就有 Result，没有轮次
  - 每个任务最多一个 Result，写入以后不变；晚于截止时间生成的 Result 都是超时
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [相同的任务要问题一字不差，复用很少发生] → 费用主要靠任务数上限和「调用预算」控制；复盘可以统计重复派发（proposal 第 2 条）
- [任务时限 5 分钟可能不够主模型重试再切换备用模型] → 「备用模型」安排每次调用的超时时一并核对任务时限，需要时改配置
- [被拒绝的请求也生成 Task，状态库多几条记录] → 每个 Result 都能找到对应的请求，PM 和复盘看得到为什么被拒绝
- [先读后写，同一个案件两次派发并发会超额] → M4 一个案件一个工作流，派发串行
- [Result ID 用 task_id 的文字，PM 抄错就引用不上] → M5 的工具结果里原样给出 task_id；引用不上时结论校验报研究不足，PM 重交
- [轮次在一次开始时算，一次里全部被拒绝就不占轮次，PM 可以反复试错] → 每次被拒绝都不调用子 agent、不花钱；PM 的工具调用次数由 M5 的对话轮数限制

## Migration Plan

只新增代码和改配置的默认字段，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
