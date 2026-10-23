# Design

## Context

现状（按 add-queue-runner 归档以后的代码说，本变更在它之后实现）：

- 紧急退出流程（`workflows/emergency_flow.py`）在巡检线程里：`advance(plan, judgement)` 先处理确认（`Gate`），第一次处理（`record.announced` 为假）写程序日志的紧急风险退出，然后拿锁做 `_advance`：第 2 步释放这只股票有有效预留、没有订单的全部方案（案件的和 `/plan` 的），第 3 步撤单，第 4 步检查、再校验、提交清仓单；锁外推送开始紧急退出的告警（`start_text`）。重启以后、本地备用止损越过确认以后 `announced` 重新为假，再推送一次开始的告警
- 第 2 步必须在锁里、清仓以前同步做：减仓方案的卖出预留会从清仓的可卖数量里扣掉
- 被释放了预留的方案，它的下单流程不知道：等确认的照常等到按钮或截止时刻，等开盘、等重试的到再校验时得到"方案已结束"
- 案件工作流（`case_workflow.py`）只在 `_deliver` 的等待里 `DBOS.recv(PLAN_TOPIC)`；`_PLAN_EVENTS` 有作废（add-queue-runner D7）没有中止。`running_cases()` 列出运行中的案件和进展（D5）
- 下单流程在每处等待里收 `DECISION_TOPIC` 上的作废（D6）：`void_plan(plan_id)` 送 `Stage.VOIDED`，下单流程报告确认消息已作废、释放（原因"新案件排队"）、已经确认的告警，送回 `PlanEvent.VOIDED`
- `QueueRunner` 的 `DbosCases`（`queue_runner.py`）有 `running`、`start`、`exists`、`void`；`run_round` 里把进展换成 `RunningCase`、`PendingPlan` 的几行是内联的（D4）
- 调度器 domain：`emergency_decision(snapshot: QueueSnapshot, symbol)` 只读快照里的运行中的案件和方案；`CaseState.abort()` 适用于复盘以外的运行中的阶段
- `PlanHandoff` 协议只有 `submit`；`CasePlanHandoff` 在 `order_workflow.py`
- DBOS 3.2.0（`_sys_db.py`）：`send` 给不存在的工作流 ID 抛 `DBOSNonExistentWorkflowError`（外键），给已经结束的工作流照常写入；`recv` 按 `created_at_epoch_ms` 取最早一条没消费的消息，`timeout_seconds=0` 时没有就立即交回 None，结果记成一步，重放时交回同样的值

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/scheduler/domain/queue.py   (改) emergency_decision 只收运行中的案件和方案（D6）
workflows/
  emergency_flow.py   (改) RunningCases 端口；开始紧急退出时取得决定、交出中止；start_text 写中止的案件
  case_workflow.py    (改) _PLAN_EVENTS 加中止；_aborted 各处收中止；被中止时交回方案；abort_case；queue_view
  plan_handoff.py     (改) PlanEvent.ABORTED；PlanHandoff.abandon
  order_flow.py       (改) VoidCause、void_reason；void_waiting（等确认时的作废）、void_queued 按原因释放、告警
  order_workflow.py   (改) void_plan 带原因；收到交回的作废以后不送回；CasePlanHandoff.abandon
  queue_runner.py     (改) DbosCases.abort；DbosCases.void 写明新案件排队；run_round 用 queue_view
bootstrap/app.py      (改) 一个 DbosCases 交给紧急退出流程和排队运行
```

- 模块之间传递的都是已有的类型：调度器的 frozen dataclass（`RunningCase`、`PendingPlan`、`EmergencyDecision`、`CaseState`），工作流层的 `CaseProgress`（frozen dataclass），contracts 的 `TradePlan`（Pydantic）。没有新的 Pydantic 模型；contracts、shared、别的模块不改；调度器 domain 只改一个函数的参数，纯区域白名单不变
- `emergency_flow.py` 新 import `case_workflow.py`（`CaseProgress`、`queue_view`）和调度器包根的 `emergency_decision`、`RunningCase`。`case_workflow.py` 不 import `emergency_flow.py`、`order_workflow.py`，没有环；`queue_runner.py` 照旧 import `case_workflow.py` 和 `order_workflow.py`

## Goals / Non-Goals

**Goals:**

- 清仓不变：释放预留照旧在锁里、清仓以前做；中止和交回只让案件和下单流程早点结束
- 案件的方案不管走到交接的哪一步（还在审核、正在交接、已经交给下单流程），都随中止结束，不留下一条还能确认的消息
- 中止和交回都是 DBOS 的持久化消息：进程重启不丢，重放时同样的结果

**Non-Goals:**

- 不打断正在进行的 step（模型调用、子 agent 任务、下单流程的一步）
- 紧急退出流程不另记"中止过哪些案件"：开始紧急退出的告警写明中止的案件，案件自己记结束原因
- 不改排队运行的决定和 `exiting()`

按「编程风格」省略的情况：

- 排队运行一轮里读了正在紧急退出的股票以后、启动案件以前，这只股票开始紧急退出（毫秒级）：启动的案件在紧急退出期间照常运行，不中止
- 重启以后、巡检第一轮以前，排队运行启动了清仓单已经建立、还没结束的股票的案件（add-queue-runner D9 的省略）：不中止；清仓单通常几秒内结束。等撤单的股票重启以后再开始紧急退出时照样中止（emergency-exit-flow spec 的场景）
- 交出中止以后、紧急退出拿到锁以前，锁正被别的线程占着，案件先交回了方案、下单流程先释放了预留：开始紧急退出的告警不列这个方案（中止的案件照样列出），释放预留由下单流程写，原因相同
- 中止送到以前下单流程已经拿锁提交了订单、送回订单已提交排在中止之后：案件记成紧急退出中止；订单由紧急退出第 3 步撤掉
- 排队运行（新案件排队）和中止差不多同时让同一个方案作废：下单流程按先收到的那条处理，后一条没人读；案件按先收到的结束（方案被作废或紧急退出中止）
- 被中止的案件没读的中止、下单流程没读的作废留在 DBOS 的消息表里：每次紧急退出最多几条，不清理

## Decisions

### D1 开始紧急退出：推送开始告警的那一次处理

中止放在 `advance` 里 `not record.announced` 的那一段：写完紧急风险退出、拿锁以前。

- 这一段就是「告警」第一行的"开始紧急退出"：不用确认的第一轮；要确认的在确认以后（`_settle` 交回真的那一轮）；重启以后（"在等撤单结束，重启以后仍然触发"再推送一次）和本地备用止损越过确认（`_bypass` 把 `announced` 置假）也各一次。不另设标志
- 和 add-queue-runner D9 的 `exiting()` 同一刻起算：从这一刻起排队运行不再启动这只股票的案件，这一刻在运行的由本变更中止
- 等撤单的之后几轮不再中止：这期间排队运行不启动这只股票的案件，再取一次决定只会得到同一个还没收中止的案件（它可能正在一次模型调用里，要几分钟），重复交出没有意义
- 要确认时等确认以后再中止，见 proposal「需要人决定」第 1 条

不选的做法：

- 触发时（请求紧急退出的确认时）就中止：人拒绝时案件白白结束；和"等确认期间什么都不动"不一致
- 紧急退出期间每一轮都取一次决定、只给新出现的案件交中止（要记交过哪些）：能补上省略清单的前两条，代价是每一轮多读一次 DBOS、多一份内存状态；两种情况都是毫秒到几秒的窗口

### D2 紧急退出流程只中止案件，案件的方案由中止的案件交回

add-queue-runner D11 设想的是紧急退出流程自己用 `void_plan` 作废决定里的方案。写下来有三个问题：

- 决定里的方案取自进展，包括还在审核的（Critic 审核、PM 回应、风控）：它们没有下单流程，`void_plan` 会抛 `DBOSNonExistentWorkflowError`
- 正在交接的案件（风控阶段，交接的检查在读券商账户、还没拿锁）：紧急退出读进展时方案还在审核，放开锁以后它才预留、启动下单流程、请求确认。紧急退出看不到这个下单流程，它会一直等确认
- 案件中止时"要释放预留"（case-stages spec「结束原因」），而释放预留由下单那一侧负责（case-workflow spec「交给下单」）：方案已经交给下单那一侧的案件被中止，本来就该告诉下单那一侧

所以：

- 紧急退出流程只按决定交出中止（D3）
- 预留照旧由第 2 步在锁里释放，清仓的股数不受影响
- 案件在交给下单以前收一次中止，中止送到时还没交接的不再交接；交接以后在等的时候收到中止，把方案交回下单那一侧（D4、D5）。正在交接的那种情况，案件在交接做完、开始等的第一下就收到中止，交回
- 决定里的 `void` 不另外执行：这些方案都属于被中止的案件，审核阶段的随案件结束，交给下单的由案件交回

不选的做法：紧急退出流程作废决定里等人工确认、等下单的方案（`void_plan`），再把这些方案和第 2 步释放的对一遍。能用上决定的 `void`，但正在交接的那种情况仍然漏掉，要另外补。

### D3 中止在拿锁以前交出

case-queue spec「紧急退出」要求在撤单和生成清仓方案之前中止。交出中止只是一条消息，不拿锁，案件自己结束：

- 案件收到中止以后交回方案，下单流程要拿锁才释放；紧急退出交出中止以后立即拿锁做第 2 步，几乎总是先拿到，第 2 步释放、写释放预留、在开始的告警里列出作废的方案，下单流程的释放什么也不改
- 锁正被别的线程占着时下单流程可能先拿到，见省略清单

### D4 案件收中止

```python
class PlanEvent(StrEnum):
    ...
    ABORTED = "aborted"  # 紧急退出中止，紧急退出流程送来


_PLAN_EVENTS[PlanEvent.ABORTED] = CaseState.abort


def abort_case(case_id: UUID) -> None:
    """按案件 ID 经 PLAN_TOPIC 送中止；在工作流以外调用（巡检线程）。"""
    DBOS.send(str(case_id), PlanEvent.ABORTED.value, topic=PLAN_TOPIC)


def _aborted(case: _Case) -> bool:
    """收一次中止，不等；收到就按 _PLAN_EVENTS 转换。交回案件是否已经结束。"""
    message = DBOS.recv(PLAN_TOPIC, timeout_seconds=0)
    if message is not None:
        case.advance(_PLAN_EVENTS[PlanEvent(message)](case.state))
    return case.state.stage is CaseStage.ENDED
```

- 收的地方（case-workflow spec「紧急退出中止」的表）：`_research` 的循环开头（调用 PM 以前）和 PM 派发任务以前；`_review` 的循环开头（调用 Critic 和 `_respond` 调用 PM 回应以前）和 Critic 补充研究生成新的一版快照以前；`_deliver` 调用 `handoff.submit` 以前。收到以后 `return`，后面的 `_review`、`_deliver` 看到阶段已结束什么都不做
- 交接以前 `PLAN_TOPIC` 上只会有中止；别的结果送到这里是错误，`_PLAN_EVENTS` 里对应的转换在这些阶段抛 `ValueError`，案件出错停下（case-stages spec「已结束的案件和不适用的事件」），不另外判断
- `_deliver` 的等待里：转换以后如果是中止，`_parts().handoff.abandon(case.plan.plan_id)`（D5）。中止和送回的结果在同一个主题上按送到的先后收，先收到哪个按哪个
- 复盘案件只调用 Reviewer，不收：紧急退出的决定不中止复盘
- 正在进行的一次调用不打断：调用是一步，DBOS 不能从外面中断 step，费用也已经花了。交回以后照常处理到下一个收的地方，PM 的结论照常检查、生效（proposal「需要人决定」第 2 条）
- 每处多一次 `recv`（DBOS 记两个 function ID：recv 和它的超时）。案件工作流的步骤变了，部署前不能有 PENDING 的 `case`（Migration Plan）

不选的做法：在等子 agent 任务时也收（`handle.get_result()` 换成轮询）：任务有 5 分钟的时限，提前结束省不了已经开始的调用；要改派发的写法。

### D5 交回方案和作废的原因

```python
class PlanHandoff(Protocol):
    def submit(self, case_id: UUID, plan: TradePlan, critic: CriticOutcome) -> Handoff | None: ...
    def abandon(self, plan_id: UUID) -> None:
        """案件被紧急退出中止，在工作流函数里调用：这个方案的下单流程按中止作废。"""


class VoidCause(StrEnum):
    """作废一个方案的原因，也是 DECISION_TOPIC 上作废的消息值。"""

    NEW_CASE = "voided"  # 这只股票有新案件排队（排队运行），值和 Stage.VOIDED 相同
    ABORTED = "aborted"  # 案件被紧急退出中止，交回方案


def void_plan(plan_id: UUID, cause: VoidCause) -> None:
    DBOS.send(str(plan_id), cause.value, topic=DECISION_TOPIC)
```

- `CasePlanHandoff.abandon(plan_id)` 是 `void_plan(plan_id, VoidCause.ABORTED)`；`DbosCases.void(plan_id)` 改为 `void_plan(plan_id, VoidCause.NEW_CASE)`，排队运行的 `CaseControl` 不变
- `abandon` 在案件工作流的工作流函数里调用，`DBOS.send` 记成案件的一步，重放时不重送。下单流程一定已经存在：等的时候才会交回，交接时已经启动了它
- 下单流程收到作废消息时，确认的进展都是 `Stage.VOIDED`（按钮回调靠它判断不在等确认），原因只决定下面三项（order-flow spec「案件中止时作废它的方案」的表）：

| 项 | `NEW_CASE` | `ABORTED` |
| --- | --- | --- |
| 释放预留的原因 | "<股票> 有新案件排队，方案作废" | "<股票> 紧急退出：作废还没下单的方案"（和 `emergency_flow` 第 2 步的相同） |
| 已经确认的告警（`void_queued`） | 推送 | 不推送 |
| 送回案件 | `PlanEvent.VOIDED`；已经有订单的送订单已提交 | 不送 |

- 等确认时收到的作废由 `OrderFlow.void_waiting(plan, requested, cause)`（step `order_plan.void_waiting`）处理，`settle` 只处理确认的结果；`void_queued` 多一个 `cause` 参数。释放的说明由 `void_reason(symbol, cause)` 给出，紧急退出第 2 步也用它。`order_workflow` 解码 `DECISION_TOPIC` 的消息：确认、拒绝是 `Stage`，两种作废是 `VoidCause`
- 收到 `ABORTED` 以后不再送回任何结果，包括之后的步骤出了意外的错误时的出错：`order_plan` 的工作流函数在收到它的那一刻记下"已经交回"，`_report` 和出错时的送回都看它。这个标志由 `recv` 的结果决定，重放时同样重建
- 释放通常什么也不改（D3），`release` 只在有有效预留时写释放预留（program-log spec「风控引擎的动作」），program-log spec 不改

### D6 emergency_decision 只收运行中的案件和方案

```python
def emergency_decision(
    running: tuple[RunningCase, ...], plans: tuple[PendingPlan, ...], symbol: str
) -> EmergencyDecision: ...
```

- 它原来收整份 `QueueSnapshot`，只读其中这两项。紧急退出流程拿不到排队的案件、股票的状态和预算，要组整份快照只能填假的值；改成只收它读的两项。规则不变，case-queue spec 不改
- 两项从运行中的案件换来，和排队运行的 `run_round` 相同。把那几行提到 `case_workflow.py`：

```python
def queue_view(
    running: Sequence[tuple[UUID, CaseProgress]],
) -> tuple[tuple[RunningCase, ...], tuple[PendingPlan, ...]]:
    """运行中的案件换成队列的类型：每个案件一个 RunningCase；有方案、方案有阶段的一个 PendingPlan。"""
```

  `run_round` 和紧急退出流程都用它
- 紧急退出只用决定里的 `abort`（D2）；`plans` 照样交进去，决定的含义不变

### D7 紧急退出流程的端口和装配

```python
class RunningCases(Protocol):
    """紧急退出对运行中的案件的那两下；生产是 DbosCases，测试换成内存的替身。"""

    def running(self) -> tuple[tuple[UUID, CaseProgress], ...]: ...
    def abort(self, case_id: UUID) -> None: ...
```

- `EmergencyFlow` 构造时多一个 `cases: RunningCases`。`advance` 的开始那一段：`queue_view(cases.running())` → `emergency_decision(...)` → 有 `abort` 就 `cases.abort(abort)`，交回这个 `RunningCase` 给告警用
- `DbosCases.abort` 是 `case_workflow.abort_case`；`running` 原来就是 `running_cases()`。一个 `DbosCases` 同时满足 `CaseControl` 和 `RunningCases`
- bootstrap：先建 `DbosCases()`（没有状态），交给 `EmergencyFlow` 和 `QueueParts`。`EmergencyFlow` 照旧先于排队运行建出来（add-queue-runner D12）
- 读运行中的案件和交出中止都在锁外；出错照 emergency-exit-flow spec「一轮里的失败」，这一项跳过（巡检的 `_item` 已经这样处理）

### D8 开始紧急退出的告警

`start_text(trigger, step, aborted)`：原因之后、作废的方案之前加一句"中止运行中的案件：<案件类型>（案件 <ID>）。"，案件类型用 `names.CASE_TYPES`。没有中止的不写。程序日志的紧急风险退出不变：它写在取得决定以前。

### D9 `/plan` 的方案不动

第 2 步照旧释放它的预留，下单流程照旧再校验时得到"方案已结束"或者到截止时刻超时（proposal「需要人决定」第 3 条）。要改时，`advance` 在锁外把 `step.voided` 里不属于中止的案件的方案也 `void_plan(plan_id, VoidCause.ABORTED)`。

### D10 重启以后

- 中止、交回都是 DBOS 的消息：交出以后进程退出，重启以后案件在下一个收的地方、下单流程在它的等待里照样收到
- 案件已经收了中止：`recv` 的结果记在 DBOS 里，重放到那里同样结束、同样交回（`abandon` 的 send 也已经记下，不重送）
- 紧急退出流程的记录在内存里：重启以后再推送开始的告警的那一次再取一次决定、再中止（D1）。已经结束的案件不在运行中的案件里，不会再收到

### D11 测试

- `tests/modules/scheduler/domain/test_queue.py`、`test_queue_machine.py`：`emergency_decision` 换参数，场景不变
- `tests/workflows/test_case_workflow.py`（真的 DBOS，要开着 OrbStack）：case-workflow spec「紧急退出中止」的场景和「交给下单」新加的「等的时候收到中止」；交回用记下调用的 `PlanHandoff` 替身；中止用 `abort_case`
- `tests/workflows/test_order_workflow.py`（真的 DBOS）：order-flow spec「案件中止时作废它的方案」的六个场景、「结果送回案件」的「案件中止交回的方案不送回」；`tests/workflows/test_order_flow.py`：`void_waiting`、`void_queued` 两种原因的释放原因和告警
- `tests/workflows/test_emergency_abort.py`（新）：emergency-exit-flow spec「中止这只股票的案件」的场景，用 `tests/fakes/cases.py` 的 `FakeCases`（加 `abort`，记下中止的案件 ID 和先后）；`tests/workflows/builders.py` 建 `EmergencyFlow` 时交 `FakeCases`
- `tests/workflows/test_queue_runner.py`：原有场景不变（`run_round` 改用 `queue_view`）；`DbosCases` 是 DBOS 的实现，由 `tests/bootstrap/` 原有的「新案件排队时作废等确认的方案」覆盖
- `tests/bootstrap/`：替身的案件在等人工确认时这只股票紧急退出：案件结束：紧急退出中止，确认消息已作废
- 测试只在本地（tests/ 被 gitignore）

## Risks / Trade-offs

- [清仓以前多读一次运行中的案件：出错时这一项推迟到下一轮（15 秒）] → 读的是同一个 PostgreSQL，它不可用时清仓本来也做不了；进展读不出来（改了 `CaseProgress` 的字段）由部署规则避免（下一条）
- [案件工作流、下单流程的步骤变了] → 部署前确认没有 PENDING 的 `case` 和 `order_plan`（Migration Plan）
- [被中止的案件要等正在进行的调用交回才结束，最长是一次 PM 调用加一轮子 agent 任务的时限（几分钟）] → 这期间它不会交给下单（交给下单以前收中止），预留在第 2 步已经释放；排队运行一直把这只股票当作忙
- [中止依赖 add-queue-runner 的 `running_cases`、`DbosCases` 和下单流程收作废] → 本变更在 add-queue-runner 归档以后实现；tasks 的第 1 组先核对这些已经在代码里

## Migration Plan

- 部署以前：add-queue-runner 已经部署；确认 `dbos.workflow_status` 里没有 PENDING 的 `case` 和 `order_plan`（「触发」组接上以前不会有案件；`/plan` 的方案等它结束）
- 部署：按 `deploy/README.md`「更新」；没有迁移
- 回滚：回到上一版代码，回滚前同样确认没有 PENDING 的 `case` 和 `order_plan`
