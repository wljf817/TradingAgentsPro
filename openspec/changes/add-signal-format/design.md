# Design

## Context

现状（M2 进行中，M3 完成了股票池、调度器和触发三组）：

- `contracts/conclusion.py` 里有四处整数评分：持仓、关注、候选的结论各一个 `score: int = Field(ge=-2, le=2)`，寻股加入的候选 `PoolAddition` 一个。调度器的结论校验不读评分
- 股票池的 `record_score(symbol, score: int)` 只收 -2 到 +2 的整数，最新评分存在 `PoolEntry.score`；收盘检查按最新评分排候选评估、判断候选是否不足（`score >= 0`、`score >= 1`）
- `contracts/agent.py` 有子 agent 的身份 `SubAgent`；还没有 Agent 网关模块，也没有 Task、Result、信号的任何类型
- case-stages spec 写明：结论本身格式不合法由 Agent 网关校验输出格式时处理，不是调度器的"校验不通过"
- contracts 的写法：Pydantic 模型 `frozen=True, extra="forbid"`，带文字的加 `str_strip_whitespace=True`；格式错误是构造时的 `ValidationError`。和上下文有关的检查放在模块的 domain，拒绝原因是 `StrEnum`、作为返回值
- 纯区域里只有 contracts 和 config 能用 pydantic，domain 不能 import 它
- M3 只写纯逻辑和测试：不接外部系统，不建表、不加迁移、不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/signal-format/spec.md 和 specs/case-types/spec.md。

### 模块边界

```
contracts
  signal.py      EntryId、Signal、NoSignal、SubAgentOutput（新增）
                 <- 子 agent 的输出（M5 解析）、PM 的结论
                 -> 任务派发（Result 的输出）、M6 的评估
  conclusion.py  HoldingConclusion、WatchConclusion、CandidateConclusion、PoolAddition：
                 score: int 改为 signal: Signal

modules/gateway（新增）
  domain/signal.py  SignalRejection、signal_rejection、conclusion_signal_rejection
  config.py         GatewayConfig
  __init__.py       导出公开的名字
```

本次新增 Agent 网关一个模块，改 contracts 的结论，不改其他模块的代码。跨模块传递的模型：

- 传入：`Signal | NoSignal`（子 agent 的输出，M5 用 `SubAgentOutput` 解析）；PM 的四种结论（M5 用调度器的 `conclusion_model` 解析），都是 contracts 的 Pydantic 模型
- 传出：`SignalRejection`，本模块的 `StrEnum`，由包根导出；任务派发把它写进 Result，M5 把它退回 PM
- 调度器、股票池和收盘检查的代码不变：结论校验不读信号；股票池仍然只收整数评分，由 M4 取 `signal.score` 传入

## Goals / Non-Goals

**Goals:**

- 格式只写一处：结构由 contracts 的模型保证，依赖配置和"现在"的两项由网关的纯函数检查；子 agent 和 PM 共用同一个 `Signal`
- spec 每张表的每一行都有测试

**Non-Goals:**

按「编程风格」省略的情况：

- 引用的条目不存在、不在本案件的快照里、或者同一条引用两次：不检查
- 数据截至时刻早得不合理（比如几年前）：不检查，只查不晚于现在
- 理由、主要风险、原因的长度，置信度的小数位数：不限制
- 评分或评估窗口写成 `true`、`"1"`、`1.0`：按 Pydantic 的宽松模式转成整数，不另外拒绝；`1.5` 这样带小数的照样是格式错误
- 寻股里哪只候选的信号不通过：只给原因，不指明是哪一只
- PM 的信号和结论的决定不一致（比如评分 -2 却加仓）：不检查
- 评估窗口的选项里有重复的值：不检查
- 一个 Task 评估的是板块或大盘、不是单只股票：不考虑

## Decisions

### D1 信号和无信号放进 contracts

```python
type EntryId = Annotated[str, Field(min_length=1)]  # 状态库条目的 ID，格式 M4 再定


class Signal(BaseModel):
    """一个信号：子 agent 的输出，或者 PM 结论里对一只股票的信号。

    评估窗口是否在配置的选项里、数据截至时刻是否晚于现在，由 Agent 网关在收到输出时检查。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    horizon: int = Field(ge=1)  # 评估窗口，交易日数
    score: int = Field(ge=-2, le=2)  # 评分，含义见 spec「评分的刻度」
    confidence: Decimal = Field(ge=0, le=1)  # 置信度
    data_as_of: AwareDatetime  # 数据截至时刻
    cited_entries: tuple[EntryId, ...] = Field(min_length=1)  # 引用的状态库条目
    rationale: str = Field(min_length=1)  # 理由
    risks: str = Field(min_length=1)  # 主要风险


class NoSignal(BaseModel):
    """无信号：根据提供的数据给不出判断。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1)  # 为什么给不出判断
    cited_entries: tuple[EntryId, ...] = ()  # 看过的状态库条目，可以为空


# 子 agent 的输出。两种的必填字段不重叠，加上 extra="forbid"，联合类型分得清
type SubAgentOutput = Signal | NoSignal
```

- 放 contracts：子 agent 的输出（任务派发、M5）、PM 的结论（`contracts/conclusion.py`）、M6 的评估都要用，模块之间不能 import
- 置信度用 `Decimal`：比例一律用 `Decimal`；JSON 里的 `0.6` 解析出来是 `Decimal("0.6")`（已确认）
- 条目 ID 只要求非空：状态库条目的 ID 要到 M4 定快照时才定。`tuple` 里的每个 ID 也会去掉首尾空白再检查（已确认）
- 评估窗口在模型里只要求正整数：选项依赖配置，由网关检查（D4），已经写入的信号读回时不再查选项
- `frozen=True` 就是"写入后不能修改"：改任何字段都报 `ValidationError`（已确认）
- 两种输出用不带标签的联合类型：已确认 Pydantic 对合法的信号和无信号各自解析出对的类型，两种都不合法时报错，错误的位置前面带着 `Signal` 或 `NoSignal`
- 考虑过：两种都加一个 `kind` 标签字段，做成带标签的联合类型。没有采用：PM 结论里的信号也要写一个永远是 `signal` 的标签；带标签的联合类型要求输入里必须有标签，模型漏写标签整份就不合法
- 考虑过：一个模型加"无信号"标志，其他字段都可以不写。没有采用：七个字段变成可选，哪些在无信号时必须为空要另写检查；PM 的信号的 schema 里会出现无信号

### D2 评分的刻度只写在 spec 和提示里

- 代码只检查评分是 -2 到 +2 的整数。每档的名字和含义（spec「评分的刻度」）由 M5 写进系统宪章：宪章是人工编写、带版本号、改动要人工确认的（Proposal「共享上下文」）
- 评分用 `int`，不用 `IntEnum`：股票池、收盘检查都把评分当整数比较和排序，枚举不多带来任何检查
- 考虑过：在 contracts 里放一张刻度表（名字、含义），M5 用它生成提示。没有采用：宪章由人编写和确认，代码里再存一份就有两个来源；本变更没有代码用到这些文字

### D3 结论里的整数评分改为信号

```python
class HoldingConclusion(_ConclusionBase):
    """持仓的结论，见 spec「持仓的结论」。"""

    signal: Signal  # PM 对这只股票的信号；信号里的评分记为股票池的最新评分
    decision: Literal[Decision.HOLD, Decision.ADD, Decision.REDUCE, Decision.CLOSE]


class PoolAddition(BaseModel):
    """寻股加入的一只候选。加入是否通过由股票池检查，来源是 PM 探索。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1)
    reason: str = Field(min_length=1)  # 理由与论点草稿
    signal: Signal  # PM 对这只候选的信号
```

（关注、候选的结论同样把 `score: int` 换成 `signal: Signal`，其余字段和 validator 不变，上面只写了两处。）

- 字段改名为 `signal`：`score` 现在是信号里的评分，`conclusion.score.score` 读不通
- 类型是 `Signal`，不是 `SubAgentOutput`：PM 的信号不能是无信号（proposal「需要人决定」第 2 条），无信号的输入在构造结论时就是格式错误
- 不放进 `_ConclusionBase`：寻股的结论没有自己的信号，每只加入的候选各有一个
- 信号自带理由、风险和引用，不借用结论的 `rationale` 和 `cited_results`（第 7 条）
- 结论的 validator 不变：信号和决定之间没有约束
- 考虑过：保留 `score: int`，在结论里另加置信度、评估窗口等字段。没有采用：和子 agent 的信号不是同一个模型，M6 评估时要分两种读；寻股的每只候选也要再加一遍

### D4 收到输出时的校验

```python
class SignalRejection(StrEnum):
    """格式正确的信号不通过的原因，按 spec「收到输出时的校验」的顺序。"""

    HORIZON_NOT_OFFERED = "horizon_not_offered"  # 评估窗口不在配置的选项里
    DATA_AFTER_NOW = "data_after_now"  # 数据截至时刻晚于现在


def signal_rejection(
    output: SubAgentOutput, now: datetime, config: GatewayConfig
) -> SignalRejection | None:
    """给出第一项不通过的原因；无信号或者都通过时为 None。now 是收到输出的时刻。"""
    if isinstance(output, NoSignal):
        return None
    if output.horizon not in config.signal_horizons:
        return SignalRejection.HORIZON_NOT_OFFERED
    if output.data_as_of > now:
        return SignalRejection.DATA_AFTER_NOW
    return None


def conclusion_signal_rejection(
    conclusion: Conclusion, now: datetime, config: GatewayConfig
) -> SignalRejection | None:
    """按顺序检查 PM 结论里的每个信号，给出第一个不通过的原因；都通过时为 None。"""
    for signal in _signals(conclusion):
        rejection = signal_rejection(signal, now, config)
        if rejection is not None:
            return rejection
    return None


def _signals(conclusion: Conclusion) -> tuple[Signal, ...]:
    """持仓、关注、候选的结论一个；寻股按加入的顺序，每只候选一个。"""
    if isinstance(conclusion, ExplorationConclusion):
        return tuple(addition.signal for addition in conclusion.additions)
    return (conclusion.signal,)
```

- 格式不合法有两个来源：格式错误由 Pydantic 构造模型时给出（`ValidationError` 的每条错误指出是哪一项、为什么）；这里的两项只对已经构造好的信号检查。domain 不能 import pydantic，所以纯函数只接收模型，不接收原始输出
- 拒绝作为返回值，不抛异常：和股票池、调度器、跟进审批一样，这是模型输出的正常结果
- 带时区的时刻直接比较，和本机时区无关；`now` 由调用方从 Clock 读取后传入
- 无信号也能传入，直接返回 None：任务派发拿到的输出是两者之一，不用先分开
- 不放进 contracts 的 validator：两项依赖配置和"现在"；已经写入的信号在改了配置以后读回时不能再查（spec「改配置不影响已经写入的信号」）
- 考虑过：用 Pydantic 的 validation context 把选项和"现在"传进模型的 validator。没有采用：有 context 才检查、没有就不查，同一个模型两种行为；PydanticAI 能不能传 context 要到 M5 才确认
- 考虑过：本变更就提供"原始输出 → 信号、无信号，或者格式不合法和原因"的函数。没有采用：模型输出以什么形式到达（文本、已经解析的工具参数、PydanticAI 的输出类型）要到 M5 才定；任务派发在同一个模块，需要时在它的 service 里把 Pydantic 的错误和这里的原因合起来
- 考虑过：把 PM 信号的检查放进调度器的结论校验。没有采用：选项在网关的配置里；格式不合法和结论校验不通过是两回事，case-stages spec 已经把前者交给 Agent 网关

### D5 配置

```python
class GatewayConfig(BaseModel):
    """Agent 网关的阈值，含义见 signal-format spec「信号的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 子 agent 和 PM 出信号时可以选的评估窗口，交易日数
    signal_horizons: tuple[PositiveInt, ...] = Field(default=(3, 5, 20, 60), min_length=1)
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 评估窗口的选项 | 3、5、20、60 个交易日 | Proposal 规定 |

- 至少一个、每个是正整数：没有选项的话任何信号都不合法；0 个交易日的窗口没法评估
- 以后的任务派发（任务数上限）、备用模型、调用预算往这里加字段。M4 由 bootstrap 从配置文件读取，测试里直接构造

### D6 后续的接法（给任务派发、M4、M5 的对照）

本变更不调用其他模块，下表是之后的接法：

| 什么时候 | 做什么 |
| --- | --- |
| 子 agent 返回输出（任务派发、M5） | 用 `SubAgentOutput` 解析：`ValidationError` 记为失败并注明格式不合法，原因是每条错误的位置和说明；解析出来以后 `signal_rejection(output, now, config)`，不是 None 也记为失败并注明格式不合法和这个原因；都通过时信号记为完成、无信号记为无信号。Result 的"引用的条目 ID"取自输出的 `cited_entries`。格式不合法不切换备用模型 |
| PM 提交结论（M5） | 用 `conclusion_model(case_type)` 解析，格式错误怎样退回 PM 由 M5 定；解析出来以后 `conclusion_signal_rejection(conclusion, now, config)`，不是 None 同样是格式不合法；都通过以后才交给调度器的 `conclusion_rejection` |
| 结论通过以后（M4） | 持仓、关注、候选的结论：股票池 `record_score(symbol, conclusion.signal.score)`；寻股的每只候选加入以后 `record_score(addition.symbol, addition.signal.score)`。case-types design D9 的表里写的 `score` 就是 `signal.score` |
| 组提示（M5） | 评分刻度写进系统宪章；`config.signal_horizons` 告诉模型可以选的评估窗口 |

### D7 包根导出和纯区域

- 包根导出 `GatewayConfig`、`SignalRejection`、`signal_rejection`、`conclusion_signal_rejection`。纯函数导出是为了测试只从包根 import。没有 service 和 ports：本变更只有纯函数，任务派发加存储时再加
- domain 只用 `datetime`、`enum`，以及 contracts 的 `signal`、`conclusion` 和本模块的 config；`contracts/signal.py` 只用 `decimal`、`typing` 和 pydantic；config 只用 pydantic。都在白名单里，不改 `tests/architecture/rules.py`
- 模块里只有 domain 和 config，module-layers 契约自动覆盖新模块，不用改 import-linter 的配置

### D8 测试

```
tests/contracts/test_signal.py                     signal-format spec「信号的格式」「评分的刻度」「无信号」
tests/contracts/test_conclusion.py                 case-types spec「结论的公共部分」「寻股的结论」改为信号
tests/modules/scheduler/builders.py                公共部分的 score 改为基准信号
tests/modules/scheduler/domain/test_validation.py  PoolAddition 的 score 改为 signal
tests/modules/gateway/builders.py                  基准信号、基准无信号、现在 2026-10-14 15:00Z；
                                                   signal(**changes) 只改基准信号的几项
tests/modules/gateway/test_config.py               「信号的配置」的「默认配置」「配置不合法」
tests/modules/gateway/domain/test_signal.py        「收到输出时的校验」「结论里的信号」「改成别的选项」
```

- 基准信号在 `tests/contracts/test_signal.py`、`tests/contracts/test_conclusion.py`、两个 builders 里各写一份，和现在的基准投资论点一样，测试目录之间不互相 import
- 性质测试（Hypothesis）：任意格式正确的信号和无信号，按 JSON 写出再用 `SubAgentOutput` 读回，得到同样类型、相等的对象。这是 M4 存进状态库再读回的前提，也说明联合类型不会把一种认成另一种
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [两种输出都不合法时，Pydantic 的错误把信号和无信号两边的问题都列出来，原因比较长] → 每条错误的位置都带着是哪一种的哪一项，写进 Result 和程序日志读得懂；子 agent 格式不合法不重试，原因只给人看
- [评分按相对板块 ETF 的超额收益定义，模型可能按绝对涨跌给分] → M5 的提示和宪章写明口径；M6 按超额收益评估，偏差会体现在命中率上
- [不检查引用的条目，模型可以编 ID] → M4 定了条目 ID 以后可以对照快照；复盘能发现
- [PM 每个结论要写两份理由（结论的和信号的）] → 两者回答的问题不同（proposal 第 7 条）；多花的 token 很少
- [`score` 改名为 `signal` 是 contracts 的破坏性修改] → 只有 contracts 和测试用到；调度器、股票池、收盘检查的代码不变

## Migration Plan

只改 contracts 和新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
