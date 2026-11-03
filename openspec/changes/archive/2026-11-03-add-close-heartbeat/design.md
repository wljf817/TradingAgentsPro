# Design

## Context

现状：

- 收盘检查的规则是纯函数：`modules/heartbeat` 的 `close_check(snapshot, calendar, config)`，输入 `CloseSnapshot`（今天、持仓和关注的 `ThesisDates`、候选的 `PoolCandidate`、上一次寻股的那一天、在排队或运行的 `CheckItem`），输出 `CheckItem` 的元组；没有调用方
- 收盘心跳要用的读写都已经有了，也都没有调用方：股票池的 `expire_candidates()`、`PoolEntry.candidate_since`；案件记录的 `last_concluded(case_type, symbol)`；公司数据的 `refresh_earnings(symbol)`（一只股票 2 个请求，取不到抛 `CompanyDataUnavailableError`，Finnhub 适配器不为限频等待）
- 排队运行（`workflows/queue_runner.py`）的 `QueueRunner.enqueue(trigger, triggered_at)` 可以从任何线程调用，存下以后写程序日志的排队；`QueueService.queued()` 给出排队的案件（带触发）；`case_workflow.running_cases()` 给出运行中的案件和 `CaseProgress`（案件类型、股票），不带触发
- 调度器的 `HeartbeatTrigger(case_type, symbol)` 对应 `CheckItem`；定时心跳开出的七种案件类型是 `domain/case_type.py` 里私有的 `_HEARTBEAT_TYPES`，只有 `HeartbeatTrigger` 开得出它们；`case_entries.case_trigger` 已经给出定时心跳的触发原因（"CCC 的持仓复查日到期"）
- 收盘对账由盘中巡检每一轮判断到期（执行模块 `domain/close.py` 的 `close_day`），状态库里的记录保证一天一次；下单流程和案件工作流用 `install(...)` 在 DBOS launch 以前交进部件，用工作流 ID 保证同一个方案、同一个案件只开始一次；下单流程的持久化等待由 `install` 交进来（生产 `wait_decision`，测试换成拨钟的替身）
- 程序日志的动作是字符串列，加动作不用迁移；迁移在 v0014

动机见 proposal.md，要求见 specs/。

### 模块边界

```
modules/heartbeat/
  domain/close_check.py   (改) heartbeat_day(calendar, now, delay)：到期的交易日
  __init__.py             (改) 导出 heartbeat_day
modules/scheduler/
  domain/case_type.py     (改) _HEARTBEAT_TYPES 改为公开的 HEARTBEAT_CASE_TYPES
  __init__.py             (改) 导出 HEARTBEAT_CASE_TYPES
modules/programlog/
  domain/entry.py         (改) Action.CLOSE_CHECK
workflows/
  close_heartbeat.py      (新) CloseHeartbeatConfig、HeartbeatParts、install、close_heartbeat 工作流、
                          CloseHeartbeat（判断的线程）、HeartbeatRuns 和 DbosHeartbeatRuns
bootstrap/app.py          (改) 装配、DBOS launch 以前 install、启动和停止
```

模块之间传递的：

- 股票池 → 收盘心跳：`PoolEntry`（状态、两种论点的复查日和催化剂、评分、过期时间、成为候选的时刻）
- 案件记录 → 收盘心跳：`CaseRecord`（`conclusion.at` 是结论时刻）
- 调度器 → 收盘心跳：`QueuedCase`（`trigger` 是 `HeartbeatTrigger` 的算心跳案件）、`HEARTBEAT_CASE_TYPES`；收盘心跳 → 调度器：`HeartbeatTrigger`
- 案件工作流 → 收盘心跳：`CaseProgress`（`case_type`、`symbol`）
- 收盘心跳 → 定时心跳模块：`CloseSnapshot`，取回 `CheckItem`。这几个是定时心跳模块的 frozen dataclass，不是 Pydantic 模型；本次唯一新的 Pydantic 模型是 `CloseHeartbeatConfig`

## Goals / Non-Goals

**Goals:** 收盘心跳每个交易日只做一次、中断以后接着做完；不拖慢排队运行的调度器心跳；刷新财报留在 Finnhub 的限额以内。

**Non-Goals:** 不改收盘检查的规则（close-check spec）；不给收盘心跳加健康检查；不改排队运行的一轮。

## Decisions

### D1 判断在自己的线程，一天的收盘心跳是一个 DBOS 工作流

`CloseHeartbeat` 开守护线程 `close-heartbeat`，开始以后立即判断，之后每隔间隔判断一次：`heartbeat_day` 给出到期的交易日 D，`HeartbeatRuns.started(D)` 为假时 `start(D)`。生产的 `DbosHeartbeatRuns` 用工作流 ID `close-heartbeat-<D 的 ISO 日期>`：`started` 是 `DBOS.get_workflow_status(id) is not None`，`start` 在 `SetWorkflowID(id)` 里 `DBOS.start_workflow(close_heartbeat, D)`，不等它做完。

- 同一天只做一次：DBOS 对同一个工作流 ID 只开始一次，判断和开始之间被别的线程抢先也不会做两次；做完、正在做、出错停下的状态都在 DBOS 的表里，不另建表
- 中断以后接着做：DBOS launch 时恢复 PENDING 的工作流，已经做完的步骤按记下的输出重放，不再做
- 简版技术栈「定时任务 → DBOS」

考虑过的：

- DBOS 的 `@DBOS.scheduled` cron：按日历时间触发，跟不上休市日和半日市；每分钟一次就是每天上千个工作流记录
- 放进排队运行的一轮：刷新 30 只股票的财报要一分半，这一轮做完以前调度器心跳不动，超过 300 秒就进入降级模式
- 自己建一张表记下做过的交易日：要加端口、repo、迁移和替身，中断以后还是要自己接着做；DBOS 两件事都给了

`heartbeat_day` 放在定时心跳模块的 domain，和执行模块的 `close_day` 是同一张表（盘中不做，D 收盘加延迟以后做），只是不看做过没有。执行模块的 domain 不在定时心跳模块能 import 的范围里，这个函数只有四行，各写各的。

### D2 工作流的步骤

```python
@DBOS.workflow(name="close_heartbeat")
def close_heartbeat(day: date) -> None:
    _expire()  # step：股票池移出到期的候选
    symbols = _pool_symbols()  # step：股票池里的股票，按代码
    failures: list[tuple[str, str]] = []
    for i, symbol in enumerate(symbols):
        if i:
            parts.wait(refresh_gap)  # install 交进来的持久化等待，生产是 DBOS.sleep
        reason = _refresh(symbol)  # step：取不到时写 WARNING，交回原因
        if reason is not None:
            failures.append((symbol, reason))
    _check(day, len(symbols), failures)  # step：组快照、收盘检查、排队、写收盘检查
```

- 每只股票一个 step：中断以后已经刷新过的按记下的输出重放，不再请求 Finnhub；取不到的原因是 step 的输出，重放以后照样写进收盘检查
- 收盘检查、排队和写程序日志是一个 step：这个 step 中途中断、恢复以后重做时，已经排进的检查项在队列里，收盘检查按「在排队或运行的不重复开」不再给出它们（候选评估的上限也算上它们），所以不会重复排；收盘检查的条目在最后写，只写一次
- 收盘检查的时刻在 `_check` 开始时从时钟读：刷新财报以后的时刻
- 工作流出错：工作流函数外面包一层，写 `_log.exception("收盘心跳 %s 出错", day)` 再原样抛出，DBOS 把它记为出错；这一天 `started` 为真，不再做（spec「同一天只做一次」）
- 等待用 `install` 交进来的 `wait`，照下单流程的写法：测试换成拨钟的替身，spec 场景里的 20:15:21Z 能在测试里验证

### D3 组出快照

`_check` 里按 spec「收盘检查的快照」读：

- `pool.entries()` 只读一次，持仓、关注、候选都从这一次读到的分出来；持仓用 `investment_thesis`、关注用 `watch_thesis` 的 `review_day` 和 `catalyst.deadline`（股票池的不变量保证它们有论点，不判空）
- 新候选：`candidate_since` 不为 None，并且 `records.last_concluded(CANDIDATE_EVALUATION, symbol)` 是 None 或者它的 `conclusion.at < candidate_since`。最多 30 只候选，一只一次查询
- 上一次寻股的那一天：`records.last_concluded(EXPLORATION, None)` 的 `conclusion.at` 用 `calendar.session_closing_after(...).day`
- 在排队或运行的心跳案件：`queue.queued()` 里 `trigger` 是 `HeartbeatTrigger` 的，`CheckItem(trigger.case_type, trigger.symbol)`；`running()` 里 `case_type in HEARTBEAT_CASE_TYPES` 的，`CheckItem(progress.case_type, progress.symbol)`。运行中的案件不带触发，按案件类型认：这七种只有定时心跳开得出（`define_case`），所以把调度器的集合公开出来用，不在收盘心跳里再写一份

### D4 排队和收盘检查的条目

- `HeartbeatParts.intake` 是只有 `enqueue(trigger, triggered_at) -> UUID` 的协议，生产是 `QueueRunner`；排队的程序日志由它写
- 收盘检查的条目：`Actor.SCHEDULER`、`Action.CLOSE_CHECK`（`"close_check"`），对象是 D 的 ISO 日期，输出的引用是排进的案件 ID；说明的检查项用 `case_trigger(trigger, at).reason`，和排队的条目写法相同。说明的写法放在 `close_heartbeat.py` 里

### D5 配置

`CloseHeartbeatConfig`（`workflows/close_heartbeat.py`，照 `EventDetectionConfig` 的写法）：`interval` 60 秒、`delay` 15 分钟、`refresh_gap` 3 秒；`interval`、`refresh_gap` `gt=timedelta(0)`，`delay` `ge=timedelta(0)`。收盘检查的阈值仍是 `HeartbeatConfig`，和案件工作流用同一份。

### D6 装配

- `HeartbeatParts`：pool、company_data、records、queue（`QueueService`）、running（`case_workflow.running_cases`）、intake（`QueueRunner`）、log、clock、calendar、heartbeat（`HeartbeatConfig`）、config（`CloseHeartbeatConfig`）
- DBOS launch 以前 `close_heartbeat.install(parts, externals.sleep)`；`Externals` 照下单流程的 `wait` 加一项 `sleep`，生产是 `DBOS.sleep`，测试换成拨钟的
- `Externals` 再加一项 `finnhub`（生产是 `FinnhubSource`），照 add-event-detection 的 design D15 的新闻源和公告源：收盘以后启动的测试里收盘心跳真的刷新财报，要换成替身，不能连 Finnhub
- `HeartbeatConfig` 由案件那一侧建一份，案件工作流的校验和收盘心跳共用；`QueueService` 建一个，排队运行和收盘心跳共用
- 事件检测之后开始 `CloseHeartbeat`，写 INFO「收盘心跳已开始，间隔 60 秒」；停止时盘中巡检之后、事件检测之前 `stop()`，写「已关闭收盘心跳」
- 线程判断出错写 `_log.exception`，下一次照常（spec「什么时候做」）

## Risks / Trade-offs

- [恢复的收盘心跳不看时刻] 程序在收盘心跳中途停下、下一个交易日开盘以后才启动，DBOS 会在盘中把前一天的检查项排进来 → 罕见；排进来的案件照队列的规则运行，和盘中排进的事件案件一样
- [出错停下的那天不再做] 数据库在收盘心跳中途出错，这一天的候选评估、寻股和每周复盘都没开 → 没续期的日期下一次收盘照样到期，候选评估晚一天；每周复盘少一次
- [Finnhub 限额] 刷新间隔 3 秒按每分钟 40 个请求算；寻股案件查板块也用 Finnhub，排在刷新完之后才开始 → 刷新期间没有别的 Finnhub 请求
- [第 3 步重做] `_check` 中途中断、恢复之前排队运行已经启动并跑完了刚排进的每周复盘（替身几秒就做完），重做时会再排一个 → 要在这几秒里崩溃，忽略

## Migration Plan

- 没有迁移，没有新的环境变量
- 部署以后主机的股票池是空的：每个交易日收盘都会排寻股（冷却期内不排），替身加进粗筛的前几只候选，下一次收盘开候选评估，替身升级为关注；之后关注股的入场条件满足时，替身的开仓方案在 Discord 请求确认
- 每个交易日收盘多 2 × 股票池只数个 Finnhub 请求
- 回滚：停掉新版本即可；DBOS 里留下的 `close_heartbeat` 工作流旧版本不认得，没做完的那个不会恢复

## 省略

- 股票加入股票池时立即刷新财报
- 收盘心跳的健康检查
- 错过的收盘心跳补做
- 工作流出错以后同一天重试
