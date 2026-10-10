# Design

## Context

现状：

- `ExecutionService.reconcile`（reconciliation spec）读券商的全部挂单、止损单和持仓，跟进每笔未结束的订单、对齐要对齐的股票，找出三类不一致，把结果记为最近一次对账（只在内存里）。`submit` 在最近一次对账有不一致时拒绝买单。现在只有启动时调用一次
- `bootstrap/startup.py` 的启动对账只写日志；`TradingApp.reconciliation` 保留结果；模块说明写着告警由「收盘对账」「健康监控接线」补上
- 盘中巡检（add-session-patrol，代码已经合进 main，还没归档）：守护线程每 15 秒一轮，`run_round` 依次跟进订单、同步预留、对齐止损单；第 1 步跟进过订单的股票，第 3 步不再对齐（Alpaca 改单时用新订单替换旧单，替换完成以前不接受第二次改单，几毫秒内连续对齐两次会把这只股票误标为无保护，见它的 design D4）；每一项开始前看 `_stopping`，包在 `_item` 里出错只写日志；`TradingApp.lock` 是全程序一把 `RLock`；订单状态、买卖方向和止损单状态的中文名是 `patrol.py` 里的 `_ORDER_STATUSES`、`_SIDES`、`_STOP_STATUSES`
- 止损保护（`Protection`）：`used` 是用过的止损单序号，每挂一张新的止损单加 1，改单不变；`unprotected_since` 在同一段无保护里保持不变（stop-protection spec）。每次变化都在 `execution_protections` 里追加一个版本，写入时刻来自时钟
- `Order.end_missing`（对账时结束查不到的订单）得到的订单，和券商回报撤销、失效以后的订单字段一样，事后分不出是哪一种。平时跟进时，受理过却查不到的订单记为状态未知，巡检每 30 秒再查一次、再记一次
- `VersionedStore`（`modules/statestore`）只有保存、按键读最新版本、列出最新版本，没有读旧版本的方法
- `NotificationService.alert` 尽力而为：@ 配置的用户；发不出去时写错误日志（含文字和原因），不抛错；超过 2000 字截短
- `TradingCalendar` 有 `session_at`、`last_session_before`、`previous_session`
- 迁移已有 v0001、v0002

要求见 specs/close-reconciliation/spec.md、specs/bootstrap/spec.md、specs/state-store/spec.md，动机见 proposal.md 的 Why。

### 模块边界

```
modules/execution/
  domain/close.py   (新) LeftoverOrder、NewStop、UnprotectedSpell、CloseReconciliation；
                         close_day、close_window_start、leftover_orders、new_stops、
                         unprotected_spells、close_streak
  ports.py          (改) 新增 CloseReconciliationStore；ProtectionStore 加只读的 all、history
  repo.py           (改) 新表 execution_close_reconciliations 和 PostgresCloseReconciliationStore；
                         PostgresProtectionStore 加 all、history
  service.py        (改) ExecutionService 加构造参数 closes，加 close_due、close_reconcile
  config.py         (改) ExecutionConfig 加 close_reconcile_delay
  __init__.py       (改) 导出新名字
modules/statestore/repo.py   (改) VersionedStore 加只读的 history
bootstrap/migrations/versions/v0003_create_close_reconciliations.py  (新)
workflows/
  names.py          (新) 订单状态、买卖方向、止损单状态的中文名：从 patrol.py 移过来，两边共用
  reconciliation.py (新) close_reconcile_if_due、close_text、startup_text
  patrol.py         (改) run_round 最后加「收盘对账」一项；中文名改从 names.py import
bootstrap/app.py    (改) 装配收盘对账记录的存储；Discord 连上以后按启动对账的结果推送告警
bootstrap/startup.py (改) 只改模块说明
```

- `workflows/reconciliation.py` 只 import 两个模块的包根：`execution`（`ExecutionService`、`BrokerUnavailableError`、`CloseReconciliation`、`Reconciliation` 和差异的几个 dataclass）、`notification`（`NotificationService`）
- 不新增跨模块的 Pydantic 模型，contracts 不改。workflows 拿到的是执行模块导出的 frozen dataclass（`CloseReconciliation`、`Reconciliation`）和交易日（`date`）
- 执行模块已有的方法（`reconcile`、`track`、`protect`、`submit` 等）一个都不改；新加的是一个改变状态的用例 `close_reconcile`（只写自己的新表，加上它调用的 `reconcile`）和只读的 `close_due`、存储的只读查询
- 纯区域的白名单不变：`domain/close.py` 只用 dataclasses、datetime、collections.abc、uuid 和项目内已经允许的包

## Goals / Non-Goals

**Goals:**

- 差异、时机、连续天数都是纯函数，单元测试不用线程、不用数据库
- 收盘对账只在巡检线程里做，和巡检共用一把锁；重启以后靠巡检的第一轮自然补做，不另写补做的代码
- 不改变执行模块已有的任何行为，启动对账、以后的降级恢复对账照旧调用 `reconcile`

**Non-Goals:**

按「编程风格」省略的情况：

- 同一轮里巡检刚改过单的股票，收盘对账几毫秒以后再对齐，可能碰上 Alpaca 还在替换（add-session-patrol 的 design D4），第二次改单被拒、误标为无保护：收盘加延迟以后订单都已结束、持仓不再变化，巡检这一轮不会改单；只在收盘前后券商长时间不可用、这一轮才补上成交，或者刚重启时出现
- 巡检刚把查不到的订单或止损单记为状态未知时，收盘对账（一轮的最后一项）总是还没满等待时长，不会结束它：这种订单记为收盘时没结束的订单；Alpaca 按客户端订单 ID 总能查到受理过的订单，几乎不出现，出现时靠重启以后的启动对账结束
- 对账时改单的止损单不算差异（已定的差异定义里没有）
- 对账以后、保存记录以前出错（数据库出错）：对账已经补上的差异在下一轮重做时看不到，那一天记为没有这些差异；ERROR 日志里有
- 保存记录以后、推送以前进程退出：这一天没有结果消息
- 结果消息发不出去时不重发
- 没有差异的结果也用告警推送（会 @ 用户）：一天一条，不另加不 @ 的消息类型
- 券商长时间不可用时，每一轮写一条 WARNING：不合并
- 启动对账补上的变化不算差异；启动对账的结果不写进收盘对账记录
- 上一个交易日的收盘对账晚做时，两个交易日的无保护时间段有重叠，重叠里的无保护两天都算
- 收盘对账的延迟设得比收盘到下一个交易日开盘还长时，永远不会到期：不检查
- 记录里只有有持仓的股票数，不存持仓明细
- 每一轮都判断一次是否到期，盘外每一轮按键读一次状态库：不在内存里缓存
- 交易日历加载范围（730 天）以外：不处理

## Decisions

### D1 收盘对账的用例放在执行模块

`ExecutionService.close_reconcile(day)` 做完整的一次收盘对账：读持仓、记下对账以前的情况、调用 `self.reconcile()`、找差异、算连续天数、保存记录。workflows 只负责什么时候调用、拿锁、推送结果。

- Proposal 的「每日对账」是执行模块的事（「订单生命周期」）；收盘对账的记录只有一个写入者，和订单、止损保护、交易记录一样属于执行模块，表名以 `execution_` 开头（state-store spec）
- 差异要比较对账前后的订单和止损保护，要读止损保护的历史、要知道对账以前是否暂停开仓（`self._last`）。在执行模块里直接读自己的存储；放到外面要为这些各加一个公开的查询
- `reconcile` 原样调用，不改它的返回：启动对账和以后「健康监控接线」的恢复对账照旧

考虑过：

- 比对放在 workflows，执行模块只加只读查询：表仍然要属于一个模块，写记录的方法仍然要加在执行模块，比对的逻辑分散在两处，公开的查询多 4 个
- 让 `reconcile` 多返回补挂的止损单、结束的订单：改变已有用例的返回，启动对账和恢复对账都受影响
- 新开一个模块：不对应架构图里的节点

### D2 由巡检每一轮的最后一项触发

```python
def run_round(self) -> None:
    """做一轮：跟进订单、同步预留、对齐止损单，最后是收盘对账。"""
    tracked = self._track_orders()
    self._sync_reservations()
    self._align_stops(tracked)
    self._close_reconcile()
```

`_close_reconcile` 和别的项一样：先看 `_stopping`，包在 `_item("收盘对账")` 里，调用 `close_reconcile_if_due(execution, notification, lock)`（D9）。巡检的构造参数不变：它已经有执行模块、通知和锁。

- 放在巡检里：重启以后巡检开始就是第一轮，到期没做的立即补做，不用另写启动时的补做；和巡检在同一个线程里，收盘对账和巡检的各项自然不会同时进行
- 放在一轮的最后：同一轮刚跟进过订单、对齐过止损单，收盘对账补上的就是巡检没能补上的。放在最前面的话，最近 15 秒里券商撤掉的止损单（比如 Alpaca 长期有效单 90 天到期）会被收盘对账重挂、算成差异
- 判断是否到期（`close_due`）不拿锁：只读日历和状态库；收盘对账表只有 `close_reconcile` 写，而它只在巡检线程里调用

考虑过：

- DBOS 定时工作流（cron）：cron 表达不了半日市和节假日，判断还是要按日历再算一遍；重启以后错过的那一次要另写补做；还要另外协调锁
- 单独的线程：多一个线程的开始和停止，换不来什么

### D3 时机：收盘加延迟到下一个交易日开盘

```python
def close_day(calendar: TradingCalendar, now: datetime, delay: timedelta) -> date | None:
    """now 该做哪个交易日的收盘对账，不看做没做过，见 close-reconciliation spec「收盘对账的时机」。"""
    if calendar.session_at(now) is not None:
        return None
    last = calendar.last_session_before(now)
    return last.day if now >= last.close + delay else None


def close_window_start(calendar: TradingCalendar, day: date, delay: timedelta) -> datetime:
    """day 的「当天出现过的无保护仓位」从这个时刻算起：上一个交易日收盘加 delay。"""
    return calendar.previous_session(day).close + delay
```

`ExecutionService.close_due()` 用时钟的现在和配置的延迟算出 `close_day`，再看 `self._closes.get(day)`：有记录时为 None。

- 不在盘中就一定在 D 收盘和下一个交易日开盘之间，所以「下一个交易日开盘以前」不用另外判断；周末、节假日自然包括在内
- 开盘以后不再补做：那时的对账反映的是新一天的交易，算不了前一天
- 延迟默认 10 分钟（spec「收盘对账配置」）：收盘截止的订单在收盘后由巡检请求撤单，一两分钟内结束
- 延迟放在执行配置：时机是「每日对账」规则的一部分，`close_due` 在执行模块里

考虑过：

- 等到没有未结束的订单再做：状态未知的订单可能一直不结束，这一天就永远不做
- 时机放在 workflows 的配置里：`close_due` 要多一个参数，巡检还要知道时钟和日历

### D4 差异怎么算

```python
def close_reconcile(self, day: date) -> CloseReconciliation:
    """做 day 的收盘对账并保存记录，见 close-reconciliation spec「收盘对账的步骤」。

    调用方拿着锁。已经有记录时直接返回它，不调用券商。读券商持仓、对账第 1 步超时或断线时
    抛 BrokerUnavailableError，不保存记录。
    """
    done = self._closes.get(day)
    if done is not None:
        return done
    holdings = len(self._broker.all_positions())
    orders = self._store.open_orders()
    used = {p.symbol: p.used for p in self._protections.all()}
    paused = self._last is not None and not self._last.clean
    result = self.reconcile()
    after = {o.plan_id: self._store.get(o.plan_id) for o in orders}
    since = close_window_start(self._calendar, day, self._config.close_reconcile_delay)
    record = CloseReconciliation(
        day=day,
        finished_at=self._clock.now(),
        result=result,
        leftover_orders=leftover_orders(orders, after),
        new_stops=new_stops(used, self._protections.all()),
        unprotected=unprotected_spells(self._protections.history(since)),
        holdings=holdings,
        resumed=paused and result.clean,
        streak=0,
    )
    previous = self._closes.get(self._calendar.previous_session(day).day)
    record = replace(record, streak=close_streak(record, previous))
    self._closes.save(record)
    return record
```

（示意；`after` 的值类型在实现里按 `Order` 处理，订单不会被删除。）

- 读持仓、记下订单和止损保护、看暂停状态都在对账以前：对账以前出错时什么都没改，下一轮可以原样重做
- 收盘时没结束的订单：对账以前还没结束的每一笔，不论对账有没有结束它。不只记「券商查不到而结束的」，原因有两个：`end_missing` 的结果和券商回报撤销的结果分不出来；而且巡检每 30 秒把查不到的订单重新记为状态未知，收盘对账在一轮的最后，这样的订单总是没满等待时长，对账不会结束它，只看「对账结束的」就会漏掉它。收盘加 10 分钟以后，截止时刻不晚于收盘的订单正常都已经结束，还没结束就说明这一天的订单没有了结，算差异合理。这比已定的「因为券商查不到而结束的订单」宽，包含它
- 对账补挂或重挂的止损单：对账以后 `used` 比对账以前大的股票（以前没有记录的算 0）。挂新的止损单一定让 `used` 加 1，改单不变，所以只看它就够
- 当天出现过的无保护仓位：读止损保护表从 `close_window_start` 起的历史（D7），每个版本的 `(symbol, unprotected_since)` 去重。同一段无保护的开始时刻不变，所以每段一条；时间段开始时已经在无保护的，由「这个时刻的版本」带进来；开始以前已经结束的，那个时刻的版本已经不是无保护，不会带进来
- 时间段从上一个交易日「收盘加延迟」算起，只看日历，不看上一个交易日实际什么时候做的收盘对账：没做、晚做都不影响这一天怎么算
- 只有对账自己补上的才算：巡检在同一轮先补上的，对账以前 `used` 已经变了；启动对账补上的，发生在收盘对账以前

```python
def leftover_orders(
    before: Iterable[Order], after: Mapping[UUID, Order]
) -> tuple[LeftoverOrder, ...]:
    """对账以前还没结束的每一笔订单，带上对账以后的状态，按创建的先后。"""


def new_stops(used: Mapping[str, int], after: Iterable[Protection]) -> tuple[NewStop, ...]:
    """对账以后用过的序号比 used 里大的股票（没有的算 0），按代码排序。"""


def unprotected_spells(history: Iterable[Protection]) -> tuple[UnprotectedSpell, ...]:
    """历史版本里出现过的每一段无保护（股票、开始的时刻），去重，按股票和开始的时刻排序。"""
```

### D5 连续天数随记录保存

```python
def close_streak(record: CloseReconciliation, previous: CloseReconciliation | None) -> int:
    """这个交易日的连续天数，见 close-reconciliation spec「连续天数」。

    previous 是上一个交易日的记录，没有时为 None。
    """
    if not record.counted:
        return 0
    return 1 if previous is None else previous.streak + 1
```

- `counted` = 没有差异并且 `holdings > 0`（proposal「需要人决定」第 1 条的推荐做法；人不同意时 `counted` 就是 `clean`，`holdings` 字段留着只用于消息）
- 存下来、只看上一个交易日的记录：记录写下以后不再改，连续天数也就不会变；不用每次从全部记录往回数，也不用列出记录的查询
- 「上一个交易日」用日历的 `previous_session`：周末、节假日不打断，没做收盘对账的交易日因为没有记录而清零
- 重启、降级不进入计算

### D6 记录和表

```python
@dataclass(frozen=True, slots=True)
class LeftoverOrder:
    """收盘对账开始时还没结束的订单；status 是对账以后的状态。"""

    plan_id: UUID
    symbol: str
    side: OrderSide
    status: OrderStatus


@dataclass(frozen=True, slots=True)
class NewStop:
    """收盘对账补挂或重挂了止损单的股票；client_order_id 是对账以后的当前止损单。"""

    symbol: str
    client_order_id: str


@dataclass(frozen=True, slots=True)
class UnprotectedSpell:
    """一段无保护：股票和无保护开始的时刻。"""

    symbol: str
    since: datetime


@dataclass(frozen=True, slots=True)
class CloseReconciliation:
    """一个交易日的收盘对账记录，见 close-reconciliation spec「收盘对账记录」。

    - result：这次对账的三类不一致
    - holdings：对账以前读到的、券商那边有持仓的股票数
    - resumed：对账以前最近一次完成的对账有不一致，这次没有
    """

    day: date
    finished_at: datetime
    result: Reconciliation
    leftover_orders: tuple[LeftoverOrder, ...]
    new_stops: tuple[NewStop, ...]
    unprotected: tuple[UnprotectedSpell, ...]
    holdings: int
    resumed: bool
    streak: int

    @property
    def clean(self) -> bool:
        """没有差异：三类不一致和另外三类都为空。"""
        return self.result.clean and not (
            self.leftover_orders or self.new_stops or self.unprotected
        )

    @property
    def counted(self) -> bool:
        """这一天计入连续天数：没有差异，收盘时有持仓。"""
        return self.clean and self.holdings > 0
```

表 `execution_close_reconciliations`，用 `versioned_table` 定义，和别的表一样只追加：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `day` | `date`，非空 | 键：交易日 |
| `version` | `integer`，非空 | 版本号 |
| `recorded_at` | `timestamptz`，非空 | 写入时刻 |
| `data` | `jsonb`，非空 | 整份 `CloseReconciliation` |

主键 `(day, version)`。没有筛选列：只按交易日读（是否做过、上一个交易日的连续天数）。正常每个交易日只有版本 1；`close_reconcile` 在已有记录时直接返回，不会写第二个版本。

迁移 `v0003_create_close_reconciliations.py`：`revision = "0003"`，`down_revision = "0002"`，按 README「数据库」一节写明每一列。另一个变更先占了 0003 时，apply 时顺延编号。

存储接口：

```python
class CloseReconciliationStore(Protocol):
    """收盘对账记录的存储：按交易日保存。数据库实现只追加不覆盖，见 state-store spec。"""

    def get(self, day: date) -> CloseReconciliation | None:
        """这个交易日的记录；没有时为 None。"""
        ...

    def save(self, record: CloseReconciliation) -> None:
        """保存这个交易日的记录。"""
        ...
```

`ExecutionService` 的构造参数在 `trades` 之后加 `closes: CloseReconciliationStore`。

### D7 读止损保护的历史

- `VersionedStore.history(since)`（state-store spec「读取一段时间里的版本」）：两部分合在一起，再按键、版本号排序。第一部分是写入时刻早于 `since` 的版本里每个键的最新一个（`DISTINCT ON` 键，按版本号倒序，要包成子查询再 `UNION ALL`，PostgreSQL 不允许带 `ORDER BY` 的查询直接参与 `UNION`）；第二部分是写入时刻不早于 `since` 的全部版本
- `ProtectionStore` 加两个只读查询：
  - `all()`：全部股票的最新记录，数据库实现是 `VersionedStore.latest()` 不带筛选
  - `history(since)`：数据库实现直接用上面的 `history`
- 内存实现（`tests/fakes/protections.py`）：构造参数加可选的时钟；有时钟时保存的内容和最新的不同才追加一个（写入时刻, 记录）；`history` 要求有时钟。已有的测试不传时钟，照旧
- `all()` 用来记下对账以前每只股票的 `used`：对账可能给没有止损保护记录、或者记录已经不需要对齐的股票挂新的止损单（比如有持仓、还没有止损单），只看 `active()` 会漏

考虑过：

- 巡检在内存里记下见过的无保护：重启就丢
- 另建一张表记每一段无保护：止损保护表的版本里已经有，再写一份就是第二个写入的地方
- 只看对账时的当前状态：白天出现、已经补挂上的无保护看不到

### D8 结果消息和启动对账告警

```python
def close_text(record: CloseReconciliation) -> str:
    """收盘对账的结果消息，见 close-reconciliation spec「结果消息」。"""


def startup_text(result: Reconciliation) -> str:
    """启动对账有不一致时的告警，见 bootstrap spec「启动对账」。"""
```

没有差异：

```
收盘对账 2026-10-14：没有差异，持仓 1 只，连续 3 个交易日无差异
```

有差异（每类差异一行或多行，没有的类不写）：

```
收盘对账 2026-10-14：有差异，连续天数清零
- 不认得的挂单：CCC，客户端订单 ID X1，券商订单号 B9
- 多出来的止损单：BBB，BBB-STOP-1（S1）、X2（S9）
- 来历不明的持仓：CCC 100 股
- 收盘时没结束的订单：BBB 买入，方案 <方案 ID>，对账以后是状态未知
- 对账补挂或重挂的止损单：BBB，BBB-STOP-2
- 当天出现过的无保护仓位：BBB，从 2026-10-14 15:00 UTC 起
已暂停开仓，之后的对账没有不一致时恢复
```

启动对账：

```
启动对账有不一致：
- 来历不明的持仓：CCC 100 股
已暂停开仓，之后的对账没有不一致时恢复
```

- 三类不一致的行由一个函数给出，`close_text` 和 `startup_text` 共用，以后「健康监控接线」恢复对账的告警也可以用
- 没有差异、收盘时没有持仓：`收盘对账 2026-10-14：没有差异，收盘时没有持仓，连续天数清零`
- 恢复开仓时末尾加 `没有不一致，已恢复开仓`
- 时刻写 UTC、到分钟，和巡检的无保护告警一样；方案 ID 写全
- 中文名：订单状态、买卖方向、止损单状态的对照从 `patrol.py` 移到 `workflows/names.py`，巡检和这里都从那里 import。放在 `reconciliation.py` 或留在 `patrol.py` 都不行：巡检要 import `reconciliation.py`，反过来就是循环
- 用告警推送：通知模块只有这一个推送接口；一天一条，@ 用户也合适（人要看连续天数）
- 日志：没有差异写 INFO（消息本身），有差异写 WARNING（消息全文）

### D9 触发、锁和失败

```python
def close_reconcile_if_due(
    execution: ExecutionService, notification: NotificationService, lock: threading.RLock
) -> None:
    """巡检每一轮的最后一项，见 close-reconciliation spec「巡检里的收盘对账」。"""
    day = execution.close_due()
    if day is None:
        return
    try:
        with lock:
            record = execution.close_reconcile(day)
    except BrokerUnavailableError as error:
        _log.warning("收盘对账 %s 没做成：券商不可用（%s），下一轮再做", day, error)
        return
    text = close_text(record)
    if record.clean:
        _log.info("%s", text)
    else:
        _log.warning("%s", text)
    notification.alert(text)
```

- 整个 `close_reconcile` 在一次拿锁里：对账以前的记录和对账以后的比较之间，别的调用方（`/plan` 的提交、以后的紧急退出）不能插进来改订单和止损单，比较才准
- 推送在锁外：Discord 最多等 10 秒
- 券商不可用只写 WARNING、不带异常栈，和巡检读持仓失败一样；别的异常不接，交给巡检的 `_item` 写 ERROR（带异常栈）
- 锁里拿着一次完整对账的时间（8 只持仓约 20 次券商请求，几秒；券商超时时可能几十秒）：收盘以后才做，这时 `/plan` 的方案本来也要等到开盘才下单

### D10 bootstrap 接线

- `_assemble`：`ExecutionService(..., PostgresCloseReconciliationStore(engine, clock), ...)`；`paper_execution` 给出的执行配置只打开错开，收盘对账的延迟用默认值
- `start`：「启动 Discord」这一步之后、「启动 DBOS」之前：

  ```python
  if not reconciliation.clean:
      parts.notification.alert(startup_text(reconciliation))
  ```

  不放进 Discord 那一步的 `step(...)` 里：`alert` 不会抛错，放在外面也不会把告警失败变成启动失败
- `startup.py` 的模块说明改为：启动对账的告警由 `app.py` 在 Discord 连上以后发出；健康的告警由「健康监控接线」补上
- bootstrap spec 只改「启动对账」。「模拟盘的装配」不改：执行模块那一行没有列出存储的每一张表，新表由 state-store spec 列出；「下单流程」也要改那条要求，少一处冲突

### D11 测试

- `tests/modules/statestore/test_repo.py`：`history` 的四个场景（连数据库）
- `tests/modules/execution/domain/test_close.py`：`close_day`（spec「收盘对账的时机」的表和场景，包括延迟的边界、半日市、周末、节假日、开盘以后）、`close_window_start`、`leftover_orders`、`new_stops`、`unprotected_spells`（去重、排序、时间段开始时已经无保护）、`close_streak` 和 `counted`；直接构造订单和止损保护
- `tests/modules/execution/test_repo.py`：收盘对账记录的保存、读取、原样读回、重启；止损保护的 `all`、`history`。内存实现和数据库实现都测，照已有的写法参数化
- `tests/modules/execution/test_close_service.py`：用 `tests/fakes/` 的券商、止损单、带时钟的内存止损保护存储和新的内存记录存储，覆盖 spec「收盘对账的步骤」「差异」「连续天数」「收盘对账记录」的场景，以及「收盘对账的失败」里执行模块那一层的场景（读持仓超时、读挂单断线、跟进超时）。"程序停止、重启"用同一套存储新建执行模块并先对账一次
- `tests/modules/execution/test_config.py`：延迟的默认值和非法值
- `tests/workflows/test_reconciliation.py`：用巡检的 `tests/workflows/builders.py`，直接调用 `run_round` 或 `close_reconcile_if_due`，覆盖「巡检里的收盘对账」（除了停止）、「结果消息」「收盘对账的失败」里巡检那一层的场景、「收盘对账的时机」里经过巡检的场景（周末补做、降级模式不清零），以及 `close_text`、`startup_text` 的每一种行；锁的场景用另一个线程拿锁、用会阻塞的通知替身
- `tests/workflows/test_patrol_thread.py`：停止时正在收盘对账（券商替身在读全部挂单时阻塞到测试放行）
- `tests/bootstrap/test_app.py`、`tests/bootstrap/test_database.py`：表的清单加上新表；启动对账的四个场景检查 `FakeGateway` 收到的告警，以及告警在「Discord 已连接」之后、「DBOS 已启动」之前；另加一个端到端：启动时刻 2026-10-14 20:15Z，巡检第一轮做 2026-10-14 的收盘对账，有超时地轮询状态库里的记录，Discord 收到结果

## Risks / Trade-offs

- [差异比已定的定义宽：收盘时没结束的订单不只是券商查不到而结束的] → 收盘加 10 分钟以后正常没有未结束的订单；出现了就是这一天的订单没有了结，值得人看；见 D4
- [锁里做一次完整对账，券商慢时拿锁几十秒] → 收盘以后才做；这时没有要立即下单的流程
- [券商一直不可用到第二天开盘，这一天没有记录、连续天数清零，而降级模式本来说好不清零] → 规则是「收盘后没有做对账就清零」；券商不可用一整夜很少见
- [对账以后、保存以前出错会丢掉差异] → 少见；ERROR 日志里有异常栈
- [每一轮都判断一次] → 盘中只算日历；盘外每一轮一次按键读，每晚约两三千次，PostgreSQL 不在乎
- [依赖巡检在运行] → 程序没运行就没有收盘对账，按规则清零；重启以后巡检的第一轮补做
- [收盘对账在巡检对齐以后几毫秒又对齐全部股票，同一轮里改过单的股票会碰上 Alpaca 不接受连续改单] → 收盘加延迟以后巡检正常不改单（见省略清单）；真碰上时是一条无保护告警和一天的差异，下一轮巡检补上

## Migration Plan

- 数据库：启动时自动升级到 0003，建出 `execution_close_reconciliations`；没有旧数据要迁移
- 部署：更新代码、重启主程序；下一个交易日收盘加 10 分钟以后 Discord 收到第一条收盘对账结果，连续天数从 1 开始
- 回滚：revert 代码时保留 `v0003` 迁移文件：数据库已经在 0003，代码里没有这个版本时 Alembic 找不到它，启动会失败。多出来的表旧代码不会用到
