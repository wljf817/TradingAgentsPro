# Design

## Context

现状：

- Agent 网关（`modules/gateway`）有三个用例，都不调用模型：
  - `TaskDispatchService`：`dispatch` 放行或拒绝任务请求；`receive(task_id, output: str, cost, model_use)` 把子 agent 的 JSON 文本解析、校验成 Result（格式不合法记失败）；`fail`、`expire` 记调用失败和超时；一个任务只有一个 Result，先写入的为准
  - `ModelFallbackService`：`begin(case_id, agent, deadline)` 给出第一步（`Attempt`：向哪个模型、最多等多久），`fail(call, error)` 给出下一步，`succeed(call)` 给出实际使用的模型；全部不可用是 `AllUnavailable`（带说明），子 agent 到了截止时间是 `DeadlineReached`。熔断状态和起点放在内存里，类注释写着"同一个实例由调用方串行调用"
  - `CallBudgetService.record(scope, agent, model, usage)`：记一条费用，模型没有单价时抛 KeyError；可以同时调用
- 模型调用的"调用方"在 M3 留空：model-fallback、call-budget、task-dispatch 三份 spec 都写"调用方按步骤尝试、报告结果、交来用量"
- contracts 里已有：任务请求、Task、Result（`contracts/task.py`），四种结论和 `PlanDraft`（`contracts/conclusion.py`），`CriticReview`、`PlanResponse`（`contracts/review.py`），两种论点（`contracts/thesis.py`），`ModelRef`、`ModelUse`、`TokenUsage`（`contracts/model.py`），`Agent`、`SubAgent`（`contracts/agent.py`），`EventCategory`；「状态库快照」刚加了 `contracts/snapshot.py` 的 `Snapshot`（案件 ID、类型、股票、生成时刻、账户、市场状态、行情、粗筛结果……，条目 ID `s<版本>/<类别>[/<键>]`）
- 调度器的 `define_case` 给出 `CaseDefinition`（案件类型、股票、问题、事件要求的子 agent）；股票池记录里有现在的关注论点和投资论点；这些都在调度器和股票池模块里，网关不能 import
- integrations 的包按适配器分目录，`__init__.py` 写说明并导出要装配的名字（比如 `integrations/finnhub`）；适配器自己的配置写在适配器里

动机见 proposal.md，要求见 specs/。

### 模块边界

```
contracts/agent_call.py               (新) CaseContext、TaskOutcome；DispatchTasks、SubmitConclusion、
                                      SubmitReview 和 PmReply、CriticReply；TaskResults、Returned、
                                      Challenge、PmTurn、CriticTurn；PmRequest、CriticRequest、
                                      SubAgentRequest、ReviewerRequest；RetrospectiveReport
modules/gateway/
  ports.py                            (改) ModelBackend、ModelReply、ModelCallError
  service.py                          (改) Answered、AgentCallService；ModelFallbackService 加锁
  __init__.py                         (改) 说明写上调用 agent；导出新名字
integrations/standin/                 (新)
  __init__.py                         说明；导出 StandInBackend、StandInConfig、stand_in_models
  config.py                           StandInConfig、stand_in_models
  backend.py                          StandInBackend：PM 的派发和回应、Critic、子 agent、Reviewer
  conclusions.py                      PM 替身每种案件类型的结论、方案、论点和信号
```

- 跨模块传递的 Pydantic 模型：`contracts/agent_call.py` 的全部模型，以及它们里面的 `Snapshot`、`Task`、`Result`、`TaskRequest`、四种结论、`CriticReview`、`PlanResponse`、两种论点；`ModelRef`、`ModelUse`、`TokenUsage`
- 网关多依赖 `contracts.agent_call` 和 `contracts.snapshot`，不 import 别的模块
- `integrations/standin` 只依赖网关的包根（端口和模型配置）、contracts 和 shared 的 `TradingCalendar`
- 「案件工作流」（workflows）用 `AgentCallService` 调用 agent，用调度器和股票池的数据组出 `CaseContext`；它和替身互相不 import，由 bootstrap 装配

## Goals / Non-Goals

**Goals:**

- 「案件工作流」只用一个用例调用六个 agent：一次调用给出输出或全部不可用，子 agent 的任务直接给出 Result；重试、备用模型、熔断、记费用都不用它管
- M5 只写一个新的适配器实现同一个端口，网关、案件工作流和 contracts 都不用改
- 替身给出的结论按已有的规则能通过校验、能下单，不为替身放宽任何规则

**Non-Goals:**

- 不做模型调用的异步接口：DBOS 的 step 是同步函数，并行由调用方用多个 step 做
- 不给替身留"按配置换结论"的开关：每种案件类型一个固定的结论，要别的结论就等 M5 的模型
- 不做替身和真实模型混用的配置：M5 一次换掉

按「编程风格」省略的情况：

- 开仓方案和升级时快照里没有案件股票的行情：报错（spec 写了），案件按系统故障结束
- 复查类案件的上下文没给现在的论点：报错
- PM（回应）的对话最后一轮交回的不是质询：报错
- 寻股的快照没有粗筛结果：报错（快照保证寻股有）
- 研究要求的子 agent 数配置为 3：替身只派两个，结论被退回，重交用完按结论无效结束
- 复查日的间隔配置在 3 到 20 以外、催化剂的间隔超过 60：论点的日期不通过，按结论无效结束
- 复查日的间隔大于复查日默认间隔、持仓又亏损：持仓复查的续期不通过
- 生成快照以后过了收盘才校验：今天往后错一个交易日，默认的间隔仍在范围内
- 股价很高，1 股就超出风控的上限：风控调整或拒绝
- 同一个任务同时运行两次：两次都调用模型，Result 先写入的为准
- 端口抛出 `ModelCallError` 以外的异常：原样抛出，不算尝试失败，不记费用
- `run_task` 收到别的案件的快照：不检查

## Decisions

### D1 端口是一次尝试，网关驱动整次调用

```python
class ModelCallError(RuntimeError):
    """一次尝试没有拿到输出：调用错误，和厂商给出的这次的用量（拿不到时为 None）。"""

    def __init__(self, error: CallError, usage: TokenUsage | None = None) -> None:
        super().__init__(error.value)
        self.error = error
        self.usage = usage


@dataclass(frozen=True, slots=True)
class ModelReply[T]:
    """一次拿到输出的尝试：输出和这次的用量。"""

    output: T
    usage: TokenUsage


class ModelBackend(Protocol):
    """调用模型：每个方法向 model 发一次请求，就是 model-fallback spec 的一次尝试。

    超过 timeout 仍没有输出、或者没有拿到输出时抛 ModelCallError，不重试、不换模型；重试、切换
    备用模型和记费用由 AgentCallService 做。PM、Critic、Reviewer 的输出是按 contracts 校验过的
    模型；子 agent 的输出是原样的文本，由网关按 task-dispatch spec 校验（D4）。
    """

    def pm(self, model: ModelRef, timeout: timedelta, request: PmRequest) -> ModelReply[PmReply]:
        """PM 的研究：派发任务或提交结论。"""
        ...

    def pm_respond(
        self, model: ModelRef, timeout: timedelta, request: PmRequest
    ) -> ModelReply[PlanResponse]:
        """PM 回应对话最后一轮交回的质询。"""
        ...

    def critic(
        self, model: ModelRef, timeout: timedelta, request: CriticRequest
    ) -> ModelReply[CriticReply]:
        """Critic：补充研究或审核。"""
        ...

    def sub_agent(
        self, model: ModelRef, timeout: timedelta, request: SubAgentRequest
    ) -> ModelReply[str]:
        """Task 的目标子 agent：信号或无信号的 JSON 文本。"""
        ...

    def reviewer(
        self, model: ModelRef, timeout: timedelta, request: ReviewerRequest
    ) -> ModelReply[RetrospectiveReport]:
        """Reviewer 的复盘报告。"""
        ...
```

- 端口放网关：Proposal 的「调度器与运行模块」把"调用模型（主模型不可用时切换备用模型）、记下每次模型调用的费用、校验输出格式"都写成 Agent 网关的职责；调度器和案件工作流不调用模型
- 一次尝试这一层：M3 的 `ModelFallbackService` 已经按"调用方逐次尝试、报告成败"写好，熔断、起点、超时都在网关；端口只做一次请求，适配器（替身、M5 的 PydanticAI）不重复这些规则，也不会各写一套
- 拿不到输出用异常：和别的端口一样（`QuoteUnavailableError`、`BrokerUnavailableError`），适配器把厂商的异常归成 `CallError` 的一种；厂商给了用量的失败带上用量，call-budget spec 要记
- 每个 agent 一个方法，不用"一个方法加 agent 参数"：输入输出的类型各不相同，按方法分开 mypy 能查；三个子 agent 输入输出相同，合成一个方法，目标看 `Task.target`
- 考虑过：端口是"一次完整的调用"，适配器自己重试和换模型。备用模型的规则就要在每个适配器里各写一遍，或者适配器反过来调用网关，成环
- 考虑过：端口放 `modules/gateway` 以外（比如单独一个 agents 模块）。Proposal 没有这样的模块，调用模型和记费用本来就在网关

### D2 输入和输出的格式

```python
class CaseContext(BaseModel):
    """案件上下文：快照以外 PM 和 Critic 要看的、由调用方给出的内容，见 agent-calls spec。

    案件 ID、案件类型和股票在快照里。不适用的项为 None。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1)  # 案件要回答的问题；跟进是申请时写下的问题
    event: EventCategory | None  # 事件案件的事件类别
    required_agent: SubAgent | None  # 事件案件至少要派给的子 agent
    watch_thesis: WatchThesis | None  # 关注股现在的关注论点
    investment_thesis: InvestmentThesis | None  # 持仓现在的投资论点


class TaskOutcome(BaseModel):
    """一个任务请求的处理结果：派发、被拒绝或复用的 Task，和它的 Result。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Task
    result: Result


class DispatchTasks(BaseModel):
    """dispatch_tasks：PM 或 Critic 派发一个或多个任务。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requests: tuple[TaskRequest, ...] = Field(min_length=1)


class SubmitConclusion(BaseModel):
    """submit_conclusion：PM 提交结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conclusion: Conclusion


class SubmitReview(BaseModel):
    """Critic 的一次审核。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review: CriticReview


type PmReply = DispatchTasks | SubmitConclusion  # PM 研究时的回复
type CriticReply = DispatchTasks | SubmitReview  # Critic 的回复


class SubAgentRequest(BaseModel):
    """调用子 agent：它只看到 Task 和快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Task
    snapshot: Snapshot


class ReviewerRequest(BaseModel):
    """调用 Reviewer。复盘要读的交易记录和案件记录由 M6 加。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: Snapshot


class RetrospectiveReport(BaseModel):
    """复盘报告。内容由 M6 定，现在只有一段总结。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
```

- `CaseContext` 只放替身和 Proposal 写明 PM 要看的、现在已经有来源的几项；近期结论、待执行的跟进、经验和历史案件的索引由建出来源的变更（「案件工作流」、M5）加字段
- 问题和事件要求的子 agent 由调用方从 `CaseDefinition` 抄过来，网关和替身不再查调度器的表：PM 要知道最低研究要求（Proposal「调度器」：类型固定 PM 要回答的问题、允许的结论、最低研究要求）
- 回复包一层（`DispatchTasks`、`SubmitConclusion`、`SubmitReview`）：对应 Proposal 的两个工具，M5 的适配器一个工具一个模型；两种回复的必填字段不同，加上 extra="forbid"，联合类型分得清
- PM 回应用已有的 `PlanResponse`，不再包一层：回应时只有这一种回复（D3）
- 快照整份放进请求：子 agent 只看到 Task 和快照（Proposal「Task / Result 通信」），怎样放进 prompt 由 M5 定；调用方按版本从状态库读出来再交进来，DBOS 的 step 不用存它

### D3 对话：每一轮是回复和交回的结果

```python
class TaskResults(BaseModel):
    """派发任务交回的结果：每个请求一个，按请求的顺序。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: tuple[TaskOutcome, ...] = Field(min_length=1)


class Returned(BaseModel):
    """结论校验不通过，退回 PM 重交的原因。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1)


class Challenge(BaseModel):
    """Critic 的一次质询，和这次审核里补充研究的 Task 和 Result。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review: CriticReview  # 结论是质询
    research: tuple[TaskOutcome, ...] = ()


class PmTurn(BaseModel):
    """PM 对话的一轮。回复和交回的结果怎样对应见 agent-calls spec「对话的每一轮」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: DispatchTasks | SubmitConclusion | PlanResponse
    feedback: TaskResults | Returned | Challenge


class CriticTurn(BaseModel):
    """Critic 对话的一轮：补充研究交回 Task 和 Result，质询交回 PM 的回应。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: DispatchTasks | SubmitReview
    feedback: TaskResults | PlanResponse


class PmRequest(BaseModel):
    """调用 PM：研究和回应都用它。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: CaseContext
    snapshot: Snapshot  # 本案件现在的快照
    turns: tuple[PmTurn, ...] = ()  # PM 在本案件的对话，按先后


class CriticRequest(BaseModel):
    """调用 Critic。要审核的方案是 conclusion 的方案；PM 修改过的条款在对话的回应里。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: CaseContext
    snapshot: Snapshot
    conclusion: HoldingConclusion | WatchConclusion  # 带交易方案的结论
    research: tuple[TaskOutcome, ...]  # PM 在本案件派过的任务和 Result，按派发的先后
    turns: tuple[CriticTurn, ...] = ()  # Critic 在本案件的对话，按先后
```

- `PmTurn`、`CriticTurn` 各有一个 validator，按 spec 的表检查回复和结果对应、派发任务交回的结果和请求一样多、Critic 的通过和否决不进对话、`Challenge` 的审核是质询；不符合时抛 ValueError。对话是 M5 换成工具调用和工具结果的依据，配错了模型会看到错位的对话，构造时就挡住
- 一轮就是一次工具调用和它的工具结果：PM 的回复都是工具调用，退回的原因、Critic 的质询就是 `submit_conclusion` 和回应的工具结果；Proposal「Task / Result 通信」写的"Critic 要求的补充研究，结果同样作为工具结果交给 PM"就是 `Challenge.research`
- 只放交回了结果的轮次：最后一次回复由调用方处理，不进下一次请求的对话
- 回应时不给派任务的工具：Proposal 写明"PM 在提交结论以前派任务，回应质询时需要的新信息由 Critic 的补充研究提供"；task-dispatch spec「PM 的轮次」里"不按阶段区分"说的是网关不看阶段，调用方不给这个工具就不会发生
- 对话用自己的格式，不用 PydanticAI 的消息：网关、案件工作流和 DBOS 的 step 都不依赖 PydanticAI，M5 的适配器负责转换；"PM 的整段对话保存、崩溃后可恢复"由「案件工作流」用 DBOS 的 step 输出做

### D4 输出的格式在哪里校验

- 子 agent 交回原样文本：task-dispatch spec 已经规定格式不合法记失败、不重新调用、不切换模型，`TaskDispatchService.receive` 收文本、自己解析；适配器不校验，模型给什么就交什么
- PM、Critic、Reviewer 交回已经按 contracts 构造好的模型：M5 用 PydanticAI 时，这些工具的参数按同样的模型校验，参数不对就在同一次调用里让模型改（PydanticAI 的输出重试），交回来的一定合法；仍然改不对时怎样处理是 M5 的事（signal-format spec「结论里的信号」已经留给 M5）。替身直接构造模型，不会不合法
- 格式由 contracts 的模型定，校验发生在网关端口的实现里，Proposal 的"Agent 网关校验输出格式"不变
- 考虑过：五个方法都交回文本，网关统一解析。PM 的结论是四种结论的联合类型，候选的移出和关注的移出字段相同，按文本读回要靠联合类型的顺序（「案件记录」design D7 的问题），网关又不知道案件类型对应哪种结论（表在调度器）；M5 也就不能用 PydanticAI 的工具校验，还要另做一套退回

### D5 AgentCallService

```python
@dataclass(frozen=True, slots=True)
class Answered[T]:
    """一次成功的调用：输出、实际使用的模型，以及这次调用各次尝试记下的费用之和。"""

    output: T
    model_use: ModelUse
    cost: Decimal


class AgentCallService:
    """调用 agent：按备用模型的步骤向模型尝试，每次拿到用量就记一条费用，见 agent-calls spec。

    不检查结论：结论的校验、备用模型的限制和结论里的信号由调用方做。可以同时调用：备用模型的
    状态由 ModelFallbackService 的锁保护（D6），费用和 Result 本来就可以同时写。
    """

    def __init__(
        self,
        backend: ModelBackend,
        fallback: ModelFallbackService,
        budget: CallBudgetService,
        tasks: TaskDispatchService,
    ) -> None:
        self._backend = backend
        self._fallback = fallback
        self._budget = budget
        self._tasks = tasks

    def pm(self, request: PmRequest) -> Answered[PmReply] | AllUnavailable:
        """调用 PM 做研究：派发任务或提交结论。"""

    def pm_respond(self, request: PmRequest) -> Answered[PlanResponse] | AllUnavailable:
        """调用 PM 回应对话最后一轮交回的质询。"""

    def critic(self, request: CriticRequest) -> Answered[CriticReply] | AllUnavailable:
        """调用 Critic：补充研究或审核。"""

    def reviewer(self, request: ReviewerRequest) -> Answered[RetrospectiveReport] | AllUnavailable:
        """调用 Reviewer。"""

    def run_task(self, task_id: UUID, snapshot: Snapshot) -> Result:
        """运行一个派发出去的任务，给出它的 Result；已经有 Result 时直接给出，不调用模型。"""
        record = self._tasks.record(task_id)
        if record.result is not None:
            return record.result
        task = record.task
        request = SubAgentRequest(task=task, snapshot=snapshot)
        outcome, cost = self._call(
            snapshot,
            Agent(task.target.value),
            task.deadline,
            lambda model, timeout: self._backend.sub_agent(model, timeout, request),
        )
        match outcome:
            case Answered():
                return self._tasks.receive(task_id, outcome.output, cost, outcome.model_use)
            case AllUnavailable():
                return self._tasks.fail(task_id, outcome.detail, cost)
            case DeadlineReached():
                return self._tasks.expire(task_id, cost)

    def _call[T](
        self,
        snapshot: Snapshot,
        agent: Agent,
        deadline: datetime | None,
        attempt: Callable[[ModelRef, timedelta], ModelReply[T]],
    ) -> tuple[Answered[T] | AllUnavailable | DeadlineReached, Decimal]:
        """一次调用：按备用模型的步骤逐次尝试，给出结果和这次调用记下的费用之和。"""
        scope = CaseScope(snapshot.case_id, snapshot.case_type, snapshot.symbol)
        step = self._fallback.begin(scope.case_id, agent, deadline)
        cost = Decimal(0)
        while isinstance(step, Attempt):
            model = step.call.model
            try:
                reply = attempt(model, step.timeout)
            except ModelCallError as failure:
                cost += self._charge(scope, agent, model, failure.usage)
                step = self._fallback.fail(step.call, failure.error)
                continue
            cost += self._charge(scope, agent, model, reply.usage)
            return Answered(reply.output, self._fallback.succeed(step.call), cost), cost
        return step, cost
```

- `_charge` 有用量时调用 `CallBudgetService.record` 并给出这一条的费用，没有用量时是 0
- 案件 ID、案件类型取自请求里的快照：费用记录要开始时确定的案件类型（call-budget spec），快照里就是它，不用另外传 `CaseScope`
- PM、Critic、Reviewer 的调用没有截止时间，不会得到 `DeadlineReached`（`start`、`after_failure` 只在有截止时间时给出）；四个方法用一个小函数把类型收窄，遇到时抛 AssertionError
- `run_task` 把子 agent 的整个任务做完，调用方不用碰 `receive`、`fail`、`expire`；先查 Result：DBOS 重做一个已经写了 Result 的 step 时不再调用模型、不多记费用
- 全部不可用不告警：model-fallback spec 写的是"调用方告警和写日志"，告警要通知模块，网关不依赖它；「案件工作流」拿到 `AllUnavailable` 时告警，子 agent 的失败 Result 带说明
- 程序日志不写模型调用和切换：Proposal「程序日志」要记每次模型调用的完整输入（含上下文版本）和输出、重试、切换备用模型及原因。完整输入就是 M5 写的 prompt，替身也不会失败和切换；M5 在这个用例里加"调用模型""切换模型"两个动作，program-log spec 跟着改。现在每次调用的模型和费用在费用记录里，子 agent 的在 Result 的条目里
- 考虑过：调用循环放「案件工作流」。备用模型和费用是网关的规则，放进 workflow 以后每个调用 agent 的流程都要写一遍

### D6 同时进行的调用

- 几个案件同时运行（并发上限默认不止一个），每个都在 DBOS 的 step 里调用 agent，同一个 `ModelFallbackService` 的熔断状态和起点会被几个线程同时读写；健康监控也要读 `cooling()`、`has_available_model()`
- `ModelFallbackService` 加一把 `threading.Lock`，每个公开方法整段拿着它；锁只护内存里的状态，不包住模型调用（模型调用在 `AgentCallService` 里，锁外）。类注释的"由调用方串行调用"改为"可以同时调用"
- 锁放在 `ModelFallbackService` 而不是 `AgentCallService`：状态是它的，健康监控和 `end_case` 也直接调用它
- 并行的两次调用让起点只往后移（model-fallback spec「并行的调用不让起点往回移」）已经由 `succeed` 的 `max` 保证，加锁以后读改写不会交错

### D7 替身的模型

```python
_VENDOR = "standin"
_CRITIC_VENDOR = "standin-critic"  # 和 PM 不同厂商，满足厂商校验


def stand_in_models() -> ModelAssignments:
    """替身的模型配置：每个 agent 一个模型，没有备用模型，单价 0。"""
    models = {agent: ModelRef(vendor=_VENDOR, name=agent.value) for agent in Agent}
    models[Agent.CRITIC] = ModelRef(vendor=_CRITIC_VENDOR, name=Agent.CRITIC.value)
    return ModelAssignments(
        agents={agent: AgentModels(primary=model) for agent, model in models.items()},
        prices=tuple(
            ModelPrice(model=model, input_price=Decimal(0), output_price=Decimal(0))
            for model in models.values()
        ),
    )
```

- 替身照常走网关的备用模型和费用：起点、`ModelUse`、费用记录都和真实模型一样产生，「案件工作流」和案件记录不用为替身写分支
- 厂商名一眼能认出是替身，M6 的评估按厂商去掉；Critic 用另一个厂商名过厂商校验，不为替身放宽
- 用量记 0、照常记一条：案件记录的调用次数（「案件记录」design D9：费用记录的条数）能数出来，预算一直不会用完
- 替身不看 `model` 和 `timeout`：一个模型、不会超时

### D8 PM 替身派发任务

```python
# stand-ins spec「PM 替身派发任务」：每个子 agent 一个任务，按这个顺序
_TASKS: Final = {
    SubAgent.NEWS: TaskType.EVENT_IMPACT,
    SubAgent.QUANT: TaskType.PRICE_REACTION,
    SubAgent.FUNDAMENTALS: TaskType.EARNINGS_VS_EXPECTATIONS,
}
_RESEARCH: Final = frozenset({SubAgent.QUANT, SubAgent.FUNDAMENTALS})
```

- 要派的子 agent：结论是开仓方案或升级时是 `_RESEARCH`，再并上 `context.required_agent`；派不派、派给谁只由案件类型和事件类别决定，和结论用同一个判断（`conclusions.py` 的 `researched(snapshot, context)`）
- 对话里已经有 `DispatchTasks` 就不再派：替身只派一轮，用不完的轮次和任务数不影响
- 输入写案件股票的行情条目 ID：子 agent 替身引用它，信号的"至少引用一条"就满足；同一个请求在同一案件里不会重复，不涉及复用
- 研究要求默认是 2 个不同子 agent，Quant 和 Fundamentals 正好；入场条件满足要求的 Quant 已经在里面
- 引用的 Result：对话里全部状态是完成的 Result 的 task_id，替身的子 agent 都完成，超时或失败的不引用

### D9 PM 替身的结论

`conclusions.py` 是纯函数：`conclusion(context, snapshot, results, calendar, config) -> Conclusion`，按 `snapshot.case_type`（关注股事件再看 `context.event`）选 stand-ins spec「PM 替身的结论」表里的一行。

```python
def to_tick(price: Decimal) -> Decimal:
    """按最小变动单位四舍五入：1 美元及以上到 0.01，低于 1 美元到 0.0001。"""
    tick = Decimal("0.01") if price >= 1 else Decimal("0.0001")
    return price.quantize(tick, rounding=ROUND_HALF_UP)


def buy_plan(snapshot: Snapshot, price: Decimal, config: StandInConfig) -> PlanDraft:
    """开仓方案的条款，price 是参考价。"""
    limit = to_tick(price * (1 + config.limit_markup))
    quantity = max(1, int(snapshot.account.equity * config.position_ratio / limit))
    return PlanDraft(
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        stop_price=to_tick(limit * (1 - config.stop_ratio)),
        emergency_threshold=config.emergency_threshold,
    )


def new_catalyst(today: date, calendar: TradingCalendar, config: StandInConfig) -> Catalyst:
    """新的催化剂：描述带上截止日期，和原来的描述不同，续期时算新的催化剂。"""
    deadline = calendar.add_sessions(today, config.catalyst_sessions).day
    return Catalyst(description=f"替身催化剂，截止 {deadline.isoformat()}", deadline=deadline)
```

- 结论只按案件类型选，不看对话里 Result 的内容：替身不做判断，被退回时交回同样的结论（spec），重交用完按结论无效结束，暴露的是规则和替身的不一致
- 今天是 `snapshot.market.trading_day`（生成时刻归属的交易日）：和 review-dates spec 的"今天"是同一个定义，不读时钟，同样的快照算出同样的日期
- 参考价：最新价，没有成交时用前一交易日收盘价；只有开仓方案和升级用价格
- 股数按权益的比例：默认 10% 的权益，止损 5%，计划风险约 0.5% 的权益，远低于风控的单笔风险上限 5%；单只市值 10% 也低于 70%。不足 1 股时买 1 股，交给风控判断
- 清仓用市价单、不写股数，去向移出股票池：不用另写关注论点；股票再被粗筛出来时从候选重新走一遍（冷却期以后）
- 默认的间隔：复查日 5 个交易日（等于 review-dates 的复查日默认间隔，亏损的持仓续期也通过），催化剂 8 个交易日（不是 5 的倍数）：买入以后第 5 个交易日持仓复查、续期到第 10 个，第 8 个交易日催化剂到期，开持仓催化剂复查清仓。两种复查不会落在同一天，持仓走完买入 → 持有 → 清仓
- 入场条件是"价格不高于参考价的 102%"：升级以后马上满足，事件检测一开出入场条件满足的事件就开仓，模拟盘里能看到下单
- 信号的数据截至时刻是快照的生成时刻：不晚于收到结论的时刻，不读时钟

### D10 回应、Critic、子 agent 和 Reviewer 的替身

- PM 回应：维持，回应的条数等于对话最后一轮 `Challenge.review.challenges` 的条数
- Critic：第一次就交回通过的 `SubmitReview`，不派补充研究；开仓和退出方案都一样，Critic 不可用、有分歧的路径在模拟盘里走不到，由 case-stages 的测试覆盖
- 子 agent：`Signal` 的 `model_dump_json()`，引用 `task.inputs`，为空时引用 `snapshot.market.entry_id`；网关照常解析和校验，替身走的是和真实模型相同的路径
- Reviewer：固定的总结

### D11 替身的配置

```python
class StandInConfig(BaseModel):
    """替身的配置和默认值，含义见 stand-ins spec「替身的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_horizon: PositiveInt = 5
    signal_score: int = Field(default=1, ge=-2, le=2)
    signal_confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    position_ratio: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    limit_markup: Decimal = Field(default=Decimal("0.005"), ge=0, lt=1)
    stop_ratio: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    emergency_threshold: Decimal = Field(default=Decimal("0.08"), gt=0, lt=1)
    target_ratio: Decimal = Field(default=Decimal("0.10"), gt=0)
    entry_markup: Decimal = Field(default=Decimal("0.02"), ge=0)
    review_sessions: PositiveInt = 5
    catalyst_sessions: PositiveInt = 8
    exploration_additions: PositiveInt = 2
```

- 配置写在适配器里，和 `FinnhubConfig` 一样；替身不读环境变量，「案件工作流」装配时用默认值
- 不和网关、调度器、心跳、风控的配置互相检查（spec）：替身是测试工具，配错了由已有的校验挡住，在案件记录和程序日志里看得到

### D12 不装配

- bootstrap 不建 `AgentCallService` 和替身：没有调用方，建出来也没人用；`stand_in_models()` 和 `StandInConfig()` 已经给好，「案件工作流」装配时是三行
- 用替身还是真实模型的开关也不加：M5 接入模型时一起定（Proposal 没有提到同时保留两套）

### D13 测试

- `tests/contracts/test_agent_call.py`：agent-calls spec「对话的每一轮」的对应和报错、复盘报告和问题为空
- `tests/fakes/model_backend.py`：`ScriptedBackend`，按调用的先后给出每次尝试的结果（`ModelReply` 或 `ModelCallError`），可以在尝试时推进 `FakeClock`；记下每次收到的模型、超时和请求
- `tests/modules/gateway/test_agent_calls.py`：agent-calls spec「一次调用」「子 agent 的任务」的场景，用真的 `ModelFallbackService`、`CallBudgetService`、`TaskDispatchService` 加 `InMemoryCostStore`、`InMemoryTaskStore`、`FakeClock`；"几个案件同时调用"用两个线程和一个屏障，让两次调用的尝试交错
- `tests/integrations/standin/`：
  - `snapshots.py`：stand-ins spec 的基准快照和案件上下文，按条目直接构造 `Snapshot`
  - `test_config.py`：替身的模型配置通过 `ModelAssignments` 的校验；配置不合法；改配置改变输出
  - `test_backend.py`：PM 替身的派发、每种案件类型的结论、参考价、开仓方案、升级、续期、寻股、信号、回应，Critic、子 agent 和 Reviewer 替身；同样的请求两次输出相同
  - `test_flow.py`：「替身的结论通过已有的校验」：每种案件类型经 `AgentCallService` 跑 PM 替身（派发经 `TaskDispatchService`，任务经 `run_task` 交给子 agent 替身），再用网关的 `conclusion_signal_rejection`、`blocked_decision`，调度器的 `conclusion_rejection`，心跳的 `thesis_dates_rejection` 和结论的 `trade_plan` 检查
- 阈值从配置读取，不在测试里硬编码

## Risks / Trade-offs

- [M5 的 PydanticAI 适配器不好把输出重试关掉、给出子 agent 的原样文本] → 子 agent 的工具可以声明成接收任意 JSON 的工具，把参数原样交回；真做不到时 M5 改端口，网关的 `receive` 不变
- [M5 发现 PM 要别的输入（近期结论、经验、查询工具的结果）] → `CaseContext` 和请求加字段，端口的方法不变；查询工具是同一次调用里的函数工具，不进对话
- [替身的结论太单一，覆盖不到 Critic 质询、PM 修改、Critic 不可用这些路径] → 这些路径由 case-stages、agent-calls 的测试用 `ScriptedBackend` 覆盖；模拟盘只验证正常路径能下单
- [替身升级以后马上开仓，模拟盘里开仓方案多] → 每个都要人工确认；寻股一次只加 2 只，候选评估一天几个，确认消息不会太多；要少一些可以调大入场条件以外的间隔或改寻股的只数
- [锁让同一个进程里的调用排队拿状态] → 只护内存里的几次读写，模型调用不在锁里

## Migration Plan

- 没有表和迁移；装配由「案件工作流」做，部署主机升级以后行为不变
- 前提：「状态库快照」已经 apply（`contracts/snapshot.py` 存在）
