# Design

## Context

现状：

- `bootstrap/app.py` 的 `run` 在 `start` 之后只是 `stop.wait()`；`start` 的说明已经留好「launch 之后开始盘中巡检」。`workflows/` 只有说明
- `ExecutionService`：`track(plan_id)` 跟进一笔订单并在之后对齐这只股票，`protect(symbol)` 单独对齐（说明写着「给定时任务调用」），`cancel_orders`、`cancel_buy_orders`、`reconcile` 都可以重复调用；类的说明写明「同一笔订单一次只能有一个调用在处理，由调用方保证」。没有列出未结束订单、按方案取订单、列出要对齐的股票的公开方法，这些只在存储接口上（`OrderStore.open_orders`、`get`，`ProtectionStore.active`）
- `RiskService`：`record_fill`、`release` 可以重复调用，已结束的预留不再变化；说明写明「一次只能检查或再校验一个方案，M2 由调用方保证串行」。没有列出有效预留的公开方法（存储接口上有 `active`）
- `NotificationService.alert(text)` 尽力而为：Discord 发不出去时只写错误日志、不抛错；一次最多等发送超时（默认 10 秒）；可以从任何线程调用
- Alpaca：每次请求超时 10 秒，每分钟最多 200 次；超时、断线、429、5xx 都是 `BrokerUnavailableError`，执行模块在跟进和对齐里把它记为状态未知、不抛出；401 等其他错误原样抛出。查止损单要沿改单产生的替换链每一跳查一次
- 状态库的写入在内容和最新版本相同时跳过（state-store），所以重复的 `record_fill`、`release` 不会多写版本
- 测试替身：`tests/fakes/` 的券商、止损单、订单、止损保护、交易记录、预留存储和通知通道，`tests/modules/execution/builders.py` 的基准数据

要求见 specs/session-patrol/spec.md 和 specs/bootstrap/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
workflows/
  __init__.py     (改) 只改说明：流程包括 DBOS 工作流和盘中巡检这样的进程内循环
  patrol.py       (新) PatrolConfig、SessionPatrol、unprotected_text

modules/execution/service.py   (改) 只读：open_orders、order、protection、symbols_to_protect
modules/risk/service.py        (改) 只读：active_plan_ids

bootstrap/app.py  (改) 装配时建锁和巡检；start 加第 10 步；TradingApp 加 lock
README.md         (改) 目录分层里 workflows 一行
```

- `workflows/patrol.py` 只 import 三个模块的包根：`execution`（`ExecutionService`、`BrokerUnavailableError`、`Order`、`Protection`、`StopOrder`）、`risk`（`RiskService`）、`notification`（`NotificationService`）。不 import integrations、bootstrap
- 不新增跨模块的 Pydantic 模型，contracts 不改。巡检拿到和传出的是执行模块已经导出的 dataclass（`Order`、`Protection`、`StopOrder`）、方案 ID（`UUID`）和股票代码；`PatrolConfig` 是 workflows 自己的配置模型
- 执行模块和风控只加只读的方法，行为、包根的导出、架构测试的白名单都不变
- bootstrap 建锁、建巡检、启动和停止；以后的流程变更从 `TradingApp.lock` 取这把锁

## Goals / Non-Goals

**Goals:**

- 一轮是一个可以直接调用的同步方法：每一轮的规则用单元测试覆盖，不用线程、不用等时间
- 后面的「紧急退出流程」等在一轮里加步骤、拿同一把锁，不用改巡检的开始和停止
- 不改变执行模块和风控的任何行为

**Non-Goals:**

按「编程风格」省略的情况：

- 重启后还在继续的一段无保护再告警一次：告警记录不持久化
- 补挂成功、无保护解除时不发 Discord，只写 INFO 日志
- 无保护告警发不出去时不重发
- 补挂一直被拒时每一轮都挂一张序号加 1 的止损单，各留一条没有成交的交易记录：不退避
- 一轮里不限制券商请求的速率：靠间隔；限流（429）按券商不可用处理，下一轮再来
- 第 1 步跟进时因为状态未知还没满等待时长、没有对齐的股票，第 3 步也不对齐：最多晚 30 秒
- 券商长时间不可用时，每一轮、每一项各写一条日志：不合并
- 盘外不放慢：省下的请求不重要
- 巡检卡在没有超时的调用上（比如数据库主机不可达，要等 TCP 超时）时停止也会等：由 systemd 的停止超时处理
- 订单和止损单的变化只记巡检看到的：别的调用方（下单流程的提交、紧急撤单）里的变化由它们自己写，或者巡检在之后一轮看到时写
- 巡检的第一轮和启动对账有重复：不跳过
- 盘后改止损单被 Alpaca 拒绝（盘后挂出的止损单是 accepted 状态，不能改）时按挂单失败处理、会告警：盘后止损目标几乎不变

## Decisions

### D1 普通线程，不用 DBOS 定时工作流

巡检是一个守护线程：做一轮，`threading.Event.wait(间隔)`，再做一轮。

- 每一轮没有需要恢复的中间状态：执行模块和风控把每一次变化存进状态库，调用都可以重复。进程重启以后第一轮从状态库和券商接着做，DBOS 的断点恢复没有东西可恢复
- DBOS 的定时工作流每触发一次就是一条工作流记录：15 秒一次，每天 5760 条写进 `dbos` schema，备份和排查都多了噪音；工作流里的调用还要做成 step、结果要能序列化，换不来任何好处
- 停止的次序由我们控制：先停巡检、等正在做的那一项做完，再 destroy DBOS；做成工作流的话，destroy 不等工作流，停在哪一步不确定
- config 里「等待用 DBOS 的持久化休眠」针对工作流里要跨重启的等待（等确认、等开盘）。巡检的等待不需要跨重启，重启后立即做一轮；`Event.wait` 还能让停止立即打断等待。`time` 模块照样不用，巡检本身不读时间（「现在」都在模块里从 Clock 取）

考虑过：

- DBOS 定时工作流：见上
- asyncio 任务：模块全是同步的，Discord 的事件循环在它自己的线程里，没有可以挂靠的循环
- 每个方案的工作流自己跟进订单：已经定了由巡检跟进（proposal）；工作流要一直活到订单结束，状态未知、到期撤单都要在工作流里再写一遍

### D2 全天运行

巡检不看交易日历，每一轮都做。

- 收盘时刻截止的订单，过了截止时刻才请求撤单、等券商回报失效；只在盘中跑的话要到第二天开盘才结束，预留占着过夜，盘后形成的方案（Proposal 的「交易时段」：盘后财报照常开案件）会按少了的额度检查
- 长期有效的止损单可能在任何时刻被券商撤销（Alpaca 创建后 90 天自动撤销）：盘外补挂，开盘时已经挂好
- 盘外不会提交订单：订单的截止时刻是生效有效期和当天收盘中较早的那个，过了截止时刻跟进只会撤单或结束，不会提交。盘外挂、改的只有止损单，它只在常规交易时段触发
- 盘外大部分轮次没有任何变化，只是读：每轮 1 + 2 × 股票数次请求，不受限制

考虑过：

- 只在常规交易时段：见上面前两条
- 盘中 15 秒、盘外放慢：多一个配置项和日历判断，省下的请求没有用处

### D3 一把可重入锁，按项拿

```python
lock = threading.RLock()  # bootstrap 装配时建，全程序一把
```

- 拿锁的调用（spec「执行模块和风控的调用串行」）：执行模块的 `submit`、`track`、`cancel_orders`、`cancel_buy_orders`、`protect`、`reconcile`，风控的 `check`、`recheck`、`record_fill`、`release`。锁只在调用方（workflows、bootstrap）拿，模块内部不拿
- 只读的不拿：`account`、`backup_stop_due`，以及 D6 新加的五个查询。它们只读状态库和券商，和写入并发时读到的是写入之前或之后的样子，下一轮会再看
- 巡检每一项拿一次锁：跟进一笔订单、同步一个方案的预留（读订单、上报成交、释放在同一次里）、对齐一只股票。一轮在券商超时时可能要几十秒到几分钟，整轮拿着锁的话 `/plan` 的提交要等这么久；按项拿，别的调用方最多等一项
- 推送告警时不拿锁：Discord 最多等 10 秒
- 可重入（`RLock`）：以后的流程会把拿锁的小函数组合起来用（比如下单流程提交以后顺手同步这个方案的预留），同一个线程再次拿锁不会卡死；对不同线程之间的互斥没有区别，代价可以忽略
- 启动对账在巡检开始之前、只有一个线程，不拿锁

考虑过：

- 普通 `Lock`：同一个线程嵌套拿锁会永远卡住，出了这种错只能靠测试超时发现
- 每只股票一把锁：风控的检查要读全部预留，执行模块的对账要动全部股票，按股票分锁保护不了它们；单进程、调用很少，一把锁足够
- 整轮拿锁：见上

### D4 每一轮的三步

```python
def run_round(self) -> None:
    """做一轮：跟进订单、同步预留、对齐止损单，见 session-patrol spec「每一轮」。"""
    self._track_orders()
    self._sync_reservations()
    self._align_stops()
```

1. 跟进订单：`execution.open_orders()`（按创建的先后），每一笔 `with lock: execution.track(plan_id)`；前后的状态或累计成交不同就写日志（D9）
2. 同步预留：`risk.active_plan_ids()`，每一个方案在同一次拿锁里：

   ```python
   order = self._execution.order(plan_id)
   if order is not None:
       self._risk.record_fill(plan_id, order.filled)
       if order.status.is_terminal:
           self._risk.release(plan_id)
   ```

3. 对齐止损单：`execution.symbols_to_protect()`（D5），每一只：第 1 步跟进成功过订单的股票 `with lock: protection = execution.protection(symbol)`（只读，不再对齐），其余的 `with lock: protection = execution.protect(symbol)`；放开锁以后写止损单的日志（D9）、按无保护处理告警（D8）

同步预留按「有效预留 → 订单」来找，而不是只在第 1 步跟进到终态时释放：

- 订单还会在别处结束：提交时被券商拒绝（`submit` 里跟进一次就到终态，下单流程到这里就结束）、紧急撤单的那次跟进、对账、进程在保存订单之后释放预留之前退出、停机期间结束而由启动对账查到。这些订单不再是未结束的订单，第 1 步看不到它们
- 有效预留不多（最多几笔），每一轮全部看一遍只是几次状态库的读；重复的上报和释放结果不变，状态库也不多写
- risk-reservations spec「预留保存在存储里」要求停机期间的成交和订单结束「由重启后的对账通过上报成交和释放补上」：启动对账跟进了订单，巡检的第一轮把它们报给风控，正好补上

先跟进、再同步、最后对齐：同步用的是刚跟进过的累计成交。

第 1 步跟进过的股票第 3 步不再对齐（apply 时发现，见下）。`track` 跟进之后已经对齐过这只股票；几毫秒后再对齐一次，如果第一次发了改单，Alpaca 那边旧单正在被替换（pending_replace），查到的还是旧股数，执行模块按 stop-protection spec 再请求一次改单，而 Alpaca 不接受正在改单的订单再改单：被拒、立即重试再被拒，这只股票就被标为无保护仓位，还会触发告警。add-alpaca-paper-broker 的 design 已经接受了「几秒内连续改两次」这种少见情况，但巡检在跟进以后马上再对齐，会让每一次减仓、卖单结束、加仓成交（错开关闭时）都碰上它。不再对齐以后，同一只股票两次对齐至少隔一个间隔（15 秒），替换早已完成。这只股票这一轮的日志和告警按跟进时对齐的结果，用只读的 `protection(symbol)` 读出。

考虑过：

- 第 3 步放在第 1 步之前：有订单的股票照样在同一轮里先对齐、再跟进时对齐，问题不变
- 在执行模块里遇到「改单请求还在路上」就不再改：改变执行模块的行为，要单独的「修改」变更；巡检这边不再连续对齐就够了

考虑过：在第 1 步跟进以后直接上报和释放，另外在启动时补一次。漏掉提交时被拒、紧急撤单这些在别处结束的订单，还要再写一处启动时的补齐。

### D5 要对齐的股票包括券商的持仓

```python
def symbols_to_protect(self) -> tuple[str, ...]:
    """需要对齐止损单的股票，按代码排序：券商那边有持仓的，加上止损保护需要对齐的。

    和 reconcile 第 4 步的范围相同。读券商全部持仓超时或断线时抛 BrokerUnavailableError。
    """
    positions = self._broker.all_positions()
    return tuple(sorted(set(positions) | {p.symbol for p in self._protections.active()}))
```

只用 `ProtectionStore.active()`（当前止损单没结束、或者无保护）会漏掉一种情况：买单成交以后跟进时对齐，查询持仓断线（stop-protection spec「查询持仓失败时不做处理」），这只股票还没有止损保护的记录；订单已经是终态，之后不会再被跟进，这只股票就一直没有止损单，直到收盘对账。加上券商的持仓只多一次请求，范围也和对账一致。

读全部持仓失败时整个第 3 步跳过、写 WARNING，下一轮再来（spec「一轮里的失败」）。不退回到只对齐 `active()` 的股票：多一条分支，只为了券商不可用的这一轮，而这时对齐本来也会失败。

`reconcile` 的第 4 步不改：它在第 1 步已经读过一次持仓，两处共用读到的结果。

### D6 只读的查询

执行模块（`ExecutionService`）：

```python
def open_orders(self) -> tuple[Order, ...]:
    """全部没有进入终态的订单，按创建的先后；只读状态库，不调用券商。"""
    return tuple(sorted(self._store.open_orders(), key=lambda o: o.created_at))


def order(self, plan_id: UUID) -> Order | None:
    """这个方案的订单，包括已经结束的；没有订单时为 None。只读状态库。"""
    return self._store.get(plan_id)


def protection(self, symbol: str) -> Protection | None:
    """这只股票的止损保护；没有记录时为 None。只读状态库，不调用券商。"""
    return self._protections.get(symbol)


def symbols_to_protect(self) -> tuple[str, ...]: ...  # 见 D5
```

风控（`RiskService`）：

```python
def active_plan_ids(self) -> tuple[UUID, ...]:
    """有有效预留的方案：没有释放、剩余股数大于 0。只读存储。"""
    return tuple(r.plan.plan_id for r in self._store.active())
```

- 都是已经导出的类的方法，包根不变；不改任何已有方法
- 返回订单（`Order`）而不只是方案 ID：巡检要用它记日志的「变化前」，也省一次读
- 返回方案 ID 而不是 `Reservation`：巡检只需要知道是哪些方案，预留的内容留在风控里

### D7 间隔默认 15 秒

```python
class PatrolConfig(BaseModel):
    """盘中巡检的配置和默认值，含义见 session-patrol spec「巡检配置」。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 一轮做完以后等这么久再做下一轮
    interval: timedelta = Field(default=timedelta(seconds=15), gt=timedelta(0))
```

每一轮的 Alpaca 请求：

| 步骤 | 请求 |
| --- | --- |
| 跟进一笔未结束的订单 | 查订单 1 次，对齐时查止损单 1 次、查持仓 1 次；要撤单、挂单、改单时再多 1 次 |
| 同步预留 | 不调用券商 |
| 对齐 | 读全部持仓 1 次；没有在第 1 步跟进过的每只股票查止损单 1 次（改过单的，替换链每一跳多 1 次）、查持仓 1 次 |

合计不超过 1 + 2 × 股票数 + 3 × 订单数。最多 8 只持仓（风控默认的最多持仓数）、同时 2 笔订单时约 23 次；15 秒一轮、加上一轮本身的耗时，每分钟最多约 92 次，不到 Alpaca 200 次的一半，剩下的留给 `/plan` 读账户、下单流程的提交和以后的对账。按 $2000 的资金通常只有 1 到 3 只持仓，每分钟 20 到 40 次。

间隔也是买单成交以后挂上止损单、止损单被撤销以后补挂的最长延迟（错开打开时，成交以后到挂上止损单之间由「紧急退出流程」的本地备用止损盯着）。15 秒对波段交易足够；`/plan` 的回复和确认按钮不经过巡检，不受影响。

间隔从上一轮做完算起（做完再等），不是固定节拍：券商慢的时候轮次自然变稀，不会叠在一起，也不会追赶。

考虑过：5 秒（每分钟最多约 276 次，超过限制）、30 或 60 秒（成交后挂止损单要等更久，省下的请求用不上）。

### D8 无保护仓位的告警

```python
def unprotected_text(symbol: str, since: datetime) -> str:
    """无保护仓位的告警文字，见 session-patrol spec「无保护仓位的告警」。"""
    return (
        f"{symbol} 是无保护仓位（从 {since.astimezone(UTC):%Y-%m-%d %H:%M} UTC 起）："
        "止损单挂单或改单被券商拒绝。巡检每一轮都会重试补挂，补挂成功之前不能加仓"
    )
```

- 巡检在内存里记 `{股票: 告警过的那一段无保护的开始时刻}`。对齐一只股票以后：
  - 是无保护仓位，开始时刻和记下的不同（包括没有记下）：先记下，再推送
  - 是无保护仓位，开始时刻相同：什么都不做
  - 不是无保护仓位（或者没有止损保护的记录），而记下过：删掉，写 INFO
- 第 3 步做完以后，记下过但这一轮不在要对齐的股票里的（卖完、不再需要对齐）：删掉，写 INFO。对齐出错的股票在要对齐的股票里，保留记录，不会因为一次出错就再告警
- 一段无保护按开始时刻区分：stop-protection spec 规定已经无保护的保留原来的时刻，解除以后再无保护是新的时刻
- 在第 3 步才看：无保护不论在哪里产生（巡检的跟进、下单流程的提交、紧急撤单、对账），都会在之后一轮的第 3 步看到；这一轮跟进过的股票按跟进时对齐的结果（D4）
- 时刻写 UTC：和日志一致；workflows 不知道 Discord 的时间格式，告警文字是普通文本
- 不持久化：只有程序重启时还在继续的一段会再告警一次，正好提醒；持久化要一张表或一个字段，为少见情况不值得
- 发不出去：`NotificationService.alert` 已经写了错误日志（含文字）；不重发，和 Proposal「Discord 发不出去时只写程序日志」一致

### D9 订单和止损单变化的日志

- 订单：第 1 步拿着跟进之前列出的 `Order` 和跟进返回的 `Order`，状态或累计成交不同就写 INFO：`订单 BBB 买入 <方案 ID>：已提交 → 部分成交，累计成交 30 / 100`
- 止损单：巡检在内存里记每只股票上次看到的止损单的（客户端订单 ID、状态、股数、止损价），对齐以后不同就写 INFO：`止损单 BBB BBB-STOP-2：挂着，100 股，止损价 45`。巡检开始以后第一次看到的也写一条：重启以后日志里有每只持仓的止损单
- 只写 Python 日志（M4 的程序日志接上以前），不发 Discord。日志用状态的中文名，对照 `OrderStatus`、`StopStatus` 写在 `patrol.py` 里

### D10 失败处理

```python
@contextmanager
def _item(self, name: str) -> Iterator[None]:
    """一项：出错写 ERROR（带异常栈和这一项的名字），不往外抛。"""
    try:
        yield
    except Exception:
        _log.exception("盘中巡检：%s 出错", name)
```

- 每一项包在 `_item` 里：不论券商的 401、数据库出错还是意外的异常，都只跳过这一项。巡检是唯一在盘中维护止损单的东西，一只股票的坏数据不能让别的股票没人管
- 列出未结束的订单、有效预留出错：写 ERROR，跳过这一步，接着做下一步
- 读全部持仓的 `BrokerUnavailableError`：写 WARNING（不带异常栈），跳过第 3 步；别的异常按 ERROR
- 线程的循环外面再包一层：`run_round` 本身抛出的任何异常写 ERROR，照常等间隔做下一轮。线程只在停止时结束
- 券商超时、断线在执行模块里已经记为状态未知，不会抛到巡检
- 只接 `Exception`：`KeyboardInterrupt`、`SystemExit` 不接（信号已经由 cli 的处理函数接管，见 add-bootstrap-wiring 的 design D10）

### D11 开始和停止

```python
class SessionPatrol:
    """盘中巡检，见 session-patrol spec。

    run_round 做一轮，可以直接调用；start 在守护线程里循环，stop 停下并等线程结束。
    """

    def __init__(
        self,
        execution: ExecutionService,
        risk: RiskService,
        notification: NotificationService,
        lock: threading.RLock,
        config: PatrolConfig,
    ) -> None: ...

    def start(self) -> None:
        """开一个守护线程 session-patrol：立即做第一轮，之后每一轮做完等一个间隔。

        开始时写一条 INFO：盘中巡检已开始，间隔 15 秒。
        """

    def stop(self) -> None:
        """不再开始新的一项和新的一轮，等线程结束；没有开始过时什么都不做。"""

    def run_round(self) -> None: ...  # 见 D4
```

- 停止用一个 `threading.Event`：循环在等间隔时用 `event.wait(秒数)`，`stop` 一设置就醒；一轮里每一项开始之前看一眼，设置了就从 `run_round` 返回。正在做的那一项不打断：它在锁里、可能刚把请求发给券商，做完、保存以后再停
- `stop` 里 `join()` 不设超时：每一项的券商调用都有 10 秒超时，最多等一项。真卡住时由 systemd 的停止超时处理（省略清单）
- 守护线程：万一停止没有走到（比如 `run` 外面出了意外），也不会让进程退不出去
- 线程名 `session-patrol`：日志的每一行带线程名

### D12 bootstrap 接线

- `_assemble` 建 `threading.RLock()` 和 `SessionPatrol(execution, risk, notification, lock, PatrolConfig())`，放进 `_Parts`；巡检配置用默认值，和别的模块一样不读配置文件
- `start` 在「启动 DBOS」之后加一步：

  ```python
  with step("开始盘中巡检"):
      parts.patrol.start()
      on_stop(stack, "盘中巡检", parts.patrol.stop)
  ```

  「盘中巡检已开始」的日志由 `start` 自己写（它知道间隔）。`ExitStack` 按相反顺序关闭，巡检登记在最后，所以最先停：先停巡检，再 destroy DBOS、关 Discord、OpenD、数据库。巡检用到的 Discord 和数据库在它停下以后才关

- `TradingApp` 加 `lock: threading.RLock`：以后的流程变更从这里拿锁；`run` 不用
- 巡检本身不放进 `TradingApp`：以后的变更改巡检的代码，不是在外面调用它
- 模块说明里「launch 之后开始盘中巡检」这句改为已经做了

### D13 测试

- `tests/modules/execution/test_query_service.py`：`open_orders` 按创建的先后、只有未结束的、不调用券商；`order` 返回已经结束的订单、没有时为 None；`symbols_to_protect` 是持仓和需要对齐的止损保护的并集、按代码排序、读持仓失败时抛 `BrokerUnavailableError`
- `tests/modules/risk/test_service.py` 补 `active_plan_ids`：只有有效预留；释放了的、全部成交的不在里面
- `tests/workflows/builders.py`：用 `tests/fakes/` 的替身和 `tests/modules/execution/builders.py` 的基准数据建执行模块、风控、通知（`FakeNotifier`）和巡检；风控的预留直接写进 `InMemoryReservationStore`（`Reservation(plan, decision)`），不跑风控规则
- `tests/workflows/test_patrol.py`：直接调用 `run_round`，覆盖 spec「每一轮」「无保护仓位的告警」「订单和止损单变化的日志」「一轮里的失败」「执行模块和风控的调用串行」的场景，以及「巡检的开始、间隔和停止」里收盘以后、周末的场景；日志用 `caplog`；锁的场景用另一个线程拿锁、用会阻塞的通知替身
- `tests/workflows/test_patrol_thread.py`：线程的开始、间隔、停止。间隔用很短的值（比如 0.05 秒），用 `threading.Event` 在替身里同步，不按墙上时间断言具体的时刻：开始后不等间隔就做第一轮；一轮做完以后才开始等；等的时候停止立即返回；做某一项时停止，等这一项做完、不做下一项；`run_round` 抛出意外异常以后照常做下一轮；停止以后线程已经结束
- `tests/workflows/test_patrol.py` 另有 `PatrolConfig` 的默认值和非法值
- `tests/bootstrap/test_app.py`：正常启动、停止的日志顺序加上盘中巡检；`TradingApp.lock` 就是巡检用的锁；「启动以后巡检跟进停机前提交的订单」用 `start` 和 `ExitStack` 直接启动，轮询状态库里的预留直到释放（有超时），再关闭；「停止时巡检正在跟进订单」用 `FakeAlpaca` 让一次查询阻塞到测试放行
- 已有的 bootstrap 测试不依赖巡检做没做第一轮：基准数据里没有订单和持仓，第一轮只读一次持仓

## Risks / Trade-offs

- [巡检线程、Discord 的工作线程、以后的 DBOS 工作流同时用同一个 Alpaca 客户端和数据库连接池] → httpx 的 `Client` 和 SQLAlchemy 的 `Engine` 都可以多线程共用；改变状态的调用又都在锁里
- [锁里有券商调用，`/plan` 的提交最多要等巡检的一项，券商超时时约 30 秒] → 按项拿锁；券商超时的时候提交本来也会失败
- [一个线程拿着锁卡住，全部流程都停] → 每一个拿锁的调用都只做有超时的券商和数据库调用；拿着锁不推送告警
- [Alpaca 限流] → 默认间隔留了一半以上的余量；被限流时按券商不可用记为状态未知，30 秒后再查，不会越积越多
- [以后的调用方忘了拿锁] → 「下单流程」「紧急退出流程」的 spec 和测试要写明在锁里调用；本变更的 spec 列出了要拿锁的调用
- [盘外每一轮都读券商] → 每分钟最多几十次只读请求，Alpaca 不限总量

## Migration Plan

- 没有数据库迁移，没有新依赖
- 部署：更新代码、重启主程序；启动日志里出现「盘中巡检已开始」
- 回滚：revert 代码；预留和订单的状态都在状态库里，回滚以后由启动对账接着用
