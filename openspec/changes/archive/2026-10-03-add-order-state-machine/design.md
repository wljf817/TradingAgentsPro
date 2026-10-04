# Design

## Context

现状（变更 2 到 4 之后）：

- `modules/risk` 是唯一的业务模块。`RiskService.recheck(plan_id, ...)` 给出 `RiskDecision`：通过或调整时有最终股数和生效的有效期，拒绝时两者都为 `None`
- `RiskService.record_fill(plan_id, filled)` 和 `release(plan_id)` 已经写好，但还没有调用方。两者都是幂等的：同一个累计成交重复上报结果不变，已经结束的预留再次释放什么都不做
- `OutsideSessionError` 定义在 `modules/risk/domain/recheck.py`，由风控包根导出
- `workflows/` 还是空的，DBOS 在 M2 接入
- `tests/fakes/` 有 `FakeClock`、纽交所样例日历和内存预留存储
- moomoo 的限制（见 M2 的调研）：
  - 模拟盘只支持限价单和市价单，只支持当日有效，没有成交推送
  - `place_order` 没有客户端订单 ID，只能写进最长 64 字节的备注，订单列表会返回备注
  - SDK 有 17 种订单状态

动机见 proposal.md 的 Why。

### 模块边界

```
contracts（Pydantic，跨模块传递，本次不改）
  trade_plan.py  TradePlan、TradeAction、OrderType   <- 下单工作流（M2）传给执行模块
  risk.py        RiskDecision（再校验的结果）          <- 下单工作流传给执行模块

shared
  trading_calendar.py  增加 OutsideSessionError（从风控移来，D10）

modules/execution（新增）
  config.py        ExecutionConfig：unknown_lookup_delay
  domain/order.py  OrderSide、OrderStatus、Order、BrokerOrderStatus、BrokerOrder、
                   OrderPausedError、new_order
  ports.py         Broker、OrderStore、BrokerUnavailableError、OrderRejectedError
  service.py       ExecutionService：submit、track
  __init__.py      导出上面这些

modules/risk
  domain/recheck.py、service.py  改为使用 shared 的 OutsideSessionError
  __init__.py                    不再导出 OutsideSessionError
```

执行模块不 import 风控模块，风控也不 import 执行模块。两者都由 M2 的下单工作流调用（D9）。

模块之间传递的 Pydantic 模型只有 `TradePlan` 和 `RiskDecision`，都由工作流传给 `ExecutionService.submit`。`Order` 和 `BrokerOrder` 是执行模块的 frozen dataclass，由包根导出。导出它们有两个用处：

- 工作流读取订单的最新状态
- 券商适配器（M2 的 integrations）和测试替身实现端口时引用它们

## Goals / Non-Goals

**Goals:**

- 订单状态怎么变全部是纯函数，可以单独测试；service 只负责调用券商、读写存储
- `submit` 和 `track` 可以重复调用：中途崩溃后再调用一次，不会重复下单，也不会重复撤单。这样 M2 可以直接把它们放进 DBOS step
- 实盘、模拟盘和回放共用同一套代码：只用限价单、市价单和当日有效，这些两边都支持

**Non-Goals:**

- 不考虑性能：未结束的订单最多十几笔，暂停检查时全部读出
- 按「编程风格」省略的情况：
  - 截止时刻之前被撤销，截止之后才看到回报：记为已过期，和已撤销的处理相同
  - 券商接受了撤单请求，订单却一直不结束：不重发撤单
  - 券商报"成交被撤销"（moomoo 的 FILL_CANCELLED）：适配器报结果未知，等对账
  - 超时后过了等待时长，券商那边才出现这笔订单：会重复下单，由变更 7 的对账发现
  - 跟进一个没有订单的方案：属于调用方的错误，不处理
  - 券商回报的累计成交超过委托股数：不校验，信任券商
  - 暂停期间方案过了有效期：工作流重跑再校验时作废
  - 同一只股票同时有买单和卖单在挂：不限制，卖出数量由风控的可卖数量保证

## Decisions

### D1 新建执行模块

执行模块是架构图里的「执行模块」节点，变更 6 的止损和变更 7 的对账也会放在这里。目录按模块的固定结构：`config.py`、`domain/`、`ports.py`、`service.py`、`__init__.py`。repo 在 M2 实现。

考虑过：放进风控模块

- 没有采用：下单权限只在执行模块中（Proposal「风控引擎与执行」）；风控也不应该知道券商接口

### D2 一个方案一个订单，客户端订单 ID 是方案 ID

```python
@property
def client_order_id(self) -> str:
    return str(self.plan_id)  # 36 个字符，写进 moomoo 最长 64 字节的备注
```

订单最多挂到当天收盘（proposal「需要人决定」第 1 条），重试要用同一个 ID，结束后也不重挂。所以一个方案最多一个订单，方案 ID 天然可以当作客户端订单 ID：

- 订单存储和预留一样，按方案 ID 存取
- 工作流拿方案 ID 就能跟进订单，也能调用风控

考虑过：每笔订单从 ID 生成端口取一个新 ID

- 没有采用：多了一个端口和一张对照表，却没有用处。止损单（变更 6）和方案不是一一对应，到时候再取 ID

### D3 订单记录

```python
class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    PENDING = "pending"  # 待提交
    SUBMITTED = "submitted"  # 已提交
    PARTIALLY_FILLED = "partially_filled"  # 部分成交
    FILLED = "filled"  # 全部成交
    CANCELLED = "cancelled"  # 已撤销
    EXPIRED = "expired"  # 已过期
    REJECTED = "rejected"  # 已拒绝
    UNKNOWN = "unknown"  # 状态未知

    @property
    def is_terminal(self) -> bool: ...  # 全部成交、已撤销、已过期、已拒绝


@dataclass(frozen=True, slots=True)
class Order:
    plan_id: UUID
    symbol: str
    side: OrderSide
    order_type: OrderType  # contracts 的 LIMIT / MARKET
    limit_price: Decimal | None
    quantity: int  # 委托股数
    deadline: datetime  # 截止时刻
    created_at: datetime
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: str | None = None  # 券商受理过才有
    filled: int = 0  # 累计成交股数
    average_price: Decimal | None = None  # 成交均价
    unknown_since: datetime | None = None  # 变为状态未知的时刻；不是状态未知时为 None
    cancel_requested: bool = False  # 已经成功请求过撤单


def new_order(
    plan: TradePlan, decision: RiskDecision, session: TradingSession, now: datetime
) -> Order:
    """买入、加仓是买单，减仓、清仓是卖单；截止时刻 = min(生效的有效期, 当天收盘)。"""
```

状态变化都是 `Order` 上的纯方法，返回新的 `Order`；终态的订单原样返回：

| 方法 | 用途 |
| --- | --- |
| `apply(report, now)` | 按券商回报更新（D5） |
| `mark_unknown(now)` | 券商调用失败，或者受理过的订单查不到 |
| `reject()` | 券商拒绝受理 |
| `expire_unsent()` | 截止时刻之后确认券商没收到 |
| `request_cancel()` | 记下已经请求撤单 |

另有三个判断：

- `lookup_due(now, delay)`：不是状态未知，或者已经满等待时长
- `cancel_due(now)`：已提交或部分成交，到了截止时刻，还没请求过撤单
- `blocks_new_orders`：待提交或状态未知

`Order` 用 frozen dataclass，不用 Pydantic，因为 domain 不能 import 第三方包。数据都来自本系统和券商，不加校验。

### D4 券商端口

```python
class BrokerOrderStatus(StrEnum):
    WORKING = "working"  # 已受理、未结束（包括部分成交、正在撤单）
    FILLED = "filled"
    CANCELLED = "cancelled"  # 撤销或失效
    REJECTED = "rejected"
    UNKNOWN = "unknown"  # 券商自己也不知道结果


@dataclass(frozen=True, slots=True)
class BrokerOrder:  # domain/order.py：apply 要用它，domain 不能依赖 ports
    broker_order_id: str
    status: BrokerOrderStatus
    filled: int
    average_price: Decimal | None


class BrokerUnavailableError(RuntimeError):
    """券商调用超时或断线：不知道这次调用有没有生效。"""


class OrderRejectedError(RuntimeError):
    """券商拒绝受理，没有生成订单。"""


class Broker(Protocol):
    def find(self, order: Order) -> BrokerOrder | None:
        """按客户端订单 ID 查这笔订单，范围覆盖订单创建的那个交易日；没有时为 None。"""

    def place(self, order: Order) -> BrokerOrder:
        """提交当日有效单，客户端订单 ID 写进备注；返回受理后的状态。"""

    def cancel(self, order: Order) -> None:
        """按券商订单号请求撤单。请求被接受就返回；订单已经结束、撤不了时也正常返回。"""
```

- 三个方法都传整个订单：适配器按创建时刻决定查当天订单还是历史订单，按券商订单号撤单
- 券商回报只区分 5 种状态。已提交还是部分成交、已撤销还是已过期，由 domain 按累计成交和截止时刻决定。这样适配器只做翻译，判断逻辑都在可以测试的纯函数里
- 拒绝受理用异常表示，因为这时没有券商订单号；受理后被拒，用回报里的拒绝状态表示

moomoo 的 17 种订单状态对应如下，由 M2 的适配器实现，接入时按实测修正：

| moomoo OrderStatus | 券商回报 |
| --- | --- |
| UNSUBMITTED、WAITING_SUBMIT、SUBMITTING、SUBMITTED、FILLED_PART、CANCELLING_PART、CANCELLING_ALL | 已受理、未结束 |
| FILLED_ALL | 全部成交 |
| CANCELLED_PART、CANCELLED_ALL、DISABLED、DELETED | 撤销或失效 |
| SUBMIT_FAILED、FAILED | 拒绝 |
| TIMEOUT、NONE、FILL_CANCELLED | 结果未知 |

正在撤单（CANCELLING_*）仍然算未结束：撤单生效之前还可能成交。

### D5 券商回报怎么改变订单

`Order.apply(report, now)`：

```
终态?                                  -- 是 --> 原样返回
report.filled < self.filled?           -- 是 --> 原样返回（过时的回报）
  |
  v
WORKING   --> filled > 0 ? 部分成交 : 已提交
FILLED    --> 全部成交
CANCELLED --> now >= deadline ? 已过期 : 已撤销
REJECTED  --> 已拒绝
UNKNOWN   --> 状态未知，unknown_since = now
  |
  v
更新 broker_order_id、filled、average_price；不是状态未知时 unknown_since = None
```

用"看到回报的时刻"和截止时刻比较，来区分已过期和已撤销（proposal「需要人决定」第 2 条），不记录撤单是谁发起的。原因：

- 本变更只在截止时刻撤单，所以由此结束的订单一定是已过期
- 变更 8 的紧急撤单发生在截止时刻之前，自然记为已撤销
- 唯一会记错的情况是截止之前被撤、截止之后才看到，两种状态的处理相同（见 Non-Goals）

### D6 跟进

```python
def track(self, plan_id: UUID) -> Order: ...
```

```
order = store.get(plan_id)
终态? -- 是 --> 返回，不调用券商
状态未知且没满等待时长? -- 是 --> 返回，不调用券商
  |
  v
report = broker.find(order)
  |-- 查到 --> order.apply(report, now)
  |-- 查不到，有券商订单号 --> order.mark_unknown(now)
  |-- 查不到，没有券商订单号:
  |       now >= deadline --> order.expire_unsent()
  |       否则 --> broker.place(order) 的结果 apply；OrderRejectedError --> order.reject()
  v
order.cancel_due(now)? -- 是 --> broker.cancel(order)，order.request_cancel()
  |
  v
上面任何一次券商调用抛 BrokerUnavailableError --> 在已经得到的结果上 mark_unknown(now)
订单有变化时保存；返回
```

- 跟进不限交易时段：收盘后也要查到最终状态。提交只会发生在截止时刻之前，而截止时刻不晚于当天收盘、不早于提交时刻，所以一定在盘中
- 撤单失败时，`mark_unknown` 用的是刚刚 `apply` 过的订单，查到的累计成交不会丢
- 只在有变化时保存：M2 的 repo 每次保存都写一个新版本

### D7 提交

```python
def submit(self, plan: TradePlan, decision: RiskDecision) -> Order: ...
```

```
store.get(plan.plan_id) 已有? -- 是 --> return track(plan.plan_id)
  |
  v
session = calendar.session_at(clock.now()) 为空? -- 是 --> 抛 OutsideSessionError
这只股票有其他订单 blocks_new_orders? -- 是 --> 抛 OrderPausedError
  |
  v
store.save(new_order(plan, decision, session, now))
return track(plan.plan_id)
```

- 新订单先以待提交保存，再由 `track` 走"先查询再提交"。所以首次提交、超时重试和崩溃后继续都是同一段代码
- 交易时段只在新建订单时检查。已有订单的方案再次提交，等于跟进一次
- `decision` 必须是通过或调整：`quantity` 和 `expires_at` 用 assert 断言非空，传入拒绝的结果属于调用方的错误
- 暂停用异常：正常情况下总是返回订单，暂停时工作流要回到再校验，和 `OutsideSessionError` 一样由工作流捕获

考虑过：暂停时也建订单，保持待提交，等暂停解除后再发

- 没有采用：等待期间行情可能变了，而这样发出的订单没有重新再校验。Proposal 说"与券商对账后再决定"，所以要回到再校验

### D8 状态未知和重新提交

- 等多久再查（`ExecutionConfig.unknown_lookup_delay`，默认 30 秒）：超时之后立刻查，券商可能还没处理完，于是查不到，重发就会重复下单。30 秒内券商一般已经能查到这笔订单；状态未知期间这只股票会暂停新订单，所以等待时间也不能太长
- 只有没有券商订单号的订单才会重新提交。受理过却查不到，说明券商那边出了问题，重新提交可能变成第二笔订单，所以保持状态未知，等对账
- 查询又失败时从这次失败重新计时，相当于固定间隔重试。断线期间每 30 秒试一次，不会很频繁

```python
class ExecutionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 订单变为状态未知之后，等这么久才再查询券商
    unknown_lookup_delay: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
```

### D9 和预留的衔接

执行模块不调用风控。M2 的下单工作流大致是：

```
decision = risk.recheck(plan_id, 实时账户, 实时价, 熔断)
没有最终股数? --> 作废，退回 PM，结束
order = execution.submit(plan, decision)    # OrderPausedError: 等一会儿，从再校验重来
循环:
    risk.record_fill(plan_id, order.filled)
    order.status.is_terminal? --> risk.release(plan_id)，结束
    持久化休眠到下一次跟进（不晚于 order.deadline）
    order = execution.track(plan_id)
```

- `record_fill` 收累计成交，同一个值重复上报结果不变；`release` 对已经结束的预留什么都不做。本变更又保证累计成交只增不减、终态不再变化，所以工作流每一步都按最新状态调用即可，DBOS step 重跑也安全
- 全部成交时，累计成交等于预留股数，预留在 `record_fill` 时就已经结束；`release` 再调用一次也没有影响
- 和变更 3 design D7 的不同：那里写的是"订单成交和结束由变更 5 接入"。现在本变更提供订单的状态，调用放在工作流里，因为跨模块的流程只能放在 workflows，而 M1 还没有工作流层

考虑过：执行模块定义一个"预留更新"端口，由 bootstrap 注入风控用例

- 没有采用：用端口包装另一个业务模块，相当于在模块之间绕开了 workflows。变更 6 的止损也要根据成交行动，放在同一个工作流里编排更直接

### D10 `OutsideSessionError` 移到 shared

提交新订单和再校验都只能在盘中调用，工作流只捕获一种错误。这个类放在 `shared/trading_calendar.py`，因为它说的是"在交易时段之外调用了盘中才能做的操作"：

- `modules/risk/domain/recheck.py` 删除这个类，改为从 shared import
- 风控包根不再导出它；`tests/modules/risk/test_service.py` 改为从 shared import
- shared 只用到 `RuntimeError`，纯区域的规则不受影响

### D11 测试

```
tests/fakes/orders.py                           InMemoryOrderStore：按方案 ID 存在 dict 里
tests/fakes/broker.py                           FakeBroker（见下）
tests/modules/execution/builders.py             基准方案、基准结果、执行用例的构造
tests/modules/execution/test_config.py          spec「执行配置」
tests/modules/execution/domain/test_order.py    new_order、apply、各个状态变化和判断（直接构造订单和回报）
tests/modules/execution/test_service.py         spec 其余的场景，以及下面的状态机性质测试
```

`FakeBroker` 满足 `Broker`，测试用它安排券商那边的情况：

- 按客户端订单 ID 保存券商那边的订单，并按顺序记录每次调用（操作、客户端订单 ID），用来断言"券商收到了什么、没收到什么"
- `update(client_order_id, status, filled, average_price)`：券商回报变为……
- `fail_next(operation, after_effect=False)`：下一次这种调用（查询、提交或撤单）抛 `BrokerUnavailableError`。`after_effect=True` 时，调用先生效再抛异常，对应"券商其实收到了"
- `reject_next_place()`：下一次提交抛 `OrderRejectedError`
- `lose(client_order_id)`：券商查不到这笔订单
- `cancel` 只记下撤单请求，不改变订单状态，由测试决定撤单什么时候生效

`test_service.py` 用 `FakeClock` 拨动时间。"重启"是用同一个存储和同一个假券商，重新创建 `ExecutionService`。

状态机性质测试用 Hypothesis 的 `RuleBasedStateMachine`。先提交基准方案：截止时刻随机取提交后 1 到 5 分钟，让一个样例的几十步能跨过截止时刻；开始时还可能安排第一次提交失败、第一次撤单失败（生效前或生效后）。之后随机交错执行下面这些操作：

- 拨动时间（0 到 120 秒）后跟进
- 券商增加成交、撤销（包括请求的撤单生效）、拒绝、报结果未知；订单在券商那边已经结束时什么都不做
- 安排下一次调用失败（生效前或生效后）
- 重启

每一步之后检查：

- 券商那边每个客户端订单 ID 最多一笔订单
- 累计成交只增不减，不超过委托股数；终态不再变化
- 截止时刻之后，券商不再收到提交
- 成功的撤单请求最多一次
- 最近一次跟进没有失败、订单也不是状态未知时，本地的状态和累计成交与券商一致
- 结束时：拨到截止时刻之后，券商不再失败；跟进一次，券商撤销还没结束的订单，再跟进一次（每次间隔满等待时长），订单一定进入终态

每个 spec 场景至少对应一个测试，测试名里写明场景。

## Risks / Trade-offs

- [moomoo 没有真正的客户端订单 ID，超时后等待时长内查不到会重复下单] → 等待时长可以配置；只有超时之后才可能重复；变更 7 的对账发现同一 ID 有两笔订单时告警
- [状态未知会暂停这只股票的全部新订单，包括退出] → 等待时长只有 30 秒，查清后立即恢复；断线期间本来也下不了单；紧急退出被暂停时怎么办，在变更 8 再看
- [多天有效期的方案，下单后只挂一天] → 已由人决定（proposal「需要人决定」第 1 条）；PM 可以第二天重新提
- [截止时刻是收盘时，撤单可能和券商的当日失效同时发生] → 撤不了时适配器正常返回，之后的查询看到失效，记为已过期
- [执行模块不调用风控，预留的更新要等 M2 的工作流接上] → 本变更保证工作流只需按最新状态调用（D9）；M2 拆变更时，把工作流的这段流程和测试列进去

## Migration Plan

新增模块，移动一个异常类。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- 下面几项只影响适配器的映射和工作流的节奏，不影响本变更的 spec 和任务，M2 接入时实测：
  - moomoo 模拟盘有没有订单推送
  - 当日单收盘失效时报 CANCELLED_ALL 还是 DISABLED
  - TIMEOUT 状态多久会变成确定的状态
  - 跟进多久一次才符合查询订单的限频（刷新缓存时 30 秒 10 次）
