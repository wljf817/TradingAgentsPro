# Design

## Context

现状：

- 调度器 `modules/scheduler` 只有 domain 和 config：`queue_decision(snapshot, config)` 从 `QueueSnapshot`（`QueuedCase`、`RunningCase`、`PendingPlan`、状态、正在紧急退出的股票、降级、两个预算标志）算出 `QueueDecision(start, drop, void)`；`emergency_decision` 留给「紧急退出中止案件」。`CaseState.void()` 只适用于等人工确认、等下单，别的阶段收到会抛 `ValueError`
- 案件工作流（`workflows/case_workflow.py`）：`install(flow, handoff)`、`start_case(case_id, definition, trigger, triggered_at)`（以案件 ID 为工作流 ID，重复开始什么都不做）、`case_progress(case_id)`（只给 PENDING 工作流的、没结束的进展）。案件状态只在 DBOS 里（add-case-workflow 的 design D2）；`_deliver` 在等人工确认和等下单时 `DBOS.recv(PLAN_TOPIC)`，按 `_PLAN_EVENTS` 转换
- 交接（`workflows/plan_handoff.py`）：`PlanEvent`、`PLAN_TOPIC`、`report_plan`；`CasePlanHandoff` 在 `order_workflow.py`，下单流程是案件工作流的子工作流，ID 是方案 ID
- 下单流程（`order_workflow.py`）：等确认是 `DBOS.recv(DECISION_TOPIC, timeout)`，按钮的结果经 `deliver_decision` 在 `Stage.WAITING` 时送进来；确认以后的等开盘、等重试是 `install` 交进来的 `sleep`（生产是 `DBOS.sleep`，测试拨钟）
- 紧急退出流程（`emergency_flow.py`）把当天的紧急退出记在内存的 `_records` 里，只在巡检线程里改
- 健康监控接线（`workflows/health.py`）的 `HealthMonitor.signals` 把模型、持仓保留预算和调度器心跳写死成"有、没有、判定的时刻"
- bootstrap 只装配了 M2 的交易侧和股票池、跟进、案件记录；网关、新闻、公司数据、粗筛、快照、替身、案件工作流都没有装配
- 迁移到 v0012；DBOS 3.2.0

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/scheduler/
  domain/queue.py     (改) QueueExit（启动、不再运行）
  ports.py            (新) QueueStore
  repo.py             (新) scheduler_queue、scheduler_queue_exits 两张表，PostgresQueueStore
  service.py          (新) QueueService：排队（分配案件 ID）、读队列、出队
modules/programlog/domain/entry.py  (改) Action 加 ENQUEUE、DROP_CASE、VOID_PLAN、BUDGET_EXHAUSTED
workflows/
  queue_runner.py     (新) QueueRunner、QueueRunnerConfig、CaseControl、DbosCases，程序日志的文字
  case_workflow.py    (改) running_cases；_PLAN_EVENTS 加作废
  plan_handoff.py     (改) PlanEvent.VOIDED
  order_workflow.py   (改) Stage.VOIDED 的处理、等开盘和等重试改为收作废的等待、void_plan、wait_decision
  order_flow.py       (改) Stage.VOIDED、settle 的作废、void_queued、作废的告警文字
  emergency_flow.py   (改) exiting
  health.py           (改) HealthMonitor 读网关和排队运行
  names.py            (改) 股票状态的中文名
bootstrap/            (改) app.py 装配和启动停止的顺序，settings.py 两个环境变量
bootstrap/migrations/versions/v0013_create_scheduler_queue.py (新)
deploy/tradingagentspro.env.example (改)
```

- 排队运行在 workflows 层，只 import 各模块的包根：调度器（`QueueService`、`QueueExit`、`queue_decision`、`QueueSnapshot`、`RunningCase`、`PendingPlan`、`CaseDefinition`、`Trigger`、`SchedulerConfig`、`StockStatus`）、网关（`CallBudgetService`、`Budget`、`BudgetStatus`、`GatewayConfig`）、健康（`HealthService`）、股票池（`StockPoolService`）、案件记录（`CaseRecordService`）、程序日志；同层的 `case_workflow`（`running_cases`、`start_case`、`CaseProgress`）、`order_workflow`（`void_plan`）、`case_entries`（`case_trigger`）、`names`
- 跨模块传递的都是已有的类型：调度器的 frozen dataclass（`QueuedCase`、`Trigger`、`CaseDefinition`、`QueueSnapshot`、`QueueDecision`、`CaseState`），网关的 `BudgetStatus`，股票池的 `PoolEntry`，contracts 的 `TradePlan`（Pydantic）和 `StockStatus`。没有新的 Pydantic 模型；contracts、shared 不改
- 调度器模块新增的 service 和 repo 只依赖 statestore（`connect` 的写法）、shared 的 `Clock`、`IdGenerator`；调度器 domain 不变（只加一个枚举），纯区域白名单不变
- `queue_runner.py` import `case_workflow.py` 和 `order_workflow.py`，这两个互相不 import，没有环

## Goals / Non-Goals

**Goals:**

- 排队的案件只有一份：调度器的两张只追加的表；运行中的案件只有一份：DBOS 里的案件工作流（add-case-workflow 的 design D2），排队运行不另记
- 每一轮从头组快照、调用纯函数，决定不依赖调用的时机；排队运行自己没有要恢复的状态，重启以后第一轮就对
- 方案的作废只由下单流程做：它知道方案在等确认、等开盘还是正在提交

**Non-Goals:**

- 不在排队、案件结束、方案换阶段时通知排队运行：每一轮都重新看（D3）
- 不加告警：调用预算用完只写程序日志（proposal「需要人决定」第 4 条）；作废的告警由下单流程推送
- 不处理紧急退出的中止（「紧急退出中止案件」）

按「编程风格」省略的情况：

- 作废在下单流程从等开盘、等重试醒来以后、提交以前送到（毫秒级）：这一次照常提交，作废不再处理，案件按订单已提交结束
- 方案进入等人工确认以后、下一轮以前本人按了确认并且盘中下了单：来不及作废（proposal「需要人决定」第 3 条）
- 重启以前作废了、案件还没结束的方案：重启以后再写一条方案作废、再送一次作废；下单流程已经结束的不理会
- 调用预算用完的日志重启以后当天再写一次
- 不再运行写了案件记录、出队以前进程退出：重启以后再写一次不再运行的程序日志，案件记录同一步只写一次（case-records spec）
- 重启以后紧急退出流程找回正在进行的紧急退出以前（巡检第一轮以前），排队运行的第一轮看不到这只股票在紧急退出，可能启动它的案件。这个案件照常运行到结束，不会被中止：「紧急退出中止案件」只在开始紧急退出的那一次中止，重启时清仓单已经挂着的不再中止；它给出的方案照常过风控和人工确认
- 数据库一直坏：每一轮写 ERROR，心跳停住，5 分钟以后健康监控进入降级模式
- 触发源在 DBOS 的 step 里排队、step 重做：多排一个案件，由「触发」组处理
- 出队表随案件数增长，不清理：每天几十行；读队列只看没出队的
- 修改 `CaseProgress`、`CaseDefinition` 的字段：运行中的案件的输入和进展读不出来，改字段的变更自己处理（和 add-case-workflow 相同）

## Decisions

### D1 不用 DBOS 的队列，排队的案件存在调度器的表里

用 DBOS 3.2.0 的队列（`DBOS.register_queue`，`Queue.enqueue`）核实过（`.venv/.../dbos/_queue.py`、`_sys_db.py` 的 `start_queued_workflows`）：

- 并发上限按队列里 PENDING 的工作流数算（`pending_count`）：案件工作流从唤醒 PM 一直 PENDING 到订单提交，等人工确认、等开盘的案件也占名额；case-queue 只计还在调用模型的
- 优先级是入队时给的整数，出队按 `priority, created_at` 排；case-queue 要按每次决定时的股票状态重新定类型和优先级，确定不了的直接不再运行
- 分区队列（每只股票一个分区，`partition_concurrency=1` 才能一只股票一次一个）：优先级只在一个分区里排，各分区按随机顺序出队（`random.shuffle(partition_keys)`），全局的"持仓风险事件先于候选评估"不成立
- 降级时不启动、普通预算用完只跑持仓案件、紧急退出中的股票不启动、寻股一次一个，DBOS 的队列都没有对应的开关

所以：排队的案件存在调度器的表里，排队运行每一轮组出 `QueueSnapshot` 调用 `queue_decision`，按决定调用 `start_case`；DBOS 只负责每个案件的工作流（它的持久化和恢复）。完整版 Proposal 的 DBOS 一行照这个改（proposal「需要人决定」第 1 条）。

不选的做法：

- DBOS 队列只管并发，规则自己算：等确认的案件占名额的问题还在，而且决定和出队是两处，要对齐
- 每个排队的案件就是一个 DBOS 工作流，先 `recv` 等"开始"或"不再运行"：队列就是 DBOS 的工作流列表，不用新表；但案件工作流的入口要改成先排队，`CaseProgress` 要多一种"排队中"，排队运行每一轮列出全部排队的工作流并读输入，比读一张表复杂

### D2 两张只追加的表

```python
class QueueExit(StrEnum):
    """排队的案件怎样离开队列。"""

    STARTED = "started"  # 开始运行
    DROPPED = "dropped"  # 不再运行


class QueueStore(Protocol):
    def add(self, case: QueuedCase, enqueued_at: datetime) -> None: ...
    def queued(self) -> tuple[QueuedCase, ...]:
        """排了队、还没出队的，按入队的先后。"""

    def leave(self, case_id: UUID, outcome: QueueExit, at: datetime) -> None:
        """出队；同一个案件第二次出队违反主键，IntegrityError 原样抛出。"""


class QueueService:
    def __init__(self, store: QueueStore, clock: Clock, ids: IdGenerator) -> None: ...
    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> QueuedCase:
        """新的案件 ID，入队时刻是现在。"""

    def queued(self) -> tuple[QueuedCase, ...]: ...
    def leave(self, case_id: UUID, outcome: QueueExit) -> None:
        """出队时刻是现在。"""
```

| 表 | 列 |
| --- | --- |
| `scheduler_queue` | `case_id`（主键）、`position`（`BIGINT GENERATED ALWAYS AS IDENTITY`，入队的先后）、`trigger`（JSONB）、`triggered_at`、`enqueued_at`（都带时区） |
| `scheduler_queue_exits` | `case_id`（主键）、`outcome`（`started` 或 `dropped`）、`exited_at`（带时区） |

- 读队列：`scheduler_queue LEFT JOIN scheduler_queue_exits USING (case_id) WHERE exits.case_id IS NULL ORDER BY position`
- 和 Agent 网关的任务表、Result 表一样一行一条、不用版本表（update-task-cost-storage 的「需要人决定」第 1 条）：一个排队的案件只有两个事实（排了队、怎样出队），写入以后不变；"只出队一次"由出队表的主键保证。版本表（`VersionedStore`）读最新版本时按键排序，给不出入队的先后；测试的假时钟下入队时刻常常相同，也不能按时刻排
- 触发存成带种类的 JSON：`{"kind": "event" | "heartbeat" | "follow_up" | "sell", ...字段}`，读回时按种类用对应 dataclass 的 `TypeAdapter` 校验。四种触发是没有标记的 dataclass 联合，按字段猜会把事件的 JSON 当成卖出成交（多出来的字段被忽略），所以不让 pydantic 猜
- 数据库的错误原样抛出；存着的触发读不出来时抛 pydantic 的 `ValidationError`，不当作没有
- 排队只写排队表，再由调用方写程序日志（D4），一个案件 ID 只在这里分配

### D3 每隔 5 秒做一轮，不靠通知

排队运行和盘中巡检一样是一个守护线程：开始时立即做一轮，之后每一轮做完 `threading.Event.wait(interval)`，停止时 set 这个事件。

- 快照的每一项都会变：排队、案件结束和换阶段（DBOS 里）、股票状态（股票池、巡检里的股票池跟上持仓）、紧急退出、降级模式（巡检）、预算（网关记账、收盘恢复）。来源分散在四五个线程里，挂通知要每处都接；每一轮从头读一遍就都覆盖了，纯函数也不需要知道什么变了
- 5 秒的延迟和案件本身的时长（替身几秒、模型几分钟）比可以不计；只有"方案刚进入等确认、几秒内按了确认"会受影响（proposal「需要人决定」第 3 条）
- 一轮要读：排队表一次、DBOS 的 PENDING 案件工作流一次加每个运行中的案件的进展一次、股票池一次、费用表今天的记录一次；运行中的案件几个到十几个，5 秒一次的读不算负担
- 一轮里出错：`run_round` 抛出，循环写 `_log.exception`，等一个间隔再来；不更新心跳（D8）

不选的做法：排队时、案件发布进展时唤醒排队运行（`threading.Event`），再加一个长一点的定时兜底。少几秒延迟，多两处接线，状态变化的那几种仍要靠定时。

### D4 QueueRunner

```python
class QueueRunnerConfig(BaseModel):
    """排队运行的配置和默认值，含义见 queue-runner spec「排队运行的配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval: timedelta = Field(default=timedelta(seconds=5), gt=timedelta(0))


class CaseControl(Protocol):
    """排队运行对案件工作流和下单流程的那几下；生产是 DbosCases，测试换成内存的替身。"""

    def running(self) -> tuple[tuple[UUID, CaseProgress], ...]: ...
    def start(self, case: QueuedCase, definition: CaseDefinition) -> None: ...
    def exists(self, case_id: UUID) -> bool: ...  # 这个案件的工作流已经建立
    def void(self, plan_id: UUID) -> None: ...


class QueueRunner:
    def __init__(self, parts: QueueParts, config: QueueRunnerConfig) -> None: ...
    def enqueue(self, trigger: Trigger, triggered_at: datetime) -> UUID: ...
    def run_round(self) -> None: ...
    def start(self) -> None: ...  # 先让已经开始运行的出队，再开线程 case-queue
    def stop(self) -> None: ...
    def heartbeat(self) -> datetime: ...
```

`QueueParts` 是 frozen dataclass：`QueueService`、`CaseControl`、`StockPoolService`、`HealthService`、`CallBudgetService`、`GatewayConfig`（预算的额度，写日志用）、`CaseRecordService`、`exiting: Callable[[], frozenset[str]]`、`SchedulerConfig`、`ProgramLog`、`Clock`。

一轮（queue-runner spec「一轮」）：

```python
running = cases.running()
budget = parts.budget.status()
self._report_budget(budget)
snapshot = QueueSnapshot(
    queued=parts.queue.queued(),
    running=tuple(
        RunningCase(i, p.case_type, p.symbol, p.state.calling_models) for i, p in running
    ),
    plans=tuple(
        PendingPlan(p.plan.plan_id, p.plan.symbol, p.plan.action, p.state.plan_stage)
        for _, p in running
        if p.plan is not None and p.state.plan_stage is not None
    ),
    statuses={e.symbol: e.status for e in parts.pool.entries()},
    exiting=parts.exiting(),
    degraded=parts.health.status.degraded,
    general_budget_exhausted=Budget.GENERAL in budget.exhausted,
    exploration_budget_exhausted=Budget.EXPLORATION in budget.exhausted,
)
decision = queue_decision(snapshot, parts.scheduler)
# 作废：跳过这次运行里已经作废过的方案；写方案作废，再 cases.void
# 不再运行：case_records.not_run(case_trigger(...))，写不再运行，queue.leave(DROPPED)
# 启动：cases.start(...)，queue.leave(STARTED)
self._beat = parts.clock.now()
```

- 运行中的方案和它的案件 ID 从同一份 `running` 里对上，方案作废的条目写给出方案的案件
- `enqueue` 可以从别的线程调用（以后的触发源）：只写表和程序日志，不碰排队运行的内存
- 排队运行不拿那把全程序的锁：它不调用执行模块和风控；作废交给下单流程（D6），下单流程自己拿锁
- `start()`：对每个还在排队的案件 `cases.exists(case_id)`，已经有工作流的按启动出队（queue-runner spec「重启以后」），然后开线程。只在开始时做一次：同一个进程里开始运行和出队在同一个线程里接着做
- 先 `start_case` 再出队：反过来时进程在两步之间退出，案件出了队却没有运行，丢了；现在这样最多是重启以后多一次 `exists`。重启以后在开始以前就结束了的案件（替身的复盘几秒就完）`exists` 也是真，不会再开始一次
- 不再运行的程序日志写触发原因（`case_trigger` 的）和股票现在的状态（股票状态的中文名，不在股票池写"不在股票池里"）；排队写触发原因和触发时刻；方案作废写动作、股票和"这只股票有新案件排队"。文字放在 `queue_runner.py`

`DbosCases` 在 `queue_runner.py`：`running` 是 `case_workflow.running_cases()`，`start` 是 `start_case`，`exists` 是 `DBOS.get_workflow_status(str(case_id)) is not None`，`void` 是 `order_workflow.void_plan`。

### D5 运行中的案件：列出 PENDING 的案件工作流

```python
def running_cases() -> tuple[tuple[UUID, CaseProgress], ...]:
    """运行中的案件和它们的进展：PENDING 的 case 工作流；还没发布进展的按输入给出刚开始的进展，
    已经结束的不算。"""
```

- `DBOS.list_workflows(name="case", status="PENDING", load_output=False)`：子工作流 `case_task` 名字不同，不在里面；出错停下的是 ERROR，不在里面
- 还没发布进展（`get_event` 为 None）：`start_case` 以后、工作流的线程还没跑到第一次 `publish` 的那一会儿。这时进展取输入里的 `CaseDefinition`：`CaseProgress(definition.case_type, definition.symbol, new_case(definition.case_type), None)`，刚开始的案件在唤醒 PM 或复盘，都算在调用模型。不这样做的话，下一轮会把这只股票当成空的
- 进展的阶段是已结束的（工作流在写结束的 step）不算运行中，和 `case_progress` 一样
- 不另外记"排队运行启动过哪些案件"：DBOS 里 PENDING 的就是运行中的，包括重启以后恢复的

### D6 作废交给下单流程

排队运行只做一件事：`void_plan(plan_id)`，即 `DBOS.send(str(plan_id), Stage.VOIDED.value, topic=DECISION_TOPIC)`，送给这个方案的下单流程（工作流 ID 是方案 ID）。下单流程在它的每一处等待里收：

| 下单流程在 | 收到作废 |
| --- | --- |
| 等确认（`DBOS.recv(DECISION_TOPIC, timeout)`，原有） | `Stage.VOIDED`：`settle` 报告确认消息已作废、拿锁释放（原因"<股票> 有新案件排队，方案作废"），不告警；送回 `PlanEvent.VOIDED` |
| 等开盘、等重试（`install` 交进来的 `wait`） | step `order_plan.void_queued`：拿锁看这个方案有没有订单，有就交给盘中巡检、送回订单已提交；没有就释放，放开锁以后报告确认消息已作废、推送作废的告警，送回 `PlanEvent.VOIDED` |

- `install(flow, wait)` 的 `wait: Callable[[float], str | None]` 代替原来的 `sleep`：等这么多秒，期间收到作废就提前交回它。生产是 `wait_decision(seconds) = DBOS.recv(DECISION_TOPIC, timeout_seconds=seconds)`（DBOS 的 `recv` 带持久化的超时，重启以后只等剩下的时间，和 `DBOS.sleep` 一样）；测试的替身照旧拨钟，需要时交回作废。bootstrap 的 `Externals.sleep` 改名 `wait`
- 确认以后 `DECISION_TOPIC` 只会收到作废：按钮的回调只在 `Stage.WAITING` 时送（`deliver_decision`）。`_execute` 里收到别的值是错误，`Stage(message)` 不是 `VOIDED` 时抛出，按下单流程意料之外的错误送回出错
- 正在做 step（请求确认、再校验和提交）时送到的作废留在主题里：做完以后要等的，下一次等待立即收到；提交了订单或者作废了的，下单流程结束，作废没人读
- 告警的文字和再校验作废的相同（`void_text`），原因一行写"<股票> 有新案件排队"
- `Stage.VOIDED` 的值和 `ConfirmationOutcome.VOIDED` 相同（`"voided"`）；`PlanEnd(stage.value)` 是已有的 `VOIDED`
- 作废以后下单流程送回案件，案件在 `_deliver` 的等待里收到 `PlanEvent.VOIDED`，按 `CaseState.void()` 结束（D7）
- case-queue spec 要调用方"先作废方案、释放预留，再结束不再运行的案件，最后启动"：作废在一轮的最前面交出去，释放由下单流程收到以后做，可能晚于这一轮的启动。开始运行的案件不看预留（风控检查在方案交给下单那一侧时才做），先后不影响结果

不选的做法：排队运行自己拿锁释放预留、报告确认消息已作废、把作废直接送给案件，下单流程照常走完（update-order-flow-case-plans 的交接说明原来的写法）。实际写下来有三个问题：

- 排队运行判断"方案还没提交订单"和下单流程提交订单是两件事，中间下单流程可能刚好提交：排队运行看到订单再放弃，但它已经把作废送给了案件，案件记成方案被作废，订单却在券商那边
- 等确认的下单流程照样等到截止时刻，然后报告确认消息已超时，把已作废盖成已超时，还写一条人的超时
- 等开盘的下单流程到开盘再校验，得到"方案已结束"，推送一条原因看不懂的作废告警；而且排队运行要拿到确认消息的标识（在下单流程的 step 输出里）才能报告已作废

### D7 案件收作废

- `PlanEvent.VOIDED = "voided"`，`_PLAN_EVENTS[PlanEvent.VOIDED] = CaseState.void`
- 只在 `_deliver` 的等待里收：作废的方案在等人工确认或等下单，这两个阶段的案件只会在这里等。"每次调用 agent 以前收一次"（add-case-workflow 的 design D3）是给中止的，那时案件可能在任何阶段；本变更不加，在调用模型的阶段收到作废反而是错误（`void()` 抛出）
- 中止可以照用这条路：紧急退出流程按案件 ID 经 `PLAN_TOPIC` 送 `PlanEvent.ABORTED`，`_deliver` 的等待已经在收；「紧急退出中止案件」再在每次调用 agent 以前收一次（D11）

### D8 心跳和健康信号

- `QueueRunner` 建好时 `_beat = clock.now()`，每一轮做完 `_beat = clock.now()`；`heartbeat()` 交回它。只有排队运行的线程写，别的线程读一个引用，不加锁
- `HealthMonitor` 的构造参数加 `fallback: ModelFallbackService`、`budget: CallBudgetService`、`heartbeat: Callable[[], datetime]`；`signals` 里 `pm_model_available=fallback.has_available_model(Agent.PM)`、`critic_model_available=...(Agent.CRITIC)`、`reserve_budget_exhausted=Budget.RESERVE in budget.status().exhausted`、`scheduler_heartbeat_at=heartbeat()`
- 启动时的第一次健康判定在 DBOS 和排队运行之前，用的是同一个 `signals`：心跳就是排队运行建好的时刻，几秒以前（bootstrap spec「第一次健康判定」）
- 一起接上模型和持仓保留预算：网关在本变更装配，health-monitoring spec 写死的理由（"M2 还没有 Agent 网关"）不再成立；add-model-fallback 的 design 和 update-task-cost-storage 的 proposal 都把这两项留给了 M4 装配网关的时候
- 读预算是一次数据库查询，出错时 `judge` 抛出，巡检按"健康判定出错"写 ERROR（health-monitoring spec「健康判定的失败」）

### D9 正在紧急退出的股票

`EmergencyFlow.exiting() -> frozenset[str]`：当天的记录里 `gate is Gate.OPEN and not ended` 的股票。

- `OPEN` 就是"开始紧急退出"：不用确认的记录一建出来就是 `OPEN`，要确认的在确认以后变成 `OPEN`；在等确认、被拒绝和超时的不算（queue-runner spec「一轮」的表）。`ended` 在清仓单结束、没有可卖的股数、到收盘还没有清仓单时为真
- `_records` 只在巡检线程里改，排队运行在自己的线程里读。`judge`、`advance`、`report` 末尾各重算一次，存成一个 frozenset 属性（换引用），`exiting()` 交回这个属性；不在两个线程之间共用 dict 的迭代
- 重启以后的找回由紧急退出流程照旧做（emergency-exit-flow spec「重启以后」），见省略的情况

### D10 调用预算用完的日志

- 每一轮读一次 `budget.status()`，同时给快照和这里用；`_reported: set[tuple[Budget, date]]` 记这次运行写过的，按 `Budget` 的顺序（普通、持仓保留、寻股）写还没写过的
- 说明：预算的中文名、交易日、今天的费用、额度（`GatewayConfig` 的三个额度）、影响（queue-runner spec 的表）；行为者系统，对象 `system`
- 不推送告警（proposal「需要人决定」第 4 条）

### D11 给以后的变更

- 「触发」组：`TradingApp.queue.enqueue(trigger, triggered_at) -> UUID`。在 DBOS 的 step 里调用时，step 重做会再排一个（新的案件 ID）；要不重复的，触发源在自己的 step 输出里记下排过了，或者按事件 ID 去重
- 「紧急退出中止案件」：`PlanEvent.ABORTED` 和 `CaseState.abort`；紧急退出流程开始一只股票的紧急退出时，用 `running_cases()` 组出快照调用 `emergency_decision`，按案件 ID 经 `PLAN_TOPIC` 送中止，作废的方案用 `void_plan`（下单流程在等的时候结束、释放、报告已作废）；案件在每次调用 agent 以前 `DBOS.recv(PLAN_TOPIC, timeout_seconds=0)`。正在紧急退出的股票已经由 D9 给出

### D12 装配

`_assemble` 里新建的部件（都取默认配置，除了下面写明的）：

| 部件 | 装配 |
| --- | --- |
| 网关 | `ModelFallbackService(clock, stand_in_models(), gateway)`、`CallBudgetService(PostgresCostStore(engine), clock, calendar, models, gateway)`、`TaskDispatchService(PostgresTaskStore(engine), clock, ids, gateway, log)`、`AgentCallService(StandInBackend(calendar, StandInConfig()), fallback, budget, tasks)`；一份 `GatewayConfig` |
| 数据 | `NewsService(AlpacaNewsSource(AlpacaNewsConfig(key, secret)), SecFilingSource(SecConfig(user_agent)), PostgresNewsStore(...))`；`CompanyDataService(FinnhubSource(FinnhubConfig(api_key)), PostgresEarningsStore(...), clock, CompanyDataConfig())`；`ScreenerService(MoomooSecuritySource(quotes, MoomooSecuritiesConfig()), AlpacaBarSource(AlpacaBarsConfig(key, secret)), company_data, PostgresScreenStore(...), clock, calendar, ScreenerConfig(), pool_config)` |
| 快照 | `SnapshotService(PostgresSnapshotStore(engine, clock), log)`、`SnapshotFlow(execution, market_data, stock_pool, news, company_data, screener, snapshots, clock, calendar, SnapshotConfig())` |
| 案件工作流 | `CaseFlow(CaseServices(...), CheckConfigs(gateway, scheduler, HeartbeatConfig()), CaseFlowConfig())`；交接 `CasePlanHandoff()` |
| 排队运行 | `QueueRunner(QueueParts(QueueService(PostgresQueueStore(engine), clock, ids), DbosCases(), stock_pool, health, budget, gateway, case_records, emergency_flow.exiting, scheduler, log, clock), QueueRunnerConfig())` |
| 健康监控 | `HealthMonitor(..., fallback, budget, runner.heartbeat)` |

- 一份 `StockPoolConfig` 给股票池和粗筛（add-stock-screen 的 design 要求同一个对象），一份 `SchedulerConfig` 给案件工作流和排队运行
- `EmergencyFlow` 先建出来，交给巡检和排队运行
- OpenD 的行情连接同时是股票列表的连接：`QuoteContext` 协议加上 `SecuritiesContext`（moomoo 的 `OpenQuoteContext` 都满足）
- `Settings` 加 `finnhub_api_key: SecretStr`、`sec_user_agent: str`，`VARIABLES` 加两项；部署的模板跟着加（deployment spec「密钥」由 `tests/deploy` 检查）
- `Externals` 加 `http: httpx.BaseTransport | None`：交给四个数据源（Alpaca 新闻、Alpaca 日线、SEC、Finnhub）；运行时为 None（各自连真的地址），测试换成按地址回答的 `MockTransport`；`sleep` 改名 `wait`（D6）
- 启动：`DBOS(config)` → `order_workflow.install(order_flow, externals.wait)` → `case_workflow.install(case_flow, CasePlanHandoff())` → `DBOS.launch()` → `runner.start()`（写 INFO：排队运行已开始，间隔 5 秒）→ `patrol.start()`。`on_stop` 的登记顺序是 DBOS、排队运行、巡检，关闭时反过来
- `TradingApp` 加 `queue: QueueRunner`
- HTTP 客户端不在停止时关闭：和现有的 Alpaca 交易客户端一样，进程退出时一起结束

### D13 程序日志的动作

- `Action.ENQUEUE`（排队）、`DROP_CASE`（不再运行）、`VOID_PLAN`（方案作废），行为者调度器；`BUDGET_EXHAUSTED`（调用预算用完），行为者系统。Proposal 程序日志表里的"调用预算消耗"就是这一条（update-task-cost-storage 的「需要人决定」第 2 条）
- 排队、不再运行的对象和案件 ID 都是这个案件；方案作废的对象是方案 ID，案件 ID 是给出方案的案件

### D14 迁移

`v0013_create_scheduler_queue.py`：`down_revision = "0012"`，建两张表，写明每一列和主键，不 import 业务代码；只升级（add-state-store 的 design D6）。

### D15 测试

- `tests/modules/scheduler/test_service.py`：`QueueService` 用 `tests/fakes/queue.py` 的内存实现：排队分配 ID、入队时刻、先后、出队
- `tests/modules/scheduler/test_repo.py`（要开着 OrbStack）：四种触发原样读回（本机时区 UTC+14）、入队的先后、出队以后不在队列里、第二次出队报错、读写出错、空数据库没有升级时报错
- `tests/workflows/test_queue_runner.py`：`run_round` 直接调用，用 `tests/fakes/cases.py` 的 `FakeCases`（记下启动和作废，按场景给出运行中的案件）、内存的队列、股票池、案件记录、费用和程序日志，真的 `HealthService` 和 `FakeClock`：queue-runner spec 除"间隔""停止"以外的场景；`start`、`stop` 和间隔照 `test_patrol_thread.py` 的写法测
- `tests/workflows/test_case_workflow.py`（真的 DBOS）：`running_cases` 给出刚开始、还没发布进展的案件；等人工确认、等下单时收到作废结束：方案被作废
- `tests/workflows/test_order_workflow.py`（真的 DBOS）：order-flow spec「作废案件的方案」的七个场景、「结果送回案件」「告警」新加的场景；`FakeSleep` 改成满足 `wait`，能在指定的一次等待里交回作废
- `tests/workflows/test_emergency_*.py`：`exiting` 在开始、等确认、确认以后、清仓单结束、没有可卖的股数、到收盘时的取值
- `tests/workflows/test_health.py`：health-monitoring spec「健康信号」新加的场景，模型从 `ModelFallbackService` 的熔断、预算从内存的费用、心跳从给定的函数
- `tests/bootstrap/`：环境变量、启动和停止的顺序、启动时持仓保留预算用完、恢复等确认的案件、替身跑到下单和作废等确认的方案（四个数据源用 `MockTransport`）；`tests/deploy` 的模板
- 阈值从配置读取，不在测试里硬编码

## Risks / Trade-offs

- [下单流程的等开盘、等重试换成了 `DBOS.recv`，`order_plan` 的步骤变了] → 部署前确认没有 PENDING 的 `order_plan`（Migration Plan）；`recv` 的超时和 `sleep` 一样是持久化的
- [作废最多晚一轮（5 秒）] → 只影响方案刚进入等确认的那几秒；proposal「需要人决定」第 3 条
- [每一轮列出 PENDING 的案件工作流并读输入] → 运行中的案件只有几个到十几个；排队运行不拿锁，慢也不挡巡检和下单
- [正在紧急退出的股票只在内存里] → 巡检每一轮都按清仓单找回；重启后第一轮可能漏一次，见省略的情况
- [`list_workflows` 的名字按 `@DBOS.workflow(name="case")` 过滤] → tasks 里先用真的 DBOS 核实 PENDING 的案件能按这个名字列出来、子工作流不在里面

## Migration Plan

- 部署以前：主机的 `/etc/tradingagentspro/tradingagentspro.env` 填上 `FINNHUB_API_KEY`（本人在 Finnhub 申请免费档的 key）和 `SEC_USER_AGENT`（写明联系方式），否则启动失败；确认 `dbos.workflow_status` 里没有 PENDING 的 `order_plan`
- 部署：按 `deploy/README.md`「更新」；启动时迁移到 v0013
- 部署以后：队列是空的，排队运行每 5 秒做一轮什么都不做；健康监控改读网关和排队运行，替身的模型不会冷却，预算的费用是 0，表现和以前一样
- 「触发」组接上以前，本人用 `/plan close` 清掉 M2 测试留下的持仓（update-order-flow-case-plans 的运维事项）
- 回滚：回到上一版代码，回滚前同样确认没有 PENDING 的 `order_plan`；两张新表留着，旧代码不读；本变更以后没有案件开始运行过，没有要恢复的案件工作流
