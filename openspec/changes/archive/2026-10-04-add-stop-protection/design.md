# Design

## Context

现状（变更 5 和费用估算之后）：

- `modules/execution` 有订单记录 `Order`、券商接口 `Broker`（`find`、`place`、`cancel`）、订单存储 `OrderStore`（`get`、`open_orders`、`save`）和 `ExecutionService`（`submit`、`track`）。配置只有 `unknown_lookup_delay`（30 秒）
- `track` 的流程是先查询再处理：查到就按回报更新；没受理过又查不到，截止前提交、截止后记为已过期；到截止时刻请求撤单。任何券商调用超时或断线都记为状态未知
- 方案 `TradePlan` 已经有止损价：买入、加仓必填，减仓、清仓没有。订单还没有记下它
- 券商（2026-10-04 决定）：模拟盘用 Alpaca，实盘用 moomoo，两者实现同一套接口，只在 bootstrap 切换
  - 两家都支持长期有效（GTC）的止损单，触发后按市价卖出，只在常规交易时段触发
  - Alpaca：原生客户端订单 ID，必须唯一；改单会生成一笔新订单，旧订单记下被谁替换；GTC 单挂满 90 天自动撤销
  - moomoo：原地改单，订单号不变；客户端订单 ID 只能写进备注；GTC 能挂多久文档没写

动机见 proposal.md 的 Why。

### 模块边界

只改执行模块。模块之间传递的 Pydantic 模型还是 `TradePlan` 和 `RiskDecision`，都不改；执行模块从 `TradePlan.stop_price` 取止损价，写进 `Order`。

```
modules/execution
  domain/order.py       Order 增加 stop_price；new_order 填写
  domain/protection.py  新增：StopStatus、StopOrder、StopReport、Protection、StopTarget、StopAction、
                        UnprotectedPositionError，以及 stop_price_of、stop_target、next_action、
                        stop_yields、backup_stop_due
  ports.py              Broker 增加 position；新增 StopBroker、ProtectionStore；OrderStore 增加 of_symbol
  service.py            ExecutionService 增加 protect、backup_stop_due；track 增加让出检查和对齐；
                        submit 拒绝无保护股票的买单
  config.py             增加 stop_immediate_retries
  __init__.py           导出新的名字
```

风控模块不受影响。风控的可卖数量本来就不扣止损单占用的股数（risk-rules spec「减仓类规则」），由执行模块在卖出前让出。

## Goals / Non-Goals

**Goals:**

- 止损单该怎么变，全部由纯函数根据"当前止损单 + 止损目标"决定；service 只负责查询、调用券商和保存
- 和订单一样可以重复调用：中途退出后再对齐一次，不会挂出两张止损单
- 两家券商的差别（改单是否换订单号、客户端订单 ID 放在哪里）都留在适配器里

**Non-Goals:**

按「编程风格」省略的情况：

- 改小止损单被拒：标记无保护，卖单等到截止时刻过期；这期间持仓仍由原来的止损单保护
- 券商持仓没有对应的开仓订单（比如在券商 App 里手动买入）：没有止损价，不挂，由对账发现
- 券商那边同一只股票有两张止损单：由对账发现
- 止损单部分成交：持仓变少，目标随之变小，按对齐规则处理，不另外判断
- 改单请求还没反映到回报：下次对齐再发一次同样的改单，不记录"已经请求过"
- 止损价不低于当前价，导致挂单被拒：按挂单被拒处理，备用止损随后触发
- 盘后挂单或改单被券商拒绝：按挂单被拒处理，M7 实测 moomoo
- 一只股票有止损单、但券商持仓查询一直失败：不处理，由健康监控和降级模式处理（变更 9）

## Decisions

### D1 每只股票一条止损保护记录

整只股票只有一个止损价、一张止损单（Proposal「加仓」），而止损单要跨好几笔订单：买入、加仓、减仓，所以按股票记录，不挂在订单上。

```python
class StopStatus(StrEnum):
    PENDING = "pending"  # 还没挂出去：已经保存，还没确认券商收到
    WORKING = "working"  # 挂着：券商已受理、未结束
    UNKNOWN = "unknown"  # 状态未知
    ENDED = "ended"  # 已经结束：成交、撤销、失效或被拒


@dataclass(frozen=True, slots=True)
class StopOrder:
    symbol: str
    client_order_id: str  # 例如 BBB-STOP-1
    quantity: int  # 挂着时是券商回报的未成交股数；还没挂出去时是要挂的股数
    stop_price: Decimal
    status: StopStatus = StopStatus.PENDING
    broker_order_id: str | None = None
    unknown_since: datetime | None = None
    requested: int | None = None  # 最近一次改单请求的股数，回报的股数等于它时清掉


@dataclass(frozen=True, slots=True)
class Protection:
    symbol: str
    used: int = 0  # 已经用过的止损单序号；下一张是 used + 1
    stop: StopOrder | None = None
    unprotected_since: datetime | None = None
```

客户端订单 ID 是 `f"{symbol}-STOP-{序号}"`：

- 记录一直保留，持仓卖光也不删，所以序号只增不减，同一只股票的客户端订单 ID 不会重复。Alpaca 要求客户端订单 ID 唯一，这一点满足
- 和订单的客户端订单 ID（方案 ID 的 UUID 字符串）格式不同，不会撞上
- 股票代码不超过 10 个字符，整个 ID 远小于 moomoo 备注的 64 字节上限

考虑过：从 ID 生成端口取 UUID

- 没有采用：多一个端口；序号已经足够唯一，而且查日志时一眼能看出是哪只股票的第几张

### D2 止损价从订单历史推出，不单独记

```python
def stop_price_of(orders: Iterable[Order]) -> Decimal | None:
    """最近创建的、累计成交大于 0 的开仓订单的止损价；没有时为 None。"""
```

- 一只股票同一时间最多一个开仓方案（风控「未完成的开仓方案」规则），订单最多挂到当天收盘，所以开仓订单不会重叠，"最近创建"就是最新的那一笔
- 不记"止损价从哪个时刻改了"，就不用处理"保存订单之后、更新止损价之前进程退出"这种情况，重启后照样推得出来
- 需要 `OrderStore.of_symbol(symbol)` 返回这只股票的全部订单，包括已经结束的。一只股票一年最多几十笔，全部读出没有问题

考虑过：在开仓订单第一次成交时，把止损价写进止损保护记录

- 没有采用：要判断"第一次成交"这个变化，进程在两次保存之间退出就会漏掉

### D3 止损目标用券商持仓计算

```python
@dataclass(frozen=True, slots=True)
class StopTarget:
    quantity: int
    stop_price: Decimal | None  # None：不知道止损价，不挂
    uncovered: int = 0  # 未结束卖单超出持仓的股数


def stop_target(position: int, orders: Iterable[Order]) -> StopTarget:
    """股数 = 持仓 − 未结束卖单的未成交股数，小于 0 时为 0，超出的记为未覆盖；止损价见 stop_price_of。"""
```

- 持仓以券商为准，所以买入成交、卖出成交、止损单自己成交、停机期间发生的变化，下一次对齐都会反映出来，不需要按事件更新
- 未结束卖单包括待提交的。这样卖单还没提交时，目标就已经扣掉了它的股数，对齐会先把止损单改小或撤销（D5）

考虑过：用本模块记录的成交自己算持仓

- 没有采用：等于再记一份券商已经有的账，还会和券商对不上

### D4 对齐：先查询，再按表处理

```python
class StopAction(StrEnum):
    NONE = "none"
    PLACE = "place"  # 按目标挂单（没有或已结束时先新建一张序号加 1 的）
    MODIFY = "modify"  # 改单为目标的股数和止损价
    CANCEL = "cancel"
    DROP = "drop"  # 放弃还没挂出去的止损单，不调用券商


def next_action(protection: Protection, target: StopTarget) -> StopAction:
    """stop-protection spec「对齐止损单」的处理表。"""
```

`ExecutionService.protect(symbol)` 的流程：

```
p = protections.get(symbol) 或者新记录
止损单状态未知且没满等待时长? -- 是 --> 返回，不调用券商
  |
  v
有止损单且没结束? -- 是 --> report = stops.find(stop)，按回报更新（D9 的查不到规则）
  |
  v
position = broker.position(symbol)；失败 --> 保存已经得到的结果，返回
target = stop_target(position, orders.of_symbol(symbol))
按 next_action 执行：
  PLACE   新建时先保存「还没挂出去」再 place；已有的还没挂出去的止损单，上面已经查过、查不到才到这里；
          place 返回受理的回报
  MODIFY  stops.modify(stop, 股数, 止损价)，只是请求，不改记录里的股数和止损价
  CANCEL  stops.cancel(stop)，只是请求
  DROP    止损单记为已经结束
被拒按 D6；超时或断线按 D9
挂着且等于目标，或者目标股数为 0 --> 解除无保护
有变化时保存；返回 p
```

- 记录里的股数和止损价只来自券商回报，改单请求不改它们。这样「卖单先让出股数」判断的是券商确认过的股数，满足 Proposal"券商确认后再下卖单"
- 另外记下最近一次改单请求的股数（`requested`），回报的股数等于它时清掉。挂着的止损单只有在没有还没生效的请求、并且股数和止损价都等于目标时才算对齐；请求的股数和目标不同时按目标再请求一次，覆盖旧的请求。这是实现时性质测试发现的：止损单改大的请求还没生效时提交卖单，券商之后处理这个请求，止损单和卖单合计就超过了持仓。券商按顺序处理改单，所以最近一次请求生效时，之前的请求都已经处理完
- 挂单时券商返回的回报就算确认：受理了就是挂着，和订单的 `place` 一样

### D5 卖单先让出股数

```python
def stop_yields(protection: Protection | None, target: StopTarget) -> bool:
    """有未覆盖的股数：没有让出。否则没有止损单、已经结束、还没挂出去，
    或者挂着、券商确认的股数不超过目标、没有还没生效的改单请求：已让出。"""
```

未覆盖的股数是实现时性质测试发现的：卖单等待让出时止损单正好触发，股票已经卖光，止损单也结束了，只看止损单就会再提交一笔卖单，卖出不存在的股票（moomoo 会拒单，Alpaca 没关做空时会变成做空）。所以卖单合计超过持仓时一律不提交，卖单按原有规则到截止时刻过期。

`track` 改为：

```
order = store.get(plan_id)
终态，或者状态未知还没满等待时长? -- 是 --> 返回
  |
  v
需要提交的卖单（待提交，或没受理过的状态未知）? -- 是 --> p, target = 对齐(symbol)；may_place = stop_yields(p, target)
  |
  v
按原来的 _advance 推进订单，may_place 为假时不提交
订单有变化时保存
这次还没对齐过，或者订单有变化 --> 对齐(symbol)
```

- 卖单提交前先对齐一次，这次对齐会把止损单改小或撤销，并且用这次查询到的回报判断是否已让出。改单之后要等下一次跟进查到新股数才提交，所以减仓、清仓比原来晚一次跟进
- 这一步省不掉：同一批股票不能同时挂在止损单和卖单上，Alpaca 会直接拒掉超过可卖股数的卖单。M2 的工作流在卖单等待让出时，隔 1 秒左右就再跟进一次，或者收到止损单的更新推送就跟进，多出来的时间只剩券商让改单生效的那一下
- 跟进之后再对齐一次，让止损单跟上这次跟进看到的成交和结束；已经对齐过、订单又没变化时跳过，免得同一次跟进里发两次同样的改单
- 卖单等待期间是待提交，按原有规则会暂停这只股票的其他新订单，直到它提交出去

考虑过：卖出前直接撤掉止损单，卖单结束后再挂新的

- 没有采用：卖单挂着的时候，剩下的股数也没有保护；Proposal 写的是改为剩余的数量

考虑过：先提交卖单，再改止损单

- 没有采用：两边可能同时卖出同一批股票，券商会拒单或者卖多

### D6 挂单或改单被拒

- `place` 和 `modify` 抛 `OrderRejectedError` 时，用同样的内容立即重试 `stop_immediate_retries` 次。挂单被拒没有生成订单，所以重试用同一个客户端订单 ID
- 都被拒：`unprotected_since` 记为现在（已经有就保留）；被拒的挂单把止损单记为已经结束，下次对齐新建一张序号加 1 的
- 被拒的改单不动记录，下次对齐照常再改
- 解除：对齐后挂着且等于目标，或者目标股数为 0
- `OrderRejectedError` 的说明改为"券商拒绝受理这次请求"，订单和止损单共用

### D7 无保护时禁止加仓放在执行模块

`submit` 新建订单前，开仓方案的股票是无保护仓位时，抛 `UnprotectedPositionError`，和 `OrderPausedError` 一样由工作流捕获，方案作废。

考虑过：在风控里加一条规则

- 没有采用：一个变更只改一个节点；而且风控要多一项输入。无保护只在挂单一再被拒时出现，很少见，代价是人已经确认的加仓作废，可以接受

### D8 备用止损只给出判断

```python
def backup_stop_due(
    protection: Protection | None, stop_price: Decimal | None, last_price: Decimal, in_session: bool
) -> bool: ...
```

`ExecutionService.backup_stop_due(symbol, last_price)` 读记录、算止损价、看当前时刻是否在交易时段，再调用它。

- 不由执行模块直接生成清仓方案：`TradePlan` 需要板块和方案 ID，执行模块没有板块；而且代码触发的卖出要经过风控（架构铁律），怎么走由变更 8 和紧急退出一起定义
- 触发条件和券商端止损单一样：只在常规交易时段，价格不高于止损价

### D9 止损单状态未知

和订单的规则相同，复用 `unknown_lookup_delay`：

- 任何止损单调用超时或断线：记为状态未知，记下时刻，其余字段保留
- 满等待时长后先查询：查到就更新；查不到且没有券商订单号，就记回还没挂出去，按目标重新挂单，客户端订单 ID 不变；查不到但有券商订单号，仍是状态未知，重新计时
- 查询持仓失败不改止损单状态，因为止损单本身没有问题；这次对齐到此为止
- 状态未知不算无保护：断线时也挂不上，这属于健康监控的事（变更 9）

### D10 接口

```python
class Broker(Protocol):
    ...  # find、place、cancel 不变

    def position(self, symbol: str) -> int:
        """券商那边这只股票的持仓股数；没有持仓时为 0。超时或断线抛 BrokerUnavailableError。"""


@dataclass(frozen=True, slots=True)
class StopReport:  # domain/protection.py
    broker_order_id: str
    status: BrokerOrderStatus  # 复用订单的 5 种回报
    quantity: int  # 未成交股数
    stop_price: Decimal


class StopBroker(Protocol):
    """券商的止损单接口：长期有效的止损市价卖单，只在常规交易时段触发。

    超时或断线抛 BrokerUnavailableError；挂单、改单被拒抛 OrderRejectedError。
    """

    def find(self, stop: StopOrder) -> StopReport | None:
        """这张止损单现在的情况：受理过的按券商订单号查，并跟着改单产生的替换找到现在那一笔；
        没受理过的按客户端订单 ID 查。查不到时为 None。"""

    def place(self, stop: StopOrder) -> StopReport: ...

    def modify(self, stop: StopOrder, quantity: int, stop_price: Decimal) -> None:
        """请求改单；结果由之后的 find 得知。"""

    def cancel(self, stop: StopOrder) -> None:
        """请求撤单；已经结束、撤不了时也正常返回。"""


class ProtectionStore(Protocol):
    def get(self, symbol: str) -> Protection | None: ...
    def save(self, protection: Protection) -> None: ...


class OrderStore(Protocol):
    ...  # get、open_orders、save 不变

    def of_symbol(self, symbol: str) -> tuple[Order, ...]:
        """这只股票的全部订单，包括已经结束的。"""
```

- 止损单接口单独定义，不并进 `Broker`：两个测试替身各管各的，接口也更小。实际的适配器可以同一个类实现两个接口
- `find` 要"跟着替换找到现在那一笔"，是给 Alpaca 用的：改单生成新订单之后，按旧订单号查要能拿到新的那笔。moomoo 原地改单，不需要
- `Broker` 的说明去掉"客户端订单 ID 写进订单的备注"，改为"客户端订单 ID 随订单交给券商，之后可以按它查到这笔订单"

### D11 配置

```python
class ExecutionConfig(BaseModel):
    ...
    # 止损单挂单或改单被券商拒绝后，立即用同样的内容重试几次
    stop_immediate_retries: int = Field(default=1, ge=0)
```

### D12 测试

```
tests/fakes/broker.py      FakeBroker 增加 positions（股票到股数），position 也记入 calls 并可以安排失败
tests/fakes/stops.py       FakeStopBroker（见下）
tests/fakes/protections.py InMemoryProtectionStore
tests/fakes/orders.py      InMemoryOrderStore 增加 of_symbol
tests/modules/execution/builders.py   execution_service 增加两个新依赖；增加"持有 BBB 100 股，止损单已对齐"的构造
tests/modules/execution/domain/test_order.py       Order 的止损价
tests/modules/execution/domain/test_protection.py  stop_price_of、stop_target、next_action、stop_yields、
                                                   backup_stop_due、按回报更新
tests/modules/execution/test_protection_service.py stop-protection spec 的场景和状态机性质测试
tests/modules/execution/test_service.py            order-lifecycle spec 新增和修改的场景
tests/modules/execution/test_config.py             立即重试次数
```

`FakeStopBroker` 满足 `StopBroker`，和 `FakeBroker` 的用法一致：

- 按客户端订单 ID 保存券商那边的止损单，按顺序记录每次调用
- `update(client_order_id, status, quantity, stop_price)`：券商回报变为……
- `modify` 和 `cancel` 只记下请求，不改变止损单，由测试决定什么时候生效
- `fail_next(operation, after_effect=False)`、`reject_next(operation)`、`lose(client_order_id)`

状态机性质测试：持有一只股票，随机交错执行买入成交、卖单提交和成交、券商让改单或撤单生效、止损单触发、调用失败或被拒、重启、拨动时间。每一步之后检查：

- 券商那边同一只股票最多一张挂着的止损单
- 卖单提交给券商的那一刻，券商那边挂着的止损单的未成交股数加上所有挂着的卖单的未成交股数，不超过持仓
- 结束时券商不再失败、改单和撤单都生效、多对齐几次之后，止损单挂着并且等于目标，或者目标为 0、没有挂着的止损单；不是无保护仓位

## Risks / Trade-offs

- [持仓和订单分两次查询，中间可能有成交] → 差一次对齐，下次纠正；持仓更新比成交慢时，可能把止损单改大到超过实际持仓而被拒，误报一次无保护，下次对齐就解除
- [减仓和清仓多等一次跟进] → M2 在卖单等待让出时隔 1 秒左右或收到推送就再跟进，总共多 1 到 2 秒。模型建议退出要经过人工确认，紧急退出是分钟级，都可以接受；本地备用止损受一点影响，但只在实盘挂单一再失败时才用到
- [限价卖单挂着的时候，让出来的股数只靠卖单本身] → 完整版本来的顺序就有这个空档，最长到当天收盘
- [Alpaca 改单生成新订单，find 要找到替换后的那一笔] → 写进 `StopBroker.find` 的约定，M2 接入时实测
- [moomoo 的客户端订单 ID 写在备注里，GTC 能挂多久不清楚] → M7 实测；到期被撤后，对齐会重新挂上

## Migration Plan

新增记录和接口，订单多一个字段。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- 下面几项只影响适配器，不影响本变更的 spec 和任务，接入时实测：
  - Alpaca 模拟盘：改单期间（pending_replace）查询看到的是旧单还是新单；改单期间再次改单会被拒，适配器要把它当作"请求还在处理"而不是被拒；GTC 到期时报 expired 还是 canceled
  - moomoo 实盘：盘后能不能改单、挂单；GTC 最长挂多久
