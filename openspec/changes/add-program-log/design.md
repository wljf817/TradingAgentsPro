# Design

## Context

现状：

- 程序的动作和技术问题都写进 Python 的 `logging`（`bootstrap/logs.py` 配成一行一条写到 stderr，由 systemd 交给 journald）。写日志的地方：`workflows/` 的 `order_flow.py`、`patrol.py`、`emergency_flow.py`、`health.py`、`reconciliation.py`、`account_snapshot.py`；`bootstrap/app.py`、`bootstrap/startup.py`；`modules/notification/service.py`；`integrations/discord/notifier.py`、`commands.py`。哪一行去哪里见 D4
- 状态库的表都用 `modules/statestore` 的版本表（按键保存版本，`VersionedStore`）；迁移在 `bootstrap/migrations/versions/`，现在到 v0003，迁移写明每一列、不 import 业务代码
- 分层由 import-linter 管：`top-layers`（bootstrap → workflows | integrations → modules → contracts → shared）、`module-layers`（模块里只能有 service、repo、ports、domain、config）、`acyclic`（兄弟包之间没有环）；模块之外只能 import 模块的包根（`tests/architecture/rules.py`）。模块之间可以互相 import 包根，比如风控和执行模块的 repo 都 import 状态库
- 风控、执行模块的用例返回结果（`RiskDecision`、`Order`、`Protection`、`Reconciliation`），自己不写日志；流程按返回的结果写。`RiskService.release` 不返回有没有释放
- 下单流程的每一步是一个 DBOS step：做完的 step 重启以后不再做（order-flow spec「持久化和恢复」）

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/programlog        新模块
  domain/entry.py         Actor、Action、LogEntry、对象的两个固定值
  ports.py                ProgramLogStore（append、latest）
  repo.py                 表 programlog_entries，PostgresProgramLogStore
  service.py              ProgramLog（write、latest）
  __init__.py             导出以上名字
modules/notification
  service.py              构造参数加 ProgramLog；alert 加对象，finish_confirmation 加方案 ID；
                          告警、请求确认、结束确认写条目
workflows
  entries.py              新：订单变化、释放预留、方案和风控结果的条目，几个流程共用
  order_flow.py           风控检查、确认的结果、等开盘、再校验、提交、释放写条目
  order_workflow.py       录入方案；启动下单流程失败时的释放
  patrol.py               订单变化、止损单变化、无保护仓位和解除、同步预留时的释放
  emergency_flow.py       熔断、紧急风险退出、撤单、作废、清仓方案的检查和提交、确认的结果
  health.py               降级模式的三种条目、恢复后的对账
  reconciliation.py       收盘对账的对账条目
bootstrap
  startup.py              启动、崩溃恢复；启动对账和第一次健康判定的条目
  app.py                  装配 ProgramLog，登记停止；TradingApp 加 log
  migrations/versions/v0004_create_program_log.py
integrations/discord/notifier.py
                          去掉三条 INFO（D4）
```

跨模块传递的 Pydantic 模型：没有新的。条目是程序日志模块自己的 frozen dataclass，写入方经 `ProgramLog.write` 的参数交给它，不跨端口传递；通知模块和流程传给 `write` 的是字符串、枚举和已有的类型。contracts、shared 不改；风控、执行模块不改。

## Goals / Non-Goals

**Goals:**

- 每个动作在它发生的地方写一条，按返回的结果写，不改风控和执行模块
- 巡检每一轮没有变化时一条都不写
- 以后的模块用同一个 `ProgramLog` 写，不破坏分层

**Non-Goals:**

- 数据库层面禁止修改和删除：程序只有追加和读取两个操作，和状态库的版本表一样靠写法保证；加触发器还要改测试清空表的做法
- 条目的编号不交给写入方：现在没有地方要引用一条条目，以后要引用时再让 `write` 返回编号
- 读取只有 `latest`：给崩溃恢复用；按方案、按时间的查询先由人用 SQL（D11）

按「编程风格」省略的情况：

- 下单流程的一步写完条目、DBOS 还没记下这一步时进程退出：重启以后这一步重做，条目多一条
- 动作做完、写条目以前进程退出：少一条；条目和动作不在一个事务里
- 紧急风险退出写在这一项之前，这一项出错时下一轮再写一次
- 重启以后巡检第一次看到的止损单、还在继续的一段无保护再写一次（和再告警一样）
- 同一个方案 ID 再次提交：录入方案和风控检查再写一次
- 等紧急退出的确认时重启、又请求一次：前一条确认消息没有人的结果和结束确认
- 按钮送达和超时同时、下单流程按超时处理：只有超时
- 第 2 步写完启动以后、启动失败之前进程被杀：下一次启动写崩溃恢复
- 用备份恢复以后的第一次启动多半写崩溃恢复：备份时程序在运行，备份里最后一条是启动
- 几个线程同时写入时，编号的先后和时间的先后可能差几毫秒

## Decisions

### D1 程序日志模块和表

`modules/programlog/domain/entry.py`：

```python
class Actor(StrEnum):
    """行为者，见 program-log spec。以后的变更加别的行为者。"""

    RISK = "risk"  # 风控引擎
    EXECUTION = "execution"  # 执行模块
    HUMAN = "human"  # 人
    SYSTEM = "system"  # 系统


class Action(StrEnum):
    """动作，见 program-log spec 四个行为者的动作表。以后的变更加别的动作。"""

    RISK_CHECK = "risk_check"  # 风控检查
    RECHECK = "recheck"  # 下单前再校验
    RELEASE = "release"  # 释放预留
    SUBMIT = "submit"  # 提交订单
    ORDER_CHANGE = "order_change"  # 订单变化
    CANCEL = "cancel"  # 撤单
    STOP_CHANGE = "stop_change"  # 止损单变化
    UNPROTECTED = "unprotected"  # 无保护仓位
    PROTECTED = "protected"  # 无保护解除
    RECONCILE = "reconcile"  # 对账
    WAIT_OPEN = "wait_open"  # 等开盘
    ENTER_PLAN = "enter_plan"  # 录入方案
    CONFIRM = "confirm"  # 确认
    REJECT = "reject"  # 拒绝
    TIMEOUT = "timeout"  # 超时
    STARTUP = "startup"  # 启动
    SHUTDOWN = "shutdown"  # 停止
    CRASH_RECOVERY = "crash_recovery"  # 崩溃恢复
    ENTER_DEGRADED = "enter_degraded"  # 进入降级模式
    DEGRADED_UPDATE = "degraded_update"  # 降级模式里的变化
    LEAVE_DEGRADED = "leave_degraded"  # 退出降级模式
    BREAKER = "breaker"  # 熔断
    EMERGENCY_EXIT = "emergency_exit"  # 紧急风险退出
    ALERT = "alert"  # 告警
    REQUEST_CONFIRMATION = "request_confirmation"  # 请求确认
    FINISH_CONFIRMATION = "finish_confirmation"  # 结束确认


ACCOUNT = "account"  # 对象：整个券商账户
WHOLE_SYSTEM = "system"  # 对象：整个程序


@dataclass(frozen=True, slots=True)
class LogEntry:
    """程序日志的一条，见 program-log spec「程序日志的条目」。"""

    recorded_at: datetime
    actor: Actor
    action: Action
    subject: str
    detail: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    case_id: UUID | None = None
    duration: timedelta | None = None
    tokens: int | None = None
    cost: Decimal | None = None
```

行为者和动作存英文代码，和项目里别的枚举一样；说明是中文。只用到白名单里的标准库，纯区域的规则不用改。

表 `programlog_entries`（`repo.py` 用 SQLAlchemy Core 定义，迁移 v0004 写明每一列）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint，自增（identity），主键 | 编号 |
| `recorded_at` | timestamptz，非空 | 时间 |
| `actor`、`action`、`subject`、`detail` | text，非空 | 说明可以是空字符串 |
| `inputs`、`outputs` | text[]，非空 | 没有引用时是空数组 |
| `case_id` | uuid | |
| `duration` | interval | |
| `tokens` | integer | |
| `cost` | numeric | 不限精度，原样保存 |

只给 `recorded_at` 建一个索引（`programlog_entries_recorded_at`）：人最常按时间查。每天几百条，按对象、引用查走全表也够快；按案件、按 agent 查的读取方（M5、M6）出现时再加索引。

不用 `VersionedStore`：条目没有键、没有版本，每次都是新的一条。表名以模块名开头，只由这个模块写（state-store spec「表的归属和迁移」）。

`PostgresProgramLogStore(engine)`：`append` 一条一个事务；`latest(actions)` 按 `id` 倒序取第一条 `action` 在给定集合里的，没有时为 None。数据库的错误原样抛出。

### D2 写入接口

```python
class ProgramLogStore(Protocol):
    def append(self, entry: LogEntry) -> None: ...

    def latest(self, actions: Collection[Action]) -> LogEntry | None: ...


class ProgramLog:
    def __init__(self, store: ProgramLogStore, clock: Clock) -> None: ...

    def write(
        self,
        actor: Actor,
        action: Action,
        subject: str,
        detail: str,
        *,
        inputs: Sequence[str] = (),
        outputs: Sequence[str] = (),
        case_id: UUID | None = None,
        duration: timedelta | None = None,
        tokens: int | None = None,
        cost: Decimal | None = None,
    ) -> None: ...

    def latest(self, actions: Collection[Action]) -> LogEntry | None: ...
```

- `write` 从时钟取时间，拼成 `LogEntry` 交给存储；数据库出错时原样抛出（program-log spec「写入失败」），不重试、不吞掉
- 写入方不自己拼 `LogEntry`：时间只能来自时钟
- `case_id`、`duration`、`tokens`、`cost` 是 Proposal 规定的字段，本变更没有写入方传它们；以后的变更（案件工作流、Agent 网关）直接用

备选：把条目做成 contracts 里的 Pydantic 模型，各模块在自己的 ports 里声明一个写日志的端口。条目不跨端口传递，写入方都直接调用 `ProgramLog`，多一层端口没有用处，不这样做。

### D3 谁来写，以后的模块怎样写

条目由动作发生的那一层按返回的结果写：

| 条目 | 写在哪里 |
| --- | --- |
| 录入方案 | `OrderPlanIntake.submit`，风控检查之前 |
| 风控检查 | `check_plan`；`EmergencyFlow._advance` 检查清仓方案以后 |
| 下单前再校验 | `OrderFlow._attempt`，作废或者提交了订单时才写（转去等重试、等开盘的那几次不写）；`EmergencyFlow._advance` |
| 释放预留 | `entries.release`（D6），在每个调用 `risk.release` 的地方 |
| 提交订单 | `OrderFlow._attempt`、`OrderFlow.void_expired`（已经有订单）；`EmergencyFlow._advance` |
| 订单变化 | `SessionPatrol._track_orders`；撤单和三种对账用 `entries.order_changes`（D5） |
| 撤单 | `EmergencyFlow.cancel_buys`、`EmergencyFlow._advance` |
| 止损单变化、无保护仓位、无保护解除 | `SessionPatrol._log_stop`、`_alert`、`_forget` |
| 对账 | `startup.reconcile_at_startup`、`reconciliation.close_reconcile_if_due`、`HealthMonitor._recover` |
| 等开盘 | `OrderFlow.timing` |
| 确认、拒绝、超时 | `OrderFlow.settle`；`EmergencyFlow._settle` |
| 启动、崩溃恢复、停止 | `startup.record_startup`；`app.start` 登记的停止（D7） |
| 进入降级模式、降级模式里的变化、退出降级模式 | `HealthMonitor._report`、`_recover`；`startup.judge_health_at_startup` |
| 熔断、紧急风险退出 | `EmergencyFlow.cancel_buys`、`EmergencyFlow.advance` |
| 告警、请求确认、结束确认 | `NotificationService`（D8） |

拿着锁的调用（风控、执行模块）在锁里接着写条目：写一条是一次本机数据库的插入；推送告警仍然不拿锁。

为什么不让执行模块自己写：它的 `_save_order`、`_save_protection` 能看到每一次变化，最准。但这要改执行模块，而且它保存的变化里有状态未知时重新计时、改单请求的记号这些对人没有意义的，还得再挑一遍。流程按调用前后对比（D5）已经覆盖订单的每一次跟进，止损单按巡检每一轮看到的写（session-patrol spec 原来的做法），所以不改执行模块。风控同理：调用方知道释放的原因，风控模块不知道。

为什么通知模块自己写：告警和结束确认发不出去时，通知模块按 discord-notification spec 吞掉错误、只写错误日志，调用方不知道发没发出去；完整版「技术栈与部署」要求 Discord 发不出去时写程序日志。只有通知模块能如实写。

为什么人的动作在流程收下结果的地方写，不在 Discord 适配器里写：适配器在 Discord 的事件循环上，送达以后结果不一定生效（截止时刻同时到了、紧急退出被本地备用止损越过）；流程收下的才是生效的决定。适配器照旧写每次按下的 Python 日志（discord-notification spec「按钮的处理」），那是通知通道的诊断。

以后的模块怎样写：在 service 里 `from tradingagentspro.modules.programlog import Action, Actor, ProgramLog`，构造参数要一个 `ProgramLog`，由 bootstrap 注入。

- `top-layers`：同在 modules 层，可以 import
- `module-layers`：只在 service 里用，repo、ports、domain、config 不碰它
- `acyclic` 和文件之间的环：程序日志模块只 import shared，永远不 import 别的模块，所以谁 import 它都不会成环
- 只 import 包根，符合 `tests/architecture/rules.py` 的规则

通知模块是第一个这样写的模块。新的行为者和动作加进 `Actor`、`Action`。

### D4 每处 Python 日志的去留

| 地方 | 去留 |
| --- | --- |
| `bootstrap/app.py` 启动、每一步的结果、启动完成、停止信号、每一项关闭、已停止、启动失败 | 留（bootstrap spec「日志」） |
| `bootstrap/startup.py` 启动对账、第一次健康判定的结果 | 留（bootstrap spec 要求每一步的结果），另写对账、进入降级模式的条目 |
| `order_flow.py` 风控、请求确认、确认的结果、释放、等开盘、再校验、提交、作废、已经有订单 | 移到程序日志；请求确认由通知模块写 |
| `order_flow.py` 已经不在交易时段、等下一个开盘 | 去掉：接着的等开盘条目写了等到的时刻 |
| `order_flow.py` 确认请求没有发出去（ERROR）、没能下单（WARNING）、下单出错（ERROR，带异常栈） | 留：技术问题 |
| `patrol.py` 订单变化、止损单变化、不再是无保护仓位 | 移到程序日志 |
| `patrol.py` 巡检已开始；这一轮或某一项出错；读取持仓失败 | 留 |
| `emergency_flow.py` 熔断撤买单、请求确认、已确认、确认被拒绝、作废方案、撤单、建立清仓单 | 移到程序日志；请求确认由通知模块写 |
| `emergency_flow.py` 告警的同文 WARNING、确认消息作废（本地备用止损） | 去掉：通知模块写告警、结束确认 |
| `emergency_flow.py` 等 N 笔挂着的订单结束（每一轮） | 去掉 |
| `emergency_flow.py` 读券商账户失败、行情不可用、行情源缺数据、记录没有结束、确认请求没有发出去 | 留 |
| `health.py` 进入降级模式、降级模式里的变化、降级模式结束 | 移到程序日志 |
| `health.py` 取不到行情、恢复后的对账没做成 | 留 |
| `reconciliation.py` 收盘对账的结果 | 移到程序日志的对账 |
| `reconciliation.py` 收盘对账没做成（券商不可用） | 留 |
| `account_snapshot.py` 取不到行情、按均价估算 | 留 |
| `notification/service.py` 告警没有发出去、确认消息没有改成（ERROR） | 留，条目里另外写明没有发出去 |
| `integrations/discord/notifier.py` 推送告警、请求确认、确认消息改为（调用方报告） | 去掉：和通知模块的条目一样 |
| `integrations/discord/notifier.py` 每次按下、连接中止、关闭出错 | 留（discord-notification spec「按钮的处理」） |
| `integrations/discord/commands.py` 每条 `/plan` 的处理 | 留（manual-plans spec「日志」） |
| `bootstrap/logs.py` | 只改说明：程序日志在数据库里，这里是启动、停止的过程和技术问题 |

### D5 订单变化按调用前后对比

`workflows/entries.py`：

- `log_order_change(log, before, after)`：状态或累计成交不同时写订单变化，说明里有股票、方向、前后的状态、累计成交和委托股数，有成交时加成交均价和累计费用
- `order_changes(execution, log)`：上下文管理器，进入时记下全部未结束的订单，退出时按方案 ID 读回每一笔，用 `log_order_change` 写。块里抛错时不写（对账读券商失败时什么都没改）

巡检的跟进照旧逐笔对比；启动对账、收盘对账、恢复后的对账，以及紧急退出的两种撤单（撤单以后执行模块马上跟进一次，可能已经撤销）都包在 `order_changes` 里。这几处跟进过的订单不再是未结束的，巡检之后的轮次不会再看到它们，所以要在这里写。

止损单：`SessionPatrol` 比较上次看到的止损单，仍按（客户端订单 ID、状态、未成交股数、止损价）；说明里加有成交时的累计成交和成交均价。先写条目、再记下，写入出错时下一轮再写。

### D6 释放预留只在有有效预留时写

`entries.release(risk, log, plan_id, reason)`：先看 `plan_id in risk.active_plan_ids()`，再 `risk.release(plan_id)`，原来有有效预留才写条目。调用方在原来调用 `risk.release` 的地方改调它，拿锁的方式不变。巡检的同步预留在 `record_fill` 之后调用，所以全部成交、预留已经用完时不写。

备选：让 `RiskService.release` 返回有没有释放。要改风控模块，不这样做。多读一次有效预留，释放很少发生。

### D7 启动、停止和崩溃恢复

- 启动的第 2 步升级数据库以后建 `ProgramLog`，调用 `startup.record_startup(log, text)`：`latest({STARTUP, SHUTDOWN})` 是启动时先写崩溃恢复（说明写上一次启动的时刻），再写启动（说明是 `tradingagentspro <版本号>，模拟盘`）。出错按这一步启动失败
- 写完启动立即 `stack.callback(...)` 登记写停止：它在数据库连接的关闭项之后登记，所以在 OpenD 行情连接关闭以后、数据库连接关闭以前执行。正常停止和启动失败都会执行；写入出错时 `ExitStack` 照常关闭数据库连接，再把错误抛出去
- `_assemble` 接收这个 `ProgramLog`，交给通知模块、各流程；`TradingApp` 加 `log`，以后的流程从这里取
- 启动对账包在 `order_changes` 里，写对账条目；第一次健康判定是降级时写进入降级模式
- 启动时的两条告警（启动对账有不一致、启动时处于降级模式）推送时给出对象 `account`、`system`

### D8 通知模块的条目

- `alert(subject, text)`：推送；发不出去时照旧写 ERROR；然后写告警的条目，发不出去的在说明末尾加一行写明没有发出去和原因
- `request_confirmation`、`request_emergency_confirmation`：发出去写请求确认（输出的引用是消息的标识，说明写截止时刻，紧急退出另写原因和当前持仓）；发不出去写同一个动作、说明写明没有发出去和原因，再把通知不可用抛给调用方
- `finish_confirmation(plan_id, message_id, outcome)`：报告；没改成照旧写 ERROR；然后写结束确认（对象是方案 ID，输入的引用是消息的标识）
- 调用方：下单流程、巡检、紧急退出、健康、收盘对账、启动按 program-log spec「系统的动作」给出告警的对象；`settle` 和紧急退出报告结果时给出方案 ID

### D9 各流程里的顺序

- 下单流程：录入方案 → 风控检查 →（通知模块）请求确认 → 确认 / 拒绝 + 释放 / 超时 +（通知模块）结束确认 + 释放 → 等开盘 → 下单前再校验 → 提交订单，或者释放 +（通知模块）告警。超时的条目要截止时刻，所以 `settle` 这一步改为接收请求确认那一步返回的 `Requested`（消息标识和截止时刻），发不出去时是 None；step 的名字和先后不变，进行中的下单流程照样恢复
- 紧急退出的一项：紧急风险退出（这次运行里第一次，在拿锁之前）→ 锁里：释放（作废的方案）、撤单和订单变化、风控检查、下单前再校验、提交订单 → 锁外：（通知模块）告警
- 熔断：熔断（这次运行里当天第一次）→ 锁里：撤单和订单变化 → 锁外：告警
- 健康：判定 → 状态变化的条目 →（通知模块）告警；恢复后的对账：锁里对账、订单变化 → 锁外：对账、退出降级模式 →（通知模块）告警
- 收盘对账：锁里对账、订单变化 → 锁外：对账 →（通知模块）告警

### D10 测试

- `tests/fakes/program_log.py`：`InMemoryProgramLogStore`，`entries` 按写入顺序记下，`latest` 同数据库实现；测试用 `ProgramLog(InMemoryProgramLogStore(), FakeClock(...))`
- `tests/modules/programlog/`：`test_repo.py` 连数据库（要开着 Docker），覆盖 program-log spec「程序日志的条目」「只追加」「写入失败」的场景，同时写入用两个线程；`test_service.py` 用内存存储，时间来自时钟
- 流程和启动的测试：原来断言 `caplog` 里动作的地方，改为断言内存存储里的条目（行为者、动作、对象、引用，说明里有关键的值）；技术问题的 `caplog` 断言不变。构造 `TradeServices`、`SessionPatrol`、`HealthMonitor`、`NotificationService` 的 builder 加一个程序日志
- `tests/bootstrap/`：数据库是真的，程序日志写进 `programlog_entries`，用 `latest` 或直接查表断言；表的清单加上这张表
- 每个测试的说明写明对应的 spec 场景

### D11 人怎样查

在部署机器上：

```
cd /opt/tradingagentspro/app/deploy
sudo docker compose exec postgres psql --username=tradingagentspro --dbname=tradingagentspro
```

```sql
-- 最近 50 条
select id, recorded_at, actor, action, subject, detail
from programlog_entries order by id desc limit 50;

-- 一个方案的全部条目
select id, recorded_at, actor, action, subject, detail
from programlog_entries
where subject = '<方案 ID>' or '<方案 ID>' = any(inputs) or '<方案 ID>' = any(outputs)
order by id;

-- 一只股票一天里的条目（止损单的对象以股票代码开头）
select id, recorded_at, actor, action, subject, detail
from programlog_entries
where recorded_at >= '2026-10-14T13:30Z' and recorded_at < '2026-10-15T13:30Z'
  and (subject = 'AAA' or subject like 'AAA-STOP-%' or detail like '%AAA%')
order by id;
```

这几条查询也写进 `deploy/README.md` 的「查看程序日志」。

## Risks / Trade-offs

- [下单流程的一步写条目时数据库出错] → 这一步抛错，DBOS 把这个工作流记为出错、不再继续，这个方案的预留留着不释放。和这些 step 里风控、执行模块访问数据库出错是同一种情况，本变更不另外处理（不兜底）；请求确认、确认、等开盘这几步原来不碰数据库，现在会碰。数据库和程序在同一台机器上，少见；出现时 DBOS 的 ERROR 日志里有方案 ID
- [告警的条目和它报告的事件内容重复] → 告警是单独的动作：记下人在什么时候被告诉了什么、有没有送到，审计要它
- [journald 里看不到动作了] → 动作用 SQL 查（D11）；journald 只剩启动、停止和问题，更容易看出故障
- [人的按下到生效有时间差] → 紧急退出最多一轮（15 秒）；条目的时间是生效的时间，按下的时间在适配器的 Python 日志里
- [条目越来越多] → 估计每天几百条，只追加不删除；M5 加模型调用以后再看索引和体积

## Migration Plan

- 迁移 v0004 建表和索引，启动时自动升级（bootstrap spec「数据库」）；已有的数据库多一张空表，部署以后第一次启动写启动，没有崩溃恢复（程序日志里还没有启动和停止）
- 按 `deploy/README.md`「更新」部署；带新迁移，更新前照常手动备份
- 回退：回到上一个提交，用更新前的备份恢复状态库（deployment spec「更新」）
