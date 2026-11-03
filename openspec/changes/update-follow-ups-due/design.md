# Design

## Context

现状：

- 跟进审批（`modules/followups`）的 `FollowUpService.trigger_due()` 已经写好：在服务的可重入锁里把触发时间不晚于现在的待执行跟进逐个存为已触发、写程序日志的触发跟进，再返回它们；没有调用方
- 存储 `PostgresFollowUpStore` 用 `VersionedStore`，端口 `FollowUpStore` 只有 `pending`、`requested_since`、`save`；`VersionedStore.get(key)` 已经能按键读最新版本
- 调度器的 `FollowUpTrigger(symbol, question)` 已经能排队、能确定案件类型（持仓跟进、关注跟进），`case_entries.case_trigger` 给出触发原因"CCC 的跟进：问题"；队列表 `scheduler_queue` 把触发存成带 `kind` 的 JSON，读回时用 `TypeAdapter` 校验
- 排队运行（`workflows/queue_runner.py`）每 5 秒一轮，`QueueRunner.enqueue(trigger, triggered_at)` 存下排队的案件、写程序日志的排队，交回案件 ID，不拿锁；一轮出错写 ERROR、心跳不变
- 案件工作流的 `CaseFlow.context` 组出 `CaseContext`；事件案件按触发里的事件 ID 读事件，读不到时 `assert`；`CaseRecordService.case(case_id)` 没有时抛 `KeyError`，`CaseRecord.conclusion` 是 `CaseConclusion | None`
- 生产库里没有种类是跟进的排队记录：`trigger_due` 从来没有被调用过

动机见 proposal.md，要求见 specs/。

### 模块边界

```
contracts/
  agent_call.py           (改) FollowUpOrigin；CaseContext 加 follow_up
modules/followups/
  ports.py                (改) FollowUpStore.get
  repo.py                 (改) PostgresFollowUpStore.get
  service.py              (改) trigger_due(enqueue)、follow_up(follow_up_id)
  domain/entries.py       (不改) 触发跟进的说明照旧是跟进的内容
modules/scheduler/
  domain/case_type.py     (改) FollowUpTrigger 加 follow_up_id
workflows/
  queue_runner.py         (改) QueueParts.follow_ups；一轮开头到点触发
  case_flow.py            (改) 跟进案件的上下文
  case_entries.py         (改) 跟进的触发原因带跟进 ID
bootstrap/app.py          (改) 排队运行的部件加上跟进审批
tests/fakes/              (改) 跟进存储的内存实现加 get
```

模块之间传递的：

- 跟进审批 → 排队运行：`FollowUp`（`follow_up_id`、`symbol`、`request`）交给排队运行给的 `enqueue`，排队运行交回案件 ID（`UUID`）
- 排队运行 → 调度器：`FollowUpTrigger(symbol, question, follow_up_id)`，触发时刻是 `request.trigger_at`
- 跟进审批 → 案件工作流：`FollowUp`（`request`、来源案件 `case_id`）；案件记录 → 案件工作流：`CaseRecord.conclusion.conclusion`
- 案件工作流 → PM 和 Critic：`CaseContext.follow_up` 是新的 Pydantic 模型 `FollowUpOrigin(request: FollowUpRequest, conclusion: Conclusion)`，两个字段都是已有的契约

## Goals / Non-Goals

**Goals:** 到点的跟进不晚于一个排队运行的间隔排进队列；跟进不会因为排进出错而丢；跟进案件的 PM 看得到申请和来源案件的结论。

**Non-Goals:** 不改跟进审批的规则；不加线程、配置和健康检查；不处理排进以后、记为已触发以前进程崩溃造成的重复。

## Decisions

### D1 到点触发放进排队运行的一轮

`QueueRunner.run_round` 的第一件事是 `parts.follow_ups.trigger_due(self._enqueue_follow_up)`，然后照旧组快照。同一轮里新排进的跟进已经在 `queue.queued()` 里，当轮就能启动。

- 跟进归调度器审批（完整版「3. 自定义跟进」），到点触发的程序日志行为者也是调度器，放在调度器的一轮里最顺
- 不加线程：启动顺序、停止顺序、配置、健康检查都不用改；排队运行的心跳本来就覆盖这一步，到点触发一直出错时心跳停住，超时以后进入降级模式，问题会被看见
- 跟进最多晚 5 秒，比事件检测和收盘心跳的 60 秒间隔还短

考虑过的：

- 自己的线程（收盘心跳的写法）：多一组启动、停止、配置和测试，没有换来什么；收盘心跳要单独的线程是因为刷新财报要一分半，到点触发只是几次数据库读写
- 每个跟进一个休眠到触发时间的 DBOS 工作流（完整版原来的写法）：要在案件工作流的结论检查里开工作流，被替换、作废的跟进醒来以后还要再判断；跟进本来就存在状态库里，重启以后照样读得到，不需要靠工作流记住
- 放进事件检测的一轮：事件检测是另一个触发源，混在一起以后它的一轮出错也会拖住跟进

### D2 先排进、再记为已触发，都在跟进审批的锁里

```python
def trigger_due(self, enqueue: Callable[[FollowUp], UUID]) -> tuple[FollowUp, ...]:
    with self._lock:
        triggered: list[FollowUp] = []
        for follow_up in due(self._store.pending(), self._clock.now()):
            case_id = enqueue(follow_up)
            done = follow_up.triggered()
            self._store.save(done)
            self._program_log.write(
                Actor.SCHEDULER,
                Action.TRIGGER_FOLLOW_UP,
                str(done.follow_up_id),
                content(done.symbol, done.request),
                outputs=[str(case_id)],
            )
            triggered.append(done)
        return tuple(triggered)
```

- `enqueue` 出错时原样抛出：这一个没有存为已触发，它和后面的都还是待执行，下一轮再排；前面的已经排进、已经存下
- 放在锁里：取出、排进、存下之间不会被审批（替换）或状态变化（作废）插进来，`same_reason` 的替换和 `voids_follow_ups` 的作废只会看到排进以前或存下以后的状态
- 拿着锁时只多做了 `enqueue`：它只写排队表和程序日志，不拿锁，所以「拿着锁时不拿别的锁」仍然成立，不会死锁
- 照旧返回这次记为已触发的跟进；服务的类说明和模块说明里"到点触发"的描述改成先排进、再记为已触发

考虑过的：

- 保持先存为已触发、再由调用方排进：排进出错时这一批全丢，只留一条 ERROR
- 拆成不拿锁的 `due()` 和 `triggered(id)`：两步之间审批可能替换、状态变化可能作废这个跟进，已经排进的案件就对不上记录了
- 排队记录里查"排过的跟进 ID"去重（卖出成交的写法）：只为了排进以后、存下以前进程崩溃这一种情况，见「风险」

### D3 跟进的触发带跟进 ID，触发时刻是触发时间

`FollowUpTrigger` 加第三个字段 `follow_up_id: UUID`，放在最后：现有的 `case FollowUpTrigger(symbol, question)` 模式按位置匹配前两个，仍然成立。队列表照旧存 JSON，`TypeAdapter` 把 UUID 存成字符串；生产库里没有旧的跟进记录，不用迁移。

`_enqueue_follow_up(follow_up)` 排进 `FollowUpTrigger(follow_up.symbol, follow_up.request.question, follow_up.follow_up_id)`，触发时刻是 `follow_up.request.trigger_at`：这是 PM 想要它开始的时刻，错过以后再排进时也不变，案件记录和排队日志看得出晚了多少（入队时刻是实际排进的时刻）。

触发原因改为"CCC 的跟进（<跟进 ID>）：问题"，和事件带事件 ID、卖出成交带客户端订单 ID 的写法一样，从案件记录就能找到跟进。

### D4 读取一个跟进

`FollowUpStore.get(follow_up_id) -> FollowUp | None` 由 `VersionedStore.get` 实现；`FollowUpService.follow_up(follow_up_id) -> FollowUp` 在锁里读，没有时抛 `KeyError`，和 `CaseRecordService.case` 一样。不写程序日志。

### D5 跟进案件的上下文

`contracts/agent_call.py`：

```python
class FollowUpOrigin(BaseModel):
    """跟进案件开出它的跟进：获批的申请和提出申请的案件的结论。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: FollowUpRequest
    conclusion: Conclusion


class CaseContext(BaseModel):
    ...
    follow_up: FollowUpOrigin | None  # 跟进案件的跟进
```

`follow_up` 和 `event` 一样没有默认值：每个组出上下文的地方都要明确给出。`CaseFlow.context` 在触发是 `FollowUpTrigger` 时读 `follow_ups.follow_up(trigger.follow_up_id)`，再读 `records.case(follow_up.case_id)`，`assert record.conclusion is not None`（跟进是来源案件的结论通过检查时获批的），给出 `FollowUpOrigin(follow_up.request, record.conclusion.conclusion)`。读不到跟进时 `KeyError` 原样抛出，和事件读不到时一样让这一步报错。

案件工作流的 `CaseServices` 已经有 `follow_ups` 和 `records`，不加部件。替身的 PM 不读上下文，不用改；`tests/integrations/standin/snapshots.py` 和 `tests/modules/gateway/test_agent_calls.py` 组的 `CaseContext` 加 `follow_up=None`。

### D6 装配

`QueueParts` 加 `follow_ups: FollowUpService`，`bootstrap/app.py` 把已经装配好的跟进审批交给排队运行。启动、停止的顺序不变。

### 省略的情况

- 排进以后、存为已触发以前进程崩溃：下一轮再排进一次，这个跟进开两个案件
- 跟进的触发时间在来源案件记下结论以前就到了：触发时间必须在审批之后，结论在审批后几毫秒内记下，上下文那一步会报错
- 队列表里旧格式的跟进触发：生产库里没有

## Risks / Trade-offs

- [到点触发一直出错时整个队列停住] → 和这一轮别的步骤出错一样：每轮写 ERROR，调度器心跳超时以后进入降级模式并告警；比悄悄丢掉跟进更容易发现
- [跟进最晚 5 秒才排进] → 跟进的问题都是"几小时后""会后"这一类，秒级的延迟没有影响
- [拿着跟进审批的锁时写排队表] → 排队只写两张表，不拿锁；审批、状态变化最多多等这几毫秒
