# Design

## Context

现状（M2 进行中，M3 完成了股票池）：

- contracts 已有股票状态 `StockStatus`、关注论点 `WatchThesis`（以及催化剂 `Catalyst`、引用 `CatalystReference`、代码能检测的条件 `Condition`）、交易方案 `TradePlan`。`TradePlan` 在自己的 `model_validator` 里按动作检查字段（risk-rules spec「交易方案的格式」）
- 股票池模块给出每只股票的状态和板块，PM 发起的操作返回 `PoolEntry | Rejection`，拒绝作为返回值
- 还没有调度器模块，也没有事件、子 agent、案件的任何类型
- M3 只写纯逻辑和测试：不接外部系统，不建表，不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/case-types/spec.md。

### 模块边界

```
contracts（Pydantic 模型和枚举，跨模块传递）
  agent.py       SubAgent                       -> 任务派发（以后）
  event.py       EventCategory                  <- 事件检测（M4）
  case.py        CaseType                       -> 案件记录（M4）、按类型评估（M6）、任务派发
  thesis.py      + InvestmentThesis             -> 复查日与催化剂、M4 存论点
  trade_plan.py  + PlanFields、check_plan_fields（TradePlan 的行为不变）
  conclusion.py  Decision、AfterClose、PlanDraft、FollowUpReason、FollowUpRequest、
                 PoolAddition、PoolRemoval、HoldingConclusion、WatchConclusion、
                 CandidateConclusion、ExplorationConclusion、Conclusion
                 <- PM（M5 的 submit_conclusion）
                 -> 股票池、风控（M4 的工作流）、跟进审批、备用模型

modules/scheduler
  domain/case_type.py   EventTrigger、HeartbeatTrigger、FollowUpTrigger、SellTrigger、Trigger、
                        CaseDefinition、define_case、conclusion_model
  domain/validation.py  ConclusionRejection、conclusion_rejection
  config.py             SchedulerConfig
  __init__.py           导出公开的名字
```

本次只新增调度器一个模块，不改其他模块。跨模块传递的 Pydantic 模型：

- 传入：PM 的结论（四种结论模型，M5 由 PM 给出）；`SubAgent`（派过任务的子 agent、Result 出自哪个子 agent，以后由任务派发给出）；`EventCategory`（M4 的事件检测给出，工作流放进 `EventTrigger`）；`StockStatus`（股票池给出，工作流传入）
- 传出：`CaseType`（在 `CaseDefinition` 里）、`TradePlan`（结论补上方案 ID、股票和板块后交给风控）
- 触发、`CaseDefinition` 和拒绝原因是本模块的 frozen dataclass 和枚举，由包根导出，和股票池的 `PoolEntry` 一样。以后要交给别的模块时再决定是否放进 contracts

## Goals / Non-Goals

**Goals:**

- 案件类型是一张表，结论的校验是一张表，都是纯函数；spec 表里每一行都有测试
- 和案件类型无关的结构要求（哪个决定附什么）由结论模型保证，校验函数只做和案件类型有关的检查
- M5 交给 PM 的结论 schema 只含这个状态允许的决定

**Non-Goals:**

按「编程风格」省略的情况：

- 排队期间别的案件已经更新了论点、复查日已经不再到期：复查案件照样运行，PM 再回答一次
- 排队期间事件已经过时（比如入场条件满足的事件，开始时已经买入）：按开始时的状态照常运行
- 案件运行期间股票状态又变了（比如持仓案件里止损单成交）：校验按开始时的类型；方案由风控和下单前再校验挡住，其余由「案件阶段」和 M4 处理
- 引用了不存在的、或者别的案件的 Result ID：不检查，只按调用方传入的已完成 Result 计数
- 寻股的加入和删除里同一只股票出现两次，或者既加入又删除：不检查，股票池按顺序处理时自然拒绝后一个
- 目标价不高于限价、证伪价位高于限价这类不合理的数值：不检查
- 文字的长度：不限制；股票代码的大小写和空白：不规范化，和股票池一样

## Decisions

### D1 contracts 里的三个枚举

```python
class SubAgent(StrEnum):
    """子 agent 的身份。"""

    NEWS = "news"
    QUANT = "quant"
    FUNDAMENTALS = "fundamentals"


class EventCategory(StrEnum):
    """事件的类别，由 M4 的事件检测标注。"""

    PRICE_VOLUME = "price_volume"  # 价格和成交量异动
    NEWS = "news"  # 新闻
    FILING = "filing"  # 公告
    EARNINGS = "earnings"  # 财报发布
    ENTRY = "entry"  # 关注股的入场条件满足
    THESIS_CONDITION = "thesis_condition"  # 持仓达到目标价或证伪条件触发


class CaseType(StrEnum):
    HOLDING_RISK_EVENT = "holding_risk_event"  # 持仓风险事件
    HOLDING_EVENT = "holding_event"  # 持仓事件
    HOLDING_REVIEW = "holding_review"  # 持仓复查
    HOLDING_CATALYST_REVIEW = "holding_catalyst_review"  # 持仓催化剂复查
    HOLDING_FOLLOW_UP = "holding_follow_up"  # 持仓跟进
    WATCH_EVENT = "watch_event"  # 关注股事件
    WATCH_REVIEW = "watch_review"  # 关注复查
    WATCH_CATALYST_REVIEW = "watch_catalyst_review"  # 关注催化剂复查
    WATCH_FOLLOW_UP = "watch_follow_up"  # 关注跟进
    CANDIDATE_EVENT = "candidate_event"  # 候选股事件
    CANDIDATE_EVALUATION = "candidate_evaluation"  # 候选评估
    EXPLORATION = "exploration"  # 寻股
    WEEKLY_RETROSPECTIVE = "weekly_retrospective"  # 每周复盘
    SELL_RETROSPECTIVE = "sell_retrospective"  # 卖出复盘
```

- 三个都放 contracts：子 agent 的身份以后任务派发要用，事件类别由 M4 的事件检测给出，案件类型要写进案件记录、按类型评估，模块之间不能互相 import
- 公告单独一类，因为它归 Fundamentals 解读（proposal「需要人决定」第 7 条）。止损单成交不是事件类别，见 D6
- 复查（review）和复盘（retrospective）用不同的词，避免混淆
- 考虑过：持仓复查只有一种类型，问题按触发原因变化。没有采用：类型固定问题；校验（催化剂到期）和按类型评估都要区分两者，不如直接是两种类型

### D2 投资论点

```python
class InvestmentThesis(BaseModel):
    """投资论点：新建仓时附上；持仓的论点更新也是一份完整的投资论点。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1)  # 买入理由
    catalyst: Catalyst
    target_price: Decimal = Field(gt=0)
    invalidation: str = Field(min_length=1)  # 证伪条件
    # 证伪条件中代码能检测的部分，任一满足就算触发；可以没有
    invalidation_conditions: tuple[Condition, ...] = ()
    review_day: date  # 复查日
```

- 放在 `contracts/thesis.py`，和关注论点共用 `Catalyst`、`Condition`，写法和 `WatchThesis` 一致：缺字段的论点构造不出来
- 目标价单独一个字段，不写成条件：它同时是收益目标；M4 设到价提醒时由代码换成"价格不低于目标价"
- 证伪条件的代码部分可以为空：很多证伪条件（毛利率下滑、管理层变动）不是价格类
- 复查日和截止日期取什么值由「复查日与催化剂」校验

### D3 结论按股票状态分成四种模型

```python
class Decision(StrEnum):
    """结论的主决定。"""

    HOLD = "hold"  # 持有
    ADD = "add"  # 加仓
    REDUCE = "reduce"  # 减仓
    CLOSE = "close"  # 清仓
    BUY = "buy"  # 开仓方案
    KEEP_WATCHING = "keep_watching"  # 继续关注
    DEMOTE = "demote"  # 降为候选
    REMOVE = "remove"  # 移出股票池
    PROMOTE = "promote"  # 升级为关注
    KEEP = "keep"  # 保持


class AfterClose(StrEnum):
    """PM 发起的清仓之后的去向。"""

    WATCH = "watch"  # 转为关注，附关注论点
    REMOVE = "remove"  # 移出股票池


class _ConclusionBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    answer: str = Field(min_length=1)  # 对问题的正面回答
    rationale: str = Field(min_length=1)  # 理由；也是方案的理由和移出的理由
    cited_results: tuple[str, ...] = ()  # 引用的 Result ID


class HoldingConclusion(_ConclusionBase):
    score: int = Field(ge=-2, le=2)
    decision: Literal[Decision.HOLD, Decision.ADD, Decision.REDUCE, Decision.CLOSE]
    plan: PlanDraft | None = None
    thesis: InvestmentThesis | None = None  # 论点更新
    follow_up: FollowUpRequest | None = None
    after_close: AfterClose | None = None
    watch_thesis: WatchThesis | None = None  # 清仓后转为关注时的关注论点


class WatchConclusion(_ConclusionBase):
    score: int = Field(ge=-2, le=2)
    decision: Literal[Decision.BUY, Decision.KEEP_WATCHING, Decision.DEMOTE, Decision.REMOVE]
    plan: PlanDraft | None = None
    thesis: InvestmentThesis | None = None  # 新建仓的投资论点
    watch_thesis: WatchThesis | None = None  # 论点更新
    follow_up: FollowUpRequest | None = None


class CandidateConclusion(_ConclusionBase):
    score: int = Field(ge=-2, le=2)
    decision: Literal[Decision.PROMOTE, Decision.KEEP, Decision.REMOVE]
    watch_thesis: WatchThesis | None = None  # 升级时的关注论点


class PoolAddition(BaseModel):
    symbol: str = Field(min_length=1)
    reason: str = Field(min_length=1)  # 理由与论点草稿
    score: int = Field(ge=-2, le=2)


class PoolRemoval(BaseModel):
    symbol: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ExplorationConclusion(_ConclusionBase):
    additions: tuple[PoolAddition, ...] = ()
    removals: tuple[PoolRemoval, ...] = ()


Conclusion = HoldingConclusion | WatchConclusion | CandidateConclusion | ExplorationConclusion
```

（每个模型都是 `frozen=True, extra="forbid"`，带文字字段的加 `str_strip_whitespace=True`，上面只写了一处。）

- 每种结论的 `model_validator` 实现 spec「持仓的结论」「关注的结论」「候选的结论」的表：
  - 持仓：持有不写方案；加仓、减仓、清仓必须写方案，按 D4 检查；清仓必须写去向，转为关注当且仅当附关注论点，不能附论点更新和跟进申请；不是清仓不写去向和关注论点
  - 关注：开仓方案必须写方案和投资论点，不附论点更新和跟进申请；继续关注不写方案和投资论点；降为候选、移出什么都不附
  - 候选：升级当且仅当附关注论点
- 决定用 `Decision` 的 `Literal` 子集，JSON schema 里只出现这个状态允许的值（已确认 Pydantic 对 `StrEnum` 成员的 `Literal` 按值校验，解析出的是枚举成员）
- 论点更新是一份完整的论点，代替原来的（proposal「需要人决定」第 5 条），所以字段类型就是 `InvestmentThesis`、`WatchThesis`
- 方案的理由、引用就是结论的 `rationale`、`cited_results`；PM 发起的移出（关注、候选）用结论的 `rationale` 作为股票池要求的删除理由
- M5 按案件类型把 `conclusion_model(case_type)`（D6）作为 PM 的 `submit_conclusion` 的输出类型
- 考虑过：一个结论模型，决定是完整的枚举，其余字段全部可选，由校验函数按表检查。没有采用：十几个可选字段；PM 的 schema 里出现不允许的决定；同一张表要在校验里再写一遍
- 考虑过：按决定分成十个模型（discriminated union）。没有采用：方案字段在加仓、减仓、清仓、开仓方案四个模型里各写一遍；按状态限制决定照样要检查
- 考虑过：把"新建仓附投资论点""清仓选去向""升级附关注论点"做成校验的拒绝原因。没有采用：它们和案件类型无关，属于格式；和股票池 D1 一样，一条规则只写一处

### D4 PM 写的交易方案

```python
class PlanDraft(BaseModel):
    """PM 写的交易方案。动作由结论的决定给出；方案 ID、股票和板块由代码补上。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: int | None = Field(default=None, gt=0)
    order_type: OrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    emergency_threshold: Decimal | None = Field(default=None, gt=0, lt=1)
    expires_at: AwareDatetime | None = None
    max_price_deviation: Decimal | None = Field(default=None, gt=0, lt=1)
```

`TradePlan` 按动作检查字段的规则原样抽成函数，`TradePlan` 和结论共用：

```python
class PlanFields(Protocol):
    """按动作检查的字段。属性只读，TradePlan 和 PlanDraft 都满足。"""

    @property
    def quantity(self) -> int | None: ...

    @property
    def order_type(self) -> OrderType: ...

    @property
    def limit_price(self) -> Decimal | None: ...

    @property
    def stop_price(self) -> Decimal | None: ...

    @property
    def emergency_threshold(self) -> Decimal | None: ...

    @property
    def max_price_deviation(self) -> Decimal | None: ...


def check_plan_fields(action: TradeAction, plan: PlanFields) -> None:
    """按动作检查字段，不符合时抛 ValueError；规则见 risk-rules spec「交易方案的格式」。"""
```

`TradePlan._check_fields_for_action` 改为调用 `check_plan_fields(self.action, self)`，错误信息不变。结论的 validator 用决定对应的动作调用它，买入和加仓另外要求 `emergency_threshold`（proposal「需要人决定」第 9 条）。

补上方案 ID、股票和板块，变成交易方案，持仓和关注的结论各有一个同样的方法：

```python
_ACTIONS: Final = {
    Decision.ADD: TradeAction.ADD,
    Decision.REDUCE: TradeAction.REDUCE,
    Decision.CLOSE: TradeAction.CLOSE,
    Decision.BUY: TradeAction.BUY,
}


def trade_plan(self, plan_id: UUID, symbol: str, sector: str) -> TradePlan | None:
    """补上方案 ID、股票和板块，变成交易方案；没有方案时为 None。"""
    if self.plan is None:
        return None
    return TradePlan(
        plan_id=plan_id,
        symbol=symbol,
        sector=sector,
        action=_ACTIONS[self.decision],
        **self.plan.model_dump(),
    )
```

- 方案 ID 由 M4 的工作流给出：`uuid4` 全项目禁用，回放时要能重现，可以像紧急退出的 `close_plan` 那样用 `uuid5` 从案件推出，做法 M4 定。股票是案件的股票，板块取股票池记录的 `PoolEntry.sector`（stock-pool spec「股票池的记录」）
- 有效期和允许价格偏离照抄，没写时由风控给默认值（pre-order-recheck spec）
- 考虑过：`PlanDraft` 自己带动作字段。没有采用：动作和决定重复，两者不一致又要多一条检查
- 考虑过：`TradePlan` 继承 `PlanDraft`。没有采用：改变 `TradePlan` 的字段顺序和类型关系；抽函数对 `TradePlan` 没有影响
- 考虑过：构造一个带占位 ID 和板块的 `TradePlan` 来复用它的校验。没有采用：占位值容易被当成真的

### D5 跟进申请

```python
class FollowUpReason(StrEnum):
    EVENT_REACTION = "event_reaction"  # 事件后反应
    EXTERNAL_SCHEDULE = "external_schedule"  # 外部日程
    OBSERVATION = "observation"  # 价格或指标观察


class FollowUpRequest(BaseModel):
    """跟进申请；审批规则在「跟进审批」。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: FollowUpReason
    # 引用的事件或日程项，或者代码能检测的条件；没有引用由「跟进审批」拒绝
    reference: CatalystReference | Condition | None = None
    question: str = Field(min_length=1)
    possible_actions: str = Field(min_length=1)  # 可能采取的动作
    trigger_at: AwareDatetime  # 触发时间
```

- 事件和日程项的引用复用 `CatalystReference`：「跟进审批」要判断"引用的就是论点里某个催化剂引用的对象"，同一个类型直接比较相等
- 两种引用的字段不重叠，加上 `extra="forbid"`，Pydantic 的联合类型能分清
- 引用可以为空，否则「跟进审批」的"没有引用具体对象就拒绝并提示改为调整复查日"永远不会发生

### D6 触发和案件类型

```python
@dataclass(frozen=True, slots=True)
class EventTrigger:
    symbol: str
    category: EventCategory
    holding_risk: bool  # 事件检测标注的持仓风险级别


@dataclass(frozen=True, slots=True)
class HeartbeatTrigger:
    case_type: CaseType  # 检查项开出的案件类型
    symbol: str | None = None  # 寻股和每周复盘没有


@dataclass(frozen=True, slots=True)
class FollowUpTrigger:
    symbol: str
    question: str


@dataclass(frozen=True, slots=True)
class SellTrigger:
    symbol: str


Trigger = EventTrigger | HeartbeatTrigger | FollowUpTrigger | SellTrigger


@dataclass(frozen=True, slots=True)
class CaseDefinition:
    """开始运行时确定的案件类型，以及它固定的问题、优先级和事件要求的子 agent。"""

    case_type: CaseType
    symbol: str | None  # 寻股和每周复盘没有
    question: str | None  # 复盘案件不唤醒 PM，没有问题
    priority: int  # 1 最高，9 最低
    required_agent: SubAgent | None  # 只有事件案件有


def define_case(trigger: Trigger, status: StockStatus | None) -> CaseDefinition | None:
    """按触发和股票现在的状态确定案件类型；None 表示案件不运行。

    status 是这只股票现在的状态，不在股票池也不是持仓时为 None；寻股和复盘不看它。
    """


def conclusion_model(case_type: CaseType) -> type[Conclusion] | None:
    """PM 要提交的结论种类；复盘案件不唤醒 PM，为 None。"""
```

- `domain/case_type.py` 里有两张表，内容就是 spec 的表：案件类型 → 针对的状态、固定的问题、优先级；事件类别 → 至少派给的子 agent。九档优先级、问题文字和事件对应的子 agent 都是 Proposal 规定或人决定的规则，不是阈值，不做成配置
- `define_case` 按 spec「开始运行时确定案件类型」的表：事件和跟进按现在的状态选类型（跟进遇到候选返回 None）；心跳触发针对一个状态，现在不是这个状态就返回 None；寻股、每周复盘、卖出复盘总是有类型
- 心跳触发直接带案件类型：检查项和案件类型一一对应，再加一个检查项枚举只是多一层映射。`__post_init__` 检查类型是心跳能开的七种，带不带股票和类型一致，不对就抛 `ValueError`
- 跟进触发不带申请时的状态（proposal「需要人决定」第 6 条）：跟进的结论本来就按股票当前的状态，关注股买入变为持仓后跟进保留、按持仓跟进运行；状态往下走时「跟进审批」作废跟进，已经排进队列的跟进开始时遇到候选或不在股票池也不运行
- 止损单成交不作为事件类别：所有卖出成交（止损单、紧急风险退出、PM 发起的减仓和清仓）都开卖出复盘，不唤醒 PM，用一个卖出成交的触发就够
- 案件队列以后用 `define_case` 的优先级排队，开始时再调用一次，返回 None 的不运行
- 考虑过：触发只给驱动方式，案件类型全部由状态推出来。没有采用：心跳的四个复查检查项在同一状态下对应不同类型，必须由触发给出

### D7 结论的校验

```python
class ConclusionRejection(StrEnum):
    """PM 的结论不通过的原因。"""

    WRONG_KIND = "wrong_kind"  # 结论不属于这个案件
    FOLLOW_UP_IN_FOLLOW_UP = "follow_up_in_follow_up"  # 跟进案件不能再申请跟进
    MISSING_RENEWAL = "missing_renewal"  # 续期缺少论点更新
    MISSING_REQUIRED_AGENT = "missing_required_agent"  # 缺少事件要求的子 agent
    INSUFFICIENT_RESEARCH = "insufficient_research"  # 研究不足


def conclusion_rejection(
    case: CaseDefinition,
    conclusion: Conclusion,
    dispatched: Collection[SubAgent],
    completed: Mapping[str, SubAgent],
    config: SchedulerConfig,
) -> ConclusionRejection | None:
    """按 spec「结论的校验」的顺序给出第一项不通过的原因，全部通过时为 None。

    dispatched：本案件派过任务的子 agent，不论 Result 的状态。
    completed：本案件状态是完成的 Result 出自哪个子 agent，键是 Result ID。
    """
```

- 结论的种类用 `type(conclusion) is conclusion_model(case.case_type)` 判断；复盘案件没有结论种类，抛 `ValueError`
- 续期：案件类型是四种复查类之一，决定是持有、加仓、减仓、继续关注，并且对应的论点更新（持仓 `thesis`，关注 `watch_thesis`）为空
- 研究要求：决定是开仓方案、加仓、升级时，`{completed[r] for r in cited_results if r in completed}` 的个数不少于 `min_cited_agents`
- 种类放在第一项：后面的检查都要按种类读字段。其余顺序是先看和案件类型有关的结构（跟进、续期），再看研究
- 拒绝作为返回值，不抛异常：和股票池 D4 一样，这是 PM 提交的正常结果，由「案件阶段」退回 PM
- 事件要求的子 agent 在这里检查，不在 Agent 网关：Proposal 说不满足最低要求就退回 PM，提交结论时才同时知道案件类型和派过哪些任务；Agent 网关只看到 `dispatch_tasks`
- 只给第一项原因：PM 改正以后再提交，下一项自然会被检查到

### D8 配置

```python
class SchedulerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 开仓方案、加仓和升级引用的已完成 Result，至少来自这么多个不同子 agent
    min_cited_agents: int = Field(default=2, ge=1, le=len(SubAgent))
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 研究要求的子 agent 数 | 2 | Proposal 规定；上限是子 agent 的个数 3 |

以后的调度器变更（队列的并发上限、收盘检查、跟进审批的上限）往这里加字段。M4 由 bootstrap 从配置文件读取，测试里直接构造。

### D9 结论交给其他模块（给 M4 的对照）

本变更不调用股票池和风控，下表是 M4 工作流的接法，校验通过以后执行：

| 结论 | 交给 |
| --- | --- |
| 持仓、关注、候选的结论 | 股票池 `record_score(symbol, score)` |
| 加仓、减仓、清仓、开仓方案 | `trade_plan(plan_id, symbol, sector)` 交给 Critic 和风控 |
| 清仓 | 成交后股票池 `closed_by_pm(symbol, watch_thesis)`，去向是移出时 `watch_thesis` 为空 |
| 持有、加仓、减仓、继续关注附的论点更新 | 「复查日与催化剂」校验取值后保存 |
| 开仓方案的投资论点 | M4 存进状态库，成交后生效 |
| 降为候选、移出、升级、保持 | 股票池 `demote`、`remove(symbol, rationale)`、`promote(symbol, watch_thesis)`、`keep` |
| 寻股的加入、删除 | 股票池 `add(symbol, PoolSource.EXPLORATION, reason, listing)` 后 `record_score`；`remove(symbol, reason)` |
| 跟进申请 | 「跟进审批」 |

### D10 包根导出和纯区域

包根导出 `SchedulerConfig`、`EventTrigger`、`HeartbeatTrigger`、`FollowUpTrigger`、`SellTrigger`、`Trigger`、`CaseDefinition`、`define_case`、`conclusion_model`、`ConclusionRejection`、`conclusion_rejection`。没有 service 和 ports：本变更只有纯函数，和风控规则最初一样；「案件队列」加存储时再加。

domain 只用 `dataclasses`、`enum`、`typing`、`collections.abc`；contracts 新增的文件只用 `datetime`、`decimal`、`enum`、`typing`、`uuid` 和 pydantic。都在纯区域白名单里，不改 `tests/architecture/rules.py`。

### D11 测试

```
tests/contracts/test_thesis.py                    + spec「投资论点的格式」
tests/contracts/test_conclusion.py                spec「结论的公共部分」「持仓的结论」「关注的结论」
                                                  「候选的结论」「寻股的结论」「PM 写的交易方案」
                                                  「跟进申请的格式」
tests/contracts/test_trade_plan.py                不改，抽函数后照样通过
tests/modules/scheduler/builders.py               基准数据：基准投资论点、基准关注论点、基准方案条款、
                                                  基准跟进申请、结论的公共部分、R-1/R-2
tests/modules/scheduler/test_config.py            spec「研究要求的配置」的前两个场景
tests/modules/scheduler/domain/test_case_type.py  spec「触发」「案件类型」「开始运行时确定案件类型」
                                                  「事件要求的子 agent」
tests/modules/scheduler/domain/test_validation.py spec「结论的校验」和「改成 1 个或 3 个」
```

- 性质测试（Hypothesis）：
  - 对任意触发和状态，`define_case` 给出的类型针对的状态就是传入的状态（针对单只股票的类型），优先级和 spec 的表一致；`conclusion_model` 的种类和类型针对的状态一致
  - 研究要求是单调的：在一份通过研究要求的结论上多引用一个已完成的 Result，不会变成"研究不足"
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [论点更新整份重写，PM 可能无意中改掉其他项] → 案件记录保留更新前后两份；复查日和截止日期的取值由「复查日与催化剂」检查
- [事件要求的子 agent 只看派没派过任务，PM 可以派一个敷衍的任务] → 下限只是兜底；开仓和升级另有"2 个子 agent 的完成 Result"
- [开始时状态变了就不运行，可能少一次检查] → 状态变化来自成交或另一个案件，新状态有自己的复查日、候选评估或卖出复盘
- [结论分成四种模型，PM 的 schema 要按案件类型选] → `conclusion_model` 给出，M5 接线时用它；校验的第 1 项兜底
- [`check_plan_fields` 抽出后，`TradePlan` 的校验多了一层调用] → 规则和错误信息不变，`test_trade_plan.py` 照样通过

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
