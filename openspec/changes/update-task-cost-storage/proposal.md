# Proposal

## Why

Agent 网关的任务派发和调用预算在 M3 只写了规则和存储接口：Task、Result 和每次模型调用的费用只存在测试的内存实现里，进程一重启就没了，派发的检查要从头算，预算会重新给一整天的额度。两个用例的说明还写着"先读后写，做法 M4 定"。「替身」「案件工作流」要让案件真正跑起来、几个案件同时运行，在那之前网关要把这些记录存进数据库，并且同时调用时结果仍然正确。

本变更的目的只有一个：**Agent 网关把 Task、Result 和模型调用的费用存进状态库所在的数据库，几个案件和并行的子 agent 同时调用时仍然正确，并把每个 Task 和 Result 写进程序日志。**

引用的 Proposal（完整版）章节：

- 「Task / Result 通信」：Task 和 Result 的字段；一个 Task 只有一个 Result，先到的为准，Result 用 task_id 标识；每个 Task 和 Result 都写入共享状态库和程序日志
- 「调度器与运行模块」运行约束里调用预算一段：Agent 网关记账；预算只在案件开始时看，已经开始的案件照常跑完，预算可以超出一点
- 「共享状态库」：Task 的状态由 Agent 网关写入，子 agent 的 Result 经 Agent 网关写入；写入带时间戳，只追加不覆盖，每类字段只有一个写入者
- 「记录、复盘与经验 · 程序日志」Agent 网关一行：每个 Task 和 Result、被拒绝的任务及原因

已经定了的：

- add-task-dispatch、add-call-budget 留给 M4 的：`TaskStore`、`CostStore` 的数据库实现，表放在网关模块；派发的检查、Result 只写一次、记到哪个预算的并发做法
- M4 存储组开始时定的：表名以 `gateway_` 开头，迁移 v0007（v0004 是程序日志，v0005、v0006 是股票池存储、跟进存储）；一个 Task 只有一个 Result 由数据库保证；记到哪个预算不串行，靠"预算可以超出一点"；网关自己写 Agent 网关的程序日志条目；还没有运行中的调用方，不装配

## What Changes

- Agent 网关模块加 `repo.py`：任务表 `gateway_tasks`、Result 表 `gateway_results`、费用表 `gateway_costs`，以及 `TaskStore`、`CostStore` 的数据库实现；迁移 v0007 建这三张表。都只追加：Task 生成时一行，一个 Task 最多一行 Result，每次尝试一行费用，写入以后不改不删
- 一个 Task 只有一个 Result 由 Result 表的主键保证：两个调用同时为一个任务生成 Result（比如截止时间那一刻同时收到输出和记超时）时只写入先写入的那个，两边都给出它。任务存储的"写入 Result"改为：已经有 Result 时不写，给出已有的
- 派发的检查（复用、轮次、任务数）不加锁：同一个案件的派发由调用方依次进行（一个案件一次只在一个阶段，PM 和 Critic 不同时派发）；生成 Result 和派发同时进行不影响检查；不同案件互不影响
- 记到哪个预算不加锁：同时记下的几条各按自己读到的预算情况，可能都记普通预算，普通预算超出一点
- 程序日志加行为者 Agent 网关和三个动作：派发任务、拒绝任务、生成 Result。由任务派发的用例写；同时生成的两个 Result 只有真正写入的那次调用写条目，所以每个 Result 正好一条
- 两个用例和两个端口的说明写明上面的做法

## Non-goals

- 装配：bootstrap 不建网关的用例和存储，现在没有运行中的调用方；「替身」「案件工作流」接上时装配。三张表随启动时的迁移建好，一直是空的
- 调用模型和它的程序日志（每次模型调用的输入和输出、token、重试、切换备用模型、PM 结论的格式校验、查询历史和经验）：M5。费用记录本身不另写程序日志条目，见「需要人决定」第 2 条
- 预算用完时的告警和程序日志、健康监控怎样读持仓保留预算：「排队运行」
- 状态库条目 ID 的格式：「状态库快照」决定；Task 的输入和 Result 引用的条目先按文字原样存
- PM 的结论和方案、Critic 的质询和 PM 的回应、PM 的整段对话：「共享状态库」把它们也列为经 Agent 网关写入的结果，存在哪里由「案件工作流」「案件记录」和 M5 决定
- 派发以后进程崩溃、DBOS 重新执行时的重复派发：「案件工作流」按 DBOS 的步骤处理
- 备用模型的熔断状态和案件内的起点（放在内存里）在并行调用下的正确性：M5 并行调用模型时处理
- 派发、记账的规则：domain 不改，task-dispatch、call-budget 原有的场景不变
- 数据库层面禁止修改和删除（触发器、权限）：和程序日志一样，程序只有追加和读取
- 省略的少见情况：见 design 的省略清单

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `task-dispatch`：新增「任务的存储」（存在数据库里、原样读回、重启以后接着算、读写出错、同时调用）；修改「一个任务只有一个 Result」（同时生成的只写入先写入的那个）
- `call-budget`：新增「费用的存储」（原样读回、读写出错、同时记下都写入、记到哪个预算不互相等待）
- `state-store`：修改「表的归属和迁移」，加网关的三张表
- `program-log`：修改「程序日志的条目」（行为者加 Agent 网关，案件 ID、耗时、费用按动作的要求填）；新增「Agent 网关的动作」

## Impact

- **代码**：
  - `modules/gateway/`：新增 `repo.py`（三张表、`PostgresTaskStore`、`PostgresCostStore`）和 `domain/entries.py`（三种条目的说明）；`ports.py` 的 `TaskStore.add_result` 改为给出已有的 Result；`service.py` 的 `TaskDispatchService` 构造参数加 `ProgramLog`、写条目，两个用例的说明写明并发的做法；`__init__.py` 导出两个数据库实现
  - `modules/programlog/domain/entry.py`：加 `Actor.GATEWAY` 和三个 `Action`
  - 迁移 `bootstrap/migrations/versions/v0007_create_gateway_tables.py`
  - 别的模块、workflows、bootstrap 的装配、contracts、shared 不改
- **测试**：`tests/modules/gateway/test_repo.py`（新，要开着 OrbStack）；`tests/fakes/tasks.py` 的写入 Result 跟着端口改；网关的 service、状态机测试和 builders 加程序日志；`tests/bootstrap/test_database.py`、`test_app.py` 的表的清单和最新迁移版本
- **依赖**：无
- **数据库**：三张新表、一个迁移；本变更以后还没有写入方
- **对后续变更的影响**：「替身」「案件工作流」用 `PostgresTaskStore`、`PostgresCostStore` 装配网关的用例，并保证同一个案件的派发依次调用；M5 让同一次模型回复里的多个 `dispatch_tasks` 依次执行

## 需要人决定

1. **Task、Result 和费用记录不用版本表（小问题）**：「共享状态库」要求写入带时间戳和版本号，案件里产生的条目带案件 ID。这三种记录写入以后不变，版本号永远是 1；Result 自己没有时刻和案件 ID 两项。
   推荐做法：一行一条，不存版本号；Task 的时刻是派发时刻，Result 的时刻是派发时刻加耗时，Result 的案件 ID 取它的 Task 的（同一个 task_id）；费用记录自带记下的时刻和案件 ID。完整版不改。
2. **「调用预算消耗」怎样记（小问题，不影响本变更的代码）**：程序日志里系统一行有"调用预算消耗"，没说是每次调用一条还是预算用完时一条；add-call-budget 的 design 曾预计 M4 为每条费用记录写一条。M4 的替身不调用模型，没有费用记录。
   推荐做法：每次调用的 token、费用和记到哪个预算写在 M5 的模型调用条目里（行为者 Agent 网关），不为费用记录另写一条；系统的"调用预算消耗"只在一个预算当天用完时写一条，由读预算情况的「排队运行」加。
