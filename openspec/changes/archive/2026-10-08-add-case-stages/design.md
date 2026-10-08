# Design

## Context

现状（M2 进行中，M3 完成了股票池、案件类型与结论、案件队列）：

- 调度器模块只有纯逻辑：`define_case` 和 `conclusion_model`（复盘案件为 None）、`conclusion_rejection`（拒绝作为返回值，注释写明"由「案件阶段」退回 PM"）、队列的 `queue_decision` 和 `emergency_decision`。队列的快照要调用方给出 `RunningCase.calling_models` 和 `PendingPlan.stage`（`PlanStage`：审核、等人工确认、等下单），注释写明由「案件阶段」给出
- contracts 有 `CaseType`、PM 的四种结论和 `PlanDraft`、`TradeAction`（`is_opening`）、`check_plan_fields`、风控结果 `RiskOutcome`（通过、调整、拒绝）
- 风控的再校验结果和首次检查同格式，拒绝就是作废；`release(plan_id)` 没有预留时什么都不做
- M2 的通知模块已经能发确认请求，确认结果有已确认、已拒绝、已超时、已作废；确认请求发不出去按超时处理；被风控调整的方案在消息里标明
- 订单状态机（`modules/execution/domain/order.py`）是本变更的写法参考：frozen dataclass，每个事件一个方法，返回新的对象，终态原样返回
- M3 只写纯逻辑和测试：不接外部系统，不建表，不写 `repo.py`；模块之间互不 import，跨模块的流程留给 M4 的工作流

动机见 proposal.md 的 Why，要求见 specs/case-stages/spec.md。

### 模块边界

```
contracts（Pydantic 模型和枚举，跨模块传递）
  case.py     + CaseEndReason
              -> 案件记录（M4）、收盘检查、复盘和反事实评估（M6）
  review.py   Verdict、CriticReview、ResponseDecision、PlanResponse
              <- Critic 和 PM 的输出（M5，经 Agent 网关校验格式）
              -> 调度器（本变更）、案件记录（M4）

modules/scheduler
  domain/stage.py  CaseStage、CriticOutcome、CaseState、new_case
  config.py        SchedulerConfig 增加 max_resubmissions、max_challenge_rounds
  __init__.py      增加导出
```

本次只改调度器一个模块和 contracts。跨模块传递的模型和枚举：

- 传入：`CaseType`（开始时确定的类型）、`TradeAction`（结论里方案的动作，来自 `TradePlan`）、`Verdict` 和 `ResponseDecision`（取自 Critic 的审核和 PM 的回应）、`RiskOutcome`（风控的结果）
- 传出：`CaseEndReason`（在 `CaseState` 里，M4 写进案件记录）
- `CaseState`、`CaseStage`、`CriticOutcome` 是本模块的 frozen dataclass 和枚举，由包根导出：只有 M4 的工作流存状态、调用事件方法，和队列的快照一样。结束原因要写进案件记录、被收盘检查和 M6 的评估读取，所以放进 contracts；Critic 审核和 PM 回应是模型的输出，和 PM 的结论一样放进 contracts

## Goals / Non-Goals

**Goals:**

- 一个不可变的案件状态，每个事件一个方法，spec 每张表的每一行都有测试
- 队列要的两项（是否调用模型、方案的阶段）从阶段推出，不让调用方另外维护
- 性质测试随机交错全部事件，检查不会回到调用模型的阶段、方案的阶段只往前走、轮次和重交次数有上限、每个案件在有限步内结束

**Non-Goals:**

按「编程风格」省略的情况：

- 同一个事件重复送达（按钮连按、工作流重试一步）：调用方去重；已结束的案件收到事件不变，运行中收到不适用的事件报错
- PM 回应的条数和质询的条数不一致：不检查，Critic 下一次审核时自己会看到
- PM 回应时要改决定、动作或论点：不支持，撤回以后在下一个案件里再提
- 修改后的方案的研究要求：引用的 Result 没变，不再校验
- 案件运行期间股票状态变了（比如持仓案件里止损单成交）：案件照常推进，方案由风控、人工确认和下单前再校验挡住
- 案件运行期间改了配置：每个事件按传入的配置算；轮次已经达到新的上限时，下一次质询就算有分歧
- 风控检查途中方案被作废：队列只作废等人工确认和等下单的方案，紧急退出走中止

## Decisions

### D1 一个不可变的状态，每个事件一个方法

和订单状态机一样：`CaseState` 是 frozen dataclass，每个事件是一个方法，返回新的状态。

- 已结束的案件收到任何事件原样返回：作废、中止、故障、确认结果来自案件流程之外，可能在案件结束以后才到
- 运行中的案件收到不适用于当前阶段的事件抛 `ValueError`：只可能是调用方的顺序错了，早点暴露
- 状态不带方案的内容，只带方案的动作：转换只看开仓类还是退出类；方案本身、Critic 的质询、PM 的回应由 M4 存进案件记录
- 考虑过：一个 `apply(event)` 加事件的联合类型。没有采用：事件大多不带参数，十几个空的事件类比方法更啰嗦；方法的签名直接写明每个事件要什么
- 考虑过：状态机自己调用 `conclusion_rejection` 校验结论。没有采用：校验要派过任务的子 agent 和已完成的 Result，这些由任务派发和 M4 给出；状态机只接收校验的结果，保持和内容无关

### D2 阶段和状态

```python
class CaseStage(StrEnum):
    """案件所处的阶段，含义见 case-stages spec「案件的阶段」。"""

    CONCLUDING = "concluding"  # 唤醒 PM：PM 研究并提交结论，校验不通过时修改再提交
    CRITIC_REVIEW = "critic_review"  # Critic 审核方案，包括它要求的补充研究
    PM_RESPONSE = "pm_response"  # PM 逐条回应质询
    RISK_CHECK = "risk_check"  # 等风控检查的结果
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # 等人工确认
    AWAITING_ORDER = "awaiting_order"  # 已经确认，等开盘和下单前再校验
    RETROSPECTIVE = "retrospective"  # 复盘：Reviewer 处理
    ENDED = "ended"


class CriticOutcome(StrEnum):
    """方案离开 Critic 审核时的结果，调用方据此在确认消息里附上说明。"""

    APPROVED = "approved"  # 通过
    DISPUTED = "disputed"  # 有分歧：开仓类方案质询满轮数，或者退出类方案有异议
    UNAVAILABLE = "unavailable"  # Critic 不可用，退出类方案跳过了审核


_CALLING_MODELS: Final = frozenset(
    {
        CaseStage.CONCLUDING,
        CaseStage.CRITIC_REVIEW,
        CaseStage.PM_RESPONSE,
        CaseStage.RETROSPECTIVE,
    }
)
_PLAN_STAGES: Final = {
    CaseStage.CRITIC_REVIEW: PlanStage.REVIEW,
    CaseStage.PM_RESPONSE: PlanStage.REVIEW,
    CaseStage.RISK_CHECK: PlanStage.REVIEW,
    CaseStage.AWAITING_CONFIRMATION: PlanStage.AWAITING_CONFIRMATION,
    CaseStage.AWAITING_ORDER: PlanStage.AWAITING_ORDER,
}


@dataclass(frozen=True, slots=True)
class CaseState:
    """一个案件的状态。事件方法都返回新的状态；已结束的案件原样返回。"""

    stage: CaseStage
    action: TradeAction | None = None  # 方案的动作；没有方案时为 None
    concluded: bool = False  # 已经有通过校验的结论
    resubmissions: int = 0  # 结论校验不通过、退回 PM 重交的次数
    rounds: int = 0  # 开仓类方案被质询、交给 PM 回应的次数
    critic: CriticOutcome | None = None  # 方案离开 Critic 审核时的结果
    end_reason: CaseEndReason | None = None  # 结束以后才有

    @property
    def calling_models(self) -> bool:
        """还在调用模型的阶段，给队列的 RunningCase.calling_models。"""
        return self.stage in _CALLING_MODELS

    @property
    def plan_stage(self) -> PlanStage | None:
        """方案所处的阶段，给队列的 PendingPlan.stage；没有方案或已结束时为 None。"""
        return _PLAN_STAGES.get(self.stage)

    @property
    def releases_reservation(self) -> bool:
        """已结束、有方案、没有提交订单：调用方要释放方案的预留。"""
        return (
            self.stage is CaseStage.ENDED
            and self.action is not None
            and self.end_reason is not CaseEndReason.ORDER_SUBMITTED
        )


def new_case(case_type: CaseType) -> CaseState:
    """开始一个案件：复盘案件不唤醒 PM，从复盘开始；其他从唤醒 PM 开始。"""
    if conclusion_model(case_type) is None:
        return CaseState(CaseStage.RETROSPECTIVE)
    return CaseState(CaseStage.CONCLUDING)
```

- 风控单独一个阶段：风控一次只处理一个方案，可能要等；更重要的是 Critic 审核结束以后案件就不再调用模型，并发上限要马上让出名额
- 等人工确认和等下单分开：队列只在这两个阶段作废开仓方案，`PlanStage` 也是这样分的
- "调用模型"和"方案的阶段"都是阶段的函数，不存：状态里只有一份事实
- 复盘案件用 `conclusion_model` 判断（复盘不唤醒 PM，没有结论种类），不另列一张表
- 考虑过：等下单以后再分"再校验"和"已下单"两个阶段。没有采用：再校验和下单在同一步里接连完成，中间没有要等的东西；订单建立以后案件就结束

### D3 唤醒 PM 和结论

```python
class CaseState:  # 续 D2
    def reject_conclusion(self, config: SchedulerConfig) -> Self:
        """结论校验不通过：重交次数没用完就退回 PM，用完以后结论无效。"""
        if self._ignores(CaseStage.CONCLUDING):
            return self
        if self.resubmissions >= config.max_resubmissions:
            return self._end(CaseEndReason.INVALID_CONCLUSION)
        return replace(self, resubmissions=self.resubmissions + 1)

    def accept_conclusion(self, action: TradeAction | None) -> Self:
        """结论通过校验；action 是结论里交易方案的动作，没有方案时为 None。"""
        if self._ignores(CaseStage.CONCLUDING):
            return self
        concluded = replace(self, concluded=True)
        if action is None:
            return concluded._end(CaseEndReason.NO_PLAN)
        return replace(concluded, stage=CaseStage.CRITIC_REVIEW, action=action)
```

- 重交次数默认 2（proposal「需要人决定」第 7 条）：结论种类不对、续期缺论点更新、跟进案件又申请跟进，照着原因改一次就能通过，再留一次余量；研究不足要先补派任务，派任务的轮次由任务派发限制。PM 的每次重交都是一次带完整上下文的调用，次数不宜多
- `concluded` 在结论通过时设上，之后不再改（proposal「需要人决定」第 8 条）：「收盘检查」只看它决定是否顺延到期事项
- 结论无效的案件没有结论，和系统故障一样不当作投资判断：PM 反复给出不合规的结论是模型的问题，不是对这只股票的判断

### D4 Critic 审核和 PM 回应

```python
class CaseState:  # 续 D2
    def review(self, verdict: Verdict, config: SchedulerConfig) -> Self:
        """Critic 给出结论。退出类方案只审一次、不能否决；开仓类方案按质询轮次转换。"""
        if self._ignores(CaseStage.CRITIC_REVIEW):
            return self
        assert self.action is not None  # 有方案才会进入 Critic 审核
        if verdict is Verdict.APPROVE:
            return self._to_risk(CriticOutcome.APPROVED)
        if not self.action.is_opening:
            return self._to_risk(CriticOutcome.DISPUTED)  # 否决也按异议处理
        if verdict is Verdict.VETO:
            return self._end(CaseEndReason.VETOED)
        if self.rounds >= config.max_challenge_rounds:
            return self._to_risk(CriticOutcome.DISPUTED)
        return replace(self, stage=CaseStage.PM_RESPONSE, rounds=self.rounds + 1)

    def respond(self, decision: ResponseDecision) -> Self:
        """PM 回应质询：维持或修改回到 Critic 审核，撤回时结束。"""
        if self._ignores(CaseStage.PM_RESPONSE):
            return self
        if decision is ResponseDecision.WITHDRAW:
            return self._end(CaseEndReason.WITHDRAWN)
        return replace(self, stage=CaseStage.CRITIC_REVIEW)
```

- 轮次数的是"交给 PM 回应的质询"（proposal「需要人决定」第 4 条）：PM 回应以后总是回到 Critic 审核，最后一次审核时再质询就是有分歧。用 `>=` 比较，运行中调低上限也不会多出一轮
- 补充研究不是事件：Critic 在一次审核里经 Agent 网关派发任务，网关把结果作为工具结果交回 Critic，阶段一直是 Critic 审核；它的额度由任务派发管
- 退出类方案不进 PM 回应（proposal「需要人决定」第 5 条），所以退出类的轮次一直是 0
- Critic 对退出类方案给出否决时按异议处理，不报错也不让网关重试：结果和异议完全相同，都交给人
- 修改只换方案条款，方案的动作不变，所以 `respond` 只需要 PM 的决定；M4 用新的方案条款生成新的方案代替原来的（D11）
- 考虑过：PM 第 2 次回应以后不再交给 Critic，直接交给人。没有采用：PM 的回应常常已经说服 Critic，这时还要人看一遍分歧，打扰人也让确认消息失去分量
- 考虑过：退出类方案也交给 PM 回应一次。没有采用：模型建议退出要在当个案件内尽快完成，PM 的理由已经写在方案里；人确认时看得到双方意见，不同意就拒绝

### D5 Critic 不可用

```python
class CaseState:  # 续 D2
    def critic_unavailable(self) -> Self:
        """Critic 的主模型和备用模型全部不可用：退出类跳过审核，开仓类按系统故障结束。"""
        if self._ignores(CaseStage.CRITIC_REVIEW):
            return self
        assert self.action is not None
        if self.action.is_opening:
            return self._end(CaseEndReason.FAILED)
        return self._to_risk(CriticOutcome.UNAVAILABLE)
```

- Critic 主备全部不可用就是模型 API 异常，健康监控会进入降级模式，开仓本来就暂停（proposal「需要人决定」第 3 条）。开仓类结束原因是系统故障，不是 Critic 否决：反事实评估不能把它算成 Critic 的判断
- 退出类不等 Critic：退出不该被模型故障挡住，人确认时看到 Critic 不可用的说明
- 切换备用模型、什么时候算"全部不可用"由「备用模型」给出，这里只接收结果

### D6 风控、人工确认和等下单

```python
class CaseState:  # 续 D2
    def risk_checked(self, outcome: RiskOutcome) -> Self:
        """风控的结果：通过和调整都进入等人工确认，拒绝时结束。"""
        if self._ignores(CaseStage.RISK_CHECK):
            return self
        if outcome is RiskOutcome.REJECTED:
            return self._end(CaseEndReason.RISK_REJECTED)
        return replace(self, stage=CaseStage.AWAITING_CONFIRMATION)
```

其余几个事件只是"检查阶段，进入下一个阶段或结束"：

| 方法 | 适用的阶段 | 结果 |
| --- | --- | --- |
| `confirm()` | 等人工确认 | 等下单 |
| `decline()` | 等人工确认 | 结束：人工拒绝 |
| `confirmation_timed_out()` | 等人工确认 | 结束：确认超时 |
| `order_submitted()` | 等下单 | 结束：订单已提交 |
| `recheck_voided()` | 等下单 | 结束：再校验作废 |

- 调整和通过走同一条路：确认消息里"已被风控调整"由通知模块按风控结果标明（discord-notification），状态不用另记
- 确认的结果用三个方法，不复用通知模块的 `ConfirmationOutcome`：调度器不能 import 别的模块；"已作废"不是确认的结果，而是队列的作废，走 `void()`
- 再校验调整不是事件：调整以后照样下单，M4 接着调用 `order_submitted()`；下单流程出错走 `fail()`
- 本变更里所有方案都进入等人工确认。「人工确认的规则」以后让一部分退出方案不经确认时，再加一个从风控直接到等下单的转换，并用 `critic` 判断（有分歧、Critic 不可用时仍要确认）

### D7 作废、中止、系统故障和复盘

```python
_CONFIRMING: Final = (CaseStage.AWAITING_CONFIRMATION, CaseStage.AWAITING_ORDER)
# 运行中的阶段；复盘以外的可以被紧急退出中止
_RUNNING: Final = tuple(s for s in CaseStage if s is not CaseStage.ENDED)
_ABORTABLE: Final = tuple(s for s in _RUNNING if s is not CaseStage.RETROSPECTIVE)
```

| 方法 | 适用的阶段 | 结果 |
| --- | --- | --- |
| `void()` | `_CONFIRMING` | 结束：方案被作废 |
| `abort()` | `_ABORTABLE` | 结束：紧急退出中止 |
| `fail()` | `_RUNNING` | 结束：系统故障 |
| `finish_retrospective()` | 复盘 | 结束：复盘完成 |

```python
class CaseState:  # 续 D2
    def _ignores(self, *stages: CaseStage) -> bool:
        """已结束的案件忽略任何事件；运行中的案件收到不适用的事件是调用方的错误。"""
        if self.stage is CaseStage.ENDED:
            return True
        if self.stage not in stages:
            raise ValueError(f"{self.stage} 阶段不接受这个事件")
        return False

    def _to_risk(self, critic: CriticOutcome) -> Self:
        return replace(self, stage=CaseStage.RISK_CHECK, critic=critic)

    def _end(self, reason: CaseEndReason) -> Self:
        return replace(self, stage=CaseStage.ENDED, end_reason=reason)
```

- `void()` 只在两个确认阶段适用，和 case-queue 的作废规则一致；紧急退出的决定里也有这个案件的方案，但 M4 对它只调用 `abort()`。先到的事件决定结束原因，之后的事件被忽略
- `abort()` 不适用于复盘：队列从不中止复盘案件
- `fail()` 适用于所有运行中的阶段：PM、Reviewer 的调用失败，风控、下单流程出错都可能在任何一步发生。系统故障不改变 `concluded`

### D8 结束原因

```python
class CaseEndReason(StrEnum):
    """案件的结束原因，见 case-stages spec「结束原因」。"""

    NO_PLAN = "no_plan"  # 结论没有交易方案
    ORDER_SUBMITTED = "order_submitted"  # 订单已提交
    VETOED = "vetoed"  # Critic 否决开仓类方案
    WITHDRAWN = "withdrawn"  # PM 撤回方案
    RISK_REJECTED = "risk_rejected"  # 风控拒绝
    HUMAN_REJECTED = "human_rejected"  # 人工拒绝
    CONFIRMATION_TIMED_OUT = "confirmation_timed_out"  # 确认超时
    RECHECK_VOIDED = "recheck_voided"  # 下单前再校验作废
    PLAN_VOIDED = "plan_voided"  # 方案被作废：这只股票有新案件排队
    EMERGENCY_ABORTED = "emergency_aborted"  # 紧急退出中止
    INVALID_CONCLUSION = "invalid_conclusion"  # 结论重交用完仍不通过
    FAILED = "failed"  # 系统故障
    RETROSPECTIVE_DONE = "retrospective_done"  # 复盘完成
```

- 放在 `contracts/case.py`，和 `CaseType` 在一起：案件记录、收盘检查、M6 的反事实评估（被否决、被拒绝的方案）都要读
- "有没有结论"不编进结束原因：系统故障和紧急退出中止都可能发生在结论之前或之后，拆成两个原因会让每个读者都要合并回来；状态里的 `concluded` 单独回答
- 状态给出 `releases_reservation`（已结束、有方案、不是订单已提交），M4 照着调用风控的 `release`：`release` 没有预留时什么都不做，所以不用判断方案到没到过风控

### D9 Critic 审核和 PM 回应的格式

```python
type _Text = Annotated[str, StringConstraints(min_length=1)]


class Verdict(StrEnum):
    """Critic 的结论。补充研究是审核过程中经 Agent 网关派发的任务，不是结论。"""

    APPROVE = "approve"  # 通过
    CHALLENGE = "challenge"  # 质询；对退出类方案就是有异议
    VETO = "veto"  # 否决；只对开仓类方案有效


class CriticReview(BaseModel):
    """Critic 对一个方案的一次审核。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    rationale: str = Field(min_length=1)
    challenges: tuple[_Text, ...] = ()  # 逐条的质询，只有质询写
    cited_results: tuple[str, ...] = ()  # 引用的 Result ID，包括补充研究的

    @model_validator(mode="after")
    def _check_challenges(self) -> Self:
        if (self.verdict is Verdict.CHALLENGE) != bool(self.challenges):
            raise ValueError("质询必须逐条写出问题，通过和否决不写")
        return self


class ResponseDecision(StrEnum):
    """PM 对一轮质询的决定。"""

    MAINTAIN = "maintain"  # 维持
    MODIFY = "modify"  # 修改方案条款
    WITHDRAW = "withdraw"  # 撤回


class PlanResponse(BaseModel):
    """PM 对一轮质询的回应。只有开仓类方案会被质询，修改后的方案条款按开仓方案检查。"""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    decision: ResponseDecision
    replies: tuple[_Text, ...] = Field(min_length=1)  # 逐条回应，按质询的顺序
    plan: PlanDraft | None = None  # 修改后的方案条款，只有修改写

    @model_validator(mode="after")
    def _check_plan(self) -> Self:
        if (self.decision is ResponseDecision.MODIFY) != (self.plan is not None):
            raise ValueError("修改必须写新的方案条款，维持和撤回不写")
        if self.plan is not None:
            check_plan_fields(TradeAction.BUY, self.plan)  # 买入和加仓的条款规则相同
            if self.plan.emergency_threshold is None:
                raise ValueError("买入和加仓必须写紧急阈值")
        return self
```

- 格式全部由 Pydantic 检查，Agent 网关校验输出格式时就挡住，状态机只收到合法的审核和回应，不需要"回应不合法、退回重交"的转换
- 只有开仓类方案会被质询，所以修改后的条款可以不带动作、直接按开仓方案检查；`check_plan_fields` 已经在 contracts，复用它。"必须写紧急阈值"和 PM 结论里的规则重复一行，不为一行抽函数
- 质询和回应都是逐条的文字，给 Critic、PM 和人看；条数是否对应不检查
- 考虑过：修改时 PM 提交一份新的结论，重新走 `conclusion_rejection`。没有采用：只有方案条款会变，整份结论重来要再检查种类、续期、研究要求，徒增一种"回应不通过"的转换

### D10 配置

```python
class SchedulerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 开仓方案、加仓和升级引用的状态是完成的 Result，至少来自这么多个不同子 agent；
    # 上限是子 agent 的个数
    min_cited_agents: int = Field(default=2, ge=1, le=len(SubAgent))
    # 同一时间还在调用模型的案件最多这么多个
    max_concurrent_cases: int = Field(default=3, ge=1)
    # 结论校验不通过时，PM 在同一个案件里最多重交这么多次
    max_resubmissions: int = Field(default=2, ge=0)
    # 开仓类方案的质询与回应最多这么多轮
    max_challenge_rounds: int = Field(default=2, ge=1)
```

| 配置项 | 默认值 | 理由 |
| --- | --- | --- |
| 结论最多重交次数 | 2 | 见 D3；0 表示不重交，第一次不通过就结论无效 |
| 质询最多轮数 | 2 | Proposal 规定；至少 1，否则开仓方案的质询永远交不到 PM 手里 |

### D11 M4 的接法（给 M4 的对照）

本变更不调用其他模块，下表是 M4 工作流的接法：

| 什么时候 | 调用 | 接着做 |
| --- | --- | --- |
| 队列的决定启动一个案件 | `new_case(definition.case_type)` | 唤醒 PM 或 Reviewer |
| PM 提交结论 | `conclusion_rejection(...)`；不通过时 `reject_conclusion(config)`，通过时 `accept_conclusion(action)` | 不通过：带着原因再唤醒 PM。通过：应用结论里方案以外的部分；有方案时补上方案 ID、股票和板块变成 `TradePlan`，唤醒 Critic |
| Critic 提交审核 | `review(review.verdict, config)` | 进入 PM 回应时把质询和补充研究的结果交给 PM |
| Agent 网关对 Critic 返回失败 | `critic_unavailable()` | |
| PM 提交回应 | `respond(response.decision)` | 修改时用新的方案条款生成新的方案（新的方案 ID），代替原来的交给 Critic |
| 风控给出结果 | `risk_checked(decision.outcome)` | 进入等人工确认时，按 `critic` 在确认消息里附上双方意见或注明 Critic 不可用，交给下单流程 |
| 确认结果 | `confirm()`、`decline()`、`confirmation_timed_out()` | |
| 再校验和下单 | 再校验拒绝时 `recheck_voided()`；订单建立以后 `order_submitted()` | |
| 队列的决定作废方案 | `void()` | |
| 紧急退出的决定中止案件 | `abort()` | 不再对这个案件的方案调用 `void()` |
| 调用失败、流程出错 | `fail()` | |
| Reviewer 完成 | `finish_retrospective()` | |
| 每次状态变化以后 | 用 `calling_models`、`plan_stage` 更新队列的快照 | 已结束时：`releases_reservation` 为真就调用风控的 `release`；结束原因写进案件记录；从运行中的案件里去掉；取得队列的决定 |

- `CaseState` 的字段都是枚举、整数和布尔，M4 存进状态库，重启后接着推进
- 风控拒绝、再校验作废以后不在同一个案件里重来：PM 在这只股票的下一个案件的上下文里看到上一个案件的结束原因（M5「共享上下文」）

### D12 包根导出和纯区域

包根增加导出 `CaseStage`、`CriticOutcome`、`CaseState`、`new_case`。仍然没有 service 和 ports。

- `domain/stage.py` 只用 `dataclasses`、`enum`、`typing`，以及 contracts、本模块的 config、`domain/case_type.py`（`conclusion_model`）和 `domain/queue.py`（`PlanStage`），不改 `tests/architecture/rules.py`；`stage` 依赖 `queue` 和 `case_type`，它们不依赖 `stage`，没有循环
- `contracts/review.py` 只用 `enum`、`typing` 和 pydantic，以及 contracts 的 `conclusion`（`PlanDraft`）和 `trade_plan`

### D13 测试

```
tests/contracts/test_review.py                        spec「Critic 审核和 PM 回应的格式」
tests/modules/scheduler/test_config.py                + spec「案件阶段的配置」
tests/modules/scheduler/builders.py                   + 开仓案件、退出案件，把案件推到某个阶段的辅助函数
tests/modules/scheduler/domain/test_stage.py          spec 其余场景
tests/modules/scheduler/domain/test_stage_machine.py  性质测试
```

- 辅助函数按事件序列推进状态，比如"开仓案件在 Critic 审核"就是 `new_case(WATCH_EVENT).accept_conclusion(BUY)`，场景里的"依次收到"直接对应
- 性质测试（Hypothesis 的 `RuleBasedStateMachine`）：一个接一个地跑案件，每个案件开始时随机取 14 种案件类型之一，结论最多重交次数取 0 到 3，质询最多轮数取 1 到 3，再取一个外来事件的阶段。每一步收到一个适用于当前阶段的事件，事件和参数都随机（结论通过时的动作取没有或四种动作之一，Critic 的三种结论，PM 的三种决定，风控的三种结果），执行并计数；Critic 不可用、方案被作废、紧急退出中止和系统故障来自案件流程之外，只在取到的阶段收到。测试里照抄 spec「已结束的案件和不适用的事件」的表，每一步以后对当前状态试遍全部事件和参数：已结束时检查状态不变，不适用时检查抛 `ValueError`。每一步以后还检查：
  - 调用模型当且仅当阶段是唤醒 PM、Critic 审核、PM 回应或复盘；复盘以外的案件一旦不调用模型，之后再也不调用
  - 方案的阶段只按审核、等人工确认、等下单的顺序往前走
  - 重交次数和质询轮次不超过配置；退出类方案的轮次一直是 0，从不进入 PM 回应
  - 有结论一旦为真就不再变；结束原因是没有交易方案、订单已提交、Critic 否决、PM 撤回、风控拒绝、人工拒绝、确认超时、再校验作废、方案被作废时一定有结论，结论无效、复盘完成时一定没有
  - Critic 否决只出现在开仓类方案；Critic 不可用的审核结果只出现在退出类方案；开仓类方案有分歧时轮次等于上限
  - 结束原因当且仅当已结束时才有；`releases_reservation` 当且仅当已结束、有方案、不是订单已提交
  - 适用的事件数不超过 重交上限 + 2 × 轮数上限 + 5（每个案件在有限步内结束）
- 用 `event` 确认样例里出现过全部 13 种结束原因、三种 Critic 审核的结果、重交用完和质询满轮数
- 考虑过：每个事件方法一条规则，在任何阶段都触发，一个样例只跑一个案件。没有采用：系统故障和紧急退出中止几乎在每个阶段都适用，案件大多在前一两步就结束，试过 1000 个样例只有 1 次 Critic 通过、没有一次订单已提交；改成规则只在适用时触发，Hypothesis 每个样例随机关掉一些规则，常常没有能触发的规则，大半样例作废
- 每个 spec 场景至少对应一个测试，测试名或说明里写明场景

## Risks / Trade-offs

- [开仓方案最多要 Critic 审 3 次、PM 回应 2 次，模型调用多] → 只有开仓方案会这样，频率低；轮数可配置；每轮的调用计入调用预算
- [退出方案有异议时 PM 没有机会回应或撤回] → 人确认时看到双方意见，不同意就拒绝；PM 在下一个案件里可以重新判断
- [不适用的事件抛错，M4 处理顺序有误时工作流会出错] → 来自流程之外的事件（作废、中止、故障）对已结束的案件不报错；队列只在确认阶段作废；性质测试覆盖所有交错
- [结论之后的系统故障让论点更新生效、方案不执行] → 符合"系统故障不当作投资判断"：PM 的判断保留，只是没执行；收盘检查只看有没有结论
- [本变更里所有方案都要人确认，夜里的退出方案可能超时不执行] → 止损单一直在；「人工确认的规则」以后决定哪些退出可以自动执行
- [盘后确认的方案让这只股票一整夜都有运行中的案件] → 开仓方案遇到新案件排队就作废；退出方案开盘就下单，排队的案件随后运行

## Migration Plan

只新增代码，没有持久化的数据，不需要迁移；需要回滚时直接 revert。
