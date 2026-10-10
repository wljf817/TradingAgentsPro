# Design

## Context

现状（本变更 apply 时，下单流程、收盘对账、改单未生效时不再改单都已经归档）：

- 盘中巡检（`workflows/patrol.py`）：守护线程每 15 秒一轮，`run_round` 依次跟进订单、同步预留、对齐止损单，最后是收盘对账；每一项先看 `_stopping`，包在 `_item` 里，出错只写 ERROR；第 1 步跟进过的股票第 3 步不再对齐（Alpaca 改单时生成新订单替换旧单，替换完成以前不接受第二次改单）。`TradingApp.lock` 是全程序一把 `RLock`
- 紧急退出模块：`EmergencyService.check(holdings, equity, previous_close_equity)` 盘外返回空，盘中判定并记下熔断（内存，保持到收盘）；`breaker_tripped()` 读内存；包根导出 `Holding`、`EmergencyExit`、`ExitReason`、`close_plan`、`reference_price`。现在没有人调用 `check`，熔断永远不会触发
- 执行模块：`account()`（读券商账户和全部持仓，两次 Alpaca 请求；失败抛 `BrokerUnavailableError`）给出每只能找到开仓订单的持仓的板块、止损价、生效的紧急阈值、今天开仓成交的均价，以及权益和前一交易日收盘时的权益（Alpaca 的 `equity`、`last_equity`）；`cancel_orders(symbol)`、`cancel_buy_orders()` 记下撤单要求再各跟进一次，可以重复调用；`submit` 新建订单前可能抛 `NotReconciledError`、`OutsideSessionError`、`OrderPausedError`；清仓卖单要等止损单撤掉（`stop_yields`）才提交，之后由跟进提交；`backup_stop_due(symbol, last_price)` 只读；只读查询 `open_orders`、`order`、`protection`
- 风控：`check` 对已经有预留的方案返回第一次的结果，拒绝不保存；`recheck` 拒绝时自己释放，没有有效预留时给"方案已结束"；退出方案只检查可卖数量（持仓减去别的卖出预留），不看最新价；`release` 可以重复调用；只读 `active_plan_ids`
- 下单流程（`workflows/order_flow.py`、`account_snapshot.py`）：`TradeServices`（执行模块、风控、紧急退出、健康监控、行情、锁）；`account_snapshot(account, quotes)` 把账户数据和行情拼成风控用的 `AccountSnapshot`。一个方案没有订单时，下单流程在再校验时遇到"方案已结束"就作废并告警
- 行情：`MarketDataService.quotes(symbols)` 一次取完，没有股票时不调用行情源；失败抛 `QuoteUnavailableError`；开盘后还没有成交时 `last_price` 是 None。moomoo 快照每 30 秒最多 60 次
- 通知：`NotificationService.alert(text)` 尽力而为，发不出去只写错误日志，最多等 10 秒

要求见 specs/emergency-exit-flow/spec.md、specs/session-patrol/spec.md 和 specs/bootstrap/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
workflows/
  emergency_flow.py   (新) EmergencyFlow（judge、cancel_buys、advance、report）、ExitCause、Trigger、
                           Breaker、Judgement、ExitStep、holdings_of 和告警文字
  names.py            (改) 加方案动作的中文名 ACTIONS，下单流程改为从这里 import
  patrol.py           (改) 构造参数加 emergency；run_round 在对齐止损单之后、收盘对账之前加一步

modules/risk/service.py   (改) 只读：active_plans

bootstrap/app.py          (改) 装配 EmergencyFlow，交给 SessionPatrol
```

- `emergency_flow.py` 只 import 模块的包根：`execution`（`Account`、`HeldPosition`、`Order`、`OrderSide`、`OrderStatus`、`BrokerUnavailableError`）、`risk`（经 `TradeServices`）、`emergency`（`EmergencyConfig`、`ExitReason`、`Holding`、`close_plan`、`reference_price`）、`marketdata`（`Quote`、`QuoteUnavailableError`）、`notification`（`NotificationService`）；`contracts`（`TradePlan`、`AccountSnapshot`、`RiskOutcome`）；`shared` 的时钟和交易日历；workflows 里的 `order_flow.TradeServices`、`account_snapshot.account_snapshot` 和中文名。不 import integrations、bootstrap、dbos
- 跨模块传的模型都已经存在，contracts 不改：
  - 代码触发的清仓方案 `TradePlan`（contracts）：紧急退出模块生成，交给风控检查、再校验，再交给执行模块提交
  - 风控用的账户数据 `AccountSnapshot`（contracts）：用执行模块的 `Account` 和行情的 `Quote` 拼成
  - 风控结果 `RiskDecision`（contracts）：再校验的结果交给执行模块
  - 判定的输入 `Holding`、结果 `EmergencyExit`（紧急退出模块导出的 dataclass）；账户数据 `Account`、`HeldPosition`，订单 `Order`、止损保护 `Protection`（执行模块导出的 dataclass）；行情 `Quote`（行情模块导出的 dataclass）
- `ExitCause`、`Trigger`、`Breaker`、`Judgement`、`ExitStep` 是 workflows 自己的冻结 dataclass 和 StrEnum，只在 `emergency_flow.py` 和 `patrol.py` 之间传
- 模块只加一个只读查询（D9），行为、包根的导出、架构测试的白名单都不变

## Goals / Non-Goals

**Goals:**

- 每一轮从状态库和券商重新推出要做什么：撤单、作废、检查、提交都可以重复调用，方案 ID 固定，中途退出不会重复下单
- 每个方法用替身直接测（`run_round` 或 `EmergencyFlow` 的方法），不用线程、不用等时间
- 不改执行模块、风控、紧急退出模块、下单流程的行为

**Non-Goals:**

按「编程风格」省略的情况：

- 撤单期间进程重启、重启以后不再触发：当天触发过的记录在内存里，不再继续，已经撤掉的订单、作废的方案不恢复
- 告警过什么记在内存里：重启以后正在进行的紧急退出再告警一次开始，熔断再告警一次
- 判定读账户以后、同一轮里提交以前止损单成交：清仓单股数超过持仓，执行模块不提交（`stop_yields` 的未覆盖股数），到收盘过期，结果告警写已过期
- 风控检查以后、提交以前进程崩溃（毫秒级），并且到收盘以后才重启：清仓方案的预留留下，没有人释放
- 没有开仓订单的持仓（对账的「来历不明的持仓」）不在账户数据里：不判定，熔断也不清它；对账已经告警并暂停开仓
- 撤单迟迟不结束时不设上限、不另外告警：开始时的告警已经写明等订单结束，到收盘有「到收盘还没有清仓单」的告警
- 等撤单、行情不可用、读账户失败时，每一轮各写一条日志：不合并
- 不检查行情的更新时刻：行情过时由健康监控判定
- 被作废的方案还在等确认时，人还能按它的确认按钮：按了以后下单流程得到"方案已结束"、作废并告警，不另外改确认消息
- 清仓提交时巡检同一轮刚改过这只股票的止损单（比如撤掉的卖单结束、止损目标恢复）：改单还在等待生效，执行模块这次不撤止损单（update-stop-pending-modify），下一轮巡检跟进清仓单时再撤，晚 15 秒；多一次改单
- 一次熔断清多只股票时，一两轮里的 Alpaca 请求可能超过巡检的预算：被限流时按券商不可用处理，下一轮再来
- 收盘前片刻建立的清仓单到收盘还没成交：按已有规则过期，结果告警

## Decisions

### D1 放在巡检的每一轮里：对齐止损单之后、收盘对账之前

```python
def run_round(self) -> None:
    """做一轮：跟进订单、同步预留、对齐止损单、健康判定、紧急退出，最后是收盘对账。"""
    tracked = self._track_orders()
    self._sync_reservations()
    broker_connected = self._align_stops(tracked)
    self._judge_health(broker_connected)  # 「健康监控接线」加的
    self._emergency_exit()
    self._close_reconcile()
```

- 放在巡检里：和巡检同一个线程，紧急退出的撤单、提交和巡检的跟进、对齐自然不会同时进行；重启以后第一轮就接着做，不用另写恢复
- 放在对齐止损单之后：同一轮里第 1 步刚把撤掉的订单跟进到终态，第 2 步刚释放了它们的预留，紧急退出这时检查清仓方案，可卖数量已经不被它们占着。放在第 1 步之前的话，撤单结束以后要多等一轮（15 秒）才能清仓，紧急退出提交的清仓单还会在几毫秒以后被第 1 步再跟进一次
- 放在收盘对账之前：收盘对账保持一轮的最后一项（add-close-reconciliation 的 design D2）。两者不会在同一轮里都做事：紧急退出只在盘中，收盘对账只在收盘加 10 分钟以后
- 「健康监控接线」也在第 3 步之后加了一项「健康判定」：两者互不依赖（紧急退出在降级模式下照常进行），apply 时合在一起，健康判定在前、紧急退出在后，都在收盘对账之前
- 判定的频率就是巡检的间隔。15 秒对「分钟级」的紧急退出足够；更快的话 Alpaca 的请求不够用（D11）

`_emergency_exit` 由巡检编排，`EmergencyFlow` 只提供每一项做什么：

```python
def _emergency_exit(self) -> None:
    """紧急退出这一步，见 emergency-exit-flow spec「巡检里的紧急退出」。"""
    if self._stopping.is_set():
        return
    judgement: Judgement | None = None
    with self._item("紧急退出的判定"):
        judgement = self._emergency.judge()
    if judgement is not None:
        if judgement.breaker is not None and not self._stopping.is_set():
            with self._item("熔断时撤掉全部买单"):
                self._emergency.cancel_buys(judgement)
        for plan in judgement.exits:
            if self._stopping.is_set():
                return
            with self._item(f"紧急退出 {plan.symbol}"):
                self._emergency.advance(plan, judgement)
    if not self._stopping.is_set():
        with self._item("紧急退出的结果"):
            self._emergency.report()
```

每一项都用巡检的 `_item` 和 `_stopping`：停止、出错只跳过这一项的规则和巡检其余各项一样，不另写。

考虑过：

- 每个触发开一个 DBOS 工作流（撤单 → 等结束 → 清仓）：要另写恢复、等待和锁的协调；工作流的每一步本来就要重新读券商和状态库，换不来什么。等撤单结束正好是巡检每一轮已经在做的事
- 单独的线程：多一个线程的开始和停止，和巡检之间又会在几毫秒内先后对齐同一只股票
- 放在第 1 步之前：见上

### D2 每一轮重新推出要做什么，内存里只记当天触发过的股票

每一轮从状态库（订单、撤单要求、预留）和券商（持仓、权益）重新推出这只股票走到了哪一步：清仓方案有没有订单、这只股票还有没有没结束的订单、还没下单的方案有没有释放。不保存「紧急退出的进度」。

内存里只记一样东西：这次运行里当天触发过的股票（`_Record`），用来：

- 当天做到底（proposal「需要人决定」第 2 条）：触发以后，等撤单结束期间价格回升，这只股票照样在之后的轮次里清仓。判定时，内存里当天触发过、还没有清仓单、也没有结束的股票加进要清仓的股票
- 告警只推一次：开始紧急退出、结果、到收盘还没下单

```python
@dataclass
class _Record:
    """这次运行里当天开始的一只股票的紧急退出。"""

    plan: TradePlan  # 当天的清仓方案
    day: date  # 触发的交易日
    trigger: Trigger | None  # 第一次触发的原因；重启以后按清仓单找回来的为 None
    announced: bool  # 开始的告警已经推送过（找回来的一开始就是）
    ended: bool = False  # 当天的紧急退出已经结束，结果已经告警
```

- 判定时先删掉更早交易日的记录（它们已经在 `report` 里结束）
- 重启以后记录没了：仍然触发的股票重新开始（撤单要求在状态库里，不再请求撤单）；已经有清仓单、还没结束的，判定时按「有持仓的股票当天的清仓方案有没有订单」找回来，不再推送开始的告警，结果照样告警（spec「重启以后」）
- 记在内存而不存表：只有在撤单期间（通常 15 到 30 秒）重启才会丢，丢了的结果是按重启以后的价格重新判定，省略清单

考虑过：

- 完全不记，每一轮只按当时的价格判定：价格回升就不卖，这只股票的订单被撤了、方案被作废了，却什么都没卖；告警也无法只推一次
- 把「已经开始」存进状态库（比如先做风控检查留下预留作为标记）：要处理标记的清理（收盘以后、隔天），只为了撤单期间重启这种少见情况

### D3 判定的输入

```python
def holdings_of(account: Account, quotes: Mapping[str, Quote]) -> tuple[Holding, ...]:
    """紧急退出判定用的持仓：账户数据的每只持仓加上行情；没有行情时两个价格为空。"""
```

`judge` 依次：

1. `calendar.session_at(clock.now())` 为空：返回 None，不读任何东西
2. `execution.account()`；`BrokerUnavailableError` 时写 WARNING，返回 None
3. `market_data.quotes(持仓的股票)`；`QuoteUnavailableError` 时写 WARNING、当作 `{}`；缺的股票写一条 WARNING 列出它们
4. `emergency.check(holdings_of(account, quotes), account.equity, account.previous_close_equity)`
5. 本地备用止损：每只有最新价、不在第 4 步结果里的持仓，`execution.backup_stop_due(symbol, last_price)` 为真时，加上 `close_plan(symbol, sector, 交易日)`（D4）
6. 合成要清仓的股票（D2 的记录）：`Judgement(day, snapshot, breaker, exits)`，`snapshot = account_snapshot(account, quotes)`，`exits` 是这一轮要推进的清仓方案（当天的记录里还没有订单、没有结束的），按代码排序

- 一次读账户、一次取行情：同一份数据给判定和风控用，每轮 2 次 Alpaca 请求、1 次 moomoo 快照
- 行情不可用时照常判定熔断：熔断只用券商给出的权益（Alpaca 用自己的价格算），和 moomoo 无关；没有价格的持仓不判定个股跌幅和备用止损（`drop_triggered` 和备用止损都要求有最新价）。只跳过判定会让 OpenD 断线期间连熔断也失效
- 读账户失败时整轮跳过：没有权益判定不了熔断，也没有风控用的账户数据，清仓也做不了
- 不复用 `read_snapshot`：它不返回 `Account`，而判定要用 `HeldPosition` 的紧急阈值和今天开仓成交的均价；拼风控数据的纯函数 `account_snapshot` 复用
- `emergency.check` 不拿锁：只有巡检调用它，它只改紧急退出模块内存里的熔断状态；别的线程读 `breaker_tripped()` 是读一个属性

### D4 本地备用止损走同一条路

- 条件只用执行模块的 `backup_stop_due`（无保护仓位或错开中、盘中、最新价不高于止损价），方案是同一个 `close_plan`。同一只股票同时触及紧急退出时只有一个方案，原因取紧急退出的
- 之后和紧急退出完全一样：撤掉这只股票挂着的订单（错开中就是那笔买单）、作废还没下单的方案、订单都结束以后清仓、执行后通知（proposal「需要人决定」第 1 条）
- 错开中的那笔买单撤掉以后，巡检的跟进会按止损价挂回止损单（stop-protection spec「买单和止损单错开」），紧急退出紧接着提交清仓单，又把它撤掉：多两次请求，但不用改执行模块。挂回时如果价格已经低于止损价，Alpaca 可能立即触发它（卖出以后清仓单没有可卖的股数，或者不提交、到收盘过期），也可能拒绝它（巡检推送无保护告警，清仓照常）。两种情况持仓都会卖掉

### D5 一只股票的清仓：一次拿锁

```python
def advance(self, plan: TradePlan, judgement: Judgement) -> None:
    """一只股票的清仓，见 emergency-exit-flow spec「一只股票的清仓」。锁里做事，锁外告警。"""
    with self._services.lock:
        step = self._advance(plan, judgement.snapshot)
    self._announce(self._records[plan.plan_id], step)
```

`_advance` 在锁里：

1. `execution.order(plan_id)` 不为空：返回「已经有清仓单」
2. `risk.active_plans()` 里这只股票、不是这个清仓方案、`execution.order` 为空的方案，逐个 `risk.release`（D6）
3. `execution.open_orders()` 里这只股票的订单不为空：其中有 `cancel_wanted` 为假的，调用 `execution.cancel_orders(symbol)`；返回「等订单结束」和撤掉的订单
4. `risk.check(plan, snapshot, daily_loss_breaker_tripped=emergency.breaker_tripped(), degraded=health.status.degraded)`；拒绝时返回「没有可卖的股数」。通过或调整时 `risk.recheck(plan_id, snapshot, Decimal(0), ...)`（退出方案不看最新价，和下单流程一样传 0）；拒绝时同上。通过或调整时 `execution.submit(plan, recheck_decision)`，返回「已建立清仓单」和订单

- 一次拿锁：作废、撤单、检查、再校验、提交之间，下单流程不会插进来给这只股票下单或改变额度。下单流程如果先拿到锁并提交了，订单就在第 3 步被撤掉；如果后拿到，方案已经释放，再校验得到"方案已结束"
- 第 1 步也挡住了 `cancel_orders` 撤掉清仓单自己：有清仓单以后这只股票不再撤单
- 第 3 步只在有还没撤的订单时撤：已经都有撤单要求的订单由巡检的第 1 步跟进，这里不再多跟进一次。`cancel_orders` 本身可以重复调用，这只是省请求
- 第 4 步只在没有没结束的订单时做：所以 `submit` 不会因为这只股票有待提交或状态未知的订单而报暂停；执行模块在巡检开始以前已经完成启动对账，不会报还没对账；盘中判定、立即提交，只在收盘那一刻的毫秒里可能报不在交易时段。这些错误不另写分支，按巡检的 `_item` 写 ERROR，下一轮再来；收盘以后由 `report` 收尾（D8）
- 检查之后接着再校验：风控检查对已经有预留的方案返回第一次的结果（上一轮提交出错时会这样），再校验按这一轮的实时持仓重新算可卖数量，满足 Proposal「提交订单前重新读取……重跑风控」。同一轮里两者的输入相同，再校验不会拒绝检查通过的方案
- 拒绝只会是可卖数量为 0（退出方案只有这一条规则）：持仓已经被止损单或撤单前成交的卖单卖完。这只股票当天的紧急退出结束，推送告警；记录标为结束，之后的轮次不再处理
- 提交以后：卖单等止损单撤掉才提交给券商，之后由巡检的第 1 步跟进时提交；被券商拒绝、过期也是在巡检里发生，结果由 `report` 告警

### D6 作废还没下单的方案：只释放预留

- 下单流程的方案（等确认、等开盘、等重试）在风控里都有有效预留、在执行模块里没有订单。释放预留以后，下单流程下一次再校验得到"方案已结束"，按 order-flow spec 作废并告警；等确认的方案超时、人工拒绝时的释放照常（可以重复）
- 不改 add-order-flow 的任何行为，也不需要知道 DBOS：紧急退出只调用风控
- 当天的清仓方案自己排除在外：上一轮提交出错时它可能已经有预留
- 有订单的方案不在这里处理：它们的订单在第 3 步被撤掉，预留由巡检的同步预留释放
- 等确认的方案的确认消息不改：人按确认以后，下单流程作废并告警（省略清单）

考虑过：

- 给等确认的下单流程发一个「拒绝」消息（`DBOS.send`）：确认消息会显示「已拒绝」，像是人拒绝的；紧急退出要依赖下单流程的工作流细节
- 取消这些 DBOS 工作流：下单流程就不会释放、不会告警，还要处理取消和恢复

### D7 熔断

```python
@dataclass(frozen=True, slots=True)
class Breaker:
    """这一轮熔断已触发：告警用的权益。"""

    equity: Decimal
    previous_close_equity: Decimal
```

- `judge` 在 `emergency.breaker_tripped()` 为真时给出 `Breaker`（保持到收盘，权益回升也在）；熔断以后紧急退出模块的判定结果就是全部持仓，原因是组合熔断
- `cancel_buys`：拿锁，`execution.open_orders()` 里有 `cancel_wanted` 为假的买单时 `execution.cancel_buy_orders()`；放开锁以后，这次运行里这个交易日还没告警过熔断就推送熔断告警（写上撤掉的买单数）。每一轮都检查：熔断以前已经通过再校验、刚好在熔断之后才提交的买单也会被撤掉
- 熔断告警的熔断比例从 `EmergencyConfig` 取：bootstrap 把同一个配置对象交给紧急退出模块和 `EmergencyFlow`

### D8 告警和日志

告警在锁外推送，同样的文字写一条 WARNING（M4 以前，Python 日志是唯一的记录）：

| 何时 | 谁推送 | 文字（举例） |
| --- | --- | --- |
| 一只股票这次运行里第一次处理当天的清仓 | `advance`，放开锁以后 | `紧急退出 AAA：个股跌幅 8.0%（参考价 100 → 最新价 92），达到紧急阈值 8.0%。作废还没下单的方案：加仓 AAA（方案 …）。撤掉挂着的订单：减仓 AAA 20 股（方案 …）。订单都结束以后市价清仓` |
| 熔断，这次运行里每个交易日第一次 | `cancel_buys` | `单日亏损熔断：前一交易日收盘时的权益 10,000，当前权益 9,300，单日亏损 700（7.0%），达到熔断比例 7.0%。清仓全部持仓，撤掉全部买单（1 笔），收盘前不再开仓` |
| 清仓单进入终态 | `report` | `AAA 紧急清仓完成：卖出 50 股，成交均价 91.5`；或 `AAA 紧急清仓单已拒绝：成交 0 / 50 股。今天不再清仓 AAA，剩下的持仓由止损单保护` |
| 之后的轮次里没有可卖的股数 | `advance` | `AAA 没有可卖的股数，紧急退出结束` |
| 收盘以后的第一轮，当天的记录还没有清仓单 | `report`，先拿锁 `risk.release(清仓方案)` | `AAA 到收盘还没下清仓单（等撤单结束），今天不再清仓 AAA` |

- 原因的写法：个股跌幅写参考价（`reference_price`）、最新价、跌幅（`(参考价 − 最新价) ÷ 参考价`，一位小数的百分比）和紧急阈值；组合熔断只写「组合熔断」；本地备用止损写「无保护仓位」或「错开中」（`execution.protection(symbol)`）、最新价和止损价
- 开始的告警写的是这一项做了什么：作废的方案（动作、方案 ID）、撤掉的订单（买卖方向、股数、方案 ID；订单上没有方案的动作），以及「已建立清仓单：市价卖出 N 股」「订单都结束以后市价清仓」「没有可卖的股数，紧急退出结束」三者之一
- `report` 每一轮都做（盘外也做）：清仓单在收盘截止、由巡检在收盘以后结束，结果要在盘外报。到收盘还没下单的释放：上一轮提交出错时清仓方案可能留着预留（D5），收盘以后没有人会再用它
- 金额用千分位（`f"{x:,}"`），价格原样，比例一位小数的百分比；动作和订单状态的中文名用 workflows 里已有的对照（下单流程的动作名、`names.py` 的订单状态），是私有名字时 apply 时移到 `names.py` 共用
- INFO 日志：撤单请求（股票、方案 ID）、等订单结束（每一轮一条）、作废的方案、建立清仓单（股数和状态）

### D9 只读查询

```python
def active_plans(self) -> tuple[TradePlan, ...]:
    """有有效预留的方案：没有释放、剩余股数大于 0。只读存储。"""
    return tuple(r.plan for r in self._store.active())
```

- 加在 `RiskService`：作废要按股票找方案，`active_plan_ids` 只有 ID
- 只读，和 `active_plan_ids` 一样不拿锁也可以；紧急退出在锁里调用它，保证读到的和接着释放的是同一批

### D10 巡检和 bootstrap 接线

```python
class EmergencyFlow:
    """盘中巡检每一轮里的紧急退出，见 emergency-exit-flow spec。

    judge 不拿锁；cancel_buys、advance 各拿一次锁，告警在锁外；report 只在释放预留时拿锁。
    """

    def __init__(
        self,
        services: TradeServices,
        notification: NotificationService,
        clock: Clock,
        calendar: TradingCalendar,
        config: EmergencyConfig,
    ) -> None: ...
```

- `SessionPatrol` 的构造参数加 `emergency: EmergencyFlow`：一轮里的项由巡检编排（D1）
- `_assemble`：下单流程已经建了 `TradeServices`；在 `NotificationService` 之后建 `EmergencyFlow(services, notification, clock, calendar, emergency_config)`，再建巡检。`EmergencyConfig()` 只建一个，紧急退出模块和 `EmergencyFlow` 共用
- 启动和停止的顺序不变：紧急退出是巡检一轮里的一步，巡检停下它就停下
- 不加配置项：判定的频率是巡检的间隔，阈值在紧急退出和风控的配置里（spec「巡检里的紧急退出」）

### D11 请求量

| 时机 | Alpaca | moomoo |
| --- | --- | --- |
| 盘中每一轮（判定） | 读账户、读持仓 2 次 | 快照 1 次（没有持仓时 0 次） |
| 一只股票开始清仓 | 撤单：每笔订单 1 次加跟进 1 到 3 次；提交：对齐止损单 3 次（查止损单、查持仓、撤止损单） | — |
| 清仓单提交给券商（巡检的跟进） | 约 4 次 | — |

- 平时：巡检的预算（session-patrol design D7，8 只持仓、2 笔订单时每轮约 23 次）加 2 次，每分钟约 100 次，Alpaca 限制 200 次
- 熔断清 8 只持仓时，触发那一轮和下一轮各多三十几次，一分钟里可能接近 200 次：被限流时执行模块按券商不可用记为状态未知，30 秒后再查，不会越积越多（省略清单）。$2000 的资金通常只有 1 到 3 只持仓
- moomoo：每 30 秒 2 次，加上健康监控和下单流程，远低于每 30 秒 60 次

### D12 测试

- `tests/modules/risk/test_service.py`：`active_plans` 只有有效预留的方案；释放了的、全部成交的不在里面
- `tests/workflows/builders.py`（巡检的 `World`）：加上 `FakeQuoteSource`（默认没有行情）、紧急退出、健康监控、行情、`TradeServices`、`EmergencyFlow`，`FakeBroker` 的权益和前一交易日收盘时的权益都设为 10,000：巡检原有的测试不会触发紧急退出，只多一次读账户的请求。「没有持仓也没有订单」按 session-patrol 的 delta 改为断言第 1 到 3 步只读了一次全部持仓（读账户之前的请求只有这一次），另加「第 3 步之后的项」
- `tests/workflows/emergency_builders.py`：spec 的基准数据。AAA、BBB 的开仓订单直接写进订单存储并标为全部成交（成交日和止损价、紧急阈值），券商持仓和均价、权益在 `FakeBroker` 上设好，`execution.protect` 挂上 AAA-STOP-1、BBB-STOP-1 以后清掉调用记录；行情在 `FakeQuoteSource` 上设好；方案 P2 到 P7 的辅助函数
- 三个测试文件：
  - `tests/workflows/test_emergency_flow.py`：`holdings_of` 和 `judge`，直接调用
  - `tests/workflows/test_emergency_steps.py`：`advance`、`cancel_buys`、`report`、每一种告警文字、锁，直接调用，巡检第 1、2 步由测试手动做
  - `tests/workflows/test_emergency_rounds.py`：spec 的场景，调用 `world.patrol.run_round()`，拨 `FakeClock`、改替身的回报模拟「之后一轮」；"重启"用同一套存储和替身新建执行模块、风控、紧急退出、`EmergencyFlow` 和巡检，再做一次对账；锁的场景用另一个线程拿锁、用会阻塞的通知替身；告警看 `FakeNotifier`，日志用 `caplog`
- 巡检的 `World` 的行情源默认有 AAA、BBB、CCC 的行情，最新价等于前一交易日收盘价：没有行情时紧急退出每一轮都会写「行情源没有……」的 WARNING，巡检原有的测试里有只认一条 WARNING 的
- `tests/workflows/test_patrol_thread.py`：「做某一项时停止」，止损单替身在撤 AAA-STOP-1 时阻塞到测试放行
- `tests/bootstrap/test_app.py`：「盘中紧急清仓」：`FakeAlpaca` 持有 AAA，状态库里写好 AAA 全部成交的开仓订单（AAA-STOP-1 由启动对账挂上），`FakeOpenD` 加 AAA 的行情；装配用的巡检间隔改成 0.1 秒；用 `start` 和 `ExitStack` 启动，有超时地轮询 `FakeAlpaca` 收到撤止损单的请求和 `FakeGateway` 收到告警，再让止损单撤销、等到市价卖单。已有的 bootstrap 测试在盘中启动的，巡检第一轮会多读一次 Alpaca 账户（没有持仓时不取行情）：断言具体 GET 请求的测试按需改

## Risks / Trade-offs

- [session-patrol spec「每一轮」原来只写三步，场景「没有持仓也没有订单」写的是券商只收到一次读取全部持仓的请求，盘中现在还多一次读账户] → 本变更 MODIFY「每一轮」：写明第 3 步之后还有别的 spec 的项（紧急退出、健康判定，收盘对账最后），场景限定为第 1 到 3 步。「健康监控接线」「收盘对账」都把规则写在自己的 spec 里，不改这条要求
- [熔断只看券商给的权益：Alpaca 盘中用自己的价格算，和 moomoo 的行情可能有几秒的差别] → 熔断线是 7%，几秒的差别不影响；行情不可用时还能判定熔断，正是因为不依赖 moomoo
- [当天做到底：价格只是瞬间跌破阈值（比如一笔异常成交）也会清仓] → 和券商端止损单一样；阈值由 PM 按波动设，范围 3% 到 20%
- [错开中的备用止损：撤掉买单以后挂回、又撤掉止损单] → 多两次请求；省掉要改执行模块，不值得（D4）
- [清仓的提交紧接在巡检对齐同一只股票之后] → 「改单未生效时不再改单」先 apply：改单在等待生效时执行模块不再改单、也不撤单，不会误标无保护仓位；止损单晚一轮撤掉，清仓晚 15 秒
- [紧急退出出错（比如数据库出错）时这只股票这一轮没处理] → 巡检的 `_item` 只跳过这一只，下一轮再做；券商端止损单一直在
- [以后的变更往每一轮里加步骤，改变紧急退出的位置] → spec 写明了位置和理由（D1）

## Migration Plan

- 没有数据库迁移，没有新依赖
- 部署：更新代码、重启主程序；盘中的日志里每一轮多一次读账户，有触发时出现紧急退出的 INFO 和 WARNING
- 回滚：revert 代码；订单、预留都在状态库里，回滚以后巡检照常跟进已经建立的清仓单
