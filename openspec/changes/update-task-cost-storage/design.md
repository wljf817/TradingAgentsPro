# Design

## Context

现状：

- `modules/gateway` 有三个用例：`TaskDispatchService`（存储端口 `TaskStore`）、`ModelFallbackService`（状态放在内存里）、`CallBudgetService`（存储端口 `CostStore`）。两个端口只有 `tests/fakes/` 的内存实现，模块里没有 `repo.py`
- `TaskDispatchService._settle`（收到输出、调用失败、记超时共用）先读这个任务，已经有 Result 就给出它，否则生成 Result 写入；内存实现对同一个 Task 写第二个 Result 时抛错。`dispatch` 读本案件的任务、检查，再逐个写 Task。说明写着"同一个案件由调用方串行调用：派发的检查和 Result 只写一次都是先读后写，做法 M4 定"
- `CallBudgetService.record` 读今天的记录、算普通预算是否用完、再写一条；说明写着"记到哪个预算是先读后写，做法 M4 定"
- 程序日志已经有了：`ProgramLog.write(actor, action, subject, detail, *, inputs, outputs, case_id, duration, tokens, cost)`；以后的模块在 service 的构造参数里拿一个 `ProgramLog`，在 `modules/programlog/domain/entry.py` 的 `Actor`、`Action` 里加自己的成员（add-program-log 的 design D3）
- 表的两种写法：状态库的版本表（`modules/statestore` 的 `VersionedStore`，按键保存版本）；程序日志的只追加表（一行一条，编号自增，`modules/programlog/repo.py`）。迁移在 `bootstrap/migrations/versions/`，现在到 v0006（v0005 股票池、v0006 跟进）
- 运行中的代码还没有调用网关：bootstrap 不建这三个用例，「替身」「案件工作流」才接上
- `tests/contracts/test_task.py` 已经有任意合法的 Task、Result 按 JSON 写出再读回相等的性质测试

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/gateway
  repo.py             新：表 gateway_tasks、gateway_results、gateway_costs；
                      PostgresTaskStore、PostgresCostStore
  ports.py            TaskStore.add_result：已经有 Result 时不写，给出已有的
  domain/entries.py   新：派发任务、拒绝任务、生成 Result 三种条目的说明
  service.py          TaskDispatchService 的构造参数加 ProgramLog，写条目；
                      TaskDispatchService、CallBudgetService 的说明写明并发的做法
  __init__.py         导出 PostgresTaskStore、PostgresCostStore
modules/programlog
  domain/entry.py     Actor 加 GATEWAY；Action 加 DISPATCH_TASK、REJECT_TASK、TASK_RESULT
bootstrap/migrations/versions/v0007_create_gateway_tables.py
```

跨模块传递的模型：没有新的。两个存储读写 contracts 的 `Task`、`Result`（Pydantic）和本模块的 `TaskRecord`、`CallCost`；网关经 `ProgramLog.write` 的参数（字符串、UUID、timedelta、Decimal）写条目，不跨端口传条目。contracts、shared 不改；派发、记账的 domain 规则和 `ModelFallbackService` 不改；bootstrap 只多一个迁移，不装配。

## Goals / Non-Goals

**Goals:**

- 两个数据库实现和内存实现行为相同，task-dispatch、call-budget 原有的场景照样成立
- 同时调用的正确性只靠一处：Result 表的主键。派发和记账不加锁
- 每个 Task、每个 Result 在程序日志里正好一条

**Non-Goals:**

- 表之间的外键、禁止修改和删除的触发器：和状态库、程序日志的表一样，靠程序只有追加和读取
- 按案件、按 agent 统计费用的读取：M6 需要时再加端口和索引

按「编程风格」省略的情况：

- 写入 Task 以后、写程序日志出错：这次派发报错，Task 留在表里、没有 Result；调用方重新派发时相同的请求复用它，到截止时间记超时。和派发以后进程崩溃一样，由「案件工作流」按 DBOS 的步骤处理
- 一次派发写到一半出错：前面的 Task 已经写入，后面的请求没有处理，同上
- 写入 Result 以后、写程序日志出错：调用报错；再调用时给出已有的 Result，这个 Result 少一条条目
- 截止时间前几毫秒收到的输出，写入晚于同时记下的超时：Result 是超时（先写入的为准）
- 同一个案件的两次派发同时进行：调用方保证不会（D4），不检查
- 同时记下的几条让普通预算超出额度、持仓案件晚一点开始用持仓保留预算：Proposal 允许预算超出一点（D5）
- Result 表没有外键，给没有写入的 Task 写 Result 不报错：service 只在读到 Task 以后写
- 存着的 Task、Result 的格式和代码对不上（以后改了 contracts）：读的时候报错，由改 contracts 的变更迁移数据
- 表一直增长：每天几十到几百行，不清理

## Decisions

### D1 三张只追加的表

`repo.py` 用 SQLAlchemy Core 定义，迁移 v0007 写明每一列：

```python
TASKS = Table(
    "gateway_tasks",
    MetaData(),
    Column("task_id", Uuid, primary_key=True),
    Column("seq", BigInteger, Identity(always=True), nullable=False),
    Column("case_id", Uuid, nullable=False),
    Column("data", JSONB, nullable=False),
    Index("gateway_tasks_case_id", "case_id"),
)

RESULTS = Table(
    "gateway_results",
    MetaData(),
    Column("task_id", Uuid, primary_key=True),
    Column("data", JSONB, nullable=False),
)

COSTS = Table(
    "gateway_costs",
    MetaData(),
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("case_id", Uuid, nullable=False),
    Column("case_type", String, nullable=False),
    Column("agent", String, nullable=False),
    Column("model_vendor", String, nullable=False),
    Column("model_name", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost", Numeric, nullable=False),
    Column("budget", String, nullable=False),
    Index("gateway_costs_recorded_at", "recorded_at"),
)
```

- 不用 `VersionedStore`：三种记录写入以后都不变，没有"新版本"；版本表"内容不同就写新版本"还会让一个 Task 有第二个 Result。和程序日志一样一行一条，表名以模块名开头（state-store spec「表的归属和迁移」）
- 一个 Task 一行 Task、最多一行 Result，不在 Task 那一行上补 Result：补就是修改，违反只追加
- Task 和 Result 存整份 JSON（`model_dump(mode="json")` 写入，`model_validate` 读回）：Result 里嵌套信号或无信号、两种原因的联合和模型，拆成列要十几个可以为空的列。JSON 往返已经有性质测试；按 JSON 模式写出的字典读回同样相等（金额、耗时都写成文字，不丢精度）。Task 的输入、Result 引用的条目都是文字，格式由「状态库快照」定，现在原样存
- `seq` 是写入的先后：一次派发的 Task 派发时刻相同，本案件的任务按 `seq` 排序才是生成的先后
- 费用记录拆成列：字段平铺，人和 M6 按天、按预算、按案件加总直接写 SQL；`cost` 是不限精度的 numeric，原样保存；模型拆成厂商和模型名两列，模型名里可以有 `/`
- Result 表不另存案件 ID 和时刻：用 task_id 和 Task 连起来，生成 Result 的时刻是派发时刻加耗时（proposal「需要人决定」第 1 条）
- 没有外键：和状态库的表一样每张表各用一个 `MetaData`
- 索引只有两个：派发每次按案件读任务；取得预算的情况每次按记下的时刻读今天的记录

### D2 两个数据库实现

`PostgresTaskStore(engine)` 满足 `TaskStore`：

- `case_records(case_id)`：本案件的 Task 左连接 Result，按 `seq` 排序，一条语句
- `record(task_id)`：同样连接，没有这一行时抛 `KeyError`
- `add_task(task)`：插入一行；task_id 重复时数据库报错，原样抛出（ID 来自 `IdGenerator`，不会重复）
- `add_result(result)`：见 D3

`PostgresCostStore(engine)` 满足 `CostStore`：

- `since(moment)`：`recorded_at` 不早于 moment 的行，按 `id` 排序，逐列拼回 `CallCost`（`ModelRef`、`TokenUsage` 和三个枚举照原样构造）
- `add(entry)`：插入一行

两个都只要 engine：时间都在记录里，不需要时钟（和 `PostgresProgramLogStore` 一样）。每个操作一个事务；数据库的错误和读不出来的记录（Pydantic 的 `ValidationError`）原样抛出，不当作没有记录（task-dispatch spec「任务的存储」、call-budget spec「费用的存储」）。数据库连接由 `statestore.connect` 建，会话时区是 UTC。

### D3 一个 Task 只有一个 Result：Result 表的主键保证

端口改为：

```python
class TaskStore(Protocol):
    def add_result(self, result: Result) -> Result | None:
        """写入一个 Task 的 Result；这个 Task 已经写入。

        这个 Task 已经有 Result 时不写入，给出已有的那个（先写入的为准）；写入了给出 None。
        """
        ...
```

数据库实现在一个事务里：`INSERT … ON CONFLICT (task_id) DO NOTHING RETURNING task_id`，有返回就是写入了；没有就再 `SELECT` 出已有的那行。另一个事务正在写同一个 task_id 时，`ON CONFLICT` 等它提交；READ COMMITTED 下后面的 `SELECT` 看得到它提交的行。

`TaskDispatchService._settle`：

```python
def _settle(self, task_id: UUID, settle: Callable[[Task, datetime], Result]) -> Result:
    """一个任务只有一个 Result：先写入的为准。"""
    record = self._store.record(task_id)
    if record.result is not None:
        return record.result
    result = settle(record.task, self._clock.now())
    existing = self._store.add_result(result)
    if existing is not None:
        return existing
    self._log_result(record.task, result)
    return result
```

- 先读仍然要：生成 Result 要 Task 的派发时刻和截止时间；已经有 Result 时不再解析输出，早于截止时间的记超时也不报错（和现在一样）
- 两个调用都读到没有 Result（子 agent 的输出和截止时间的超时同时到，或者 DBOS 重做一个 step）时，只有一个写入，另一个给出它；只有写入了的那次写程序日志，所以一个 Result 正好一条条目（D6）
- `dispatch` 写被拒绝的 Result 时 Task 刚生成，不会冲突，返回值不用
- 内存实现改成同样的行为：已经有 Result 时给出它；给没有写入的 Task 写 Result 仍然抛 `KeyError`
- 考虑过：service 捕获数据库的唯一约束错误。没有采用：service 只依赖端口，不认识 SQLAlchemy 的异常
- 考虑过：按 task_id 加行锁或 advisory lock，把读和写包在一个事务里。没有采用：端口要多一个事务的概念，主键已经够
- 考虑过：`add_result` 返回是否写入，没写入时 service 再读一次。没有采用：多一次读，还要处理读到空的类型

### D4 派发的检查：同一个案件由调用方依次调用

`dispatch` 先读本案件的任务、算复用、轮次和任务数，再写 Task。两次派发同时进行会超出上限，所以同一个案件的派发由调用方依次调用（task-dispatch spec「任务的存储」）：

- 一个案件一次只在一个阶段（case-stages spec「案件的阶段」）：PM 在唤醒 PM 时派发，Critic 在 Critic 审核时派发，两者不同时；PM 的 `dispatch_tasks` 等全部 Result 回来才交回工具结果，下一次派发在那之后
- 生成 Result 和派发同时进行不影响检查：轮次只看 Task 上的轮次；任务数只看 Task 和被拒绝的 Result，被拒绝的 Result 只由派发自己写。复用时读到"还没有 Result"、随后同时超时或失败的任务，就是 add-task-dispatch 已经省略的"截止时间已过、还没记超时的任务被复用"
- 不同案件只读写自己的任务，互不影响，不加锁
- 给后面的约定：「案件工作流」不让一个案件的两个阶段同时运行；M5 接 PydanticAI 时，同一次模型回复里有几个 `dispatch_tasks` 调用的，要依次交给网关（确认框架是否并行执行同一次回复里的工具调用）

`TaskDispatchService` 的说明改为：同一个案件的 `dispatch` 由调用方依次调用；`receive`、`fail`、`expire` 可以和任何调用同时进行，一个任务只有一个 Result 由存储保证。

- 考虑过：按案件加 advisory lock，让同时的派发自己排队。没有采用：调用方本来就依次调用，加锁要改端口

### D5 记到哪个预算：不串行

`record` 不改：读今天的记录、判断记之前普通预算是否用完、写一条。几个调用同时记下时，都在别人写入以前读到"没用完"的都记普通预算，普通预算超出额度。

- Proposal「调度器与运行模块」：预算只在案件开始时看，可以超出一点。同时记下的最多是并发的几个案件里同时进行的尝试（几个案件，各自几个并行的子 agent），每条是一次尝试的费用（几美分），多出的通常是几美分
- 持仓案件的尝试和普通预算用完同时发生时，可能多记一条普通预算，持仓保留预算晚一条开始用，不影响哪些案件能开始
- 同时记下的每一条都写入：表的主键是自增编号，插入之间不冲突
- `CallBudgetService` 的说明改为：可以同时调用；记到哪个预算按这一条记下时读到的情况，同时记下的几条不互相等待（call-budget spec「费用的存储」）
- 考虑过：按天加 advisory lock，或者用 SERIALIZABLE 事务把读和写串起来。没有采用：事务要跨过端口的两次调用，换来的只是少超出几美分

### D6 程序日志的条目

`modules/programlog/domain/entry.py` 加：

```python
class Actor(StrEnum):
    GATEWAY = "gateway"  # Agent 网关


class Action(StrEnum):
    DISPATCH_TASK = "dispatch_task"  # 派发任务
    REJECT_TASK = "reject_task"  # 拒绝任务
    TASK_RESULT = "task_result"  # 生成 Result
```

（只列新加的成员。）三种条目都由 `TaskDispatchService` 写，对象是 `str(task_id)`，案件 ID 是 Task 的案件：

| 动作 | 写在哪里 | 输入的引用 | 耗时、费用 |
| --- | --- | --- | --- |
| 派发任务 | `dispatch` 写入派发的 Task 以后 | Task 的输入 | 空 |
| 拒绝任务 | `dispatch` 写入 Task 和被拒绝的 Result 以后 | Task 的输入 | 空 |
| 生成 Result | `_settle` 写入了 Result 以后 | Result 引用的条目 | Result 的耗时和费用 |

说明由 `domain/entries.py` 的三个纯函数给出：

```python
def dispatched_text(task: Task) -> str:
    """派发任务的说明，比如：PM 派给 Quant：价格反应 BBB，问题"…"；
    截止时间 2026-10-14 15:05:00 UTC；PM 的第 1 轮。Critic 的任务没有轮次那一段。
    """
    ...


def rejected_text(task: Task, reason: TaskRejection) -> str:
    """拒绝任务的说明，比如：股票不是案件的股票；PM 派给 News：事件影响评估 AAA，问题"…"。"""
    ...


def result_text(result: Result) -> str:
    """生成 Result 的说明，比如：
    - 完成：评分 1，置信度 0.6，评估窗口 5 个交易日；模型 google/s-1
    - 无信号：<原因>；模型 google/s-1
    - 超时
    - 失败，格式不合法：<说明>；模型 openai/s-2（备用模型）
    - 失败，调用失败：<说明>
    """
    ...
```

- 写在 service 里，不在调用方：同时生成 Result 时两个调用拿到的 Result 一样，只有 service 知道是不是自己写入的（D3）
- 发起者、目标、任务类型、原因、状态的中文名放在 `domain/entries.py`：只有条目用；时刻写成 UTC，和别的条目一样精确到秒（截止时间是派发时刻加 5 分钟，带秒）
- 格式不合法的 Result 的说明是 signal-format 的原因代码（`horizon_not_offered`、`data_after_now`）时，条目里写它的中文（评估窗口不在配置的选项里、数据截至时刻晚于现在）；结构错误的位置和原因、调用失败的说明照原样写
- 被拒绝的 Result 写在拒绝任务里，不另写生成 Result：Proposal 的"被拒绝的任务及原因"就是这一条，一个被拒绝的请求一条
- 复用不写：没有生成 Task；PM 拿到的工具结果在 M5 每次模型调用的条目里
- token 不写：一个任务的 token 分在几次尝试里，由 M5 每次模型调用的条目写；费用记录也不另写条目（proposal「需要人决定」第 2 条）
- 用到的只有白名单里的标准库（`datetime`、`typing`），纯区域的规则不用改

### D7 迁移 v0007

`v0007_create_gateway_tables.py` 建三张表和两个索引，照 v0004 的写法写明每一列、不 import 业务代码；`revision = "0007"`，`down_revision = "0006"`（跟进的迁移）；`downgrade` 照旧不支持。

### D8 测试

```
tests/fakes/tasks.py                          add_result 已经有 Result 时给出它（修改）
tests/modules/gateway/builders.py             task_service 加程序日志的内存存储（修改）
tests/modules/gateway/test_repo.py            两个数据库实现（新，要开着 OrbStack）
tests/modules/gateway/test_service.py         程序日志的条目、已经有 Result 时给出它（补充）
tests/modules/gateway/test_budget_service.py  同时记下的几条都记普通预算（补充）
tests/modules/gateway/test_state_machine.py   构造参数；条目的不变量（修改）
tests/bootstrap/test_database.py、test_app.py  表的清单、列、主键、索引、归属、最新版本（修改）
```

- `test_repo.py` 用 `database` fixture：两个实现各自的往返、排序、`KeyError`、`add_result` 冲突时给出先写入的；task-dispatch「任务的存储」的场景用真的数据库跑 `TaskDispatchService`（重启就是用同一个 engine 新建存储和用例，`FakeIds` 接着用）；「同时生成 Result」用两个线程，存储包一层，在 `record` 之后等对方也读完再写，断言表里一行、两边给出同一个 Result、条目一条；「几个案件同时派发」用两个线程；call-budget「费用的存储」的原样读回、读写出错（连不上的地址、没升级的空数据库）、同时记下都写入（十个线程，行数和总额）
- `test_service.py`：program-log「Agent 网关的动作」的场景用内存存储和内存程序日志；「同时生成 Result」的内存版：存储包一层，在 `record` 之后插入另一个 Result，断言给出那一个、不写条目
- `test_budget_service.py`：「同时记下的几条都记普通预算」用两个线程，存储包一层，在 `since` 里等对方也读完
- 状态机：每一步之后检查每个 Task 正好一条派发任务或拒绝任务；派发出去的 Task 有 Result 时正好一条生成 Result，没有时没有
- 每个测试的说明写明对应的 spec 场景

## Risks / Trade-offs

- [调用方同时派发同一个案件] → 任务数、轮次可能超出上限一两个；D4 写明「案件工作流」和 M5 的约定
- [普通预算因为同时记下而超出] → 几美分；Proposal 允许，D5
- [写 Task、Result 和写条目不在一个事务里] → 写条目出错时 Task 或 Result 已经写入；同一个数据库，少见；和程序日志的「写入失败」一致
- [整份 JSON 的列，以后改 contracts 的字段旧行就读不出来] → 读的时候报错，不静默；改 contracts 的变更负责迁移已有的行

## Migration Plan

- 迁移 v0007 建三张空表和两个索引，启动时自动升级（bootstrap spec「数据库」）；本变更以后没有写入方，表一直是空的，直到「案件工作流」装配
- 按 `deploy/README.md`「更新」部署，更新前照常手动备份
- 回退：回到上一个提交，用更新前的备份恢复状态库
