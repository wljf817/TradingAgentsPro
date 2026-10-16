# Design

## Context

现状：

- 跟进模块 `modules/followups`（add-follow-ups）：domain 的 `FollowUp` 是 frozen dataclass，带原样的 `FollowUpRequest`（contracts 的 Pydantic 模型）；存储端口 `FollowUpStore` 有 `pending()`、`requested_since(moment)`、`save(follow_up)`；`FollowUpService` 只读写这个端口，测试用 `tests/fakes/follow_ups.py` 的内存实现。没有 `repo.py`，没有表。用例的说明写着"同一个实例由调用方串行调用……做法 M4 和股票池一起定"
- 状态库 `modules/statestore`：`versioned_table(name, key, *columns)` 建版本表；`VersionedStore` 的 `save(key, record, **columns)` 内容没变时不写，`get`，`latest(**where)` 列出每个键的最新版本里筛选列"等于"给定值的，`history(since)`。写入时刻 `recorded_at` 只用来排查，不按它查询（add-state-store design D3）
- 程序日志 `modules/programlog`：`Actor`、`Action` 两个枚举；`ProgramLog.write(actor, action, subject, detail, *, inputs, outputs, case_id, ...)` 从时钟取时间、追加一条，出错原样抛出。以后的模块在 service 的构造参数里拿一个 `ProgramLog`，新的行为者和动作加进这两个枚举（add-program-log design D3）
- 迁移在 `bootstrap/migrations/versions/`，现在到 v0004；v0005（股票池存储）和 v0007（任务和费用存储）由同时进行的另两个变更加。迁移写明每一列，不 import 业务代码；启动时 `upgrade head`，迁移要保持一条线
- 交易侧的串行是一把进程内的可重入锁，由调用方（workflows、bootstrap）拿：一次拿锁里要组合执行模块和风控的几个调用（session-patrol spec「执行模块和风控的调用串行」，add-session-patrol design D3）
- 架构是单进程的模块化单体；同一只股票一次只运行一个案件（case-queue spec），不同股票的案件、到点触发的定时任务、股票池的状态变化可以同时调用跟进审批
- 现在没有运行中的代码调用 `FollowUpService`

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/followups
  repo.py              新：表 FOLLOW_UPS（followups_follow_ups）和 PostgresFollowUpStore
  domain/entries.py    新：条目的说明（跟进的内容、拒绝的原因、状态变化）
  service.py           改：构造参数加 log；四个公开方法拿同一把锁；保存以后写条目
  ports.py             改：只改说明
  __init__.py          改：说明；导出 PostgresFollowUpStore
modules/programlog
  domain/entry.py      改：Actor 加 SCHEDULER，Action 加五个跟进的动作
modules/statestore     不改：用 versioned_table 和 VersionedStore
bootstrap
  migrations/versions/v0006_create_follow_ups.py    新
```

跨模块传递的 Pydantic 模型：没有新的。

- 跟进的记录 `FollowUp`（带 contracts 的 `FollowUpRequest`）整份存成 JSONB，由状态库的 `TypeAdapter` 转换；读写都在本模块里
- 写程序日志时交给 `ProgramLog.write` 的是枚举和字符串
- 用例的公开方法、参数和返回值不变；只有构造参数多一个程序日志

## Goals / Non-Goals

**Goals:**

- 数据库实现和内存实现对端口的行为一致：端口的测试两种实现都跑
- 串行只靠跟进审批自己的一把锁，调用方不用知道
- 审批规则、domain 的记录和存储端口都不改

**Non-Goals:**

按「编程风格」省略的情况：

- 几个进程同时运行：架构是单进程
- 保存以后、写条目以前进程退出：少一条条目
- 审批保存以后报错（写条目时数据库出错），调用方重做这一步：同一个申请再获批一次，替换前一次，当天的次数多算一次
- 作废写到一半时数据库出错：剩下的跟进还是待执行，状态变化不会再传一次；到点时案件按那时的状态不运行（case-types spec「开始运行时确定案件类型」）
- 到点触发写到一半时数据库出错：已经记为已触发的这次没有返回给调用方，这次触发丢了，和「跟进到点」要处理的入队前退出是同一类
- 锁没有超时：拿着锁时只有几次本机数据库的读写
- 时钟回退使获批时刻倒序：不处理

## Decisions

### D1 跟进表和数据库实现

```python
FOLLOW_UPS = versioned_table(
    "followups_follow_ups",
    Column("follow_up_id", Uuid, nullable=False),
    Column("pending", Boolean, nullable=False),
)


class PostgresFollowUpStore:
    """满足 FollowUpStore：跟进存在 followups_follow_ups 表里，待执行的 pending 为真。"""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._versions = VersionedStore(engine, clock, FOLLOW_UPS, FollowUp)

    def pending(self) -> tuple[FollowUp, ...]:
        return self._versions.latest(pending=True)

    def requested_since(self, moment: datetime) -> tuple[FollowUp, ...]:
        return tuple(f for f in self._versions.latest() if f.requested_at >= moment)

    def save(self, follow_up: FollowUp) -> None:
        pending = follow_up.status is FollowUpStatus.PENDING
        self._versions.save(follow_up.follow_up_id, follow_up, pending=pending)
```

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `follow_up_id` | uuid | 键 |
| `version`、`recorded_at`、`data` | integer、timestamptz、jsonb | 版本表共有的列（add-state-store design D1） |
| `pending` | boolean | 筛选列：这个版本是待执行 |

- 表名按"模块名_内容"：模块是 `followups`，内容是跟进
- 一个跟进最多 2 个版本：获批时 1 个（待执行），状态变化一次（已触发、被替换、已作废）再 1 个，以后不再变化（follow-ups spec「跟进的状态」）
- 只有一个筛选列：端口的 `pending()` 列出全部待执行的，按股票筛在用例里（add-follow-ups design D8）；端口没有按股票或按跟进 ID 读的方法
- 2026-10-16 在临时脚本里验证过：`FollowUp` 经 `TypeAdapter` 转 JSON 再读回，引用是日程项、事件、条件（值 `Decimal("55.10")`、`Decimal("-0.05")`）或没有，触发时间带 -05:00 时差，读回的都和原来相等，引用读回的类型也对（两种引用的字段不重叠）

### D2 每天的申请次数：读全部跟进，按获批时刻筛

端口的 `requested_since(moment)` 要"获批时刻不早于 moment 的跟进，不论现在的状态"。`VersionedStore.latest` 只能按筛选列"等于"筛，所以数据库实现取每个跟进的最新版本，在 Python 里留下 `requested_at >= moment` 的（D1）。

- 默认每天最多 5 个跟进获批，一年一千多个；审批一天只有几次，每次读一遍全部跟进
- 获批时刻不随版本变化，按最新版本筛和按哪个版本筛结果相同
- 读不出来的旧记录照常报错（state-store spec「读写出错时报错」）；记录格式改了本来就要写数据迁移改旧版本（add-state-store design D4）

考虑过：

- 给 `VersionedStore` 加按范围筛的读取：要改状态库模块和 state-store spec，现在只有这一个调用方。股票池存储的 `changed_since` 也是按时刻筛，要加的话两边一起定
- 记录里加"获批的交易日"，用筛选列按"等于"查：要改 domain 的记录和 follow-ups spec；交易日是从获批时刻和日历推出来的，多存一份
- 按 `recorded_at` 查：写入时刻只用来排查（add-state-store design D3），和获批时刻也不是同一次读时钟

### D3 一把锁放在跟进审批里

```python
class FollowUpService:
    def __init__(
        self,
        store: FollowUpStore,
        clock: Clock,
        calendar: TradingCalendar,
        ids: IdGenerator,
        config: FollowUpConfig,
        log: ProgramLog,
    ) -> None:
        ...
        self._program_log = log
        self._lock = threading.RLock()  # 四个公开方法一次只进行一个

    def trigger_due(self) -> tuple[FollowUp, ...]:
        with self._lock:
            triggered: list[FollowUp] = []
            for follow_up in due(self._store.pending(), self._clock.now()):
                done = follow_up.triggered()
                self._store.save(done)
                self._program_log.write(
                    Actor.SCHEDULER,
                    Action.TRIGGER_FOLLOW_UP,
                    str(done.follow_up_id),
                    content(done.symbol, done.request),
                )
                triggered.append(done)
            return tuple(triggered)
```

四个公开方法（`pending`、`request`、`trigger_due`、`status_changed`）的整个方法体都在锁里：读时钟、读存储、判断、保存、写条目。

- 要保护的是跟进审批自己的先读后写：每天的次数跨股票；同一个跟进的替换、触发和作废来自不同的调用方（案件、定时任务、股票池的变化）。锁在模块里，以后接线的变更不用记得拿
- 叶子锁：拿着它时只读写跟进的表、写程序日志（一次插入，不拿锁），不调用别的模块、不拿别的锁，所以不会和交易侧的那把锁互相等待
- 可重入：`status_changed` 调用 `pending`，同一个线程再次拿锁
- 读时钟也在锁里：获批的先后和获批时刻的先后一致，"按获批的先后"排序靠的是获批时刻
- `pending` 也拿锁：不会读到替换做了一半（旧的已经被替换、新的还没保存）的样子

考虑过：

- 像交易侧那样由调用方拿锁：那把锁要在一次拿锁里组合执行模块和风控的几个调用；跟进审批的每个方法自己就是完整的一步，没有要组合的
- 每只股票一把锁：每天的申请次数跨股票，保护不了
- 数据库的事务或行锁：`VersionedStore` 每次保存是一个事务，读和写放不进同一个事务，要加接口；单进程用进程内的锁就够
- 普通 `Lock`：`status_changed` 调用 `pending` 时会卡死

### D4 程序日志：调度器的五个动作

`modules/programlog/domain/entry.py` 加：

```python
class Actor(StrEnum):
    """行为者，见 program-log spec。"""

    ...
    SCHEDULER = "scheduler"  # 调度器


class Action(StrEnum):
    """动作，见 program-log spec 各个行为者的动作表。"""

    ...
    APPROVE_FOLLOW_UP = "approve_follow_up"  # 批准跟进
    REJECT_FOLLOW_UP = "reject_follow_up"  # 拒绝跟进
    REPLACE_FOLLOW_UP = "replace_follow_up"  # 替换跟进
    VOID_FOLLOW_UP = "void_follow_up"  # 作废跟进
    TRIGGER_FOLLOW_UP = "trigger_follow_up"  # 触发跟进
```

- 行为者是调度器：完整版「共享状态库」里待执行的自定义跟进由调度器写，「程序日志」也把跟进的动作列在调度器下。以后调度器的开案件、排队、案件阶段也用 `SCHEDULER`
- 动作名带"跟进"：`Action` 是一个枚举，人的"拒绝"（`REJECT`）已经有了
- 完整版的"审批"分成批准和拒绝两个动作，查被拒绝的申请只看动作；"触发跟进"见 proposal「需要人决定」第 1 条

用例在锁里、保存以后写条目（add-program-log design D3：拿着锁的调用在锁里接着写，写一条是一次本机数据库的插入）：

| 方法 | 写什么 |
| --- | --- |
| `request` 不通过 | 拒绝跟进：对象是股票，案件 ID 是 `case_id` |
| `request` 通过 | 保存（替换时先保存被替换的旧跟进，再保存新的）以后写批准跟进（对象是新跟进的 ID）；替换时接着写替换跟进（对象是旧跟进的 ID，输入是新跟进的 ID）。案件 ID 都是 `case_id` |
| `trigger_due` | 每个到点的跟进：保存已触发的版本，接着写它的触发跟进（D3 的代码） |
| `status_changed` 作废时 | 每个待执行的跟进：保存已作废的版本，接着写它的作废跟进 |
| `pending`；没有到点的 `trigger_due`；不作废的 `status_changed` | 不写 |

- 每保存一个就写它的条目，不是全部保存完再写：中途出错时，写了条目的都已经保存，保存了没写条目的最多一个
- 作废和触发不带案件 ID：状态变化的调用方不传案件（止损成交、紧急退出这类状态变化不在案件里）；触发时还没有案件，开出的案件由「跟进到点」写排队
- 对象：跟进 ID 写 UUID 的字符串，和方案 ID 一样；拒绝时还没有跟进 ID，写股票

### D5 说明的写法

`domain/entries.py`，纯函数，只给用例用，不从包根导出：

```python
def content(symbol: str, request: FollowUpRequest) -> str:
    """跟进的内容：股票、原因类型、引用的对象、触发时间（UTC）、问题和可能采取的动作。"""


def rejection_detail(rejection: FollowUpRejection, symbol: str, request: FollowUpRequest) -> str:
    """拒绝跟进的说明：不通过的原因；申请的内容。"""


def void_detail(before: StockStatus | None, after: StockStatus | None, follow_up: FollowUp) -> str:
    """作废跟进的说明：之前和之后的状态；跟进的内容。"""
```

基准数据的日程申请获批、不写引用的事件申请被拒绝、事件申请因清仓作废时的说明：

```
CCC，外部日程，日程项 S-9，触发时间 2026-11-19 14:30 UTC；问题：竞争对手 11 月 18 日发财报，之后对这只股票有什么影响？；可能采取的动作：减仓
没有引用具体对象：提示 PM 改为调整复查日；CCC，事件后反应，没有引用，触发时间 2026-10-14 18:00 UTC；问题：……
持仓变为候选；CCC，事件后反应，事件 V-7，触发时间 2026-10-14 18:00 UTC；问题：……
```

- 几张中文名字的表：原因类型（事件后反应、外部日程、价格或指标观察）；引用（日程项 S-9、事件 V-7、条件"价格不低于 55"，条件的量是价格、当日涨跌幅、当日成交量，比较是不低于、不高于）；拒绝原因（follow-ups spec「审批的顺序」的原因一栏）；股票状态（持仓、关注、候选，空的写不在股票池）
- 时刻写成 `%Y-%m-%d %H:%M UTC`，和别的条目相同
- 放 domain 不放用例：用例只管读时钟、生成 ID、读写存储和写条目（add-follow-ups 的 Goals）；只用 `datetime`、contracts 和本模块的 domain，纯区域的规则不用改

### D6 包根和装配

- 构造参数 `log` 放在最后，和通知模块一样；用例 import 程序日志模块的包根（`Action`、`Actor`、`ProgramLog`）：同在 modules 层、只在 service 里用，程序日志模块只依赖 shared，不成环（add-program-log design D3）
- 包根加导出 `PostgresFollowUpStore`，给以后的 bootstrap 装配
- 不接 bootstrap：没有调用方。迁移照常在启动时执行，表在部署时就建好

### D7 迁移 v0006

`bootstrap/migrations/versions/v0006_create_follow_ups.py`，和 v0002、v0003 一样写明每一列：

```python
revision: str = "0006"
down_revision: str | None = "0005"


def upgrade() -> None:
    op.create_table(
        "followups_follow_ups",
        sa.Column("follow_up_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("follow_up_id", "version"),
    )


def downgrade() -> None:
    raise NotImplementedError("迁移只升级，见 add-state-store 的 design D6")
```

编号固定是 v0006。`down_revision` 在 apply 时设为那时最新的迁移：股票池存储的 v0005 已经在 main 上时是 `"0005"`，否则是 `"0004"`，迁移保持一条线。

### D8 测试

```
tests/modules/followups/builders.py             改：follow_up_service 加程序日志（默认新建内存的）
tests/modules/followups/test_repo.py            新：数据库实现，follow-ups spec「跟进的存储」
tests/modules/followups/test_service.py         改：program-log spec「调度器的动作」
tests/modules/followups/test_service_threads.py 新：follow-ups spec「同时进行的调用」
tests/modules/followups/test_state_machine.py   改：用例带程序日志，加条目的检查
tests/bootstrap/test_database.py                改：表清单、列、主键、归属加跟进表
```

- `test_repo.py`：端口的行为同时测内存实现和数据库实现（`params=["memory", "postgres"]`，同 `tests/modules/risk/test_repo.py`）：`pending()` 只看最新版本；`requested_since` 的边界（等于 moment 的算），已触发、被替换、已作废的也给出。「跟进的存储」的场景只测数据库实现：版本直接查表；重启用同一个数据库新建 Engine、存储和用例；连不上用连不上的地址（同 `tests/modules/programlog/test_repo.py`）
- `test_service_threads.py`：一个包住内存存储的替身，第一个调用读存储时等第二个调用也来读，最多等 0.5 秒。有锁时第二个调用卡在锁外，第一个等满 0.5 秒照常做完，第二个接着做；没有锁时两个调用都读到旧的状态，场景失败。结果不靠线程调度的运气，没有锁时一定失败。两个审批在 `requested_since` 上等；审批和到点触发的场景让到点触发先进去、在 `pending()` 上等，结果是"到点触发在前"的那一种
- `test_service.py`：程序日志用 `tests/fakes/program_log.py` 的内存存储，用它的 `one`、`of`、`kinds` 断言行为者、动作、对象、案件 ID、引用，以及说明里的关键值
- 状态机：每一步之后加检查：每个跟进正好一条批准跟进；已触发、被替换、已作废的各有一条对应动作的条目，待执行的没有；每次拒绝一条拒绝跟进
- 数据库的测试要开着 Docker（`open -a OrbStack`）

## Risks / Trade-offs

- [每次审批读一遍全部跟进] → 一年一千多条；读得慢了再给状态库加按范围筛的读取（D2）
- [锁只管一个进程] → 架构是单进程
- [记为已触发以后、入队以前进程退出，这次触发就丢了] → 本变更不改（add-follow-ups 的 Risks）；「跟进到点」接线时处理
- [保存在前、写条目在后，中间退出少一条条目] → 和别的条目一样（add-program-log 的省略清单）

## Migration Plan

- 部署时启动程序把数据库升级到 v0006，建出空的跟进表；没有调用方，运行中的行为不变
- 回滚：revert 代码；迁移只升级，空表留着不影响旧代码
