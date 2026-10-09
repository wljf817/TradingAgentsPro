# Design

## Context

现状（M3 完成了股票池、调度器、触发三组和 Agent 网关的信号格式、任务派发）：

- Agent 网关模块 `modules/gateway/`：`GatewayConfig`（评估窗口的选项、派发的四个阈值，配置项之间没有约束）；domain 的 `signal.py`（信号的两项检查）、`dispatch.py`（派发规则）、`result.py`（`received`、`call_failed`、`expired`、`rejected`）；`ports.py` 的 `TaskStore`；`service.py` 的 `TaskDispatchService`（`receive(task_id, output, cost)`、`fail(task_id, detail, cost)`、`expire`）
- contracts：`SubAgent`（三个子 agent）、`Dispatcher`（PM、Critic）、`Task`、`Result`（还没有实际使用的模型；`TaskFailure.CALL_FAILED` 的注释写着"什么时候算由「备用模型」规定"）、`Decision` 和四种结论（`contracts/conclusion.py`）。还没有六个 agent 的身份，也没有模型的类型
- 健康监控 `HealthSignals` 有 `pm_model_available`、`critic_model_available` 两个布尔值；`HealthService` 把状态放在内存里，新建以后是正常，进程重启以后从头算
- 调度器的案件状态：`reject_conclusion(config)` 不带原因，只数重交次数；`critic_unavailable()`、`fail()` 处理 Critic 不可用和调用失败
- M3 只写纯逻辑和测试：不接外部系统，不建表、不加迁移、不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/model-fallback/spec.md 和两份修改的 delta spec。

### 模块边界

```
contracts
  agent.py        Agent（新增：六个 agent）；SubAgent（不改）
  model.py        ModelRef、ModelUse（新增）
                  -> ModelUse：Result、M4 的案件记录、M6 的按模型评估
  task.py         Result 加 model_use（修改）
  conclusion.py   Decision、Conclusion（不改；备用模型的限制读它）

modules/gateway
  config.py           AgentModels、ModelAssignments（新增）；GatewayConfig 加七个配置项和一项交叉检查
  domain/fallback.py  CallError、Breaker、ModelCall、Unavailable、ModelSwitch、Attempt、AllUnavailable、
                      DeadlineReached；起步、失败以后、尝试的超时、还有可用的模型、备用模型的限制（新增）
  domain/result.py    received 多收实际使用的模型（修改）
  service.py          ModelFallbackService（新增）；TaskDispatchService.receive 多收实际使用的模型（修改）
  __init__.py         导出新的名字
```

本次只改 Agent 网关一个模块的代码和 contracts；case-stages spec 只改措辞，调度器的代码不改。跨模块传递的模型：

- 传入：`ModelAssignments`（本模块 config 的 Pydantic 模型，M4 的 bootstrap 从配置文件构造）；`CallError`（M5 把调用异常归成它）；`Conclusion`（contracts）；案件 ID 和 `Agent`
- 传出：`ModelUse`（contracts 的 Pydantic 模型）写进 Result 和案件记录；`ModelSwitch`、`AllUnavailable`（本模块的 frozen dataclass，由包根导出）交给 M4 写日志和告警；"还有可用的模型"的布尔值交给健康监控的 `HealthSignals`；被限制的 `Decision` 交给 M4，M4 调用调度器的 `reject_conclusion`

## Goals / Non-Goals

**Goals:**

- 网关不调用模型：只告诉调用方下一步向哪个模型尝试、最多等多久，调用方报告每次尝试成功还是失败。M5 接上真正的调用时，规则不用改
- 规则都在 domain 里，是 frozen dataclass 和纯函数；service 只读当前时刻、查模型的配置、在内存里保存熔断状态和案件内的起点
- spec 每张表的每一行都有测试；状态机性质测试拿随机的调用、失败、成功、拨动时间和案件结束检查不变量

**Non-Goals:**

按「编程风格」省略的情况：

- 被截止时间截短的那次尝试超时：照样算一次失败
- 并行的调用：一次调用开始以后起点再往后移，不影响它，它仍从原来的位置往后试
- 进程重启时正在运行、已经切到备用模型的案件：剩下的调用从主模型开始
- 调用方忘了告诉网关案件结束，或者案件结束以后才报告成功：起点留在内存里，进程重启时清掉
- 同一次尝试重复报告失败或成功、报告别的实例的 `ModelCall`：不检查，调用方保证
- 运行中改配置：不支持，配置在启动时读取
- 厂商的别名（比如同一家公司的两个名字）：不识别，按写的厂商比较
- 冷却中收到的成功：直接恢复正常，网关分不出这次调用是不是冷却以前开始的
- PM 用备用模型时不能提的决定配成某种结论的全部决定：不检查，PM 只能重交到结论无效
- PM 回应质询时才切到备用模型、修改方案的条款：不限制，决定在提交结论时已经定了
- Critic、子 agent 和 Reviewer 用备用模型：不限制结论
- 时钟倒退：不处理

## Decisions

### D1 agent 和模型的身份放进 contracts

```python
class Agent(StrEnum):
    """调用模型的 agent，每个各配一套模型。子 agent 的值和 SubAgent 相同。"""

    PM = "pm"
    NEWS = "news"
    QUANT = "quant"
    FUNDAMENTALS = "fundamentals"
    CRITIC = "critic"
    REVIEWER = "reviewer"
```

```python
class ModelRef(BaseModel):
    """一个模型：厂商和模型名都相同就是同一个模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # 模型出自哪家公司，不是调用的平台；转成小写，厂商校验按它比较
    vendor: Annotated[str, StringConstraints(to_lower=True, min_length=1)]
    name: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"{self.vendor}/{self.name}"


class ModelUse(BaseModel):
    """实际使用的模型，以及它是不是这个 agent 的备用模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelRef
    fallback: bool  # 不是这个 agent 的主模型
```

- `ModelUse` 放 contracts：Result 在 contracts，案件记录（M4）和按模型的评估（M6）都要读
- `Agent` 放 contracts：配置的键、M4 和 M5 调用网关时都用它；子 agent 的任务用 `Agent(task.target)` 转过来，值相同
- `ModelRef` 是 frozen 的 Pydantic 模型，可以做 dict 的键：熔断状态按模型存
- 厂商转成小写（proposal 第 9 条）：只多一个约束，避免 "OpenAI" 和 "openai" 通过厂商校验。模型名大小写不变，厂商的接口区分大小写
- 考虑过：`Agent` 合并 `SubAgent` 和 `Dispatcher`。没有采用：任务派发靠这两个枚举只有三个、两个值来保证"只能派给子 agent""只有 PM 和 Critic 能派"

### D2 模型的配置和厂商校验

```python
class AgentModels(BaseModel):
    """一个 agent 的主模型和按顺序的备用模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: ModelRef
    fallbacks: tuple[ModelRef, ...] = ()

    @property
    def chain(self) -> tuple[ModelRef, ...]:
        """按尝试的顺序：主模型在前，备用模型按配置的顺序。"""
        return (self.primary, *self.fallbacks)

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        if len(set(self.chain)) != len(self.chain):
            raise ValueError("同一个 agent 的主模型和备用模型不能重复")
        return self


class ModelAssignments(BaseModel):
    """六个 agent 各自的模型，见 model-fallback spec「模型的配置」「厂商校验」。没有默认值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: Mapping[Agent, AgentModels]

    def of(self, agent: Agent) -> AgentModels:
        return self.agents[agent]

    @model_validator(mode="after")
    def _check(self) -> Self:
        missing = set(Agent) - set(self.agents)
        if missing:
            raise ValueError(f"缺少这些 agent 的模型：{'、'.join(sorted(missing))}")
        shared = _vendors(self.agents[Agent.CRITIC]) & _vendors(self.agents[Agent.PM])
        if shared:
            raise ValueError(f"Critic 和 PM 不能用同一家厂商的模型：{'、'.join(sorted(shared))}")
        return self


def _vendors(models: AgentModels) -> set[str]:
    return {model.vendor for model in models.chain}
```

- 厂商校验写成配置的 validator：bootstrap 构造配置时就报错，"启动时校验、不满足就拒绝启动"不需要另外的函数和调用，测试也只要构造配置
- 和 `GatewayConfig` 分开：阈值有默认值，测试里 `GatewayConfig()` 就能用；模型是部署的数据，没有合理的默认值，单独作为 `ModelFallbackService` 的参数
- 同一个 agent 的模型不能重复（proposal 第 9 条）：切换到同一个模型没有意义，起点按位置记（D6）也要求不重复
- 考虑过：六个 agent 写成六个字段。没有采用：按 `Agent` 取模型要再写一张对照表

### D3 一次调用的步骤

一次调用由调用方驱动：先 `begin`，按返回的步骤去尝试，每次尝试的结果报告给网关，网关给出下一步。

```python
class CallError(StrEnum):
    """调用错误，见 spec「什么算不可用」。M5 把调用异常归成其中一种。"""

    TIMEOUT = "timeout"  # 超时
    CONNECTION = "connection"  # 连接失败
    RATE_LIMITED = "rate_limited"  # 限流
    QUOTA_EXHAUSTED = "quota_exhausted"  # 额度或余额用完：厂商返回的，不是本系统的调用预算
    AUTH_FAILED = "auth_failed"  # 认证失败
    SERVER_ERROR = "server_error"  # 服务端出错或过载
    OTHER = "other"  # 其他调用错误


@dataclass(frozen=True, slots=True)
class Unavailable:
    """一次调用里不可用的一个模型。error 是最后一次尝试的调用错误；冷却中被跳过时为 None。"""

    model: ModelRef
    error: CallError | None


@dataclass(frozen=True, slots=True)
class ModelSwitch:
    """一次切换，调用方写进程序日志。error 和 Unavailable 的相同。"""

    case_id: UUID
    agent: Agent
    from_model: ModelRef
    to_model: ModelRef
    error: CallError | None
    at: datetime


@dataclass(frozen=True, slots=True)
class ModelCall:
    """一次调用进行到哪里：正在向 chain[index] 做第 attempt 次尝试。

    - deadline：子 agent 任务的截止时间；PM、Critic、Reviewer 没有
    - unavailable：这次调用里已经不可用的模型，按先后
    """

    case_id: UUID
    agent: Agent
    chain: tuple[ModelRef, ...]
    index: int
    attempt: int
    deadline: datetime | None
    unavailable: tuple[Unavailable, ...] = ()

    @property
    def model(self) -> ModelRef:
        return self.chain[self.index]


@dataclass(frozen=True, slots=True)
class Attempt:
    """下一步：向 call.model 发一次请求，最多等 timeout。switches 是走到这一步经过的切换。"""

    call: ModelCall
    timeout: timedelta
    switches: tuple[ModelSwitch, ...] = ()


@dataclass(frozen=True, slots=True)
class AllUnavailable:
    """起点和它后面的模型都不可用：调用失败，告警的依据。"""

    case_id: UUID
    agent: Agent
    unavailable: tuple[Unavailable, ...]
    switches: tuple[ModelSwitch, ...] = ()

    @property
    def detail(self) -> str:
        """调用失败的说明，比如"模型都不可用：anthropic/p-1 超时；anthropic/p-2 冷却中"。"""
        reasons = (f"{u.model} {_REASONS[u.error]}" for u in self.unavailable)
        return "模型都不可用：" + "；".join(reasons)


@dataclass(frozen=True, slots=True)
class DeadlineReached:
    """到了任务的截止时间：不再尝试，任务由 task-dispatch 记超时。"""

    call: ModelCall


type CallStep = Attempt | AllUnavailable | DeadlineReached
```

（`_REASONS` 是调用错误和 None 到"超时""冷却中"等文字的对照表，上面省略。）

- 网关返回步骤、不自己调用（Goals 第一条）：M5 的调用是异步的、在 DBOS 的 step 里，网关保持同步的纯逻辑，测试里直接驱动
- 三种结束分开：全部不可用要给出调用失败并告警；到了截止时间什么都不做，由 M4 在截止时间记超时（proposal 第 3 条）。合成一种的话，截止时间到了也会告警
- 切换的事件放在步骤里一起返回：调用方拿到下一步时顺手写日志，网关不用另外的事件队列
- 被跳过的模型也记进 `unavailable`、原因是冷却中：说明里能看到为什么没试它，告警时一目了然
- 调用错误不分种类处理（proposal 第 8 条），种类只用于说明和日志
- 考虑过：调用方把整次调用交给网关、网关回调调用函数。没有采用：网关要变成异步、要懂超时的执行，纯区域放不下

### D4 熔断

```python
@dataclass(frozen=True, slots=True)
class Breaker:
    """一个模型的熔断状态，见 spec「熔断」。状态变化的方法都返回新的状态。

    - failures：上次成功以来连续不可用的时刻，只留最近"连续不可用次数"个
    - cooling_until：冷却结束的时刻；冷却结束以后、还没成功时也保留，这时再不可用就直接再冷却
    """

    failures: tuple[datetime, ...] = ()
    cooling_until: datetime | None = None

    def cooling(self, now: datetime) -> bool:
        """冷却中：现在早于冷却结束的时刻。"""
        return self.cooling_until is not None and now < self.cooling_until

    def succeeded(self) -> Self:
        """成功：不论在哪个状态，都回到正常、清零。"""
        return type(self)()

    def failed(self, now: datetime, config: GatewayConfig) -> Self:
        """在 now 不可用了一次。"""
        if self.cooling_until is not None:
            if now < self.cooling_until:
                return self  # 冷却中：不变
            return type(self)(cooling_until=now + config.breaker_cooldown)  # 冷却结束：再冷却
        failures = (*self.failures, now)[-config.breaker_failures :]
        if len(failures) == config.breaker_failures and now - failures[0] <= config.breaker_window:
            return type(self)(cooling_until=now + config.breaker_cooldown)
        return type(self)(failures=failures)
```

- 三种状态不存成枚举：冷却中和冷却结束由现在和 `cooling_until` 推出来，和健康监控一样，状态只在事件时变化，时间流逝不用推进
- 数"不可用"，不数单次尝试（proposal 第 5 条）；成功（包括格式不合法的输出）清零
- 冷却结束以后保留 `cooling_until`，第一次不可用就直接再冷却（proposal 第 5 条）；成功才清掉
- 只留最近 N 个时刻：判断只看最近连续的 N 次，内存不随失败次数增长
- 按模型存（proposal 第 5 条）：PM 和 Reviewer 共用 p-1 时一起数，厂商故障通常是一个模型整体不可用
- 考虑过：冷却结束以后清零、重新数满 N 次。没有采用：故障没恢复时，每次冷却结束都要再赔 N 次调用的重试和等待

### D5 起步和失败以后

```python
_SUB_AGENTS: Final = frozenset({Agent.NEWS, Agent.QUANT, Agent.FUNDAMENTALS})
_NORMAL: Final = Breaker()


def start(
    case_id: UUID,
    agent: Agent,
    chain: tuple[ModelRef, ...],
    first: int,
    deadline: datetime | None,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
) -> CallStep:
    """一次调用的第一步：从起点 chain[first] 开始，跳过冷却中的模型。"""
    return _try_from(ModelCall(case_id, agent, chain, first, 1, deadline), breakers, now, config)


def exhausted(call: ModelCall, config: GatewayConfig) -> bool:
    """这次尝试是这个模型在这次调用里的最后一次：失败就不可用。"""
    return call.attempt > config.call_retries


def after_failure(
    call: ModelCall,
    error: CallError,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
) -> CallStep:
    """正在进行的尝试失败以后的下一步：到了截止时间就停止；还能重试就重试；否则换下一个模型。"""
    if call.deadline is not None and now >= call.deadline:
        return DeadlineReached(call)
    if not exhausted(call, config):
        retry = replace(call, attempt=call.attempt + 1)
        return Attempt(retry, attempt_timeout(retry, now, config))
    return _move_past(call, error, breakers, now, config, ())


def attempt_timeout(call: ModelCall, now: datetime, config: GatewayConfig) -> timedelta:
    """这次尝试的超时：子 agent 不超过离截止时间还剩的时间。"""
    if call.agent not in _SUB_AGENTS:
        return config.call_timeout
    limit = config.subagent_call_timeout
    return limit if call.deadline is None else min(limit, call.deadline - now)


def _try_from(
    call: ModelCall,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
    switches: tuple[ModelSwitch, ...] = (),
) -> CallStep:
    """向 call.model 尝试；它在冷却中就跳过、换下一个。"""
    if call.deadline is not None and now >= call.deadline:
        return DeadlineReached(call)
    if breakers.get(call.model, _NORMAL).cooling(now):
        return _move_past(call, None, breakers, now, config, switches)
    return Attempt(call, attempt_timeout(call, now, config), switches)


def _move_past(
    call: ModelCall,
    error: CallError | None,
    breakers: Mapping[ModelRef, Breaker],
    now: datetime,
    config: GatewayConfig,
    switches: tuple[ModelSwitch, ...],
) -> CallStep:
    """call.model 在这次调用里不可用了（error 为 None 时是冷却中被跳过），换到下一个模型。"""
    gone = (*call.unavailable, Unavailable(call.model, error))
    if call.index + 1 == len(call.chain):
        return AllUnavailable(call.case_id, call.agent, gone, switches)
    following = replace(call, index=call.index + 1, attempt=1, unavailable=gone)
    switch = ModelSwitch(call.case_id, call.agent, call.model, following.model, error, now)
    return _try_from(following, breakers, now, config, (*switches, switch))
```

- 截止时间先看（spec「尝试的超时」）：到了截止时间，不论还能不能重试、后面还有没有模型，都停止，不告警
- 重试不看熔断状态：重试中途这个模型被别的调用熔断，这次照样重试完，规则简单；换模型时才看
- 熔断状态由 service 在调用 `after_failure` 以前更新（D7）：`after_failure` 只给下一步，`exhausted` 两边共用，不会算得不一致
- 一次换一个、每越过一个模型一个切换的事件（spec「切换的事件」）

### D6 案件内的起点

service 用 `dict[tuple[UUID, Agent], int]` 记每个案件、每个 agent 的起点在 `chain` 里的位置，没有记录时是 0（主模型）：

- 调用成功时 `max(原来的起点, call.index)`：只往后移，并行调用里后成功的主模型不会把起点拉回去（proposal 第 7 条）
- 全部不可用、到了截止时间不改起点：没有成功的模型，对话里没有它的输出
- 记位置不记模型：配置运行中不变，位置直接比较先后；`ModelUse.fallback` 就是位置大于 0
- 放在内存里（proposal 第 6 条），`end_case` 清掉一个案件的所有起点。一个案件六个 agent 最多六条，忘了清也不多
- 考虑过：起点交给调用方（M4 的工作流）保存、每次调用传进来。没有采用：调用方要多带一份状态，PM、Critic 的调用和子 agent 的任务都要传；重启丢起点的情况极少见
- 考虑过：从本案件 Result 的模型推出起点。没有采用：PM、Critic、Reviewer 的调用没有 Result

### D7 用例

```python
class ModelFallbackService:
    def __init__(self, clock: Clock, models: ModelAssignments, config: GatewayConfig) -> None: ...

    def begin(self, case_id: UUID, agent: Agent, deadline: datetime | None = None) -> CallStep: ...

    def fail(self, call: ModelCall, error: CallError) -> CallStep: ...

    def succeed(self, call: ModelCall) -> ModelUse: ...

    def case_model(self, case_id: UUID, agent: Agent) -> ModelUse: ...

    def end_case(self, case_id: UUID) -> None: ...

    def has_available_model(self, agent: Agent) -> bool: ...

    def cooling(self) -> dict[ModelRef, datetime]: ...
```

```
begin：chain = models.of(agent).chain；first = 起点（没有时 0）
       start(case_id, agent, chain, first, deadline, breakers, now, config)
fail：exhausted(call) 时 breakers[call.model] = 原状态.failed(now)
      after_failure(call, error, breakers, now, config)
succeed：breakers[call.model] = 原状态.succeeded()
         起点 = max(起点, call.index)；返回 ModelUse(call.model, call.index > 0)
case_model：起点的模型，ModelUse(chain[起点], 起点 > 0)
end_case：删掉 case_id 的所有起点
has_available_model：chain 里有一个不在冷却中
cooling：所有 cooling(now) 的模型和 cooling_until
```

- 和 `HealthService` 一样状态放在内存里（proposal 第 6 条），没有存储端口；同一个实例由调用方串行调用
- `now` 都从 Clock 读：`fail`、`succeed` 的时刻就是收到结果的时刻
- `case_model` 给 M5 组 PM 的提示用（起点是备用模型时写明哪些决定不能提），也让「案件结束以后清掉」可以测试
- 和 `TaskDispatchService` 分开：一个管任务和 Result、有存储；一个管选模型、只在内存里。子 agent 的调用两边都用，由 M5 串起来（D10）

### D8 Result 记录实际使用的模型

```python
class Result(BaseModel):
    # ……原有的字段
    model_use: ModelUse | None = None  # 给出输出的模型：完成、无信号、格式不合法的失败才有

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        # ……原有的三项检查
        received = self.status in _OUTPUTS or self.reason is TaskFailure.FORMAT_INVALID
        if received != (self.model_use is not None):
            raise ValueError("完成、无信号和格式不合法的失败记下给出输出的模型，其他状态不记")
        return self
```

- `received(task, output, now, cost, model_use, config)`：截止时间以前收到时，完成、无信号、格式不合法的失败都带 `model_use`；晚于截止时间是超时，不带。`TaskDispatchService.receive(task_id, output, cost, model_use)` 原样传下去
- 调用失败、超时、被拒绝没有模型：没有输出，评估用不上；调用失败试过哪些模型写在说明里
- 嵌套一个 `ModelUse`：模型和是否为备用模型总是一起有、一起没有，一条检查就够；它就是 `succeed` 的返回值，原样写进去
- 考虑过：Result 平铺模型和是否为备用模型两个字段。没有采用：要多一条"两个字段同时有或同时没有"的检查

### D9 备用模型的限制

```python
def blocked_decision(
    conclusion: Conclusion, use: ModelUse, config: GatewayConfig
) -> Decision | None:
    """提交结论的调用用的是备用模型、决定在不能提的决定里时，给出这个决定；否则为 None。"""
    if not use.fallback or isinstance(conclusion, ExplorationConclusion):
        return None
    decision = conclusion.decision
    return decision if decision in config.fallback_blocked_decisions else None
```

- 放在网关：只有网关知道结论是哪个模型给的；和 `conclusion_signal_rejection` 一样是收到 PM 结论时的检查
- 返回被限制的决定，不另设原因的枚举：只有一种原因，M4 拼退回 PM 的消息时写上是哪个决定
- 按结论校验不通过处理（proposal 第 2 条）：M4 先调用这个函数，不是 None 就调用调度器的 `reject_conclusion`，不再做调度器的结论校验，免得 PM 按"研究不足"补研究以后仍被限制
- 只看提交结论的那次调用：起点只往后移，PM 在这之前用过备用模型的话，这次也一定是备用模型

### D10 配置和默认值

```python
class GatewayConfig(BaseModel):
    # ……原有的五项

    # 一个模型在一次调用里失败以后最多再试这么多次
    call_retries: NonNegativeInt = 1
    # News、Quant、Fundamentals 每次尝试最多等这么久，并且不超过离任务截止时间还剩的时间
    subagent_call_timeout: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0))
    # PM、Critic、Reviewer 每次尝试最多等这么久
    call_timeout: timedelta = Field(default=timedelta(seconds=180), gt=timedelta(0))
    # 最近连续这么多次不可用，最早一次到最后一次不超过时间窗口，就冷却
    breaker_failures: PositiveInt = 3
    breaker_window: timedelta = Field(default=timedelta(minutes=10), gt=timedelta(0))
    breaker_cooldown: timedelta = Field(default=timedelta(minutes=5), gt=timedelta(0))
    # PM 用备用模型时不能提的决定；为空时不限制
    fallback_blocked_decisions: frozenset[Decision] = frozenset(
        {Decision.BUY, Decision.ADD, Decision.PROMOTE}
    )

    @model_validator(mode="after")
    def _check_fallback_fits(self) -> Self:
        if (self.call_retries + 1) * self.subagent_call_timeout * 2 > self.task_timeout:
            raise ValueError("子 agent 的主模型和第一个备用模型的全部尝试放不进任务时限")
        return self
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 重试次数 | 1 | 偶发的超时和限流重试一次多半能好；再多就拖慢切换，换备用模型本身就是更好的重试 |
| 子 agent 的单次调用超时 | 60 秒 | 子 agent 一次调用通常在一分钟以内（task-dispatch 的 design D9）；主模型和第一个备用模型各 2 次尝试共 4 分钟，放得进 5 分钟的任务时限 |
| PM、Critic、Reviewer 的单次调用超时 | 180 秒 | 输出更长（结论、质询、复盘报告），常用推理；没有任务时限，宁可多等，也不把正常的慢调用当成不可用 |
| 熔断的连续不可用次数 | 3 | 每次不可用已经含一次重试，一两次可能是偶发；连续三次没有一次成功，模型多半出了问题 |
| 熔断的时间窗口 | 10 分钟 | "短时间内"：调用稀疏时隔几小时的失败不该凑在一起 |
| 冷却时间 | 5 分钟 | 限流的窗口通常一分钟左右，厂商故障多是几分钟到几小时；5 分钟以后试一次，代价是一次调用的重试 |
| PM 用备用模型时不能提的决定 | 开仓方案、加仓、升级 | Proposal 的例子加上加仓（proposal 第 1 条） |

- 交叉检查只保证主模型和第一个备用模型（proposal 第 3 条）：后面的备用模型在前面的很快失败时才轮得到，要求全部放得进的话超时只能设得很短
- 改 `GatewayConfig` 的 docstring：配置项之间有这一项约束
- M4 由 bootstrap 从配置文件读取，测试里直接构造

### D11 后续的接法（给 M4、M5、调用预算的对照）

本变更不调用其他模块，下表是之后的接法：

| 什么时候 | 做什么 |
| --- | --- |
| 启动（M4） | bootstrap 从配置文件构造 `ModelAssignments` 和 `GatewayConfig`，报错就拒绝启动；新建一个 `ModelFallbackService` |
| 调用一个 agent（M5） | `step = begin(case_id, agent, deadline)`；`Attempt` 时用 `step.call.model` 和 `step.timeout` 发请求，异常归成 `CallError` 交给 `fail(step.call, error)`，拿到输出交给 `succeed(step.call)` 得到 `ModelUse`；关掉厂商 SDK 自带的重试 |
| 子 agent 的输出（M5） | `TaskDispatchService.receive(task_id, output, cost, model_use)` |
| 全部不可用（M4、M5） | 子 agent：`TaskDispatchService.fail(task_id, step.detail, cost)`；PM、Reviewer：案件状态 `fail()`；Critic：`critic_unavailable()`；都告警，说明是 `step.detail` |
| 到了截止时间（M4） | 不做什么；持久化休眠睡到截止时间以后 `expire` |
| 切换（M4） | 每一步的 `switches` 写程序日志 |
| PM 提交结论（M4） | 先 `blocked_decision(conclusion, model_use, config)`，不是 None 时 `reject_conclusion`；是 None 再做调度器的结论校验 |
| 案件结束（M4） | `end_case(case_id)` |
| 健康判定（M4） | `has_available_model(Agent.PM)`、`has_available_model(Agent.CRITIC)` 填进 `HealthSignals`；`cooling()` 交给健康监控记录 |
| 案件记录（M4） | PM 的结论、Critic 的审核记下 `ModelUse` |
| 组 PM 的提示（M5） | `case_model(case_id, Agent.PM).fallback` 为真时写明不能提的决定 |
| 预算（调用预算） | 预算用完时在 `begin` 以前拒绝，不经过切换和熔断 |

- 网关的内存状态不进 DBOS 的工作流：一次调用的 `begin`、尝试、`fail`、`succeed` 都在同一个 step 里，step 的输出（输出、`ModelUse`）由 DBOS 记下

### D12 包根导出和纯区域

包根导出 `ModelFallbackService`、`AgentModels`、`ModelAssignments`、`CallError`、`Breaker`、`ModelCall`、`Unavailable`、`ModelSwitch`、`Attempt`、`AllUnavailable`、`DeadlineReached`、`CallStep`，以及 domain 的 `start`、`exhausted`、`after_failure`、`attempt_timeout`、`blocked_decision`。纯函数导出是为了测试只从包根 import。

- domain 只用 `collections.abc`、`dataclasses`、`datetime`、`enum`、`typing`、`uuid`，以及 contracts 的 `agent`、`model`、`conclusion` 和本模块的 config；`contracts/model.py` 只用 `typing` 和 pydantic。都在白名单里，不改 `tests/architecture/rules.py`
- config 用 contracts 的 `Agent`、`ModelRef`、`Decision`：config 可以 import contracts
- 模块里只有允许的几种文件，module-layers 契约自动覆盖，不改 import-linter 的配置

### D13 测试

```
tests/contracts/test_model.py                         ModelRef、ModelUse
tests/contracts/test_task.py                          Result 的模型（补充）
tests/modules/gateway/builders.py                     基准模型的配置、fallback_service()（补充）
tests/modules/gateway/test_config.py                  模型的配置、厂商校验、备用模型的配置（补充）
tests/modules/gateway/domain/test_fallback.py         熔断、起步和失败以后、超时、限制
tests/modules/gateway/domain/test_result.py           received 记下模型（补充）
tests/modules/gateway/test_fallback_service.py        model-fallback spec 其余场景
tests/modules/gateway/test_fallback_state_machine.py  状态机性质测试
tests/modules/gateway/test_service.py                 receive 多收模型（补充）
tests/modules/scheduler/domain/test_stage.py          「备用模型的限制也退回 PM 重交」（补充）
```

- service 测试用 `FakeClock`（2026-10-14 15:00Z）和 spec 的基准模型的配置；"p-1 不可用"写成辅助函数：连续报告两次失败
- 熔断的场景直接构造 `Breaker` 拨动时间，也通过 service 驱动一遍
- 状态机性质测试（Hypothesis 的 `RuleBasedStateMachine`）：两个案件，PM（三个模型）、Reviewer（和 PM 共用主模型、没有备用模型）、News（两个模型，带截止时间）；随机交错开始调用、报告失败（随机调用错误）、报告成功、拨动时间、结束案件；配置用较小的值（重试 1 次、2 次不可用就冷却、窗口 10 分钟、冷却 5 分钟）。每一步之后检查：
  - 每个案件每个 agent 的起点只往后移，结束案件以后回到 0
  - 每次调用里，同一个模型的尝试不超过重试次数加 1；模型的位置只往后走，不早于开始时的起点
  - 换到的模型（第 1 次尝试）在那一刻不在冷却中
  - 子 agent 的尝试超时大于 0，不超过离截止时间还剩的时间；到了截止时间只会是停止
  - `has_available_model` 和"chain 里有不在冷却中的模型"一致；冷却中的模型和按 spec 的表写的参考模型一致
  - 全部不可用的说明按先后列出起点以后的每个模型，各一次
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [状态在内存里，重启就丢] → 重启不频繁；丢了只是多试一次已经故障的主模型，然后照常切换和熔断（proposal 第 6 条）
- [60 秒对子 agent 可能偏紧，推理模型偶尔超过] → 超时以后重试一次、再换备用模型，任务仍在时限以内；M5 接入以后按实际耗时调整
- [PM 的主模型挂起时，一次调用最多等 6 分钟才换] → 熔断以后后面的案件直接跳过；案件侧不需要秒级，盘中风险由止损单和紧急风险退出兜底
- [限制默认打开，主模型故障期间错过开仓机会] → 只影响用到备用模型的案件；下一次事件或复查还会再看；可以配置为空（proposal 第 1 条）
- [熔断按模型计，厂商整体故障时每个模型要各自熔断] → 每个模型 3 次不可用就冷却；需要时再加按厂商的熔断
- [同一案件同一 agent 的并行调用可能混用模型] → 只出现在同一次派给同一个子 agent 的几个任务，每个任务本身是一段独立的对话

## Migration Plan

只新增代码和改 contracts 的 Result；Result 还没有写进数据库（M4），没有持久化的数据，不需要迁移。需要回滚时直接 revert。
