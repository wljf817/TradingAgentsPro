# Design

## Context

现状（变更 5、6 之后）：

- `ExecutionService` 有 `submit`、`track`、`protect`、`backup_stop_due`
  - `track` 和 `protect` 都是先按客户端订单 ID 查询券商，再处理，可以重复调用
  - 重启后对每笔未结束的订单调一次 `track`，对每只持仓调一次 `protect`，停机期间的成交、订单结束、止损单被撤或到期都能补上
- 券商接口只能按单笔查询：`Broker` 有 `find`、`place`、`cancel`、`position(symbol)`；`StopBroker` 有 `find`、`place`、`modify`、`cancel`
- 受理过（有券商订单号）却查不到的订单和止损单，记为状态未知，重新计时，"等对账处理"。状态未知的订单会让这只股票暂停新订单（`Order.blocks_new_orders`），卖单也算
- `OrderStore` 有 `get`、`open_orders`、`of_symbol`、`save`；`ProtectionStore` 只有 `get`、`save`，没有办法列出全部记录
- 券商（2026-10-04 决定）：模拟盘用 Alpaca，实盘用 moomoo
  - Alpaca 有原生的客户端订单 ID；改单会生成一笔新订单，订单号随之改变
  - moomoo 原地改单；客户端订单 ID 只能写进备注
- 账户只给系统用（2026-10-04 人决定）：券商那边系统解释不了的挂单和持仓都算不一致

动机见 proposal.md 的 Why，要求见 specs/reconciliation/spec.md。

### 模块边界

只改执行模块。不新增也不修改跨模块传递的 Pydantic 模型。对账结果 `Reconciliation` 是执行模块的 frozen dataclass，由包根导出，给 M2 的 bootstrap、定时心跳和健康监控用来发告警。

```
modules/execution
  domain/reconciliation.py  新增：WorkingOrder、Listing、Reconciliation、NotReconciledError、
                            ReconcileMismatchError，以及 stray_orders、extra_stops、position_explained
  domain/order.py           Order 增加 end_missing
  ports.py                  Broker 增加 working_orders、positions；StopBroker 增加 working_stops；
                            ProtectionStore 增加 active
  service.py                ExecutionService 增加 reconcile；submit 增加两条检查；
                            内部的跟进和对齐可以接收 Listing
  __init__.py               导出新的名字
```

风控模块不受影响：用对账之后的订单状态调用"上报成交"和"释放"，仍由 M2 的下单工作流做。

## Goals / Non-Goals

**Goals:**

- 找不一致全部由纯函数完成，输入是券商的挂单、持仓和执行模块的记录；service 只负责读取、调用和保存
- 补上停机期间的变化，复用已有的 `track` 和 `protect`，不另写一套"以券商为准修正"的逻辑
- 不误报：只有执行模块操作券商时，对账结果总是没有不一致。性质测试检查这一条
- 两家券商列挂单的差别留在适配器里

**Non-Goals:**

按「编程风格」省略的情况：

- 受理过的订单按 ID 查不到，券商挂单里却有它（按 ID 查询失灵）：仍是状态未知，不再处理
- 受理过的止损单查不到，这只股票却还挂着别的止损单：仍是状态未知；那张止损单如果是多出来的，已经由"多出来的止损单"报出
- 执行模块没有在用的止损单，券商却挂着一张，而这只股票又有持仓：对齐时照常挂一张新的，券商那边会有两张止损单；靠告警由人处理，第二张卖单会被拒（见 Risks）
- 已经结束的订单，券商事后又报成交：不处理
- 持仓股数和订单成交的合计对不上（比如拆股）：不核对，止损目标本来就以券商持仓为准
- 当天已经结束、但不是执行模块的订单（比如已经成交的重复单）：只看挂着的订单；它带来的持仓变化由止损目标和"来历不明的持仓"兜住
- 停机期间券商报成交的订单，对账时恰好查询超时：按已有规则记为状态未知，之后跟进再补

## Decisions

### D1 对账的顺序：先读全量，再比对，再补，最后看持仓

```
reconcile()
  working = broker.working_orders()        任何一项超时或断线：抛 BrokerUnavailableError，
  stops   = stops.working_stops()          什么都没改，对账状态不变（D5）
  positions = broker.positions()
  |
  stray = stray_orders(working, store.open_orders())             D2
  extra = extra_stops(stops, 当前止损单在用的股票)                 D2
  |
  listing = Listing(挂着的券商订单号, 挂着止损单的股票)
  对每笔未结束的订单：_track(plan_id, listing)                     D4
  对 positions 里的股票 + protections.active() 的股票：
      _align(symbol, now, listing)                                 D4
  |
  unexplained = [(s, q) for s, q in positions if not position_explained(store.of_symbol(s))]   D3
  result = Reconciliation(stray, extra, unexplained)
  self._last = result                                               D5
  return result
```

顺序是有讲究的：

- **先读全量**：读失败时什么都没做，不会出现"补了一半、对账又没完成"的状态
- **先比对，再跟进和对齐**：对齐可能新挂一张止损单。如果先对齐再比对，券商那边那张执行模块以为已经结束、其实还挂着的止损单，会和新挂的那张凑成"一张"，看上去就一致了。比对用的是第 1 步读到的挂单和跟进之前的记录，两边都是对齐动手之前的样子
- **来历不明的持仓放在跟进之后**：停机期间成交的买单，跟进之前记录里的累计成交还是 0，先看持仓就会误报
- 跟进时 `track` 本来就会对齐这只股票；第 4 步对没有未结束订单的股票再对齐一次，已经对齐过的再对齐只多一次查询

考虑过：比对放在跟进和对齐之后，用最新的记录

- 没有采用：会漏掉上面说的那张孤儿止损单

### D2 怎样算执行模块的挂单

普通订单按 ID 比：

```python
def stray_orders(
    working: Iterable[WorkingOrder], open_orders: Iterable[Order]
) -> tuple[WorkingOrder, ...]:
    """券商挂着、不是执行模块的普通订单。

    是执行模块的：有一笔未结束的订单，客户端订单 ID 相同，并且它还没有券商订单号、或者券商订单号相同。
    """
```

- 普通订单不改单，券商订单号受理之后就不变，可以直接比
- "还没有券商订单号"这一条是给提交之后、保存结果之前进程退出的订单用的
- 同一个客户端订单 ID 的第二笔订单，券商订单号不同，会被报出来。moomoo 按备注找订单时，偶尔会重复下单，就是这种情况

止损单按每只股票的张数比：

```python
def extra_stops(
    working_stops: Iterable[WorkingOrder], in_use: Collection[str]
) -> tuple[WorkingOrder, ...]:
    """券商挂着的止损单里不一致的那些：一只股票挂着多于一张，
    或者这只股票不在 in_use 里（执行模块没有在用的止损单）却挂着一张。返回这些股票挂着的全部止损单。

    in_use：当前止损单还没挂出去、挂着或状态未知的股票。
    """
```

- 不按 ID 比，是因为 Alpaca 改单会生成订单号不同的新订单。记录里的券商订单号要等下一次 `find` 才会更新，改单请求发出、还没再查询的时候，按 ID 比就会把执行模块自己的止损单当成多出来的
- 账户只给系统用，一只股票挂着两张止损单一定是出了问题，不需要分辨哪张是谁的

考虑过：发现多出来的挂单就撤销

- 没有采用：止损单按 ID 认不准，可能撤掉执行模块自己的止损单，让持仓失去保护；这些情况也很少见。只告警并暂停开仓，由人在 App 里处理

### D3 来历不明的持仓

```python
def position_explained(orders: Iterable[Order]) -> bool:
    """这只股票的持仓有来历：有累计成交大于 0 的买单，或者有未结束的买单。"""
```

- 账户只给系统用，所以持仓只能来自执行模块的买单
- 未结束的买单也算来历，因为它可能已经成交了、只是还没查到，比如提交超时以后还在等待时长里。只看"有没有止损价"的话，这种情况会被误报，开仓就会暂停到下一次对账
- 一股没成交就结束的买单不算来历。D4 结束查不到的订单时，累计成交保留已经记下的数，如果它其实成交过，这里会报出来

### D4 结束查不到的订单和止损单：用第 1 步读到的挂单做证据

```python
@dataclass(frozen=True, slots=True)
class Listing:
    """对账第 1 步读到的券商挂单：挂着的普通订单的券商订单号，挂着止损单的股票。"""

    order_ids: frozenset[str]
    stop_symbols: frozenset[str]
```

`track` 内部改为 `_track(plan_id, listing: Listing | None)`，对外的 `track(plan_id)` 传 `None`。`_advance` 和 `_align` 跟着接收 `listing`，只改"受理过却查不到"这一个分支：

```
订单：查不到，有券商订单号
  listing 为 None，或者 listing.order_ids 里有它的券商订单号  --> mark_unknown（不变）
  否则                                                          --> end_missing(now)

止损单：查不到，有券商订单号
  listing 为 None，或者 listing.stop_symbols 里有这只股票     --> not_found（不变，状态未知）
  否则                                                          --> end()，之后照常对齐，挂序号加 1 的新止损单
```

```python
def end_missing(self, now: datetime) -> Self:  # Order
    """受理过、查不到、券商挂单里也没有：当前时刻早于截止时刻记为已撤销，否则记为已过期；
    累计成交和成交均价保留已经记下的。"""
```

- 结束的依据是"这次查询查不到，并且全量挂单里也没有"。查询超时不算查不到，仍按原有规则记为状态未知。不能用"状态未知、挂单里没有"作为依据：查询超时以后订单如果已经成交，就不再挂着，误判成结束会丢掉成交
- 订单不在挂单里，说明它已经成交、被撤或失效，不会再成交，也不会再占着这只股票。结束它是安全的，不会重复下单
- 记为已撤销还是已过期，用订单原有的规则：以截止时刻为界。成交只能保留已经记下的，见 D3 和 Risks
- 状态未知、还没满等待时长的订单和止损单，`track` 和 `_align` 本来就不调用券商，对账时也一样
- 平时的跟进不传 `listing`，行为不变

考虑过：一直保持状态未知，等人处理

- 没有采用：状态未知的订单会让这只股票永远不能下新订单，变更 8 的紧急退出也会被挡住；而且人也没有工具处理

考虑过：只在收盘后结束，因为当日单收盘一定已经失效

- 没有采用：止损单是长期有效的，这条推不出来；用挂单做证据，订单和止损单可以用同一条规则

### D5 对账状态放在执行模块实例的内存里

```python
self._last: Reconciliation | None = None  # 最近一次完成的对账；新建以后还没有
```

`submit` 新建订单之前，在已有的检查之前先做两条检查：

```
self._last is None                                  --> NotReconciledError
plan.action.is_opening and not self._last.clean     --> ReconcileMismatchError
```

- 不需要存储：重启以后一定要先对账，结果会重新算出来
- 对账失败时 `_last` 不变：还没对过账的仍然不接受新订单；有不一致的仍然暂停开仓
- 已经有订单的方案再次提交、`track`、`protect`、`backup_stop_due` 都不检查。重启以后，下单工作流可以照常跟进已有的订单
- `NotReconciledError` 由 M2 的工作流像 `OrderPausedError` 一样处理：等对账完成，再从下单前再校验开始。`ReconcileMismatchError` 像 `UnprotectedPositionError` 一样处理：方案作废

考虑过：把对账结果存起来，重启以后沿用

- 没有采用：重启以后反正要先对账，存下来的结果用不上

考虑过：暂停开仓放在风控里

- 没有采用：一个变更只改一个节点；而且风控要多一项输入。和无保护时禁止加仓一样，放在执行模块的最后一道关口

### D6 接口

```python
@dataclass(frozen=True, slots=True)
class WorkingOrder:  # domain/reconciliation.py
    """券商那边一笔挂着（已受理、未结束）的订单或止损单。"""

    symbol: str
    client_order_id: str
    broker_order_id: str


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """一次对账的结果：三类不一致。"""

    stray_orders: tuple[WorkingOrder, ...] = ()  # 不认得的挂单
    extra_stops: tuple[WorkingOrder, ...] = ()  # 多出来的止损单：不一致的股票挂着的全部止损单
    unexplained: tuple[tuple[str, int], ...] = ()  # 来历不明的持仓：（股票, 券商持仓股数）

    @property
    def clean(self) -> bool:
        """没有不一致：三类都为空。"""


class NotReconciledError(RuntimeError):
    """执行模块新建以后还没完成过对账，不能为方案新建订单。"""


class ReconcileMismatchError(RuntimeError):
    """最近一次对账有不一致，暂停开仓：不能为买入、加仓方案新建订单。"""


class Broker(Protocol):
    ...  # find、place、cancel、position 不变

    def working_orders(self) -> tuple[WorkingOrder, ...]:
        """券商那边全部挂着的订单，不含止损单；包括部分成交、正在撤单的。"""

    def positions(self) -> Mapping[str, int]:
        """券商那边的全部持仓：股票到股数，只含股数大于 0 的。"""


class StopBroker(Protocol):
    ...  # find、place、modify、cancel 不变

    def working_stops(self) -> tuple[WorkingOrder, ...]:
        """券商那边全部挂着的止损单。改单还在替换过程中时，新旧两笔只算一张。"""


class ProtectionStore(Protocol):
    ...  # get、save 不变

    def active(self) -> tuple[Protection, ...]:
        """需要对齐的记录：当前止损单还没结束（还没挂出去、挂着、状态未知），或者是无保护仓位。"""
```

- 超时或断线都抛 `BrokerUnavailableError`，和已有操作一样
- `ExecutionService.reconcile() -> Reconciliation`
- 三个读取放在各自的接口里，和止损单单独成一个接口的理由一样：两个测试替身各管各的。实际的适配器可以一次调用拿到全部挂单，再按类型分开
- `ProtectionStore.active` 只返回需要对齐的记录，不返回全部：记录一直保留，做过的股票会越来越多，对齐每一只都要查询一次持仓
- 阈值：没有新的配置项

### D7 测试

```
tests/fakes/broker.py         FakeBroker 增加 working_orders、positions；add_working 安排券商另外挂着的单；
                              lose 增加 listed 参数：查不到时挂单里是否还有它，默认没有
tests/fakes/stops.py          FakeStopBroker 增加 working_stops、add_working；lose 同上
tests/fakes/protections.py    InMemoryProtectionStore 增加 active
tests/modules/execution/builders.py
                              execution_service 增加 reconciled 参数，默认 True：新建以后先对账一次，
                              再清掉替身的调用记录；重启的场景按需要传 False
tests/modules/execution/domain/test_order.py           end_missing
tests/modules/execution/domain/test_reconciliation.py  stray_orders 表的每一行、extra_stops 表的每一行、
                                                       position_explained、Reconciliation.clean
tests/modules/execution/test_reconciliation_service.py reconciliation spec 的场景
tests/modules/execution/test_service.py                order-lifecycle spec 修改后的「提交订单」；
                                                       状态机里的重启之后先对账，对账结果没有不一致
tests/modules/execution/test_protection_service.py     状态机增加"对账"这一步，重启之后先对账；
                                                       每次完成的对账都没有不一致
```

- 新的读取都记入替身的 `calls`，可以用 `fail_next` 安排超时或断线，和已有操作一致
- 已有的两个状态机性质测试里，只有执行模块在操作券商，所以对账结果必须没有不一致。这条直接检查"不误报"。止损保护的状态机会交错止损单触发、改单生效、挂单超时和被拒，最容易测出误报
- 对账读取失败的情况也放进状态机：失败后 `submit` 抛 `NotReconciledError`，按 `OrderPausedError` 的方式跳过

## Risks / Trade-offs

- [结束查不到的订单时，它其实已经成交，累计成交少记] → 持仓以券商为准，止损目标不受影响；如果是这只股票唯一的买单，会被报成来历不明的持仓，告警并暂停开仓。用 Alpaca 时，受理过的订单查不到几乎不会发生
- [执行模块没有在用的止损单，券商却挂着一张，这只股票又有持仓：对齐时会再挂一张] → 告警并暂停开仓，由人在 App 里撤掉多出来的那张。两张都触发时，第二张卖单因为可卖股数不够被拒；M2 要把 Alpaca 模拟盘账户的做空关掉，避免它变成做空
- [Alpaca 改单还在替换过程中时，列表里新旧两笔可能都挂着] → 写进 `working_stops` 的约定：只算一张。M2 接入时实测
- [对账和下单工作流同时处理同一笔订单] → 已有约定：同一笔订单一次只能有一个调用在处理，由调用方保证。M2 让执行模块的调用串行
- [有不一致时开仓一直暂停，直到下一次对账] → 对账可以随时再跑，人处理完以后就能恢复，不用等到收盘
- [moomoo 查询订单列表限频：每 30 秒 10 次] → 一次对账只读一次全量；逐笔跟进的频率在 M7 按限频安排

## Migration Plan

只新增代码和接口方法。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- 下面几项只影响适配器，不影响本变更的 spec 和任务，接入时实测：
  - Alpaca：`GET /v2/orders?status=open` 里止损单怎样和普通订单区分；改单替换过程中，旧单和新单在列表里怎样出现
  - moomoo：订单列表里挂着的长期有效单，是不是不管哪天下的都会列出来
