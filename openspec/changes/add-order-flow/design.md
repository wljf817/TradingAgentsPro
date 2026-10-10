# Design

## Context

现状：

- `bootstrap/order_flow.py` 的 `AccountPlanIntake.held_sector` 已经查执行模块的账户数据，`submit` 和 `refuse_decision` 一律抛 `OrderFlowMissingError`。bootstrap 在 Discord 之后构造 DBOS 并 launch，没有登记任何工作流
- 「盘中巡检」（`add-session-patrol`，先于本变更 apply）加了全程序一把 `RLock`（`TradingApp.lock`）、执行模块的只读查询 `order(plan_id)`，并在每一轮跟进全部未结束的订单、上报成交、订单终态时释放预留
- 用例：`RiskService.check`（已有预留的方案返回第一次的结果）、`recheck`（盘外抛 `OutsideSessionError`，拒绝时自己释放）、`release`；`ExecutionService.submit`（已经有订单时只跟进一次；新建前依次可能抛 `NotReconciledError`、`ReconcileMismatchError`、`OutsideSessionError`、`OrderPausedError`、`UnprotectedPositionError`；券商超时、断线记为状态未知，不抛）、`account()`（读券商失败抛 `BrokerUnavailableError`）；`EmergencyService.breaker_tripped()`、`HealthService.status.degraded` 是读内存；`MarketDataService.quotes`（失败抛 `QuoteUnavailableError`；盘前、开盘后还没成交时 `last_price` 是 None）
- 通知：`NotificationService.request_confirmation` 发不出去时抛 `NotificationUnavailableError`，`alert`、`finish_confirmation` 发不出去只写日志。`DiscordNotifier` 按 custom_id 里的截止时刻在本地拒绝截止以后的按钮；回调在工作线程里调用、抛错时提示没有送达；`finish_confirmation` 报告的结果覆盖按钮显示的结果，回调运行期间报告的，回调返回以后不再改消息
- DBOS 3.2.0（读过源码）：
  - `@DBOS.workflow`、`@DBOS.step` 在 import 时按名字登记到进程内的注册表；launch 时恢复本执行器上 `PENDING` 的、应用版本相同的工作流。默认的应用版本是全部工作流源码的哈希：改了工作流的代码，旧的进行中的工作流就不再恢复。配置 `enable_patching: True` 把版本固定为一个常量，并可以用 `DBOS.patch` 兼容改过的步骤
  - `DBOS.recv(topic, timeout_seconds)` 和 `DBOS.sleep(seconds)` 是持久化的：第一次执行时记下醒来的时刻，恢复时只等剩下的时间；已经记下结果的 step、recv、sleep 恢复时直接返回记录
  - `DBOS.send` 发给不存在的工作流抛错；发给存在的工作流时消息存进库里，直到某次 `recv` 取走
  - `DBOS.set_event` 记下工作流的一个键值；`DBOS.get_event(id, key, timeout_seconds=0)` 立即返回当前值，没有时为 None
  - `SetWorkflowID` 里 `start_workflow` 一个已经存在的 ID，返回已有的工作流，不另起
  - 输入、输出、step 的结果和事件默认用 pickle 存
  - 工作流在 DBOS 自己的 `ThreadPoolExecutor` 线程里运行，线程不是守护线程。`DBOS.destroy()` 不等它们；但解释器退出时会 join 这些线程，一个在 `DBOS.sleep` 或 `recv` 里等的工作流会让进程等到它醒来（用一个 `time.sleep` 的任务验证过）。部署的 systemd 单元 `TimeoutStopSec=90`，过了就 SIGKILL

要求见 specs/order-flow/spec.md 和 specs/bootstrap/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
workflows/
  account_snapshot.py   (新) 风控用的账户数据：account_snapshot（纯函数）、read_snapshot
  order_flow.py         (新) TradeServices、OrderFlowConfig、check_plan、OrderFlow（每一步的用例）、
                             step 的结果类型、告警文字。不 import dbos
  order_workflow.py     (新) DBOS 工作流 order_plan 和它的 step、install、OrderPlanIntake、
                             deliver_decision、NotAwaitingConfirmationError

bootstrap/app.py        (改) 装配 TradeServices、OrderPlanIntake、deliver_decision、OrderFlow；
                             launch 之前 install；DBOS 配置加 enable_patching
bootstrap/order_flow.py (删) 占位的提交接口和回调
bootstrap/cli.py        (改) 加 entry：main 返回以后立即结束进程
pyproject.toml          (改) 命令 tradingagentspro 的入口改为 cli:entry
```

- workflows 只 import 模块的包根：`execution`（`ExecutionService`、`Account`、`HeldPosition`、`Order`、`OrderStatus`、`BrokerUnavailableError` 和 submit 会抛的四种错误）、`risk`（`RiskService`）、`emergency`（`EmergencyService`）、`health`（`HealthService`）、`marketdata`（`MarketDataService`、`Quote`、`QuoteUnavailableError`）、`notification`（`NotificationService`、`ConfirmationOutcome`、`NotificationUnavailableError`）；以及 `shared.clock`、`shared.trading_calendar`（`OutsideSessionError`）、`contracts`。不 import integrations、bootstrap。`dbos` 只在 `order_workflow.py` 里
- 跨模块传的模型都已经存在，contracts 不改：
  - 方案 `TradePlan`、风控结果 `RiskDecision`（contracts）：工作流的输入，也是确认消息和提交订单的输入
  - 账户数据 `AccountSnapshot`、`Position`（contracts）：workflows 用执行模块的 `Account`、`HeldPosition` 和行情的 `Quote` 拼成，交给风控
  - 订单 `Order`（execution 导出的 dataclass）：提交的结果
  - `ConfirmationOutcome`、`PendingConfirmation`（notification 导出）：确认的结果和确认消息
- step 的结果（`Requested`、`Timing`、`Attempt`、`PlanEnd`）是 workflows 自己的冻结 dataclass 和 StrEnum，只在 `order_flow.py` 和 `order_workflow.py` 之间传
- 模块不加方法、不改行为，包根的导出、架构测试的白名单都不变。用到的只读查询 `execution.order` 由「盘中巡检」加

## Goals / Non-Goals

**Goals:**

- 每一步是 `OrderFlow` 的一个普通方法：规则用替身直接测，不用 DBOS、不用等时间
- 工作流函数只编排：确定性，只按 step 的结果分支；时间、I/O、日志都在 step 里
- 不改执行模块、风控、通知模块的行为

**Non-Goals:**

按「编程风格」省略的情况：

- 提交接口保存预留以后、启动工作流以前进程崩溃（毫秒级）：预留留下，没有人释放
- 同一个方案重复提交、第二次启动工作流时数据库出错：会把第一次的预留释放掉
- 确认请求超过发送超时、消息后来才发出去：按发不出去处理，那条消息的按钮按了提示没有送达，不去改它
- Discord 启动以后、DBOS 启动以前那几秒收到的 `/plan` 和按钮：启动工作流、读事件失败，回复提交出错或提示没有送达
- 回调读到「在等确认」以后、送达以前流程超时：按超时处理，送去的消息留在 DBOS 里没人取
- 确认消息上的截止时刻和工作流等的时长各自从时钟算，相差几毫秒：以工作流为准
- 熔断和降级读的是内存里的当前值，首次检查和再校验之间可能变化：再校验会再读一次
- 等重试持续很久（比如无保护仓位几小时）时每 30 秒一次，DBOS 的记录随之变多：不退避
- 等重试期间每一次失败各写一条日志：不合并
- 停止时正在做的 step（比如提交订单）随进程结束：等同崩溃，恢复时重做，提交按客户端订单 ID 不会重复
- 改了 `TradePlan`、`RiskDecision` 的字段，进行中的工作流按旧的 pickle 恢复可能出错：改这些模型时等进行中的下单流程结束再部署
- 再校验时传给风控的退出方案最新价是 0：风控不看退出方案的最新价

## Decisions

### D1 提交接口先检查，再以方案 ID 启动工作流

`/plan` 的提交接口要同步拿到风控结果（manual-plans spec「提交接口」），所以检查放在提交接口里，工作流从「请求确认」开始，输入是方案和风控结果：

```python
class OrderPlanIntake:
    """满足 PlanIntake，见 order-flow spec「查持仓的板块」「提交方案」。"""

    def __init__(self, services: TradeServices) -> None:
        self._services = services

    def submit(self, plan: TradePlan) -> RiskDecision:
        decision = check_plan(self._services, plan)
        if decision.outcome is RiskOutcome.REJECTED:
            return decision
        try:
            with SetWorkflowID(str(plan.plan_id)):
                DBOS.start_workflow(order_plan, plan, decision)
        except Exception:
            with self._services.lock:
                self._services.risk.release(plan.plan_id)
            raise
        return decision
```

- `check_plan`（`order_flow.py`）：`read_snapshot` 读账户数据和行情（不拿锁），拿锁 `risk.check`，熔断取 `emergency.breaker_tripped()`、降级取 `health.status.degraded`，写结果的 INFO
- 方案 ID 是工作流 ID：同一个方案再次提交，`risk.check` 返回第一次的结果，`start_workflow` 返回已有的工作流，不会下两次单
- 启动失败时释放预留再抛出：没有工作流，就没有人释放它。保存预留和启动之间进程崩溃的毫秒级窗口不兜底（省略清单）
- `held_sector` 照旧读 `execution.account()`，从 `bootstrap/order_flow.py` 搬过来

考虑过：

- 检查也放进工作流（第一个 step），提交接口用 `get_event` 等结果：崩溃窗口没了，但提交接口要另设一个等结果的超时，结果和错误都要经事件传回来；M4 的案件工作流里风控检查是案件自己的 step，之后启动的正是这个下单流程，现在的分法和它一致

### D2 一个方案一个工作流，只按 step 的结果分支

```python
@DBOS.workflow(name="order_plan")
def order_plan(plan: TradePlan, decision: RiskDecision) -> PlanEnd:
    """一个方案的下单流程，见 order-flow spec。只按 step 的结果分支，不读时间、不写日志。"""
    DBOS.set_event(CONFIRMATION, Stage.WAITING)
    requested = _request_confirmation(plan, decision)
    if requested is None:
        DBOS.set_event(CONFIRMATION, Stage.UNSENT)
        _settle(plan, Stage.UNSENT, None)
        return PlanEnd.UNSENT
    choice = DBOS.recv(DECISION_TOPIC, timeout_seconds=requested.wait_seconds)
    stage = Stage.TIMED_OUT if choice is None else Stage(choice)
    DBOS.set_event(CONFIRMATION, stage)
    _settle(plan, stage, requested.message_id)
    if stage is not Stage.CONFIRMED:
        return PlanEnd(stage)
    return _execute(plan, decision)


def _execute(plan: TradePlan, decision: RiskDecision) -> PlanEnd:
    """确认以后：等开盘、再校验、提交，直到订单交给巡检或者方案作废。"""
    failure: str | None = None  # 最近一次没能下单的原因
    alerted = False  # 这个方案出意外的错时已经告警过
    while True:
        timing = _timing(decision)
        if timing.expired:
            _void_expired(plan, failure)
            return PlanEnd.VOIDED
        if timing.wait_seconds > 0:
            _sleep(timing.wait_seconds)
            continue
        attempt = _attempt(plan, alert_error=not alerted)
        if attempt.kind is AttemptKind.SUBMITTED:
            return PlanEnd.SUBMITTED
        if attempt.kind is AttemptKind.VOIDED:
            return PlanEnd.VOIDED
        failure = attempt.reason
        alerted = alerted or attempt.alerted
        if attempt.kind is AttemptKind.RETRY:
            _sleep(_retry_seconds())
```

`PlanEnd` 的值和 `Stage` 的超时、拒绝、发不出去三个值相同，另加 `SUBMITTED`、`VOIDED`。

每个 step 是 `order_workflow.py` 里一个 `@DBOS.step` 的模块级函数，只调用 install 进来的 `OrderFlow` 的同名方法：

| step | 做什么 | 拿锁 | 结果 |
| --- | --- | --- | --- |
| `request_confirmation` | `notification.request_confirmation`；算出现在到截止时刻还有几秒；写 INFO | 否 | `Requested(message_id, deadline, wait_seconds)`；发不出去时写 ERROR，返回 None |
| `settle` | 已确认：写 INFO。已拒绝：拿锁释放，写 INFO。已超时：`finish_confirmation(TIMED_OUT)`，拿锁释放，写 INFO。发不出去：拿锁释放 | 只在释放时 | 无 |
| `timing` | 读时钟，按 D4 | 否 | `Timing(expired, wait_seconds)`；要等时写「等到 X」的 INFO |
| `attempt` | 再校验和提交，按 D5、D6 | 再校验和提交时 | `Attempt(kind, reason, alerted)` |
| `void_expired` | 没有订单就拿锁释放；告警（D8） | 只在释放时 | 无 |

确定性：

- 工作流函数只按 step、`recv` 的结果和常量分支，不读时钟、不读库、不调用模块；日志都在 step 里，恢复时重放的部分不重复写（spec「日志」）
- 循环每一圈至少有一个记下结果的操作（`timing` 和 `attempt`），恢复时按记录走同样的路径
- `failure`、`alerted` 是工作流函数里的局部变量，由 step 的结果推出来，恢复时重算出同样的值；`alert_error` 作为参数传进 `attempt`，第一次出错的告警在 step 里发，恢复时不重发
- `_sleep` 是 install 进来的持久化休眠（D9），`_retry_seconds()` 读 install 进来的配置
- step 的结果都是模块级定义的冻结 dataclass 或 StrEnum，可以 pickle
- step 不开 DBOS 的自动重试：每个 step 自己接住预料中的错误，变成结果；意料之外的错误按 D6 的最后一行

### D3 等确认：`recv` 加一个「确认」事件

- 工作流在请求确认之前把事件 `confirmation` 设为 `waiting`，`recv` 返回以后设为结果（`confirmed`、`rejected`、`timed_out`），发不出去时设为 `unsent`。先设 `waiting` 再发消息：人在消息刚出现就按，也送得到（消息存在库里，`recv` 一开始就取到）
- `recv` 的超时是 `request_confirmation` 这个 step 算出的「到截止时刻还有几秒」：截止时刻由 `NotificationService` 按确认超时算出、印在按钮上，工作流等到同一个时刻。`recv` 是持久化的，重启以后只等剩下的时间；停机超过截止时刻的，恢复时立即超时
- 回调：

```python
def deliver_decision(plan_id: UUID, outcome: ConfirmationOutcome) -> None:
    """满足 DecisionHandler：这个方案的下单流程在等确认时把结果送给它，否则抛错。"""
    workflow_id = str(plan_id)
    if DBOS.get_event(workflow_id, CONFIRMATION, timeout_seconds=0) != Stage.WAITING:
        raise NotAwaitingConfirmationError(f"方案 {plan_id} 没有在等确认")
    DBOS.send(workflow_id, outcome.value, topic=DECISION_TOPIC)
```

- 事件存在库里：重启以后还在等的方案仍是 `waiting`，重启以后按的按钮照样送达（DiscordNotifier 的按钮不依赖内存，见 add-discord-notify 的 design D6）；已经有结果、已经结束、不存在的方案都不是 `waiting`，抛错，按钮提示没有送达，不会显示已确认却不下单
- 和超时的竞争：回调读到 `waiting`，工作流在 `send` 之前超时。工作流在 `settle` 里报告已超时：如果回调还没返回，DiscordNotifier 回调返回以后不再改消息；如果回调先返回、消息先显示已确认，报告的已超时覆盖它（discord-notification spec「结束确认」）。两种先后消息最后都是已超时，方案没有下单。送去的那条消息留在库里没人取（省略清单）
- 抛的 `NotAwaitingConfirmationError` 是 workflows 自己的 `RuntimeError`；DiscordNotifier 对任何异常都一样处理

考虑过：

- 只看工作流的状态是不是 `PENDING`：确认以后等开盘、等重试时也是 `PENDING`，重启以后再按一次已确认的消息会被当作还在等
- 在 bootstrap 或 DiscordNotifier 里记内存：重启就丢；DBOS 的事件本来就持久化

### D4 等开盘：每次醒来重新判断

`timing` 这个 step：

```python
def timing(self, decision: RiskDecision) -> Timing:
    """确认以后、每次重试之前：到期、盘中还是要等，见 order-flow spec「等开盘」。"""
    now = self._clock.now()
    assert decision.expires_at is not None  # 通过或调整的结果都有生效的有效期
    if now >= decision.expires_at:
        return Timing(expired=True, wait_seconds=0)
    if self._calendar.session_at(now) is not None:
        return Timing(expired=False, wait_seconds=0)
    wake = self._calendar.next_session_after(now).open
    return Timing(expired=False, wait_seconds=(wake - now).total_seconds())
```

- 先看有效期：生效的有效期是某个交易日的收盘时刻，早于有效期时下一个开盘一定早于它，不会白等一夜再作废
- 一次休眠到开盘，醒来以后再判断一次：系统时钟和 DBOS 的计时各自走，醒来时如果还没到开盘（时钟差几毫秒），再睡剩下的一点
- 再校验或提交抛 `OutsideSessionError`（刚好收盘）时回到这里，不另写分支

考虑过：每分钟醒来一次看时钟。一夜要记几百次休眠，恢复时逐个重放，换不来什么

### D5 再校验和提交是一个 step、一次拿锁

`attempt`：

1. `execution.order(plan_id)` 已经有订单：返回 `SUBMITTED`，不再校验
2. `read_snapshot` 读实时账户数据和行情（持仓的股票，开仓方案再加上这只股票），不拿锁。读账户的 `BrokerUnavailableError`、开仓方案这只股票没有行情或没有当天的最新价：写 WARNING，返回 `RETRY`
3. 拿锁：`risk.recheck(plan_id, snapshot, price, ...)`，开仓方案的 `price` 是这只股票当天的最新价，退出方案传 0（风控不看）；拒绝时放开锁以后告警，返回 `VOIDED`；通过或调整时在同一次拿锁里 `execution.submit(plan, recheck_decision)`，按 D6 分类

为什么是一个 step：

- 两个 step 时，再校验记下结果以后进程退出，恢复时直接用几小时前的再校验结果提交，违反「提交前必须重新读取实时数据重跑风控」。一个 step 没记下结果就整个重做
- 重做时可能订单已经建立（提交以后、记下结果之前退出）：第 1 步发现已经有订单就交给巡检。不这样的话，重做的再校验会改这个方案的预留股数，甚至在订单挂着时作废并释放预留
- 同一次拿锁：再校验和按它的结果提交之间，别的方案不会插进来改变额度；`submit` 里的券商调用都有超时，最多拿着锁几十秒

### D6 提交出错的分类

| `attempt` 里 | 结果 | 理由 |
| --- | --- | --- |
| `submit` 返回订单，不是已拒绝 | `SUBMITTED`，写 INFO（状态、委托股数） | 订单交给巡检；状态未知的也由巡检查清 |
| `submit` 返回订单，已拒绝 | `SUBMITTED`，告警 | 预留由巡检的同步预留释放（session-patrol design D4） |
| `NotReconciledError`、`OrderPausedError` | `RETRY`，写 INFO | 执行模块的说明：等一会儿再从再校验开始 |
| `UnprotectedPositionError` | `RETRY`，写 INFO | 见下 |
| `OutsideSessionError`（再校验或提交） | `NEXT_OPEN` | 回到 D4 |
| `ReconcileMismatchError` | 放开锁前释放预留；告警；`VOIDED` | 见下 |
| 别的 `Exception`（读数据、再校验、提交） | 写 ERROR（带异常栈）；`alert_error` 时告警，`alerted=True`；`RETRY` | 认证错误、数据库出错、意外的异常：方案还在有效期里，先重试；下一次如果订单已经建立，第 1 步交给巡检 |

- `ReconcileMismatchError`（最近一次对账有不一致，暂停开仓）作废：Proposal「每日对账」写的是不一致即告警并暂停开仓，「故障处理」对同属暂停开仓的降级模式写明已经确认、还没下单的开仓方案作废，处理一致。另外不一致只有之后的对账没有不一致才解除，M2 里下一次对账在收盘以后，而默认有效期就是当天收盘，等下去多半也是到期作废
- `UnprotectedPositionError`（加仓的股票是无保护仓位）等：Proposal「止损单挂单失败」写的是「补挂成功前禁止加仓」，是暂时的；盘中巡检每一轮都在补挂，多数情况几十秒内恢复，人已经确认过这个方案，补挂成功以后照常下单；到有效期还没补挂成功，按有效期作废。无保护本身已经由巡检告警，这里不另外告警
- 这两条都写进了 proposal 的「需要人决定」，请人确认 Proposal 的措辞

### D7 风控用的账户数据

`account_snapshot.py`：

```python
def price_of(position: HeldPosition, quote: Quote | None) -> Decimal:
    """持仓的最新价：行情的最新价，没有时用前一交易日收盘价，取不到行情时用均价。"""
    if quote is None:
        return position.average_cost
    if quote.last_price is None:
        return quote.previous_close
    return quote.last_price
```

- `account_snapshot(account, quotes)`：纯函数，把 `Account` 的权益、已结算资金、最近成交日和每只 `HeldPosition` 加上 `price_of` 拼成 `AccountSnapshot`
- `read_snapshot(execution, market_data, extra)`：读 `execution.account()`（失败原样抛出），一次取持仓股票加上 `extra` 的行情；`QuoteUnavailableError` 时当作一只都没取到；缺行情的持仓各写一条 WARNING。返回账户数据和取到的行情（再校验从里面取开仓方案这只股票的最新价）
- 放在 workflows：要用执行模块和行情两个模块的数据，模块之间不能互相 import；拼装是跨模块的流程
- 取不到行情时用均价、不报错：
  - 减仓类规则只看可卖数量（risk-rules spec），退出方案的检查不能因为 OpenD 没开而被挡住
  - 开仓方案的最新价只影响单只仓位上限和板块集中度对已有持仓的估算；下单前还会在盘中用实时数据再校验，那时开仓方案这只股票必须有当天的最新价
  - 盘外 OpenD 没开不算健康异常（degraded-mode spec 盘外不检查数据延迟），`/plan` 在盘外照常能录入
- 没有最新价时用前一交易日收盘价：盘前、开盘后几秒内都是这样，市值按前一天收盘估计比按均价准

考虑过：取不到行情就报错。实现更短，但 OpenD 一断退出方案就录不进去

### D8 告警

- 只有三类告警（spec「告警」）：确认过的方案作废（再校验拒绝、到期、对账不一致）、券商拒绝受理、第一次出意外的错。人工拒绝和确认超时人自己知道；订单的状态变化按 Proposal 只写程序日志（M2 写 Python 日志），巡检也不推送
- 告警都在 step 里、放开锁以后发：恢复时不重发；Discord 最多等 10 秒，不能拿着锁
- 文字在 `order_flow.py` 里拼，动作的中文名在这里另写一份（workflows 不能 import integrations 的 `ACTION_NAMES`）：

| 告警 | 文字 |
| --- | --- |
| 作废 | `方案作废，不会下单：买入 BBB 20 股（方案 ID …）` 加一行原因：再校验拒绝的每条规则一行（规则名：说明）；到期的写「到了有效期 2026-10-14 20:00 UTC 还没下单」和最近一次没能下单的原因；对账不一致的写「最近一次对账有不一致，暂停开仓」 |
| 券商拒绝 | `下单被券商拒绝：买入 BBB 20 股（方案 ID …）` |
| 出错 | `下单出错：买入 BBB（方案 ID …）：<异常类型>: <信息>。每 30 秒重试一次，到有效期为止` |

- 时刻写 UTC，和巡检的告警一致

### D9 工作流函数是模块级的，部件经 install 交给它

```python
def install(flow: OrderFlow, sleep: Callable[[float], None] = DBOS.sleep) -> None:
    """DBOS launch 之前调用：之后的下单流程（包括恢复的）都用这些部件。sleep 只有测试才换。"""
    global _installed
    _installed = _Installed(flow, sleep)
```

- DBOS 按名字在 import 时登记工作流和 step；恢复时按名字找函数。工作流要用的部件（模块的用例、锁、时钟、配置）在装配时才有，所以放进 `order_workflow.py` 里的一个槽，step 从槽里取。没有 install 就调用 step 时抛错
- `sleep` 可以换：生产用 `DBOS.sleep`（持久化，bootstrap 从 `Externals` 传入，D13）；测试换成把 `FakeClock` 拨到醒来时刻、立即返回的替身，不用真的等到开盘
- `OrderFlow` 不 import dbos：每一步都能不起 DBOS 直接测

考虑过：

- `DBOSConfiguredInstance`：实例按名字登记，同一个进程里第二次装配（测试里每个测试都装配一次）同名实例会报重复登记；清空注册表又会丢掉 import 时登记的函数
- 装配时再用 `DBOS.workflow(...)` 包一层闭包：每次装配都重复登记同一个名字，DBOS 写 WARNING，bootstrap 测试要求正常启动没有 WARNING

### D10 程序更新以后照样恢复：打开 patching

```python
DBOS(config={"name": APP_NAME, "system_database_url": url, "enable_patching": True})
```

- 默认的应用版本是工作流源码的哈希，部署新代码以后，旧版本进行中的下单流程永远不会恢复：等确认、等开盘的方案停在那里，预留没有人释放，「未完成的开仓方案」规则还会挡住这只股票以后的开仓方案
- 打开以后应用版本固定，新代码接着跑旧的进行中的流程。只要 step 的顺序和名字不变，恢复照常；改了步骤（加、删、换顺序），用 `DBOS.patch` 让旧的流程走旧的路径，或者等进行中的下单流程都结束再部署（Migration Plan）
- 只有一台机器、一个进程，不需要按版本分流

考虑过：固定 `application_version`。效果一样，但不能用 `DBOS.patch`

### D11 停止时不等工作流线程

`cli.py`：

```python
def entry() -> None:
    """命令 tradingagentspro 的入口：main 返回以后立即结束进程，见 add-order-flow 的 design D11。"""
    code = main()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
```

- 在等确认（最多 30 分钟）、等开盘（最多几天）的工作流线程里是 `recv`、`time.sleep`，DBOS 的 destroy 不打断它们，解释器正常退出时又会 join 它们：`systemctl stop` 要等 90 秒再被 SIGKILL，`restart` 每次都卡 90 秒
- `run` 已经按顺序关完巡检、DBOS、Discord、OpenD 和数据库，写了「已停止」；之后直接结束进程。还在等的工作流由下次启动的 DBOS 从库里恢复，和崩溃一样安全（spec「持久化和恢复」）
- 只在 `run` 正常返回以后这样做：`--version` 和用法不对时 argparse 抛 `SystemExit`，照常退出
- `main` 不变，`test_cli.py` 照旧调用它；`entry` 另外测（把 `os._exit` 换成记录调用的替身）

考虑过：

- 把长时间的等待切成每分钟一次：线程最多晚一分钟退出，超过 systemd 的 90 秒的风险仍在，DBOS 的记录多几百倍
- 停止时取消进行中的工作流：取消以后要 resume，恢复的路径多一条

### D12 锁

- 提交接口的 `risk.check`、`attempt` 里的 `risk.recheck` 加 `execution.submit`、各处的 `risk.release` 拿 `TradingApp.lock`（session-patrol 的那把 `RLock`）
- 读账户数据、行情、熔断和降级状态、`execution.order` 不拿锁（session-patrol design D3 的只读调用）
- 请求确认、报告结果、告警、`recv`、休眠都在锁外
- `RLock` 可重入：`attempt` 里拿着锁调用 `submit`，`submit` 不拿锁，不涉及嵌套；以后的组合用法不会卡死

### D13 bootstrap 接线

`_assemble`：

1. 建执行模块、风控、紧急退出、健康监控、行情（不变）和锁（session-patrol）
2. `services = TradeServices(execution, risk, emergency, health, market_data, lock)`
3. `PlanCommand(OrderPlanIntake(services), ...)` 取代 `AccountPlanIntake`
4. `DiscordNotifier(gateway, ..., deliver_decision)` 取代 `refuse_decision`
5. `NotificationService` 之后建 `OrderFlow(services, notification, clock, calendar, OrderFlowConfig())`，放进 `_Parts`

不成环：提交接口不需要通知（确认在工作流里请求），所以 `OrderPlanIntake` 可以在 Discord 之前建，`OrderFlow` 在通知之后建。

`start` 的「启动 DBOS」一步：`DBOS(config=...)` 之后、`DBOS.launch()` 之前 `install(parts.flow, externals.sleep)`；`enable_patching`（D10）。其余步骤和停止顺序不变。`TradingApp` 不加字段：以后的流程从 install 进去的 `OrderFlow` 用部件。

`Externals` 加一项 `sleep`（持久化休眠）：`SYSTEM_EXTERNALS` 是 `DBOS.sleep`，bootstrap 测试换成拨 `FakeClock` 的替身。等待是程序和真实时间接触的地方，和时钟一样放在 `Externals` 里；不这样的话，bootstrap 测试里一个盘后确认的方案会真的睡到开盘。

`bootstrap/order_flow.py` 和 `tests/bootstrap/test_order_flow.py` 删掉：查持仓的板块搬到 `OrderPlanIntake`，它的测试搬到 `tests/workflows/`。

### D14 配置

```python
class OrderFlowConfig(BaseModel):
    """下单流程的配置和默认值，含义见 order-flow spec「下单流程配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 没能下单（暂停、无保护、取不到价格、读券商失败、意外的错）以后，等这么久再从再校验开始
    retry_delay: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
```

确认超时用 `NotificationConfig.confirmation_timeout`，不另设。

### D15 测试

三层：

- `tests/workflows/test_account_snapshot.py`：`price_of` 的三种取法，`account_snapshot` 的各项，`read_snapshot` 只取持仓股票、一次取完、没有持仓不取、行情不可用和缺一只时的 WARNING、读账户失败原样抛出。纯函数，不连库
- `tests/workflows/test_order_flow.py`：不起 DBOS，直接调用 `check_plan`、`OrderPlanIntake.held_sector` 和 `OrderFlow` 的每个方法。用 `tests/fakes/` 的券商、止损单、订单、止损保护、交易记录、预留存储、通知通道、行情源，`FakeClock`，真实的 `ExecutionService`、`RiskService`（内存预留）、`EmergencyService`、`HealthService`、`MarketDataService`、`NotificationService` 和一把 `RLock`。基准数据放 `tests/workflows/order_builders.py`（按 spec 的基准数据：AAA 的开仓订单直接写进订单存储并标为全部成交，券商账户和持仓在 `FakeBroker` 上设好，行情在 `FakeQuoteSource` 上设好）。覆盖 spec「提交方案」「查持仓的板块」「风控用的账户数据」的全部场景，以及 `timing` 的三行、`attempt` 的每一种分类、`settle` 的四种结果、告警文字、锁的场景（另一个线程拿锁；通知替身阻塞时测试线程 `acquire(blocking=False)` 成功）。熔断用 `EmergencyService.check` 让它触发，降级用 `HealthService.check` 传异常的信号
- `tests/workflows/test_order_workflow.py`：真实 DBOS，连 testcontainers 的空数据库（`empty_database`，要开着 Docker）。每个测试：构造 DBOS（`enable_patching`）、`install(flow, sleep=拨钟的替身)`、launch，结束时 destroy。经 `OrderPlanIntake.submit` 启动，经 `deliver_decision` 按按钮，用有超时的轮询等工作流结束（`DBOS.get_workflow_status`）或等事件变为 `waiting`。确认超时的场景把 `NotificationConfig.confirmation_timeout` 设成 1 秒。覆盖 spec「请求人工确认」「确认的结果」「按钮的结果只送给在等确认的方案」「等开盘」「下单前再校验和提交」「提交的结果」「告警」「日志」里要走完整个流程的场景
- 重启和恢复（spec「持久化和恢复」）：
  - 已经做完的步骤不重做：工作流跑完以后把它在 `dbos.workflow_status` 里的状态改回 `PENDING`，换一套新的替身 install，调用 `DBOS._recover_pending_workflows()`，检查替身没有收到第二次请求确认、券商没有第二次提交、没有新的告警和日志
  - 进程在某一步退出：替身在指定的调用里抛一个 `BaseException` 的子类（DBOS 只接 `Exception`，这一步不记结果，工作流保持 `PENDING`），然后 destroy、重新构造 DBOS、install 新的替身、launch，由恢复接着做。用它覆盖「停机超过确认的截止时刻」（在 `settle` 里退出）、「等开盘时重启」「停机期间过了开盘」（在拨钟的 sleep 里退出）、「提交以后、记下结果之前进程退出」（券商替身受理以后退出）
  - 「等确认时重启，重启以后按确认」：工作流在 `recv` 里等的时候没有替身调用可以注入退出。这一条用 destroy、重新构造、install、launch 模拟重启，旧的线程留在 `recv` 里（DBOS 3.2 按执行的归属区分新旧两次，旧的那次取消息会失败）；然后 `deliver_decision`，检查只请求过一次确认、券商收到订单。apply 时先验证旧线程不会抢走消息；不成立就把这一条改为检查重启以后 `get_event` 仍是 `waiting`、`send` 成功、恢复的那一次取到确认并下单，旧线程用很短的确认超时让它先结束，不改 spec
  - 「程序更新以后恢复」：在退出和重新 launch 之间多登记一个测试用的工作流（源码哈希因此变了），恢复照常；对照组不打开 patching 时不恢复，证明测试测到了版本
- `tests/bootstrap/test_app.py`：「录入并确认一个买入方案」（经 `World` 的 `PlanCommand` 运行 `/plan`，经 `FakeGateway` 按按钮，`FakeAlpaca` 记下买单，`FakeOpenD` 加上 BBB 的行情）、「启动时恢复等开盘的方案」（第一次启动在盘后录入并确认，`World` 的休眠替身抛 `BaseException` 的子类模拟进程在等开盘时退出，停止；拨钟到下一个交易日盘中、换回普通的休眠替身再启动）、「停止时有方案在等确认」（`run` 不等工作流线程就返回 0；用 monkeypatch 把装配用的确认超时改成 1 秒，让留下的线程很快结束，`no_threads_left_behind` 照常通过）、正常启动不变
- `tests/bootstrap/test_cli.py`：`entry` 在 `main` 返回以后调用 `os._exit`，退出码就是 `main` 的返回值；`--version` 时不调用

## Risks / Trade-offs

- [workflows 里有一个模块级的槽，测试之间可能串] → 每个 DBOS 测试开始时 install 新的部件；没有 install 时 step 抛错，不会悄悄用上一个测试的
- [`os._exit` 跳过 atexit：日志没刷完、别的库的清理没做] → 先 `logging.shutdown()`、刷 stdout 和 stderr；数据库连接、Discord、OpenD 已经由 `run` 关掉；DBOS 的状态都在库里
- [打开 patching 以后，改下单流程的步骤会让旧的流程在恢复时对不上] → Migration Plan 写明部署规则；DBOS 发现步骤名字对不上时报错，工作流进入 ERROR，写日志，不会悄悄走错路径；这种情况要人看日志处理预留
- [下单流程长时间等重试，每 30 秒读一次账户和行情] → 每次几次 Alpaca 请求，远低于每分钟 200 次；只有被暂停的方案才这样
- [一个方案作废和巡检同时改它的预留] → 都在锁里；作废只发生在没有订单的方案上，巡检只同步有订单的方案
- [提交卖单（错开打开时还有买单）时 `submit` 会对齐这只股票的止损单，发出改单或撤单请求；巡检如果在几百毫秒之内又对齐同一只股票，Alpaca 那边旧单还在替换，第二次改单被拒，这只股票会被误标为无保护仓位并告警。巡检在一轮之内已经避开了同样的问题（session-patrol design D4），但下单流程的提交和巡检的一轮之间没有这样的间隔] → 下一轮巡检对齐时补挂成功，误报自己消失，止损单其实一直挂着；本变更不改执行模块的行为。10 个交易日的观察期里出现了，再单独开「修改」变更：执行模块在改单请求还没生效时不再改单

## Migration Plan

- 没有数据库迁移；DBOS 第一次跑工作流时在 `dbos` schema 里记录
- 部署：更新代码、`uv sync`（命令入口变了，要重装一次项目）、重启主程序
- 以后改下单流程的步骤：先看 `dbos.workflow_status` 里有没有 `PENDING` 的 `order_plan`；有就等它们结束（确认、作废或下单），或者用 `DBOS.patch` 兼容旧的路径，再部署
- 回滚：revert 代码；已经在等的方案由回滚后的代码恢复（步骤没变时）；预留和订单都在状态库里
