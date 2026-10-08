# Design

## Context

现状（M2 进行中，M3 完成了股票池、案件类型与结论）：

- 调度器模块只有纯逻辑：触发（`EventTrigger`、`HeartbeatTrigger`、`FollowUpTrigger`、`SellTrigger`，都有 `symbol` 属性，寻股和每周复盘的心跳触发为 `None`）、`CaseDefinition`、`define_case(trigger, status)`（返回 None 表示不运行）、结论的校验和 `SchedulerConfig`（只有研究要求的子 agent 数）。没有 service、ports 和存储
- contracts 有 `CaseType`（14 种）、`StockStatus`、`TradeAction`（`is_opening` 区分开仓类和减仓类）
- 风控的 `release(plan_id)` 释放方案剩余的预留，没有预留时什么都不做（risk-reservations spec「方案或订单结束时释放预留」）
- 健康监控给出是否处于降级模式（降级和等待对账都算），对账完成后回到正常；紧急退出模块给出要清仓的股票，M2 的紧急退出工作流按股票串行撤单、下清仓单（emergency-exit 的 design D6）
- M3 只写纯逻辑和测试：不接外部系统，不建表，不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/case-queue/spec.md。

### 模块边界

```
modules/scheduler
  domain/queue.py  PlanStage、QueuedCase、RunningCase、PendingPlan、QueueSnapshot、
                   CaseStart、QueueDecision、EmergencyDecision、
                   queue_decision、emergency_decision
  config.py        SchedulerConfig 增加 max_concurrent_cases
  __init__.py      增加导出
```

本次只改调度器一个模块，contracts 不变。跨模块传递的 Pydantic 模型和枚举：

- 传入：`StockStatus`（股票池给出，工作流放进快照）、`TradeAction`（方案的动作，来自 `TradePlan`）、`CaseType`（运行中的案件开始时确定的类型）
- 传出：没有新的。启动的案件带本模块的 `CaseDefinition`
- 快照、决定和方案的阶段是本模块的 frozen dataclass 和枚举，由包根导出：只有 M4 的工作流组快照、执行决定，工作流 import 调度器的包根即可，和 `CaseDefinition` 一样，以后要交给别的模块时再决定是否放进 contracts

## Goals / Non-Goals

**Goals:**

- 两个纯函数，输入一份快照，输出决定；spec 每张表的每一行都有测试
- 作废条件随时成立：每次决定都从快照重新算，不靠调用方记得在哪几个时机检查
- 性质测试随机交错各种变化，检查一只股票最多一个复盘以外的案件在跑、并发不超过上限、启动顺序符合优先级、作废条件随时成立

**Non-Goals:**

按「编程风格」省略的情况：

- 快照本身不一致（同一只股票有两个复盘以外的案件在运行、方案的股票没有对应的案件、触发时刻不带时区）：不检查，调用方保证
- 状态变走又变回来（候选升级为关注又降回候选）：第一次确定不了类型时就不再运行，不保留
- 同一个触发重复排队：不去重，M4 入队时按触发去重
- 调低并发上限以后重启，调用模型的案件超过上限：不中止，只是暂时不启动新的
- 紧急退出时这只股票的方案正在风控检查的中途：调用方串行处理，做法 M4 定
- 两个每周复盘同时排队：不限制，每周只开一次
- 快照里同一只股票的状态在一次决定里变化：快照是一个时刻的，按快照算

## Decisions

### D1 两个纯函数，任何输入变化以后调用一次

```python
def queue_decision(snapshot: QueueSnapshot, config: SchedulerConfig) -> QueueDecision:
    """启动哪些案件、哪些排队的案件不再运行、作废哪些方案，规则见 case-queue spec。"""


def emergency_decision(snapshot: QueueSnapshot, symbol: str) -> EmergencyDecision:
    """symbol 触发紧急风险退出时，中止哪个案件、作废哪些方案。"""
```

- 队列存在哪里、什么时候调用都由 M4 定；这里每次都从快照把整个决定重新算一遍，所以作废、不再运行和启动不依赖调用的时机：新案件排队、方案进入人工确认、案件结束、降级恢复都是"输入变了，再算一次"
- 紧急退出单独一个函数：它由紧急退出工作流在撤单之前调用，要做的事（中止、作废全部方案）和队列的决定不同，也不看降级、预算和并发
- 考虑过：一个有状态的队列对象，带入队、出队、案件结束、方案进入人工确认等方法。没有采用：要在内存里另存一份队列，和 M4 的存储重复，重启后还要恢复；每个方法都要各自判断作废和启动，漏掉一处就违反随时成立的条件
- 考虑过：只返回下一个要启动的案件。没有采用：降级恢复或预算恢复以后一次能启动好几个，调用方要循环调用、每次更新快照；一次给出全部更简单

### D2 快照和决定

```python
class PlanStage(StrEnum):
    """案件给出的、还没提交订单的方案所处的阶段。"""

    REVIEW = "review"  # 审核：Critic 审核、风控检查，还没交给人工确认
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 等人工确认
    AWAITING_ORDER = "awaiting_order"  # 已经确认，等开盘或下单前再校验


@dataclass(frozen=True, slots=True)
class QueuedCase:
    """排队的案件。"""

    case_id: UUID
    trigger: Trigger
    triggered_at: datetime  # 触发时刻，带时区


@dataclass(frozen=True, slots=True)
class RunningCase:
    """运行中的案件。"""

    case_id: UUID
    case_type: CaseType  # 开始时确定的类型
    symbol: str | None  # 寻股和每周复盘没有
    # 还在调用模型的阶段：唤醒 PM 到 Critic 审核结束，复盘案件一直是；由「案件阶段」给出
    calling_models: bool


@dataclass(frozen=True, slots=True)
class PendingPlan:
    """案件给出的、还没提交订单的方案；手工录入的方案不放进来。"""

    plan_id: UUID
    symbol: str
    action: TradeAction
    stage: PlanStage


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """取得决定时的一份快照，由调用方从各模块组出。"""

    queued: tuple[QueuedCase, ...]  # 按入队的先后
    running: tuple[RunningCase, ...]
    plans: tuple[PendingPlan, ...]
    # 股票池里的股票和持仓的状态；不在里面的股票既不在股票池也不是持仓
    statuses: Mapping[str, StockStatus]
    exiting: frozenset[str]  # 正在紧急退出的股票：清仓卖单还没结束
    degraded: bool  # 处于降级模式：降级或等待对账
    general_budget_exhausted: bool  # 普通预算用完
    exploration_budget_exhausted: bool  # 寻股预算用完


@dataclass(frozen=True, slots=True)
class CaseStart:
    """启动一个排队的案件，用这次确定的类型。"""

    case_id: UUID
    definition: CaseDefinition


@dataclass(frozen=True, slots=True)
class QueueDecision:
    """队列的决定；调用方按作废、不再运行、启动的顺序执行。"""

    start: tuple[CaseStart, ...]  # 按启动的先后
    drop: tuple[UUID, ...]  # 不再运行的排队案件
    void: tuple[UUID, ...]  # 作废的方案


@dataclass(frozen=True, slots=True)
class EmergencyDecision:
    """一只股票紧急退出时的决定。"""

    abort: UUID | None  # 中止的案件；没有复盘以外的运行中案件时为 None
    void: tuple[UUID, ...]  # 作废的方案
```

- 排队的案件只带触发和触发时刻，不带案件类型：类型要按每次决定时的状态现算（D3）
- 运行中的案件只带类型、股票和是否在调用模型：决定只用到这几项。是否在调用模型由「案件阶段」按它的状态给出，本变更不定义案件的阶段
- 方案的动作直接用 contracts 的 `TradeAction`，开仓类就是已有的 `is_opening`；阶段只分三种，等人工确认和等下单在本变更里处理相同，分开是为了让调用方照实填写，测试也能覆盖 spec 表里的每一行
- 案件 ID 和方案 ID 都用 `UUID`：方案 ID 已经是 `UUID`（risk-reservations）；案件 ID 由 M4 生成，同样从 ID 生成端口取或用 `uuid5` 推出
- 正在紧急退出的股票是一个集合：紧急退出工作流在作废和中止以后加入，清仓卖单结束（成交、被拒或撤销）以后去掉（proposal「需要人决定」第 7 条）
- 两个预算标志由「调用预算」给出；持仓保留预算用完会让健康监控进入降级模式，所以不需要第三个标志
- 考虑过：快照和决定放进 contracts。没有采用：没有别的模块传递它们，M4 的工作流 import 调度器的包根就够

### D3 每次决定都按现在的状态确定排队案件的类型

代码见 D6 的 `queue_decision` 开头：四种触发都有 `symbol`（寻股和每周复盘为 `None`），按它从快照取状态，调用 `define_case`。

- `define_case` 返回 None 的案件放进决定的 `drop`，马上不再运行（proposal「需要人决定」第 9 条）；其余按这次确定的 `CaseDefinition` 排序，启动时原样交给调用方，调用方不再调用 `define_case`
- 优先级用这次确定的类型：排队期间股票状态变了，优先级跟着变（比如关注股买入以后，排队的事件从关注股事件变成持仓事件）
- 考虑过：只在轮到它的时候才确定类型。没有采用：确定不了类型的案件没有优先级，排不进顺序；两种做法只在状态变走又变回来时结果不同

### D4 启动

```python
_RETROSPECTIVES: Final = frozenset({CaseType.WEEKLY_RETROSPECTIVE, CaseType.SELL_RETROSPECTIVE})
# 针对持仓状态的案件类型：普通预算用完后改用持仓保留预算
_HOLDING_CASES: Final = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)


def _budget_exhausted(case_type: CaseType, snapshot: QueueSnapshot) -> bool:
    """这种案件用的预算是否用完；持仓案件用完普通预算后改用持仓保留预算。"""
    if case_type is CaseType.EXPLORATION:
        return snapshot.exploration_budget_exhausted
    return snapshot.general_budget_exhausted and case_type not in _HOLDING_CASES


def _starts(
    snapshot: QueueSnapshot,
    waiting: list[tuple[QueuedCase, CaseDefinition]],
    config: SchedulerConfig,
) -> tuple[CaseStart, ...]:
    """按优先级、触发时刻、入队的先后依次检查，能启动的就启动，直到名额用完。"""
    # 复盘以外的案件各占一个位置：针对单只股票的占这只股票，寻股占 None，所以寻股一次一个
    busy = {r.symbol for r in snapshot.running if r.case_type not in _RETROSPECTIVES}
    busy |= snapshot.exiting
    slots = config.max_concurrent_cases - sum(r.calling_models for r in snapshot.running)
    starts: list[CaseStart] = []
    # sorted 是稳定排序：优先级和触发时刻都相同时保持入队的先后
    for queued, definition in sorted(waiting, key=lambda w: (w[1].priority, w[0].triggered_at)):
        if len(starts) >= slots:
            break
        if _budget_exhausted(definition.case_type, snapshot):
            continue
        if definition.case_type not in _RETROSPECTIVES:
            if definition.symbol in busy:
                continue
            busy.add(definition.symbol)
        starts.append(CaseStart(queued.case_id, definition))
    return tuple(starts)
```

- 处于降级模式时不调用 `_starts`，启动为空；不再运行和作废照常算（D6）
- 不能启动的 `continue`，看下一个：忙的股票、预算用完的案件不挡别的；名额用完才 `break`
- 寻股用 `None` 占位：寻股和每周复盘的股票都是 `None`，但每周复盘是复盘、不占位置，所以 `None` 只代表寻股。这样"同一时间一个寻股"和"一只股票一个案件"是同一条规则（proposal「需要人决定」第 6 条）
- 复盘不看也不占位置，但计入名额（D5）
- 持仓的五种案件用一个集合列出，和 case-types spec「案件类型」表里针对持仓的五行一致，由测试逐个检查
- 考虑过：按股票分组，每只股票一个等待队列，再在各组队首之间按优先级选。没有采用：全局排一次序、跳过忙的股票，结果相同，代码更短

### D5 并发上限只计调用模型的案件

`slots = 并发上限 - 调用模型的运行中案件数`，不大于 0 时一个也不启动（proposal「需要人决定」第 5 条）：

- 并发上限是为了不触发 API 频率限制，风控、等人工确认、等下单的案件不调用模型；等确认可能要几个小时，让它们占名额会挡住持仓风险事件
- 这些案件仍然占着这只股票（D4 的 `busy` 不看 `calling_models`）
- 只限制启动，从不中止；调低并发上限以后重启，调用模型的案件可能暂时超过上限

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 并发上限 | 3 | 收盘检查一次可能开出几个复查和候选评估，3 个并发让它们较快跑完，盘中来的持仓风险事件也不用等很久；每个案件最多并行派发 6 个任务（Proposal「Task / Result 通信」），3 个案件同时在途的模型请求最多二十个左右，离常见的频率限制有余量；账户小、预算有限，再高的并发只会更快用完预算 |

寻股同一时间最多一个，是规则不是阈值，不做成配置（D4）。

### D6 作废

```python
# 已经交给人工确认的阶段：开仓类方案在这些阶段会被新排队的案件作废
_CONFIRMING: Final = frozenset({PlanStage.AWAITING_CONFIRMATION, PlanStage.AWAITING_ORDER})


def queue_decision(snapshot: QueueSnapshot, config: SchedulerConfig) -> QueueDecision:
    waiting: list[tuple[QueuedCase, CaseDefinition]] = []
    drop: list[UUID] = []
    for queued in snapshot.queued:
        symbol = queued.trigger.symbol
        status = None if symbol is None else snapshot.statuses.get(symbol)
        definition = define_case(queued.trigger, status)
        if definition is None:
            drop.append(queued.case_id)
        else:
            waiting.append((queued, definition))
    # 有复盘以外的案件在排队的股票：暂时启动不了的、这次启动的都算
    queued_symbols = {
        d.symbol for _, d in waiting if d.symbol is not None and d.case_type not in _RETROSPECTIVES
    }
    void = tuple(
        p.plan_id
        for p in snapshot.plans
        if p.action.is_opening and p.stage in _CONFIRMING and p.symbol in queued_symbols
    )
    start = () if snapshot.degraded else _starts(snapshot, waiting, config)
    return QueueDecision(start=start, drop=tuple(drop), void=void)
```

- 作废在启动之前算，用的是这次确定得了类型的全部排队案件：这次启动的案件也让同一只股票已确认的开仓方案作废，新案件的 PM 想买就重新提（风控规则「需要人决定」第 5 条）
- 审核阶段的方案属于运行中的案件，不作废；退出类方案不作废，排队的案件因为这只股票忙自然要等运行中的案件结束（proposal「需要人决定」第 3、4 条）
- 只作废，不中止：方案所属的案件怎么结束由「案件阶段」处理
- 降级模式、预算、并发上限都不影响作废

### D7 紧急退出

```python
def emergency_decision(snapshot: QueueSnapshot, symbol: str) -> EmergencyDecision:
    abort = next(
        (
            r.case_id
            for r in snapshot.running
            if r.symbol == symbol and r.case_type not in _RETROSPECTIVES
        ),
        None,
    )
    void = tuple(p.plan_id for p in snapshot.plans if p.symbol == symbol)
    return EmergencyDecision(abort=abort, void=void)
```

- 一只股票最多一个复盘以外的案件在运行，所以最多中止一个；复盘案件不中止（proposal「需要人决定」第 8 条）
- 这只股票的方案不论动作和阶段全部作废：退出类方案的卖出预留会占住可卖股数，让紧急清仓被拒（额度预留「需要人决定」第 4 条）；开仓类方案在紧急清仓以后不该再买
- 排队的案件不动，决定里也没有它们；紧急退出期间它们因为这只股票在 `exiting` 里而不启动，结束后按新的状态确定类型（D3、D4）
- 紧急退出自己的清仓方案由紧急退出模块在这之后生成，不是案件给出的方案，不在快照里

### D8 配置

```python
class SchedulerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 开仓方案、加仓和升级引用的状态是完成的 Result，至少来自这么多个不同子 agent；
    # 上限是子 agent 的个数
    min_cited_agents: int = Field(default=2, ge=1, le=len(SubAgent))
    # 同一时间还在调用模型的案件最多这么多个
    max_concurrent_cases: int = Field(default=3, ge=1)
```

M4 由 bootstrap 从配置文件读取，测试里直接构造。

### D9 M4 的接法（给 M4 的对照）

本变更不调用其他模块，下表是 M4 工作流的接法：

| 什么时候 | 调用 | 执行 |
| --- | --- | --- |
| 新案件排队、案件结束、方案进入新的阶段、股票状态变化、紧急退出结束、降级模式或预算标志变化 | `queue_decision` | 1. 对 `void` 里的每个方案调用风控的 `release`，交给「案件阶段」结束它所属的案件；2. `drop` 里的案件写进案件记录、移出队列；3. 按 `start` 的顺序生成快照、启动案件工作流 |
| 紧急风险退出判定出一只股票 | `emergency_decision` | 中止 `abort` 的案件工作流，对 `void` 里的每个方案调用 `release`，把股票记为正在紧急退出；然后按 emergency-exit 的 design D6 撤单、下清仓单；卖单结束以后去掉标记，再取得一次队列的决定 |

- 同一时间只执行一个决定：两个决定基于同一份快照，会启动同一只股票的两个案件。串行的做法 M4 定，和风控的"一次只检查一个方案"一起选
- M4 用 DBOS 运行案件工作流时，并发和优先级已经由这里决定，不再交给 DBOS 队列的并发和优先级，避免两处规则不一致
- 组合熔断清仓全部持仓时，对每只持仓各调用一次 `emergency_decision`
- 手工录入的方案不放进快照，紧急退出时由 M2 的紧急退出流程自己作废

### D10 包根导出和纯区域

包根增加导出 `PlanStage`、`QueuedCase`、`RunningCase`、`PendingPlan`、`QueueSnapshot`、`CaseStart`、`QueueDecision`、`EmergencyDecision`、`queue_decision`、`emergency_decision`。仍然没有 service 和 ports：本变更只有纯函数，队列的存储在 M4。

`domain/queue.py` 只用 `collections.abc`、`dataclasses`、`datetime`、`enum`、`typing`、`uuid`，都在纯区域白名单里，不改 `tests/architecture/rules.py`。

### D11 测试

```
tests/modules/scheduler/builders.py                   + 队列的基准数据：五只股票的状态、各个触发、
                                                      P-1 到 P-3、运行中的案件、新建快照的辅助函数
tests/modules/scheduler/test_config.py                + spec「队列配置」的「默认配置」「配置不合法」
tests/modules/scheduler/domain/test_queue.py          spec 其余场景
tests/modules/scheduler/domain/test_queue_machine.py  性质测试
```

- 基准数据和 spec 一致：AAA、DDD 是候选，BBB 是关注，CCC、EEE 是持仓；触发时刻在 2026-10-14；案件 ID 用固定的 `UUID`，按名字取
- 性质测试（Hypothesis 的 `RuleBasedStateMachine`）：五只股票，并发上限取 1 到 3。随机交错这些变化：
  - 入队任意触发（四种触发、寻股、每周复盘，触发时刻取几个固定值，常常相同）
  - 运行中的案件结束；复盘以外的案件离开调用模型的阶段（只有这个方向：「退回 PM」就是案件结束，案件不会回到调用模型的阶段）
  - 换一个并发上限（调整配置以后重启），让调用模型的案件有时超过上限
  - 运行中的复盘以外的案件给出这只股票的方案（开仓类或退出类，在审核）；方案进入等人工确认、等下单，或者提交订单（离开快照）
  - 股票状态变成任意一种，或者不在股票池
  - 紧急退出一只股票（执行紧急退出的决定，记为正在紧急退出）；紧急退出结束
  - 切换降级模式、普通预算和寻股预算的标志

  每次变化以后取得队列的决定并执行（作废的方案移出快照，不再运行的移出队列，启动的变成调用模型的运行中案件），检查：
  - 每只股票最多一个复盘以外的案件在运行，最多一个寻股在运行；启动时这只股票不在紧急退出
  - 有启动时，启动以后调用模型的案件不超过并发上限
  - 处于降级模式时不启动；普通预算用完时不启动持仓和寻股以外的案件；寻股预算用完时不启动寻股
  - 启动的案件按（优先级、触发时刻、入队的先后）排列；留在队列里、本来能启动的案件（股票空闲、预算允许、不处于降级模式），一定是名额用完了，并且排在每个启动的案件之后
  - 不再运行的案件 `define_case` 为 None，留下的都不是
  - 作废随时成立：执行决定以后，没有一个处于等人工确认或等下单的开仓类方案，它的股票有复盘以外、确定得了类型的案件在排队；队列的决定只作废这种方案
  - 紧急退出的决定执行以后，这只股票没有复盘以外的运行中案件，也没有还没提交订单的方案；排队的案件和别的股票没有变化
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [事件频繁的股票，开仓方案可能一直走不到确认就被作废] → 每个新案件都用最新数据重新判断，PM 想买就再提；事件检测的阈值（M4）控制频率。这是"新案件排队就作废"规则的代价
- [普通预算用完时，排队的关注、候选案件要等到下一个交易日，这期间同一只股票已确认的开仓方案已经作废] → 交易日在收盘时切换，收盘以后预算就恢复；作废是为了不让没有重新判断的方案下单
- [等人工确认的案件不计入并发上限，运行中的案件总数可能远超上限] → 它们不调用模型，不影响 API 频率；每只股票最多一个
- [调低并发上限以后重启，调用模型的案件可能暂时超过上限] → 超出期间不启动新的，很快降回
- [紧急退出期间，这只股票的持仓风险事件要等清仓卖单结束] → 市价卖单通常很快结束；被拒时紧急退出也随之结束
- [调用方漏了某个时机没有取得决定] → 下一次任何输入变化时，整个决定会重新算出来；M4 也可以定时补算一次

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
