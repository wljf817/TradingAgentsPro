# Design

## Context

现状（M3 完成了股票池、调度器、触发三组和 Agent 网关的信号格式、任务派发、备用模型）：

- Agent 网关模块 `modules/gateway/`：`GatewayConfig`（评估窗口、派发和备用模型的阈值）；`ModelAssignments`（六个 agent 的主模型和备用模型，没有默认值，校验厂商）；domain 的 `dispatch.py`（`CaseScope(case_id, case_type, symbol)`、派发规则）、`result.py`、`fallback.py`、`signal.py`；`ports.py` 的 `TaskStore`；`service.py` 的 `TaskDispatchService`（`receive`、`fail`、`expire` 都收调用方给出的 `cost`）和 `ModelFallbackService`（只给下一步向哪个模型尝试，不调用模型）
- contracts：`Agent`（六个 agent）、`ModelRef`、`ModelUse`（`contracts/model.py`）；`Result.cost` 的注释写着"怎么算由「调用预算」定"；`CaseType`（14 种案件类型）
- 调度器的 `QueueSnapshot` 有 `general_budget_exhausted`、`exploration_budget_exhausted` 两个布尔值，持仓的五种案件类型在 `queue.py` 的 `_HOLDING_CASES` 里；健康监控的 `HealthSignals` 有 `reserve_budget_exhausted`
- 交易日历有 `session_closing_after`（时刻归属的交易日）和 `last_session_before`（一个交易日从上一个交易日的收盘开始）；跟进审批和股票池按这两个方法数"每天"
- task-dispatch 和 model-fallback 的 design 曾预计本变更"预算用完时拒绝任务""在 begin 以前拒绝"；本变更不这样做（proposal 第 4 条）
- M3 只写纯逻辑和测试：不接外部系统，不建表、不加迁移、不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/call-budget/spec.md 和 case-stages 的 delta spec。

### 模块边界

```
contracts
  model.py        TokenUsage（新增）            <- M5 从模型的返回里取得；M4 的程序日志
  task.py         Result.cost 只改注释

modules/gateway
  config.py           ModelPrice（新增）；ModelAssignments 加单价和两项检查；GatewayConfig 加三个额度
  domain/budget.py    Budget、CallCost、BudgetStatus；cost_of、charged_budget、budget_status（新增）
  ports.py            CostStore（新增）
  service.py          CallBudgetService（新增）
  __init__.py         导出新的名字
```

本次只改 Agent 网关一个模块的代码和 contracts；case-stages spec 只改措辞，调度器的代码不改。跨模块传递的模型：

- 传入：`TokenUsage`（contracts 的 Pydantic 模型，M5 从模型的返回里取得）；`CaseScope`（本模块已有）、`Agent` 和 `ModelRef`（contracts）；单价随 `ModelAssignments` 由 bootstrap 从配置文件构造
- 传出：`CallCost`、`BudgetStatus`（本模块的 frozen dataclass，由包根导出）。M4 把 `BudgetStatus` 拆成队列快照的两个布尔值和健康信号的一个布尔值，把 `CallCost` 写进程序日志；M5 把一个任务的 `CallCost.cost` 加起来交给 `TaskDispatchService`
- 没有新的跨模块 Pydantic 模型：费用记录只有本模块和 M4 读写，和跟进审批的 `FollowUp` 一样放在模块里

## Goals / Non-Goals

**Goals:**

- 规则都在 domain 里，是纯函数；service 只读当前时刻、查单价、读写存储端口
- spec 每张表的每一行都有测试；状态机性质测试拿随机的记录、拨动时间和重启检查不变量

**Non-Goals:**

按「编程风格」省略的情况：

- 超时或断开的请求厂商仍可能按已经生成的 token 收费：拿不到用量，不记
- 同一次尝试被记两次（调用方重复报告、DBOS 的 step 重试）：不去重，M4 把记下费用放进调用模型的同一个 step
- 厂商按上下文长度分档计价（比如超过 20 万 token 单价更高）：不区分，一个模型一份单价
- 写入缓存的 token 比普通输入贵：按普通输入单价算
- 运行中改单价或额度：不支持，配置在启动时读取；重启以后按新的额度和当天已有的记录算
- 调高普通预算的额度后重启，当天已经记到持仓保留预算的费用：不挪回普通预算
- 报告的模型不在模型的配置里：取不到单价，抛 `KeyError`，调用方只用配置里的模型
- 案件 ID 和案件类型对不上、agent 没有配这个模型：不检查，调用方保证
- 记下的时刻超出交易日历的覆盖范围：日历报覆盖范围错误，按系统故障处理
- 时钟倒退、记下的时刻晚于取得预算的情况的时刻：不处理
- 两个调用同时记下：同一个实例由调用方串行调用，和跟进审批一样，做法 M4 再定

## Decisions

### D1 存储端口加用例，不放在内存里

预算要跨进程重启：放在内存里的话，重启就重新给一整天的额度（proposal 第 10 条）。所以照跟进审批的写法：domain 是纯函数，service 读 Clock、读写存储端口；测试用 `tests/fakes/` 的内存实现，表和 repo 放 M4。

- 只存每次尝试的记录，不存"今天花了多少"的累计值：累计值从当天的记录加出来，跨交易日不用重置，重启也不会和记录对不上；记录同时是程序日志里 token 和费用的来源
- 考虑过：和备用模型一样放在内存里。没有采用：熔断状态丢了只是多试一次故障的模型，预算丢了是多花一天的钱
- 考虑过：从 Task 的 Result 里加出费用。不能：PM、Critic、Reviewer 的调用没有 Result
- 考虑过：单独建一个预算模块。没有采用：Proposal 写明每日调用预算由 Agent 网关执行；单价和模型的配置在一起

### D2 token 用量放进 contracts

```python
class TokenUsage(BaseModel):
    """一次尝试的 token 用量，调用方从模型的返回里取得。推理 token 算输出，缓存命中算输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
```

- M5 在 integrations 里从 PydanticAI 的返回取得，交给网关；程序日志也记 token（Proposal「程序日志」），所以和 `ModelUse` 一起放在 `contracts/model.py`
- 小于 0 或带小数由 Pydantic 报错，不记下

### D3 单价放在模型的配置里

```python
class ModelPrice(BaseModel):
    """一个模型的单价：每百万 token 多少美元。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelRef
    input_price: Decimal = Field(ge=0)
    output_price: Decimal = Field(ge=0)


class ModelAssignments(BaseModel):
    # ……原有的 model_config 和 agents

    prices: tuple[ModelPrice, ...]

    def price(self, model: ModelRef) -> ModelPrice:
        """这个模型的单价；没有配时抛 KeyError。"""
        for price in self.prices:
            if price.model == model:
                return price
        raise KeyError(model)

    @model_validator(mode="after")
    def _check(self) -> Self:
        # ……原有的两项检查：缺少 agent、Critic 和 PM 同一家厂商
        priced = [price.model for price in self.prices]
        if len(set(priced)) != len(priced):
            raise ValueError("同一个模型不能配两份单价")
        used = {model for models in self.agents.values() for model in models.chain}
        unpriced = sorted(str(model) for model in used - set(priced))
        if unpriced:
            raise ValueError(f"这些模型没有单价：{'、'.join(unpriced)}")
        return self
```

- 放进 `ModelAssignments`：和厂商校验一样在启动时一次校验，bootstrap 从同一份配置文件读取，报错就拒绝启动（proposal 第 5 条）
- 单价写成带模型的列表，不写成以 `ModelRef` 为键的 dict：配置文件里的键只能是文字，列表能直接从配置文件读进来；模型最多十来个，`price` 逐个比较就够
- 输入、输出两个单价，单位美元/百万 token，和厂商的价目表一致；`Decimal` 不丢精度
- 不检查多配的单价：没有用到的模型配了单价不影响什么
- 测试的 `assignments()` 默认带上基准单价，model-fallback 的配置测试照样只因为原来的原因报错
- 考虑过：单价单独一个配置类，`CallBudgetService` 新建时检查覆盖了用到的模型。没有采用：多一个要装配的对象，检查也离开了配置
- 考虑过：调用方直接给出费用（有的平台在返回里带费用）。没有采用：各厂商不一致，按单价算到处都一样，单价也写在配置里可以查

### D4 预算、记录和三条规则

```python
class Budget(StrEnum):
    """三个预算，见 call-budget spec「费用记到哪个预算」。"""

    GENERAL = "general"  # 普通预算
    RESERVE = "reserve"  # 持仓保留预算：普通预算用完以后持仓的案件用
    EXPLORATION = "exploration"  # 寻股预算


# 针对持仓状态的案件类型：普通预算用完以后改用持仓保留预算
_HOLDING_CASES: Final = frozenset(
    {
        CaseType.HOLDING_RISK_EVENT,
        CaseType.HOLDING_EVENT,
        CaseType.HOLDING_REVIEW,
        CaseType.HOLDING_CATALYST_REVIEW,
        CaseType.HOLDING_FOLLOW_UP,
    }
)
_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class CallCost:
    """一次尝试的费用记录，写入以后不改。"""

    case_id: UUID
    case_type: CaseType  # 案件开始运行时确定的类型
    agent: Agent
    model: ModelRef
    usage: TokenUsage
    cost: Decimal  # 美元
    budget: Budget
    recorded_at: datetime  # 记下的时刻


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """现在归属的交易日里三个预算的情况。"""

    day: date  # 现在归属的交易日
    spent: Mapping[Budget, Decimal]  # 每个预算今天的费用，没有记录的是 0
    exhausted: frozenset[Budget]  # 今天用完的预算


def cost_of(usage: TokenUsage, price: ModelPrice) -> Decimal:
    """一次尝试的费用：用量乘每百万 token 的单价，精确计算、不舍入。"""
    spent = usage.input_tokens * price.input_price + usage.output_tokens * price.output_price
    return spent / _MILLION


def charged_budget(case_type: CaseType, general_exhausted: bool) -> Budget:
    """这一条记到哪个预算；general_exhausted 是记这一条以前普通预算是否已经用完。"""
    if case_type is CaseType.EXPLORATION:
        return Budget.EXPLORATION
    if general_exhausted and case_type in _HOLDING_CASES:
        return Budget.RESERVE
    return Budget.GENERAL


def budget_status(costs: Iterable[CallCost], day: date, config: GatewayConfig) -> BudgetStatus:
    """day 是现在归属的交易日，costs 是记下的时刻归属这一天的记录。"""
    spent = dict.fromkeys(Budget, Decimal(0))
    for entry in costs:
        spent[entry.budget] += entry.cost
    limits = {
        Budget.GENERAL: config.general_budget,
        Budget.RESERVE: config.reserve_budget,
        Budget.EXPLORATION: config.exploration_budget,
    }
    exhausted = frozenset(budget for budget in Budget if spent[budget] >= limits[budget])
    return BudgetStatus(day, spent, exhausted)
```

- 费用的除法只是移动小数点，`Decimal` 的默认精度（28 位有效数字）对 token 数和单价绰绰有余，所以不舍入也不会丢精度（spec「不舍入」）
- 记到哪个预算只看记之前普通预算是否用完，整笔记一个预算（proposal 第 6 条）：跨过额度的那一条记普通预算，之后持仓案件的才记持仓保留预算；寻股不看普通预算；寻股预算和持仓保留预算是否用完不影响记到哪里
- 用完是"不小于额度"（proposal 第 7 条）；三个预算各自比较
- `BudgetStatus` 给出今天的费用和用完的预算，不直接给三个布尔值：M4 用 `Budget.GENERAL in status.exhausted` 这样一行取出；今天的费用给告警和程序日志用
- `_HOLDING_CASES` 和调度器 `queue.py` 里的同名集合内容相同：模块之间不能 import，各自一份，测试各自逐个检查 14 种案件类型和 spec 的表一致。考虑过：给 contracts 的 `CaseType` 加"是不是持仓的案件"。没有采用：要改调度器的代码，本变更只改一个节点
- 记录叫 `CallCost`，变量名用 `entry`，避免 `cost.cost`

### D5 每日的划分

```python
def _status(self, now: datetime) -> BudgetStatus:
    """now 归属的交易日从上一个交易日的收盘时刻开始。"""
    start = self._calendar.last_session_before(now).close
    day = self._calendar.session_closing_after(now).day
    return budget_status(self._store.since(start), day, self._config)
```

- 一个交易日从上一个交易日收盘（含）到这个交易日收盘（不含）（已决定第 2 条），和跟进审批、股票池数"每天"的方法相同：记下的时刻不早于上一个交易日收盘的记录就是今天的
- 收盘那一刻起 `start` 变成这个交易日的收盘，三个预算一起归零，不需要定时重置（spec「收盘时恢复」）；夏令时、提前收盘和休市日都由交易日历处理
- 预算的情况每次从存储重新算，不缓存：一天几十到上百条记录，量很小

### D6 已经开始的案件不拒绝

网关不加"预算已用完"的拒绝原因，`TaskDispatchService` 和 `ModelFallbackService` 都不改（proposal 第 4 条）：

- 预算只在案件开始时起作用：队列看普通预算、寻股预算是否用完（case-queue spec 已有），健康监控看持仓保留预算是否用完、用完进入降级模式（degraded-mode spec 已有）
- 已经开始的案件跑完，调用照常记下，预算可以超出；超出的部分受每个案件的任务数、轮次、重交次数、质询轮次和并发上限约束
- case-stages spec「系统故障」只改措辞：去掉"网关因为预算用完拒绝 PM 的调用"，`test_stage.py` 的测试说明跟着改，调度器代码不改
- 考虑过：按 task-dispatch design D10 的预计，预算用完时拒绝任务。没有采用：PM 缺少信息照样会出结论，研究要求不够时退回重交，白花更多；拒绝 PM、Critic 的调用会让案件因系统故障结束，已经花掉的钱白花，持仓案件还可能在提出退出方案以前被打断
- 考虑过：只拒绝非持仓案件的任务。没有采用：规则多一层，超出的钱本来就有上限

### D7 子 agent 任务的费用由调用方加总

`record` 返回这一条的 `CallCost`；M5 在调用子 agent 的同一个 step 里把这个任务每次尝试的 `cost` 加起来，交给 `receive`、`fail`、`expire`（proposal 第 11 条）。`TaskDispatchService` 不改，`Result.cost` 只改注释："这个任务各次尝试记下的费用之和，见 call-budget spec「子 agent 任务的费用」"。

- 考虑过：记录带上 task_id，由网关从存储加出 Result 的费用。没有采用：`receive`、`fail`、`expire` 的参数要改，任务派发要依赖费用的存储；调用方本来就在同一个 step 里拿着每次尝试的结果

### D8 存储端口

```python
class CostStore(Protocol):
    """模型调用费用的存储。只追加：每次尝试写入一条，写入以后不改。

    网关是唯一的写入者；数据库实现（M4）建本模块的表。
    """

    def since(self, moment: datetime) -> tuple[CallCost, ...]:
        """记下的时刻不早于 moment 的记录，按写入的先后。"""
        ...

    def add(self, entry: CallCost) -> None:
        """写入一条记录。"""
        ...
```

- 只有按时刻查：算今天的预算只要今天的记录；按案件、按 agent 统计等 M6 需要时再加
- `tests/fakes/costs.py`：`InMemoryCostStore`，按写入的先后存在 list 里
- M4 的 repo 按记下的时刻建索引；记录写入以后不改

### D9 用例

```python
class CallBudgetService:
    """调用预算的入口：记下每次尝试的费用，给出三个预算的情况，见 call-budget spec。

    这里只读当前时刻、查单价、读写存储。同一个实例由调用方串行调用：记到哪个预算是先读后写，
    做法 M4 定。
    """

    def __init__(
        self,
        store: CostStore,
        clock: Clock,
        calendar: TradingCalendar,
        models: ModelAssignments,
        config: GatewayConfig,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._models = models
        self._config = config

    def record(
        self, scope: CaseScope, agent: Agent, model: ModelRef, usage: TokenUsage
    ) -> CallCost:
        """为案件 scope 里 agent 的一次尝试记下费用；model 没有单价时抛 KeyError。"""
        now = self._clock.now()
        general_exhausted = Budget.GENERAL in self._status(now).exhausted
        entry = CallCost(
            case_id=scope.case_id,
            case_type=scope.case_type,
            agent=agent,
            model=model,
            usage=usage,
            cost=cost_of(usage, self._models.price(model)),
            budget=charged_budget(scope.case_type, general_exhausted),
            recorded_at=now,
        )
        self._store.add(entry)
        return entry

    def status(self) -> BudgetStatus:
        """现在归属的交易日里三个预算的情况。"""
        return self._status(self._clock.now())
```

- 案件用已有的 `CaseScope`：M4 派发任务时已经有它，PM、Critic、Reviewer 的调用也用同一个；股票不记
- 模型用 `ModelRef`：M5 每次尝试用的是 `step.call.model`，成功以后才有 `ModelUse`，失败但有用量的尝试也要记
- "现在"由 service 从 Clock 读取；三个规则都在 domain
- 和另外两个 service 分开：一个管任务和 Result，一个管选模型，一个管费用；M5 把三者串起来（D11）

### D10 配置和默认值

```python
class GatewayConfig(BaseModel):
    # ……原有的各项

    # 三个预算的额度：每个交易日记到这个预算的费用到这么多美元就用完
    general_budget: Decimal = Field(default=Decimal("2.00"), gt=0)
    reserve_budget: Decimal = Field(default=Decimal("1.00"), gt=0)
    exploration_budget: Decimal = Field(default=Decimal("0.50"), gt=0)
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 普通预算的额度 | 2.00 美元 | 一个案件约 0.2 到 0.4 美元（PM 3 次左右、每次 2 万输入和 2 千输出 token；子 agent 用便宜模型；交易方案另有 Critic），够平常一天 5 到 10 个案件 |
| 持仓保留预算的额度 | 1.00 美元 | 普通预算用完以后还够 3 到 4 个持仓案件，坏日子里几只持仓的风险事件能处理；止损单和紧急风险退出不靠模型 |
| 寻股预算的额度 | 0.50 美元 | 寻股有 3 个交易日的冷却期，够一次寻股（PM 看粗筛的名单，输入多一些，最多 6 个任务） |

- 每天最多约 3.5 美元，一个月约 75 美元，是 2000 美元账户的 4% 左右；平常用不满（proposal 第 8 条）。模拟盘阶段按 M5 的实际用量再调
- 都要求大于 0：持仓保留预算是 0 的话普通预算一用完就进入降级模式；配置项之间没有必须满足的关系
- 放进 `GatewayConfig`：和派发、备用模型的阈值一样是网关的配置。M4 由 bootstrap 从配置文件读取，测试里直接构造

### D11 后续的接法（给 M4、M5 的对照）

本变更不调用其他模块，下表是之后的接法：

| 什么时候 | 做什么 |
| --- | --- |
| 启动（M4） | bootstrap 从配置文件构造带单价的 `ModelAssignments` 和带额度的 `GatewayConfig`，报错就拒绝启动；新建 `CallBudgetService`，注入费用的 repo、Clock 和交易日历；第一次健康判定的"持仓保留预算用完"改为取 `status()` |
| 每次尝试拿到用量（M5） | `record(scope, agent, step.call.model, usage)`，放在调用模型的同一个 step 里；拿不到用量的尝试不记 |
| 子 agent 的任务出 Result（M5） | 这个任务各次 `record` 的 `cost` 之和交给 `receive`、`fail` 或 `expire` |
| 取得队列的决定（M4） | `status()` 的 `GENERAL`、`EXPLORATION` 是否在 `exhausted` 里，填进 `QueueSnapshot` 的两个布尔值 |
| 健康判定（M4） | `RESERVE in status().exhausted` 填进 `HealthSignals.reserve_budget_exhausted` |
| 某个预算刚用完（M4） | `record` 以后 `exhausted` 比之前多了：写程序日志、告警；取得队列的决定（预算标志变化）；持仓保留预算刚用完时让健康监控判定，进入降级模式 |
| 交易日收盘（M4） | 预算恢复：取得队列的决定，让健康监控判定（持仓保留预算用完引起的降级在这里转入等待对账） |
| 程序日志（M4） | 每条 `CallCost` 写一条：Agent 网关的 token 和费用、系统的调用预算消耗（Proposal「程序日志」） |

- 记下费用和调用模型放在同一个 step：step 的输出由 DBOS 记下，重放时不会再记一次

### D12 包根导出和纯区域

包根导出 `CallBudgetService`、`CostStore`、`ModelPrice`、`Budget`、`CallCost`、`BudgetStatus`，以及 domain 的 `cost_of`、`charged_budget`、`budget_status`。纯函数导出是为了测试只从包根 import。

- domain 只用 `collections.abc`、`dataclasses`、`datetime`、`decimal`、`enum`、`typing`、`uuid`，以及 contracts 的 `agent`、`case`、`model` 和本模块的 config；`contracts/model.py` 只多用 pydantic 的 `NonNegativeInt`。都在白名单里，不改 `tests/architecture/rules.py`
- service 用 shared 的 `TradingCalendar`、`Clock`；模块里只有允许的几种文件，module-layers 契约自动覆盖，不改 import-linter 的配置

### D13 测试

```
tests/fakes/costs.py                                 费用存储的内存实现
tests/contracts/test_model.py                        TokenUsage（补充）
tests/modules/gateway/builders.py                    基准单价、K-5 到 K-8、spend()、budget_service()（补充）
tests/modules/gateway/test_config.py                 「模型的单价」「预算的配置」（补充）
tests/modules/gateway/domain/test_budget.py          费用的算法、记到哪个预算、预算的情况
tests/modules/gateway/test_budget_service.py         call-budget spec 其余场景
tests/modules/gateway/test_budget_state_machine.py   状态机性质测试
tests/modules/scheduler/domain/test_stage.py         「PM 的调用失败」的说明（修改）
```

- builders：基准单价加进 `assignments()` 的默认值；K-1 到 K-4 沿用 task-dispatch 的 `CaseScope`，新增 K-5（每周复盘）、K-6（CCC 的持仓跟进）、K-7（BBB 的关注跟进）、K-8（AAA 的候选评估）；`spend(service, case, x)` 是 spec 的"K-n 记下 x"：p-1、输入 x 乘 500,000 个 token、没有输出，agent 是 PM，K-5 是 Reviewer；`budget_service(clock=None, store=None, **config)` 用测试时钟（停在 2026-10-14 15:00Z）、纽交所样例日历、基准模型的配置和内存存储（传入同一个存储就是 spec 的"进程重启"）
- 金额用 `Decimal("0.06")` 这样的字面量比较，`Decimal` 按数值相等
- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：随机交错
  - 记下：随机的案件类型（14 种）、agent、模型和用量，费用从 0 到几十美分
  - 拨动时间：几分钟到几天，跨收盘、跨周末、跨感恩节，不晚于 2027-01-28
  - 重启：用同一个存储新建 service
  - 配置用较小的额度（比如 0.50、0.20、0.10），让额度经常被碰到

  每一步之后检查（和测试里按 spec 写的参考模型比较）：
  - 每个预算今天的费用等于参考模型里记下的时刻归属今天、记到这个预算的费用之和；用完当且仅当不小于额度
  - 寻股的记录都在寻股预算，寻股预算里只有寻股的记录
  - 持仓保留预算里只有持仓的五种案件类型的记录，并且每一条记下以前普通预算已经用完；其他非寻股的记录都在普通预算
  - 持仓保留预算今天的费用大于 0 时，普通预算已经用完
  - 跨过收盘以后、新的记录以前，三个预算今天的费用都是 0
  - 重启前后预算的情况相同
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [已经开始的案件不拒绝，预算会超出] → 超出的部分受每个案件的任务数、轮次、重交次数、质询轮次和并发上限约束：通常不到一美元，最坏是三个并发的案件都用满上限，两三美元（proposal 第 4 条）
- [默认额度是估算，M5 接入真实模型以后可能太紧或太松] → 都是配置项；模拟盘阶段按实际用量调整（proposal 第 8 条）
- [普通预算用完以后非持仓案件要等到收盘] → 已决定第 3 条；收盘以后预算就恢复，盘后到第二天开盘以前的案件照常运行
- [缓存命中按普通输入单价算，费用偏高] → 偏保守，只会让预算早一点用完；M5 用了缓存、差别大时再用修改类变更加缓存单价
- [单价要随厂商调价手工更新] → 写在配置文件里，改了重启生效；忘了改只让费用不准，不影响交易
- [持仓的五种案件类型在调度器和网关各有一份] → 两边的测试都逐个检查 14 种案件类型和 spec 的表一致
- [记下费用和调用模型不在同一个 step 的话会重复或漏记] → D11 写明放在同一个 step

## Migration Plan

只新增代码，改 contracts 的 `TokenUsage` 和 `ModelAssignments` 的必填项；配置文件和费用记录都还没有接入（M4），没有持久化的数据，不需要迁移。需要回滚时直接 revert。
