# Design

## Context

现状：

- 下单流程（`workflows/order_workflow.py`）：`OrderPlanIntake.submit(plan)` 写录入方案、首次检查（`order_flow.check_plan`），通过或调整时以方案 ID 启动 DBOS 工作流 `order_plan(plan, decision)`：请求确认 → 等按钮（`DBOS.recv`）→ `settle` → 等开盘、再校验、提交，直到 `PlanEnd`。每个 step 调用 `OrderFlow` 的同名方法；部件在 DBOS launch 之前 `install`。确认的进展用 `DBOS.set_event(CONFIRMATION, Stage)` 发布，按钮回调只送给在等确认的方案
- 确认规则已经有纯函数 `notification.case_plan_confirmation(action, critic_approved=, degraded=, config=)`，给出 `CaseConfirmation(required, alert_on_timeout)`；`NotificationConfig.auto_execute_model_exits` 默认关
- 确认消息由 `integrations/discord/messages.py` 的 `confirmation_content(ConfirmationRequest)` 写；`ConfirmationRequest` 只有方案、风控结果和截止时刻
- 执行模块：`submit(plan, decision)` 用 `new_order` 建订单；交易记录由订单（`trade_of_order`）和止损单（`trade_of_stop`，来源方案取 `opening_order`）推出；订单和交易记录都存在只追加的版本表里，记录本身以 JSON 存，读回时用 Pydantic 校验
- 股票池的代码操作（`bought`、`closed_by_code`、`closed_to_watch`、`closed_and_removed`）已经有，还没有调用方；跟进的 `status_changed` 同样
- 盘中巡检每一轮：跟进订单 → 同步预留 → 对齐止损单 → 健康判定 → 紧急退出 → 收盘对账
- bootstrap 只装配了交易侧；股票池、跟进、案件记录有仓储实现，没有装配
- `/plan` 的板块是自由文字，说明是「板块，比如 科技」；股票池的板块是 `companydata.SectorEtf` 的代码
- DBOS 3.2.0 不允许在 step 里启动工作流：`DBOSContext.create_start_workflow_child` 里 `assert cur_ctx.is_workflow()  # Not in a step`

动机见 proposal.md，要求见 specs/。

### 前提：add-case-workflow 的 D4 要先改成这样

本变更按下面的接口写；add-case-workflow 定稿时照改（它还没有提交）：

1. 交接端口、交接的结果、送回的结果和送回的入口放在 `workflows/plan_handoff.py`，不放在 `case_workflow.py`：`order_workflow.py` 要 import `report_plan`，bootstrap 装配下单流程时就不会 import 案件工作流（add-case-workflow 的 D10 要求不 import）
2. `submit` 在案件工作流的工作流函数里调用，不放在 step 里：它要启动下单流程，DBOS 不允许在 step 里启动工作流。风控结果写进案件记录放在随后的一个 step
3. `submit` 给出 `Handoff`：风控结果之外还有要不要人工确认。case-stages 的「风控」加一行「通过或调整，不要人工确认 → 进入等下单」，「人工确认」的第一句跟着改；`CaseState.risk_checked(outcome, *, confirming)`；case-workflow spec「交给下单」的第一张表加这一行。案件阶段的转换由案件工作流推进，所以这几处都在 add-case-workflow 里改，本变更不改 case-stages

```python
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

`PlanEvent` 的六个值和 add-case-workflow 的 D4 相同。

### 模块边界

```
workflows/
  order_flow.py      (改) CaseSource、CaseCheck；OrderFlow.check_case_plan；request_confirmation 带案件的
                     意见；settle 给退出方案告警；attempt 带来源案件；自动执行和超时告警的文字
  order_workflow.py  (改) order_plan 带 source、各结局 report_plan；CasePlanHandoff；step check_case_plan
  pool_sync.py       (新) PoolSync：股票池跟上持仓；pool_change 纯函数
  patrol.py          (改) 第 3 步之后加股票池跟上持仓
  emergency_flow.py  (改) 提交订单时 case_id=None
modules/notification/
  domain/confirmation.py  (改) Disputed、Unreviewed、CaseNote；ConfirmationRequest 加 note
  service.py              (改) request_confirmation 接收 note
modules/execution/
  domain/order.py    (改) Order.case_id；new_order 接收 case_id
  domain/trade.py    (改) Trade.case_id；trade_of_stop 接收来源的开仓订单
  service.py         (改) submit 接收 case_id；新增 held_symbols、orders
modules/programlog/domain/entry.py  (改) Action.AUTO_EXECUTE
integrations/discord/
  messages.py        (改) 确认消息的案件意见和截短
  gateway.py、plans.py (改) /plan 的板块选项
bootstrap/app.py     (改) 装配股票池、跟进、案件记录
```

- 跨模块传递的 Pydantic 模型：`TradePlan`、`RiskDecision`、`CriticReview`、`PlanResponse`（下单流程从案件记录读出，交给通知模块）、`WatchConclusion`、`HoldingConclusion`、`InvestmentThesis`、`WatchThesis`（股票池跟上持仓从案件记录读出，交给股票池）
- 在 workflows 里组装的 frozen dataclass：`CaseSource`、`CaseCheck`、`Handoff`；模块给出的 `CaseConfirmation`、`CaseNote`、`Order`、`PoolEntry`、`CaseRecord`、`ProposalRecord`
- 模块之间不新增依赖：通知模块只多 import contracts 的 `review`；执行模块只多一个可以为空的字段和两个读取。调度器的 `CriticOutcome` 只在 workflows 里用；Discord 适配器多 import `modules.companydata` 的包根（`SectorEtf`），integrations 本来就可以 import 模块的包根

## Goals / Non-Goals

**Goals:**

- `/plan` 的方案下单的行为和现在完全一样：不送回、不附意见、不告警超时；它的成交只在卖完股票池里的持仓时改股票池（转为候选）
- 案件不会一直等：下单流程每个结局都送回，意料之外的错误也送回
- 股票池的持仓和券商持仓对得上：按状态比对，漏掉一轮、重启以后都能补上

**Non-Goals:**

- 不改案件阶段的转换和案件工作流（见「前提」）
- 不加表、不加迁移

按「编程风格」省略的情况：

- 买单挂着时股票被移出股票池、之后成交：股票不在池子里，不看；这只持仓和 `/plan` 的一样不在股票池里
- 本人用 `/plan` 买入股票池里的候选或关注：股票池不动，每一轮读一次它的订单再跳过
- 股票池改了状态、交给跟进审批以前进程退出：这次的跟进不作废
- 交接时启动下单流程失败（数据库出错）：异常原样抛进案件工作流，案件的运行出错停下，预留不释放
- 出错停下的下单流程不释放预留，和 `/plan` 的一样（add-program-log 时已接受）；送回出错以后案件按系统故障结束
- 送回结果时数据库出错：送回出错也失败，案件一直等
- 确认消息的意见在请求确认时读一次，之后不更新
- 部署时有进行中的下单流程：按 add-order-flow 的部署规则等它们结束再部署（Migration Plan）
- 交接以后案件被中止或方案被作废：「紧急退出中止案件」「排队运行」；下单流程照常送回，已结束的案件不理会
- 部署以前的订单、交易记录在部署以后第一次保存时多写一个版本（多出空的来源案件），即使别的内容没变

## Decisions

### D1 交接：首次检查是 step，下单流程是案件工作流的子工作流

```python
class CasePlanHandoff:
    """满足 PlanHandoff，见 order-flow spec「案件的方案」。只能在案件工作流的工作流函数里调用。"""

    def submit(self, case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> Handoff | None:
        checked = _check_case_plan(case_id, plan, critic)  # step order_plan.check_case_plan
        if checked is None:
            return None
        if checked.source is None:  # 风控拒绝
            return Handoff(checked.decision, confirming=False)
        with SetWorkflowID(str(plan.plan_id)):
            DBOS.start_workflow(order_plan, plan, checked.decision, checked.source)
        return Handoff(checked.decision, checked.source.confirmation.required)
```

- `OrderFlow.check_case_plan(case_id, plan, critic) -> CaseCheck | None`：和 `/plan` 的首次检查共用读账户（不拿锁）、读熔断和降级、拿锁检查并预留、写风控检查；`BrokerUnavailableError` 时写 WARNING、返回 None。通过或调整时用同一次读到的 `degraded` 调用 `case_plan_confirmation`（`critic_approved = critic is CriticOutcome.APPROVED`），不要确认时写程序日志的自动执行（D6），给出 `CaseCheck(decision, CaseSource(case_id, critic, confirmation))`；拒绝时 `source` 为 None
- 下单流程是案件工作流的子工作流，ID 用方案 ID（`SetWorkflowID`），和 `/plan` 的一样：按钮回调按方案 ID 找它。案件工作流重放时 DBOS 给出记下的 step 输出和子工作流，不再检查、不再启动
- `CasePlanHandoff` 没有自己的部件：step 用 `install` 交进来的 `OrderFlow`。「排队运行」装配案件工作流时交给它一个 `CasePlanHandoff()`

不选的做法：

- 在案件工作流的 step 里调用 `submit`：DBOS 不允许在 step 里启动工作流（见 Context）
- 首次检查放进下单流程的第一个 step、`submit` 只启动工作流：风控结果要等子工作流跑完第一个 step 才能拿到，案件工作流得另外等一个事件

### D2 下单流程带上来源

```python
@dataclass(frozen=True, slots=True)
class CaseSource:
    """案件的方案从哪个案件来，交接时的决定，见 order-flow spec「案件的方案」。"""

    case_id: UUID
    critic: CriticOutcome
    confirmation: CaseConfirmation


@DBOS.workflow(name="order_plan")
def order_plan(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    """一个方案的下单流程；source 是案件的方案的来源，/plan 的为 None。见 D3。"""
    try:
        return _run(plan, decision, source)
    except Exception:
        _report(source, PlanEvent.FAILED)
        raise
```

- `/plan` 的 `OrderPlanIntake.submit` 传 `source=None`，step 的先后和名字不变
- `source` 交给三个 step：`request_confirmation`（附意见，D4）、`settle`（超时告警，D4）、`attempt`（订单的来源案件，D5）。`timing`、`void_expired` 不变
- 不要确认的方案（`source.confirmation.required` 为假）跳过请求确认和等按钮，直接进入 `_execute`；不发布确认的进展，按钮回调照「按钮的结果只送给在等确认的方案」报错

### D3 结果送回案件

```python
def _run(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    if source is not None and not source.confirmation.required:
        return _executed(plan, decision, source)
    DBOS.set_event(CONFIRMATION, Stage.WAITING)
    requested = _request_confirmation(plan, decision, source)
    if requested is None:
        stage = Stage.UNSENT
    else:
        choice = DBOS.recv(DECISION_TOPIC, timeout_seconds=requested.wait_seconds)
        stage = Stage.TIMED_OUT if choice is None else Stage(choice)
    DBOS.set_event(CONFIRMATION, stage)
    _settle(plan, decision, stage, requested, source)
    _report(source, _EVENTS[stage])
    if stage is not Stage.CONFIRMED:
        return PlanEnd(stage.value)
    return _executed(plan, decision, source)


def _executed(plan: TradePlan, decision: RiskDecision, source: CaseSource | None) -> PlanEnd:
    end = _execute(plan, decision, source)
    _report(source, PlanEvent.SUBMITTED if end is PlanEnd.SUBMITTED else PlanEvent.RECHECK_VOIDED)
    return end


def _report(source: CaseSource | None, event: PlanEvent) -> None:
    if source is not None:
        report_plan(source.case_id, event)
```

- `_EVENTS`：确认 → `CONFIRMED`，拒绝 → `DECLINED`，超时和发不出去 → `TIMED_OUT`
- `/plan` 的方案 step 的先后不变：设置确认的进展、请求确认、（等按钮）、设置确认的进展、`settle`
- `_execute` 的 `PlanEnd.SUBMITTED` 包括券商拒绝受理和作废时已经有订单（order-flow spec「提交的结果」），`VOIDED` 包括再校验拒绝、到有效期、对账不一致，正好对上「订单已提交」「再校验作废」
- `report_plan` 在工作流函数里调用，DBOS 把一次 send 记成一步：重放时不重送
- 意料之外的错误：step 抛出的异常（`attempt` 自己接住、等重试的除外）到工作流函数，送回 `FAILED` 再原样抛出，工作流进入 ERROR。DBOS 的取消和接管是 `BaseException`，`except Exception` 接不到，也不该送回
- 送回的时机在 `settle` 之后：释放、告警、结束确认都做完，案件才往下走

不选的做法：工作流结束时只送一次（结局）。案件在等人工确认和等下单两个阶段都要知道，盘后确认的方案要等一夜才下单，案件要在确认时就进入等下单（case-stages spec「等下单」）。

### D4 确认消息的意见和退出方案的超时告警

通知模块 `domain/confirmation.py`：

```python
@dataclass(frozen=True, slots=True)
class Disputed:
    """Critic 有分歧：确认消息附上双方的意见，见 discord-notification spec「确认消息」。"""

    rationale: str  # PM 的方案理由：结论的理由
    reviews: tuple[CriticReview, ...]  # Critic 的每次审核，按先后
    responses: tuple[PlanResponse, ...]  # PM 的每轮回应，按先后


@dataclass(frozen=True, slots=True)
class Unreviewed:
    """Critic 不可用，方案没有经过审核。"""


type CaseNote = Disputed | Unreviewed
```

- `ConfirmationRequest` 加 `note: CaseNote | None`；`NotificationService.request_confirmation(plan, decision, note)`，`/plan` 的传 None
- `OrderFlow.request_confirmation` 按 `source.critic` 组意见：通过为 None；Critic 不可用为 `Unreviewed()`；有分歧时 `CaseRecordService.case(case_id).conclusion` 的结论的理由，`proposal(case_id)` 的审核和回应。有方案的案件一定有结论和方案记录，读不到是调用方的错误，原样抛出
- Discord：`confirmation_content` 在方案 ID 后面写意见：审核和回应按下标交替（第 i 次审核以后是第 i 轮回应，回应比审核少一轮或者没有）；修改的回应写 `PlanDraft` 的股数、限价、止损价和紧急阈值。整条超过 2000 字时从结尾截短，最后一个字是「…」，和告警的截短共用一个函数：意见在最后，截掉的只会是意见
- 用 Pydantic 模型而不是写好的文字：消息的格式属于 Discord 适配器，和确认消息的其余内容一样
- 退出方案的超时告警：`OrderFlow.settle` 在超时和发不出去时，`source.confirmation.alert_on_timeout` 为真就在释放预留以后（锁外）推送告警，文字见 order-flow spec「告警」的最后一行

不选的做法：意见放在 embed 里（上限 4096 字）。结束确认时换上结果的 embed 会把它替换掉，要改 `Gateway.edit` 保留原来的 embed；先截短，M5 用 prompt 限制长度（proposal「需要人决定」第 1 条）。

### D5 订单和交易记录的来源案件

- `Order.case_id: UUID | None = None`，`new_order(plan, decision, session, now, case_id)`；`ExecutionService.submit(plan, decision, *, case_id)`，keyword 必填：下单流程传 `source.case_id`（`/plan` 是 None），紧急退出传 None
- `Trade.case_id: UUID | None = None`：订单的取订单的；止损单的取来源开仓订单的，`trade_of_stop(stop, source, calendar)` 改为接收 `opening_order` 给出的订单（方案 ID 和来源案件都从它取）
- 默认值 None 只为读回旧的记录：版本表里已经存着的订单和交易记录的 JSON 没有这个键，Pydantic 读回时用默认值，不用迁移；新写的都由调用方给出。版本表按 JSON 比较是否有变化，旧记录在部署以后第一次保存时多出 `"case_id": null`，多写一个版本，只有这一次（省略清单）
- 新增两个只读：`held_symbols() -> frozenset[str]`（券商的全部持仓，超时或断线抛 `BrokerUnavailableError`）、`orders(symbol) -> tuple[Order, ...]`（这只股票的全部订单，按创建的先后，只读状态库）。给 D7 用

不选的做法：交易记录不存来源案件，读的时候按方案 ID 去案件记录查。股票池跟上持仓也要订单的来源案件，每次都要跨模块查；订单本来就是执行模块记方案事实的地方。

### D6 程序日志

- `Action.AUTO_EXECUTE`（自动执行），行为者系统，对象方案 ID，在 `check_case_plan` 里紧接风控检查写：决定就在这一步做出，下单流程启动失败时案件也会出错停下（省略清单）
- 下单那一侧的条目照旧以方案 ID 为对象，不写案件 ID：program-log spec 的案件 ID 只在动作的要求写明时才有；方案 ID 经案件记录的方案记录对上案件。通知模块写的请求确认、结束确认、告警也就不用改接口
- 「模型退出自动执行」打开这件事不在启动时另写：每个自动执行的方案各写一条，看程序日志就知道它在起作用

### D7 股票池跟上持仓

`workflows/pool_sync.py`：

```python
@dataclass(frozen=True, slots=True)
class Bought:
    """候选或关注有了持仓，最近创建的买单来自案件 case_id。"""

    case_id: UUID


@dataclass(frozen=True, slots=True)
class SoldByCase:
    """持仓卖完，最近创建的订单是案件 case_id 的卖单并且全部成交；是不是清仓看那个案件的结论。"""

    case_id: UUID


@dataclass(frozen=True, slots=True)
class ClosedByCode:
    """持仓卖完，不是案件的卖单全部成交：转为候选。"""


type PoolChange = Bought | SoldByCase | ClosedByCode


def pool_change(status: StockStatus, held: bool, orders: Sequence[Order]) -> PoolChange | None:
    """按 pool-position-sync spec「股票池跟上持仓」的表，orders 是这只股票的全部订单。

    None 是不动：没有变化、/plan 的买入，或者持仓卖完了但还有没结束的订单。
    """
    ...
```

- `PoolSync(execution, pool, follow_ups, case_records, calendar)`：`targets()` 读股票池，空时不读券商；否则 `held_symbols()`，给出状态和持仓对不上的股票（候选或关注有持仓、持仓没有持仓）；读券商失败时写 WARNING、给出空。`follow(entry, held)` 读这只股票的订单，按 `pool_change` 做：
  - `Bought`：案件记录的结论是 `WatchConclusion`（决定开仓方案），`bought(symbol, conclusion.thesis, day)`
  - `SoldByCase`：结论是 `HoldingConclusion` 并且决定清仓时按 `after_close`：`closed_to_watch(symbol, watch_thesis, day)` 或 `closed_and_removed(symbol)`；减仓卖完的按 `ClosedByCode`
  - `ClosedByCode`：`closed_by_code(symbol)`
  - 状态变了以后 `FollowUpService.status_changed(symbol, before, after)`
  - `day` 是 `calendar.session_closing_after(record.conclusion.at).day`：结论时刻归属的交易日
- 巡检：第 3 步之后一项「列出股票池要跟上的股票」，然后每只股票一项，各自按「一轮里的失败」出错不外抛。不拿锁：股票池和跟进各有自己的锁，执行模块只读
- 按状态比对而不是按订单变化：漏掉的一轮（读券商失败、某只股票出错、停机）下一轮照样补上；订单变化的事件只在巡检跟进到的那一轮有，止损单的成交又在对齐止损单里，两处都要接
- 买入看"最近创建的买单"而不是"最近有成交的买单"：这一轮跟进买单失败时，订单记录还没有成交，有成交的买单可能是这只股票上一段持仓的，会用错论点；券商已经有持仓、股票池还是候选或关注，持仓只能来自最近这一笔买单
- 清仓要等订单都结束：清仓单的记录还没跟上成交时，最近的订单看起来没有全部成交，会被误判为代码触发的清仓
- 每一轮多一次读券商全部持仓（股票池不为空时）：15 秒一次，Alpaca 每分钟 200 次的额度里多 4 次

不选的做法：

- 在同步预留（第 2 步）里看买单的成交：止损单成交、紧急退出这些代码触发的清仓不经过那一步，还得另外接
- 在下单流程里等成交：下单流程在订单交给巡检时就结束了（订单由执行模块独立跟踪，Proposal「订单生命周期」）

### D8 /plan 的板块

- `buy`、`add` 的 `sector` 用 `app_commands.choices`，11 个 `Choice(name=中文板块名, value=SectorEtf 的值)`，按 manual-plans spec 的顺序；中文名放在 `integrations/discord/plans.py`，`SectorEtf` 从 `modules.companydata` 的包根 import
- `PlanEntry.sector` 仍是 `str`（选项的值）；`build_plan` 去掉板块的空白和为空的检查：Discord 只让选这 11 个
- 说明文字改为「板块」：选项名已经是中文

### D9 装配

- bootstrap 建 `StockPoolService(PostgresStockPoolStore(engine, clock), clock, calendar, StockPoolConfig(), program_log)`、`FollowUpService(PostgresFollowUpStore(engine, clock), clock, calendar, ids, FollowUpConfig(), program_log)`、`CaseRecordService(PostgresCaseRecordStore(engine, clock), clock)`
- `OrderFlow(services, notification, notification_config, case_records, clock, calendar, config)`：同一份 `NotificationConfig` 给通知、紧急退出流程和下单流程
- `SessionPatrol(..., pool=PoolSync(execution, stock_pool, follow_ups, case_records, calendar))`
- 现在就装配：下单流程和巡检已经在运行，它们的新参数要有实现；股票池在案件开始运行以前是空的，巡检这一项不读券商、不做事
- 不装配案件工作流和 `CasePlanHandoff`：没有开始运行的案件，「排队运行」装配时一起
- `TradingApp` 加 `stock_pool`、`follow_ups`、`case_records`，以后的流程从这里取

### D10 测试

- `tests/modules/execution/`：订单和交易记录的来源案件（买单、卖单、止损单、加仓换来源），旧记录没有 `case_id` 时读回为 None（repo 测试，要开着 OrbStack），`held_symbols`、`orders`
- `tests/modules/notification/`：`request_confirmation` 把意见交给通知通道
- `tests/integrations/discord/`：确认消息的四种意见和截短；`/plan` 的板块选项、方案的板块是选项的值
- `tests/workflows/test_order_flow.py`：`check_case_plan` 的每一行（通过、拒绝、读券商失败、自动执行、有分歧和降级时要确认），意见的组装，退出方案超时和发不出去的告警，`attempt` 的来源案件
- `tests/workflows/test_order_workflow.py`（真的 DBOS）：测试里定义一个收结果的工作流当作案件，用测试的工作流函数调用 `CasePlanHandoff.submit`；order-flow spec「结果送回案件」的每一行和重启不重送（照现有的 `Crash`），`/plan` 的方案不送
- `tests/workflows/test_pool_sync.py`：`pool_change` 的每一行；`PoolSync` 用内存的股票池、跟进、案件记录和执行替身，覆盖 pool-position-sync spec 的场景；`test_patrol.py` 加这一项的先后
- `tests/bootstrap/`：装配了股票池、跟进和案件记录，巡检里股票池跟上持仓

## Risks / Trade-offs

- [前提没有照改，`submit` 放在 step 里] → DBOS 在启动子工作流时断言失败，案件出错停下；tasks 第 1 组先核对接口，不对就停下
- [`order_plan` 加了参数，部署时进行中的旧流程恢复失败] → 部署前查 `dbos.workflow_status` 里没有 PENDING 的 `order_plan`（Migration Plan）
- [意见被截短，人看不到全部] → 截掉的只是意见的结尾；全文在案件记录里；M5 限制长度
- [股票池跟上持仓按"最近的订单"判断，和券商持仓同时变化时可能多等一轮] → 等的那一轮不做错事，下一轮补上
- [股票池的一只股票每一轮都出错（比如案件记录损坏）] → 每一轮写一条 ERROR，别的股票不受影响，人按日志处理

## Migration Plan

- 没有迁移；旧的订单和交易记录读回时没有来源案件
- 部署前：主机上 `dbos.workflow_status` 没有 PENDING 的 `order_plan`（等确认、等开盘的都结束了）
- 部署：按 `deploy/README.md`「更新」；`/plan` 的选项在启动注册命令时更新
- 部署以后股票池是空的，股票池跟上持仓什么都不做；`/plan` 的行为除了板块选项都不变
- 「排队运行」部署以前，本人用 `/plan close` 清掉 M2 测试留下的持仓（proposal）
- 回滚：回到上一版代码，回滚前同样确认没有 PENDING 的 `order_plan`。新写的订单和交易记录多一个 `case_id` 键，旧代码读回时忽略它（Pydantic 对 dataclass 忽略多余的键）；股票池、跟进的变化留着，旧代码不读
