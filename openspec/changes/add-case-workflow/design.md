# Design

## Context

现状：

- 调度器 `modules/scheduler` 只有纯逻辑：`define_case(trigger, status)` 给出 `CaseDefinition`（类型、股票、问题、优先级、事件要求的子 agent）；`conclusion_rejection` 按案件类型校验结论；`CaseState` 是不可变的案件状态，每个事件一个方法，"状态存在哪里、事件从哪里来由调用方（M4 的工作流）负责"；`queue_decision` 要调用方给出运行中的案件（`RunningCase`：类型、股票、是否在调用模型）和还没提交订单的方案（`PendingPlan`：方案 ID、股票、动作、阶段），`CaseState.calling_models`、`plan_stage` 就是给它们的
- Agent 网关：`TaskDispatchService`（派发、本案件的任务和用量、`dispatched_agents`、`completed_results`）、`ModelFallbackService`（`blocked_decision`、`end_case`）、`CallBudgetService`；「替身」加 `AgentCallService`（`pm`、`pm_respond`、`critic`、`reviewer` 给出 `Answered` 或 `AllUnavailable`，`run_task` 直接给出 Result）、端口 `ModelBackend` 和 `contracts/agent_call.py`（`CaseContext`、`PmRequest`、`PmTurn`、`CriticRequest`、`CriticTurn`、`TaskResults`、`Returned`、`Challenge`……）。`CostStore` 只有按时刻读
- 「案件记录」加 `CaseRecordService`：`start`、`conclude`、`review`、`critic_unavailable`、`respond`、`risk_checked`、`add_findings`、`end`，每一步只写一次；`recent_ended(symbol, limit)`、`proposal(case_id)`；触发记成 `CaseTrigger`（种类、触发原因、股票、时刻），由调用方从调度器的 `Trigger` 换过来（它的 design D8）；不记阶段，"现在的方案由「案件工作流」自己的状态带着"
- 「状态库快照」：`SnapshotFlow.take(case_id, case_type, symbol, version=, reason=)` 同一版再生成时给出存下的；数据读不到时原样抛出 `BrokerUnavailableError`、`QuoteUnavailableError`、`ScreenDataUnavailableError`，"由调用方决定怎样处理"；`SnapshotService.snapshot(case_id, version)`、`resolve(case_id, entry_id)`
- 股票池 `StockPoolService` 的 PM 操作都带 `case_id`，不通过时返回 `Rejection`；`add` 要调用方查好 `Listing`（快照的粗筛名单里每只股票带一份）；跟进 `FollowUpService.request`（获批即保存）、`pending`、`status_changed`；心跳 `thesis_dates_rejection(ThesisUpdate, today, ...)`、`last_review_day_after`
- 下单流程（`workflows/order_workflow.py`）的做法：部件在 DBOS launch 之前 `install`；工作流函数只按 step 的结果分支；I/O、时间和日志都在 step 里（step 调用 `order_flow.py` 的用例）；确认的进展用 `DBOS.set_event` 发布，按钮的结果用 `DBOS.send` 送进工作流
- bootstrap 只装配了 M2 的交易侧；M4 的股票池、跟进、网关、新闻、公司数据、粗筛、快照都还没有装配。DBOS 的系统库就是状态库所在的数据库（`system_database_url` 和 `DATABASE_URL` 相同）
- 迁移到 v0012（「案件记录」）
- DBOS 3.2.0 不允许在 step 里启动工作流（`_context.py` 的 `assert cur_ctx.is_workflow()  # Not in a step`）；「方案接下单」（`update-order-flow-case-plans`）按本变更 D4 的接口写，交接会启动下单流程

动机见 proposal.md，要求见 specs/。

### 模块边界

```
workflows/
  case_workflow.py   (新) DBOS 工作流 case 和子工作流 case_task、各个 step；install、start_case、
                     case_progress；CaseProgress
  plan_handoff.py    (新) PlanHandoff、Handoff、PlanEvent、PLAN_TOPIC、report_plan：交给下单的接口
  case_flow.py       (新) CaseFlow：每个 step 的用例（不 import dbos）；CaseServices、CaseFlowConfig
  case_conclusion.py (新) 结论的检查（第 1 到 4 项是纯函数）和结论生效
  case_entries.py    (新) case_trigger（调度器的触发换成案件记录的触发）、程序日志和退回原因的文字
  names.py           (改) 案件类型、阶段、结束原因、事件类别、决定的中文名
contracts/agent_call.py      (改) CaseContext 加 recent_cases、pending_follow_ups；新增 RecentCase
modules/gateway/             (改) CostStore.case_costs、PostgresCostStore、CallBudgetService.case_costs
modules/programlog/domain/entry.py  (改) Action 加 OPEN_CASE、CASE_STAGE、WAKE_AGENT
modules/scheduler/domain/stage.py   (改) CaseState.risk_checked 加 confirming
```

- 案件工作流在 workflows 层，只 import 各模块的包根：调度器（`CaseDefinition`、`Trigger`、`CaseState`、`CaseStage`、`CriticOutcome`、`new_case`、`conclusion_rejection`、`SchedulerConfig`）、网关（`AgentCallService`、`TaskDispatchService`、`CallBudgetService`、`ModelFallbackService`、`CaseScope`、`Dispatch`、`conclusion_signal_rejection`、`blocked_decision`、`dispatched_agents`、`completed_results`、`usage`、`GatewayConfig`）、案件记录、状态库、股票池、跟进、心跳、通知、程序日志；还 import 同层的 `snapshot_flow.SnapshotFlow` 和 `plan_handoff`
- 跨模块传递的 Pydantic 模型：contracts 的四种结论、`TradePlan`、`CriticReview`、`PlanResponse`、`RiskDecision`、`ModelUse`、`Snapshot`、`Task`、`TaskRequest`、`Result`、`FollowUpRequest`、两种论点，以及 `contracts/agent_call.py` 的全部模型；调度器、网关、案件记录、心跳的 frozen dataclass（`CaseDefinition`、`Trigger`、`CaseState`、`Dispatch`、`TaskRecord`、`CallCost`、`CaseTrigger`、`CaseRecord`、`Finding`、`ThesisUpdate`）在 workflows 里组装
- 模块之间不新增依赖：网关只多一个读取；程序日志只多三个动作；contracts 只多一个模型和两个字段
- 下单那一侧（「方案接下单」）实现 `PlanHandoff`，用 `report_plan` 把结果送回；两边都只 import `plan_handoff.py`，下单流程不 import 案件工作流；由 bootstrap 装配
- 调度器只改 `CaseState.risk_checked` 的参数，规则见 case-stages spec「风控」

## Goals / Non-Goals

**Goals:**

- 一个案件的阶段只有一份：DBOS 工作流按步骤的结果推进 `CaseState`，崩溃以后重放步骤得出同一个状态（D2）
- 「排队运行」只用两个函数就能启动案件、组出队列的快照；「方案接下单」只实现一个端口、调用一个函数（D3、D4）
- 本变更不依赖「方案接下单」就能测完：交接用测试替身，结果由测试送回

**Non-Goals:**

- 不做异步工作流：子 agent 的任务用子工作流并行（D8），其余步骤顺序进行
- 不给案件工作流加超时：等下单那一侧时一直等（D4）
- 不加新表：案件状态在 DBOS 里（D2），案件记录、快照、任务和费用已有各自的表

按「编程风格」省略的情况：

- PM 的轮次和任务数用完以后仍反复派发、Critic 的补充研究额度用完以后仍反复派发：每次都被网关拒绝、再调用，M5 在工具里处理；替身只派一次
- 结论生效执行到一半进程退出：重做时股票池操作和跟进审批可能多做一次（股票池给出"已在池子里""状态不允许"，跟进替换成同样的一条），发现照重做时股票池的结果写
- 开始运行以后、生成快照以前案件股票被移出股票池：生成快照报错，案件的运行出错停下
- 下单那一侧送回的结果丢了、下单流程出错停下：案件一直等，这只股票的后续案件一直排队；「方案接下单」要保证每条路径都送回（D4）
- 交接时启动下单流程失败（数据库出错）：异常原样抛进案件工作流，案件的运行出错停下
- 同一个案件两个进程同时运行：DBOS 按工作流 ID 只运行一个，部署也只有一个进程
- 读写数据库出错（案件记录、程序日志、状态库、股票池、跟进）：原样抛出，案件的运行出错停下
- 快照的账户里没有案件股票的持仓（持仓案件开始以后刚好清仓）：浮动盈亏比例不传，review-dates 按亏损处理
- 结论检查和结论生效跨过收盘：今天按检查时算，生效用同一个今天
- 一次派发里复用了本次前面派发出去、还在运行的任务：等派发出去的任务都有了 Result 以后再读，复用的也有了
- 同一时刻送回两个结果：按 DBOS 收到的先后处理，不适用的那个报错
- 修改 `CaseProgress`、步骤的输出等 dataclass 的字段：运行中的案件重放时读不出来，改字段的变更自己处理（和下单流程一样）

## Decisions

### D1 一个案件一个工作流，步骤和用例分开

和下单流程一样分两层：`case_workflow.py` 的工作流函数只推进 `CaseState`、按步骤的结果分支、发布进展；每个 step 只调用 `case_flow.py` 里 `CaseFlow` 的同名方法，I/O、时钟、日志、告警都在那里。

```python
@DBOS.workflow(name="case")
def run_case(
    case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
) -> CaseEndReason:
    """一个案件，见 case-workflow spec。只按 step 的结果分支，不读时间、不写日志。"""
    case = _Case(case_id, definition)  # 案件状态、现在的方案、对话、快照版本，都在内存里
    _start(case_id, definition, trigger, triggered_at)
    case.publish()
    version = _snapshot(case_id, definition, 1, None)
    if version is None:
        case.advance(case.state.fail())
    elif case.state.stage is CaseStage.RETROSPECTIVE:
        _retrospective(case, version)
    else:
        case.begin(_context(case_id, definition, trigger), version)
        _research(case)
        _review(case)
        _deliver(case)
    _end(case_id, case.state)
    return case.end_reason
```

- `_Case` 是工作流内存里的一个可变对象：案件状态、现在的方案、PM 和 Critic 的对话、PM 派过的任务的结果、最新的快照版本、案件上下文。崩溃以后 DBOS 重放工作流函数，step 的输出从库里读回，`_Case` 一步步重建成同样的内容，不另存
- `case.advance(new)` 换上新的案件状态：阶段变了时调用 step `case.stage` 写案件阶段变化，然后 `DBOS.set_event` 发布进展（D3）
- `_research`、`_review`、`_deliver`、`_retrospective` 是工作流里的普通函数（不是 step），各自循环到离开自己的阶段：`_research` 在唤醒 PM，`_review` 在 Critic 审核和 PM 回应，`_deliver` 在风控、等人工确认和等下单
- 每次 agent 调用单独一个 step，step 的输出就是回复：重放时不再调用模型，对话从 step 的输出重建（agent-calls spec 的对话）。写案件记录、生效这些后续的写入放在下一个 step，调用交回以后进程退出也不会丢掉已经花了钱的回复
- step 的参数传小的值（案件 ID、快照版本、对话），快照在 step 里按版本从状态库读出来再交给 `AgentCallService`

| step | `CaseFlow` 的方法做什么 | 输出 |
| --- | --- | --- |
| `case.start` | 写案件记录的开始运行（`case_trigger` 换出触发）、写开案件 | 无 |
| `case.snapshot` | `SnapshotFlow.take`；三种"读不到"的异常换成 None | 版本号或 None |
| `case.context` | 组出案件上下文（D7） | `CaseContext` |
| `case.call_pm` | 读快照，调用 `pm` 或 `pm_respond`；写唤醒 agent；全部不可用时告警 | `Answered` 或 None |
| `case.dispatch` | `TaskDispatchService.dispatch`（PM 或 Critic，案件的 `CaseScope`） | 每个请求的 task_id 和是不是派发出去的 |
| 子工作流 `case_task` | step `case_task.run`：读快照，`AgentCallService.run_task` | 无 |
| `case.outcomes` | 按请求的顺序读每个 Task 和 Result | `tuple[TaskOutcome, ...]` |
| `case.conclude` | 结论的检查；通过时写案件记录的结论、结论生效（D5、D6） | 退回的原因或现在的方案 |
| `case.call_critic` | 读快照和 PM 派过的任务，调用 `critic`；写唤醒 agent；全部不可用时告警 | `Answered` 或 None |
| `case.record_review`、`case.record_response`、`case.critic_unavailable` | 写案件记录 | 无 |
| `case.record_risk` | 把交接给出的风控结果写进案件记录（`submit` 在工作流函数里调用，D4） | 无 |
| `case.call_reviewer` | 读快照，调用 `reviewer`；唤醒 agent 写报告的总结；全部不可用时告警 | 是否成功 |
| `case.stage` | 写案件阶段变化 | 无 |
| `case.end` | 写案件记录的结束（Task 数、调用次数）、`ModelFallbackService.end_case` | 无 |

- 生成快照时数据读不到按系统故障结束：快照 spec 把处理交给调用方；三种异常是券商、行情、日线数据源断线，常见，案件结束以后没有结论，复查日由收盘检查顺延；不另外告警，这几种断线由健康监控告警
- 告警的对象写案件 ID，说明写 agent 和网关给出的说明（`AllUnavailable.detail`）
- 唤醒 agent 的耗时用调用前后两次读时钟相减，费用是 `Answered.cost`；调用失败时没有费用（失败的尝试有用量时费用在网关的费用记录里）

不选的做法：

- 一个 step 做完整个阶段（比如研究的全部轮次）：中途退出时整个阶段重来，模型调用重复、费用重复
- 案件工作流直接调用下单流程的 step：下单的规则（确认、再校验、重试）要在两个工作流里各写一份

### D2 案件状态存在 DBOS 工作流里

`CaseState` 只在工作流函数里推进；每次变化以后用 `DBOS.set_event(PROGRESS, CaseProgress(...))` 发布，别人只读这个事件，不写。

- 一份：DBOS 本来就为恢复存下了每个 step 的输出，工作流按它们重放得出状态；再写一张调度器的表就是两份，崩溃在写表和 step 之间时要定哪份为准
- 在状态库里：DBOS 的系统表和状态库是同一个数据库（bootstrap 的 `system_database_url`），Proposal 的"案件的阶段写入共享状态库、写入者是调度器"照样成立，写入者是调度器的案件工作流
- 案件记录照它的 design D3 只记事实（开始、结论、审核、回应、风控结果、结束），不记阶段，队列不读它
- 运行出错停下的工作流（DBOS 状态是 ERROR）不算运行中：`case_progress` 只给 PENDING 的工作流的进展

不选的做法：调度器加一张 `scheduler_cases` 表，每次阶段变化写一个版本。队列读表方便，但和 DBOS 的状态是两份；进程退出在写表以前时表落后于工作流。

### D3 给队列的接口

```python
PROGRESS = "progress"  # 工作流的事件：案件的进展


@dataclass(frozen=True, slots=True)
class CaseProgress:
    """一个运行中案件的进展，见 case-workflow spec「案件的进展」。"""

    case_type: CaseType
    symbol: str | None
    state: CaseState
    plan: TradePlan | None  # 现在的方案，PM 修改过的条款已经代入


def start_case(
    case_id: UUID, definition: CaseDefinition, trigger: Trigger, triggered_at: datetime
) -> None:
    """以案件 ID 为工作流 ID 开始运行；这个 ID 已经开始过时什么都不做。"""
    with SetWorkflowID(str(case_id)):
        DBOS.start_workflow(run_case, case_id, definition, trigger, triggered_at)


def case_progress(case_id: UUID) -> CaseProgress | None:
    """运行中案件的进展；没有这个案件、已经结束、出错停下时为 None。"""
    workflow = DBOS.get_workflow_status(str(case_id))
    if workflow is None or workflow.status != "PENDING":
        return None
    progress: CaseProgress | None = DBOS.get_event(str(case_id), PROGRESS, timeout_seconds=0)
    if progress is None or progress.state.stage is CaseStage.ENDED:
        return None
    return progress
```

- 「排队运行」按队列决定里的 `CaseStart` 调用 `start_case`，按它自己记下的已启动案件逐个读 `case_progress`：`RunningCase(case_id, p.case_type, p.symbol, p.state.calling_models)`；`p.plan` 不为空、`p.state.plan_stage` 不为空时是 `PendingPlan(p.plan.plan_id, p.plan.symbol, p.plan.action, p.state.plan_stage)`
- 队列要在"案件结束、方案进入新的阶段"以后重新取决定（case-queue spec「队列的输入和决定」）：怎样知道变化（定时读进展，或者在发布进展的地方加一个通知）由「排队运行」定，本变更只发布事件
- 不再运行的案件由「排队运行」写案件记录的不再运行，触发用同一个 `case_trigger` 换
- 「紧急退出中止案件」「排队运行」的中止和作废：用 `plan_handoff.PLAN_TOPIC` 送给案件（`PlanEvent` 加作废和中止两个值），案件在 `_deliver` 的等待里和每次调用 agent 以前收一次（`DBOS.recv(PLAN_TOPIC, timeout_seconds=0)`），按 `CaseState.void()`、`abort()` 转换；本变更不加这两个值，也不在步骤之间收

### D4 交给下单的接口

`workflows/plan_handoff.py`（新）放交接的接口，案件工作流和下单流程都 import 它，下单流程不 import `case_workflow.py`：

```python
PLAN_TOPIC = "plan"  # 下单那一侧送回结果的消息主题


class PlanEvent(StrEnum):
    """下单那一侧送回案件的结果，见 case-workflow spec「交给下单」。"""

    CONFIRMED = "confirmed"  # 已确认
    DECLINED = "declined"  # 已拒绝
    TIMED_OUT = "timed_out"  # 已超时，包括确认请求发不出去
    SUBMITTED = "submitted"  # 订单已提交
    RECHECK_VOIDED = "recheck_voided"  # 再校验作废
    FAILED = "failed"  # 出错


@dataclass(frozen=True, slots=True)
class Handoff:
    """交接给出的结果，见 case-workflow spec「交给下单」。"""

    decision: RiskDecision
    confirming: bool  # 通过或调整以后要人工确认；风控拒绝时为 False


class PlanHandoff(Protocol):
    """下单那一侧，由「方案接下单」实现。"""

    def submit(self, case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> Handoff | None:
        """在工作流函数里调用：检查并预留，通过或调整时启动下单流程；读不到券商账户时为 None。"""
        ...


def report_plan(case_id: UUID, event: PlanEvent) -> None:
    """把一个结果送给案件 case_id，在下单流程的工作流函数里调用。"""
    DBOS.send(str(case_id), event.value, topic=PLAN_TOPIC)
```

- `submit` 在案件工作流的工作流函数（`_deliver`）里调用，不放在 step 里：它要启动下单流程，DBOS 3.2.0 不允许在 step 里启动工作流（`_context.py` 的 `assert cur_ctx.is_workflow()  # Not in a step`）。它里面的首次检查是下单流程那一侧的 step，下单流程是案件工作流的子工作流，重放时 DBOS 给出记下的结果，不再检查、不再启动
- 风控结果写进案件记录放在随后的 step `case.record_risk`；`submit` 给出 None 时不写
- 风控结果和要不要人工确认同步给出：两者都是确定性代码在首次检查时算出的（confirmation-rules spec「案件方案的确认」），案件工作流按 `CaseState.risk_checked(outcome, confirming=handoff.confirming)` 转换
- 之后的结果走消息：确认要等人（最多 30 分钟）、下单要等开盘，下单流程是另一个长时间运行的工作流。`DBOS.send` 在下单流程的工作流函数里调用是持久的，案件工作流 `DBOS.recv(PLAN_TOPIC)` 收到才往下走；收不到时继续等（每次等一天，超时以后接着等，不设时限）
- 转换就是 `CaseState` 的方法：`risk_checked`、`confirm`、`decline`、`confirmation_timed_out`、`order_submitted`、`recheck_voided`、`fail`；结果不适用于当前阶段时 `CaseState` 抛 ValueError，案件的运行出错停下（spec「不适用的结果」）
- 本变更里装配的交接只有测试替身（`tests/fakes/plan_handoff.py`）：给出脚本里的 `Handoff` 或 None，记下收到的方案；测试再调用 `report_plan`

案件状态的"风控 → 等下单"（不要人工确认的退出方案）在本变更里加：案件阶段的转换由案件工作流推进，和交接一起定。

- `CaseState.risk_checked(outcome, *, confirming)`：拒绝时结束、不看 `confirming`；通过或调整时 `confirming` 为真进入等人工确认，为假进入等下单
- case-stages spec「风控」的表加这一行，「人工确认」的第一句跟着改；`stage.py` 的说明里"所有方案都进入等人工确认"一并改掉
- 调用方只有本变更的案件工作流，没有别的地方要改

「方案接下单」（`update-order-flow-case-plans`）要做的事：

1. 实现 `PlanHandoff`（`CasePlanHandoff`）：以方案和来源案件做首次检查并预留，按 confirmation-rules spec 定要不要确认；通过或调整时以方案 ID 启动下单流程，把案件 ID 带进去
2. 下单流程在每个结局调用 `report_plan`：确认 → `CONFIRMED`，拒绝 → `DECLINED`，超时和发不出去 → `TIMED_OUT`，交给巡检 → `SUBMITTED`，作废 → `RECHECK_VOIDED`；意料之外的错误停下以前送 `FAILED`，保证案件不会一直等
3. 确认消息按 case-stages spec「人工确认」附上 Critic 的意见：从案件记录的方案记录和结论的理由读
4. 成交以后的股票池：由它的 pool-position-sync（盘中巡检按券商持仓把买入成交和清仓交给股票池、状态变化交给跟进审批）做；不是 PM 发起的清仓也转为候选。案件工作流不管
5. 交易记录的来源案件 ID；释放预留照下单流程现在的做法（案件状态的"要释放预留"只是核对）

### D5 结论的检查

`case.conclude` 先检查，第 1 到 5 项是 `case_conclusion.py` 的纯函数，输入都由 `CaseFlow` 读好传进去：

| 顺序 | 检查 | 用到的 |
| --- | --- | --- |
| 1 | `conclusion_signal_rejection(conclusion, now, gateway_config)` | 现在：检查时读一次时钟 |
| 2 | `blocked_decision(conclusion, model_use, gateway_config)` | 提交结论那次调用的 `Answered.model_use` |
| 3 | `conclusion_rejection(definition, conclusion, dispatched, completed, scheduler_config)` | `dispatched_agents`、`completed_results` 取 `TaskDispatchService.records(case_id)` |
| 4 | 寻股加入的每只股票都在 `snapshot.screen.candidates` 里 | 本案件最新一版快照 |
| 5 | 每一份新写的论点 `thesis_dates_rejection(update, today, calendar, heartbeat_config)` | `ThesisUpdate`：见下 |
| 6 | `FollowUpService.request(symbol, request, case_id, catalyst)` | 获批即保存，所以放在最后 |

- `ThesisUpdate` 由 `thesis_updates(definition, conclusion, entry, snapshot)` 给出，按 spec 的表：新设不传原来的论点；续期（复查类案件）和平时调整传股票池记录的论点和 `last_review_day`；`pnl_ratio` 是 `(快照里的最新价或前一交易日收盘价 − 快照账户里持仓的均价) / 均价`，只有投资论点传；`schedule_day` 是催化剂引用日程项时 `SnapshotService.resolve(case_id, ref_id)` 查到的 `EarningsEntry` 的发布日
- 日程项按快照的条目 ID 查：现在状态库里有日期的日程只有财报发布（M5 才有宏观日程）；条目 ID 带版本、在案件内唯一，PM 和 M5 的 prompt 看到的就是它
- 今天是 `calendar.session_closing_after(now).day`，和第 1 项的"现在"同一次读时钟
- 寻股只能加粗筛名单里的股票（人定的，2026-10-18）：Proposal 的寻股流程是"粗筛出前 N 只 → PM 挑选"，名单以外的股票在快照里没有事实，PM 和子 agent 没有依据；按结论校验不通过退回，和别的检查一样计入重交次数。放在 workflows 而不是调度器的 `conclusion_rejection`：要看快照，调度器的 domain 不读快照
- 不通过的原因写成中文交给 PM（`case_entries.rejection_text`）：信号是"结论里的信号不合法：<原因>"，备用模型是"用备用模型时不能提这个决定"，名单是"加入的 <股票> 不在粗筛名单里"，其余照各 spec 的原因
- 信号不合法按退回重交：signal-format spec 说怎样退回由 M5 和其他格式错误一起定；替身的信号总是合法，现在只有测试走到这里，退回重交和别的不通过一样计入重交次数，最简单；M5 定了别的做法时改这一处
- `case.conclude` 不通过时返回 `Returned(reason)`，工作流 `reject_conclusion`；没用完时把 `PmTurn(reply=SubmitConclusion, feedback=Returned)` 加进对话，下一次唤醒 PM 的原因写明第几次退回和原因

不选的做法：检查放在工作流函数里。要读时钟、任务、股票池、快照，都是 I/O；跟进审批还要写。

### D6 结论生效

第 6 项通过以后，同一个 step 接着：

1. 有交易方案时得出方案：案件记录已经有这个案件的方案记录时用它（重做时方案 ID 不变），否则 `conclusion.trade_plan(ids.new_id(), symbol, entry.sector)`
2. `CaseRecordService.conclude(case_id, conclusion, model_use, plan)`
3. 按 spec「结论生效」的表调用股票池和跟进：先 `record_score`，再论点更新（`update_investment_thesis` / `update_watch_thesis`，上次复查的那一天是 `last_review_day_after(update, today)`）或状态变化（`promote(thesis, today)`、`keep`、`demote`、`remove(rationale)`）；`demote`、`remove` 通过以后 `FollowUpService.status_changed(symbol, before, after)`
4. 寻股：按顺序 `add(symbol, PoolSource.EXPLORATION, reason, listing, case_id=)`，`listing` 取快照 `screen.candidates` 里这只的（第 4 项保证在名单里）；通过时 `record_score`；再按顺序 `remove`，通过时 `status_changed(symbol, before, None)`
5. `CaseRecordService.add_findings(case_id, findings)`：寻股的全部增删、候选和关注的移出各一项，`rejection` 是股票池 `Rejection` 的值
6. 返回现在的方案（没有方案时为 None），工作流 `accept_conclusion(plan.action if plan else None)`

- 评分先记：移出以后股票不在池子里，记评分会被拒绝
- 股票池不通过不退回 PM：stock-pool spec 把这些拒绝定为请求的正常结果，股票池写程序日志，增删的结果进发现（「案件记录」的 design D4）；follow-ups spec 明确要求审批不通过退回 PM，所以审批在检查里、股票池操作在生效里
- 清仓的去向、开仓的投资论点不在这里写进股票池：股票池在成交时才变（stock-pool spec「状态变化」），由「方案接下单」的 pool-position-sync 做（D4 第 4 条）
- 写结论在生效以前：案件状态的"有结论"在结论通过时就有了（case-stages spec「结束原因」），之后生效出错时案件记录里也有结论

### D7 案件上下文

```python
class RecentCase(BaseModel):
    """这只股票最近结束的一个案件，放进案件上下文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_type: CaseType
    ended_at: AwareDatetime
    end_reason: CaseEndReason
    conclusion: Conclusion | None  # 没有结论的为 None
```

`CaseContext` 加两项（有默认值，替身和已有的测试不用改）：

```python
recent_cases: tuple[RecentCase, ...] = ()  # 按结束时刻从晚到早
pending_follow_ups: tuple[FollowUpRequest, ...] = ()  # 按触发时间
```

- `case.context` 在第 1 版快照以后组一次，案件里 PM 和 Critic 的每次调用都用它：同一个案件读同一份上下文，和快照一样
- 最近结束的案件：`CaseRecordService.recent_ended(symbol, config.recent_cases)`，复盘案件也在里面（结束原因看得到，没有结论）；跟进：`FollowUpService.pending(symbol)` 的申请
- 现在加这两项：Proposal 的案件上下文要求"近期结论""待执行的跟进"，来源（案件记录、跟进）都已经有了；case-stages spec 也要求结束原因在下一个案件的上下文里看得到
- 不加的：经验和历史案件的数量索引（M5 的查询工具、M6 的经验库）、事件内容（「事件检测」）、跟进案件的原案件结论和申请原因（「跟进到点」给触发加跟进 ID 以后）

```python
class CaseFlowConfig(BaseModel):
    """案件工作流的配置，含义见 case-workflow spec「案件工作流的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recent_cases: int = Field(default=3, ge=0)  # 案件上下文里最近结束的案件最多几个
```

### D8 子 agent 的任务并行

- 一次派发里派发出去的任务，每个开一个子工作流 `case_task(task_id, case_id, version)`，里面一个 step 调用 `AgentCallService.run_task`；父工作流 `DBOS.start_workflow` 逐个启动，再逐个 `get_result`，都完成以后 step `case.outcomes` 按请求的顺序读 Task 和 Result
- 要并行：Task 的截止时间是派发时刻加 5 分钟（task-dispatch spec），顺序运行时第三个任务可能还没开始就过了截止时间
- 子工作流的 ID 用 DBOS 默认的（父工作流 ID 加顺序），重放时不会重复启动；`run_task` 已经有 Result 时直接给出，不再调用模型
- 不选：DBOS 的队列。并行度就是一次派发的任务数（PM 默认最多 6 个），不需要另外限流；替身也不会限流

### D9 Critic 审核、补充研究和 PM 回应

- Critic 的请求：`CriticRequest(context, snapshot=最新一版, conclusion, research=PM 派过的任务和 Result, turns=Critic 的对话)`，`research` 在 step 里从 `TaskDispatchService.records(case_id)` 取发起者是 PM 的，按派发的先后
- 补充研究：先 `case.snapshot(case_id, definition, version + 1, reason)`，原因"Critic 第 n 次补充研究"，n 是本案件 Critic 派发任务的次数；再以 Critic 为发起者派发，任务读新的一版；`CriticTurn(reply=DispatchTasks, feedback=TaskResults)` 加进 Critic 的对话；这次审核的补充研究的结果另外留着，质询时放进 `Challenge.research`
- 每次补充研究都生成新的一版（人定的，2026-10-18；完整版 Proposal「共享上下文」已改为"Critic 要求补充研究时生成新的快照版本并记录原因"）：要不要新数据不好由代码判断，Critic 审核时离第 1 版已经过了 PM 的整个研究，用最新的数据核实
- 审核：`record_review`，工作流 `review(verdict, config)`；质询并进入 PM 回应时，PM 的对话加 `PmTurn(reply=上一次的回复, feedback=Challenge(review, research))`，上一次的回复是提交的结论或者上一轮的 `PlanResponse`
- 回应：`record_response`，工作流 `respond(decision)`；维持或修改时 Critic 的对话加 `CriticTurn(reply=SubmitReview(质询), feedback=PlanResponse)`；修改时用现在的方案的方案 ID、股票、板块、动作加上回应里的条款重新构造 `TradePlan`（条款的字段和 `PlanDraft` 同名），照常校验
- Critic 不可用：`critic_unavailable` 写案件记录，工作流 `critic_unavailable()`；告警在 `case.call_critic` 里

### D10 不装配

- bootstrap 不 install 案件工作流，也不 import `case_workflow`：没有谁调用 `start_case`，DBOS 里不会有案件工作流要恢复。「方案接下单」改的下单流程只 import `plan_handoff.py`，bootstrap 装配下单流程时也不会带进案件工作流
- 装配要先装配 M4 的股票池、跟进、网关（`TaskStore`、`CostStore`、备用模型、预算）、新闻、公司数据、粗筛、快照、案件记录、替身和交接；这些都还没有装配，交接要等「方案接下单」。「排队运行」是第一个运行中的调用方，到时一起装配，`install(flow, handoff)` 放在 DBOS launch 以前
- 替身装配时是 `AgentCallService(StandInBackend(...), ModelFallbackService(clock, stand_in_models(), config), ...)`，`StandInConfig()` 用默认值（「替身」design D12）

### D11 按案件读费用记录

```python
def case_costs(self, case_id: UUID) -> tuple[CallCost, ...]:
    """这个案件的全部费用记录，按写入的先后。"""
    ...
```

- `CostStore` 和 `PostgresCostStore` 加 `case_costs`（`WHERE case_id = ... ORDER BY id`），`CallBudgetService.case_costs` 转给存储；`tests/fakes/costs.py` 跟着加
- 调用次数是条数（「案件记录」design D9）；不加 `case_id` 的索引：每个案件结束时读一次，表一天几百行，M5、M6 查询多了再加（加索引要迁移）

### D12 程序日志和文字

- `Action` 加 `OPEN_CASE`（开案件）、`CASE_STAGE`（案件阶段变化）、`WAKE_AGENT`（唤醒 agent），行为者都是调度器，对象和案件 ID 都是案件 ID
- 唤醒 agent 一个动作覆盖 PM、Critic 和 Reviewer：说明里写 agent，Proposal 的"唤醒 PM 和 Critic"都在里面；质询轮次写在进入 PM 回应的阶段变化里，不另设动作
- 文字都在 `case_entries.py`，中文名在 `names.py`（案件类型、阶段、结束原因、事件类别、决定），和 `ACTIONS`、`ORDER_STATUSES` 一样
- `case_trigger(trigger, triggered_at) -> CaseTrigger` 按「案件记录」design D8 的表写触发原因；定时心跳的检查项按案件类型写（"持仓复查日到期"等，case-types spec「触发」的表）；卖出成交现在只写股票，怎样卖出的由「事件检测」给触发加上以后再写

### D13 测试

- `tests/modules/scheduler/domain/test_stage.py`：case-stages spec「风控」的新行和改过的场景（`risk_checked` 的 `confirming`）
- `tests/workflows/test_case_entries.py`：`case_trigger` 四种触发、退回原因的文字
- `tests/workflows/test_case_conclusion.py`：检查的顺序和每一项（纯函数，构造结论、`CaseDefinition`、股票池记录、快照、任务记录）；`thesis_updates` 的三种情形、浮动盈亏比例、日程项
- `tests/workflows/test_case_flow.py`：`CaseFlow` 的每个方法，用 `tests/fakes/` 的内存实现（任务、费用、案件记录、快照、股票池、跟进、程序日志、通知）、`ScriptedBackend`（「替身」的测试替身）和 `FakeClock`：上下文、调用和唤醒的条目、告警、派发、结论生效的每一行、交接、结束的 Task 数和调用次数
- `tests/workflows/test_case_workflow.py`：真的 DBOS 和 testcontainers 的数据库（要开着 OrbStack），`ScriptedBackend` 按场景给回复，`tests/fakes/plan_handoff.py` 的 `ScriptedHandoff`（在工作流函数里被调用，不启动工作流）；spec 的整条路径、进展、同一个案件开始两次、并行的任务、崩溃恢复（照 `test_order_workflow.py` 的 `Crash`）
- `tests/modules/gateway/`：`case_costs` 的服务和 repo（repo 要开着 OrbStack）
- 阈值从配置读取，不在测试里硬编码

## Risks / Trade-offs

- [交接靠下单流程送回结果，送丢了案件一直等，这只股票的后续案件一直排队] → 「方案接下单」保证每条路径都送回，意外错误送 `FAILED`（D4 第 2 条）；出错停下的下单流程可以按程序日志由人处理
- [出错停下的案件在案件记录里一直是进行中] → 队列按 DBOS 状态不再算它运行中（D2）；和「案件记录」的风险一样，由人按程序日志处理
- [案件工作流的 step 多，每个案件几十个] → DBOS 每个 step 一行记录，每天几十个案件，量小
- [「方案接下单」按本变更 D4 的接口写，两边要一起定稿] → 它的 tasks 1.1 在 apply 前核对本变更已经归档、接口一致；本变更的 delta 按归档后的 agent-calls、case-stages 主 spec 写
- [真实模型的回复比替身大，对话放在 step 的输出里，每一轮都存一份] → 每个案件最多几次 PM 和 Critic 调用，M5 发现太大时再改成按版本存对话

## Migration Plan

- 没有迁移、没有装配：部署以后行为不变
- 前提：「案件记录」「替身」已经 apply 和归档（已经满足）；apply 时核对 agent-calls、case-stages 的 delta 仍和主 spec 对得上
- 回滚：删掉新增的文件；网关多出的读取和程序日志的动作没有人用，可以留着
