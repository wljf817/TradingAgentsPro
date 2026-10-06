# Design

## Context

现状（M1 和 `add-state-store` 之后）：

- 执行模块有订单和止损保护两张版本表，每次变化都保存：
  - `Order` 记下了止损价，没有板块和紧急阈值；`BrokerOrder` 回报状态、累计成交和成交均价，没有费用
  - `StopOrder` 只记券商回报的未成交股数和止损价；`StopReport` 没有累计成交和成交均价。止损单一结束（成交、撤销、被拒都一样）就只剩"已经结束"，成交了多少没有记下
  - 一只股票的止损保护只存当前这张止损单，以前的止损单只在旧版本里
- 风控要的账户数据（`AccountSnapshot`）、紧急退出要的持仓（`Holding`）都由调用方传入，现在没有任何地方能给出：
  - 已结算资金：要区分已结算和未结算的卖出款
  - 最近成交日：止损单成交也算
  - 持仓的板块、止损价、紧急阈值，今天开仓成交的均价
- 风控的「执行交易日」：盘中是当天，否则是下一个开盘的交易日（`risk/domain/exposure.py` 的 `execution_day`）
- 券商：模拟盘是 Alpaca，只有保证金账户，有 `cash`、`equity`、`last_equity`，不报已结算资金，模拟盘不收费；实盘是 moomoo（M7），费用要另外查询
- 美股现在按 T+1 结算

动机见 proposal.md 的 Why，要求见 specs/。

### 模块边界

只改执行模块，另加一个迁移：

```
modules/execution
  domain/order.py        Order 加 sector、emergency_threshold、fees；BrokerOrder 加 fees；
                         新增 opening_order（stop_price_of 改用它）
  domain/protection.py   StopReport 加 filled、average_price、fees；
                         StopOrder 加 created_at、filled、average_price、fees、filled_at；
                         Protection.new_stop 增加参数 now
  domain/trade.py        新增：Trade、TradeKind，trade_of_order、trade_of_stop
  domain/account.py      新增：BrokerAccount、BrokerPosition、Account、HeldPosition，
                         unsettled_proceeds、last_trade_days、held_position、account_of
  ports.py               Broker 加 account；新增 TradeStore
  repo.py                新增 execution_trades 表和 PostgresTradeStore
  service.py             构造时多一个 TradeStore；保存订单和止损保护时先写交易记录；新增 account
  __init__.py            导出新的名字
bootstrap/migrations/versions/v0002_create_trade_records.py   新增
```

- 不新增、也不修改跨模块传递的 Pydantic 模型。`AccountSnapshot`、`Position` 不变；执行模块给出自己的 `Account`（dataclass），由 M2 的流程变更拼成风控和紧急退出的输入（D7）
- 执行模块不 import 风控和紧急退出模块
- 新增的 domain 文件只用 `dataclasses`、`datetime`、`decimal`、`enum`、`uuid`、`collections.abc` 和 shared 的交易日历，都在纯区域的白名单里

## Goals / Non-Goals

**Goals:**

- 交易记录和订单、止损单不会对不上：由它们直接推出，同一处保存
- 交易记录不落后于已经保存的订单和止损单，中途出错也一样
- 账户数据里本系统负责的部分（已结算资金、开仓信息、最近成交日）都在执行模块里算好，流程只负责拼装

**Non-Goals:**

按「编程风格」省略的情况：

- 结算日只按交易日算：交易所开市、银行休息的日子（哥伦布日、退伍军人节）不顺延
- 券商受理以后又拒绝的止损单，交易记录记为已撤销；还没挂出去就放弃的止损单，记为已拒绝（D3）
- 长期有效的止损单到期失效，记为已撤销，不区分已过期
- 停机跨过交易日时，止损单的成交日记为发现成交之前最后一个收盘的交易日，可能偏晚（D6）
- 止损单在两个交易日分别成交时，成交日记为后一次
- 未结算的卖出款按成交额算，不扣费用
- 订单进入终态以后券商补报的成交不再记，M1 已经是这样
- 券商的持仓没有累计成交大于 0 的开仓订单时，不列入账户数据，包括来历不明的持仓，以及买单成交了、还没跟进到的那一会儿
- 列出有成交的交易记录时读全部历史，不按日期过滤
- 改单还没生效时，止损单交易记录的委托股数是券商确认的旧股数

## Decisions

### D1 交易记录由订单和止损单推出，存成执行模块的一张版本表

交易记录不单独维护状态，而是订单或止损单每次保存时，用一个纯函数从它推出一条交易记录，一起保存：

```python
def trade_of_order(order: Order, calendar: TradingCalendar) -> Trade:
    """订单的交易记录。"""


def trade_of_stop(stop: StopOrder, plan_id: UUID | None, calendar: TradingCalendar) -> Trade:
    """止损单的交易记录；plan_id 是这只股票止损价来自的开仓订单的方案 ID。"""
```

`Trade` 的字段就是 spec「交易记录的内容」那张表：客户端订单 ID、类型、股票、来源方案 ID、委托股数、累计成交、成交均价、累计费用、状态（`OrderStatus`）、创建时刻、成交日、结算日。结算状态不存，按结算日和执行交易日现算（D6）。

存储用 state-store 的版本表：

| 表 | 键 | 筛选列 | 记录 |
| --- | --- | --- | --- |
| `execution_trades` | `client_order_id`（text） | `traded`：累计成交大于 0 | `Trade` |

键用客户端订单 ID：订单的是方案 ID 的字符串，止损单的是 `BBB-STOP-1` 这样的，不会重复。一条交易记录随订单或止损单变化追加新版本，正好是 Proposal 说的"状态变化时更新这一条"，旧版本还能查到。

考虑过：

- 不建表，需要时从订单和止损保护推出：以前的止损单只在止损保护的旧版本里，要扫历史版本才能找全；M5 的查询工具和 M6 的复盘也需要一张现成的流水
- 每次成交一行的流水：券商只报累计成交和成交均价，不报每一笔；Proposal 也要求一笔订单一条、状态变化时更新
- 交易记录自己维护状态，按回报更新：要再写一遍订单和止损单的状态变化，两边容易不一致

### D2 先写交易记录，再保存订单或止损保护

执行模块里所有保存订单和止损保护的地方，都换成两个私有方法：

```python
def _save_order(self, order: Order) -> None:
    self._trades.save(trade_of_order(order, self._calendar))
    self._store.save(order)


def _save_protection(self, protection: Protection) -> None:
    if protection.stop is not None:
        source = opening_order(self._store.of_symbol(protection.symbol))
        plan_id = None if source is None else source.plan_id
        self._trades.save(trade_of_stop(protection.stop, plan_id, self._calendar))
    self._protections.save(protection)
```

顺序是先写交易记录：

- 保存订单出错时，交易记录已经是新的，订单还是旧的。下次跟进按旧订单查询券商，得到同样或更新的回报，订单追上；交易记录内容不变，不写新版本
- 反过来先存订单，中间出错时交易记录落后。订单已经进入终态的，以后不会再保存，交易记录就永远停在旧状态

止损单在止损保护里，只有当前这张会变化；换成新的止损单之前，旧的那张结束时已经保存过一次，它的交易记录也就停在最终的状态。

内容没变时 `VersionedStore` 不写新版本，所以只有撤单要求、改单请求这类跟进用的状态变化时，交易记录不会多出版本。

考虑过：

- 订单和交易记录放在一个数据库事务里：存储接口各自独立，要共享事务就得改接口，让 service 知道数据库；按顺序写已经够用
- 由工作流在调用执行模块之后写交易记录：交易记录的写入者是执行模块（Proposal），而且工作流看不到跟进和对齐内部的每次变化

### D3 止损单记下成交

券商回报和止损单各加几个字段，都有默认值，M1 的测试不用改：

```python
@dataclass(frozen=True, slots=True)
class StopReport:
    broker_order_id: str
    status: BrokerOrderStatus
    quantity: int  # 未成交股数
    stop_price: Decimal
    filled: int = 0  # 累计成交，包括改单之前的
    average_price: Decimal | None = None
    fees: Decimal = Decimal(0)
```

`StopOrder` 加 `created_at`（必填，`new_stop` 多一个参数 `now`）、`filled`、`average_price`、`fees`、`filled_at`（发现累计成交增加的时刻）。`apply` 的变化：

- 已受理、未结束：和现在一样，另外记下累计成交、成交均价和费用
- 全部成交、撤销或失效、拒绝：结束，另外记下未成交股数、累计成交、成交均价和费用。以前结束时不更新股数，现在要更新，全部成交时未成交股数才是 0
- 累计成交比记下的多时，`filled_at` 记为这次的 `now`
- 结果未知：不变

止损单的股数仍是未成交股数，部分成交以后止损目标（持仓减卖单）也随之变小，两者照样对得上，对齐的逻辑不用改。

交易记录的状态按 spec「止损单交易记录的状态」从止损单推出：

| 止损单 | 状态 |
| --- | --- |
| 还没挂出去 | 待提交 |
| 挂着，累计成交 0 / 大于 0 | 已提交 / 部分成交 |
| 状态未知 | 状态未知 |
| 已经结束，未成交 0，累计成交大于 0 | 全部成交 |
| 已经结束，有券商订单号，其他 | 已撤销 |
| 已经结束，没有券商订单号 | 已拒绝 |

考虑过：止损单记下结束的原因（成交、撤销、被拒、放弃），状态就能分得更细。但 `end()` 的三个调用点都要传原因，而推不准的只有"受理后被拒""没挂出去就放弃"两种少见情况，见省略清单。

### D4 订单记下板块、紧急阈值和费用

- `Order.sector`：取方案的，必填，放在 `symbol` 后面。测试里只有一处直接构造 `Order`
- `Order.emergency_threshold`：买单取再校验结果里生效的紧急阈值，卖单为 None
- `Order.fees`、`BrokerOrder.fees`：券商回报的累计费用，默认 0；`apply` 照抄

板块和紧急阈值和止损价一样，都是"这只股票最近一次有成交的开仓方案的值"，所以和止损价一样抄在订单上，用同一个 `opening_order` 找出来：

```python
def opening_order(orders: Iterable[Order]) -> Order | None:
    """最近创建的、累计成交大于 0 的开仓订单；没有时为 None。"""
```

`stop_price_of` 改用它，行为不变。

考虑过：从风控的预留里读方案的板块和紧急阈值。执行模块不能读别的模块的表；预留属于风控，执行模块也不该依赖它。

### D5 费用以券商回报为准，模拟盘由适配器估算

执行模块只记券商回报的累计费用，不自己估算。Alpaca 模拟盘不收费，「Alpaca 模拟盘」的适配器按每笔佣金的配置给出估算值（订单有成交时记一笔佣金）；moomoo 实盘（M7）由适配器查询实际费用。这样模拟盘和实盘的差别只在适配器里，业务代码不分模拟盘和实盘。

考虑过：执行配置里放一个估算的每笔佣金，券商不报费用时用它。这样业务代码里就要区分"券商报了"和"券商没报"，接实盘时还要改。

### D6 成交日、结算日和已结算资金

成交日：

- 订单：`calendar.session_at(order.created_at).day`。订单只在盘中新建，都是当日有效单，只在这个时段成交，哪怕收盘以后才查到
- 止损单：`filled_at` 所在的交易日。在盘中就是当天；不在盘中就是 `last_session_before(filled_at)`，因为止损单只在盘中触发，发现得晚只能是之前的时段成交的

止损单用发现的时刻，不用券商报的成交时刻：适配器不用多映射一个字段；发现得晚只会让成交日偏晚，结算偏晚、交易间隔偏长，都是偏保守的一边。

结算日是 `calendar.next_session(traded_on)`。结算状态：执行交易日不早于结算日时为已结算。执行交易日在 `account.py` 里按风控的同一个定义写一个私有函数（盘中是当天，否则是 `next_session_after(now)`），执行模块不能 import 风控的 domain。

已结算资金：

```python
def unsettled_proceeds(trades: Iterable[Trade], day: date) -> Decimal:
    """卖出和止损单里，执行交易日为 day 时还没结算的成交额之和。"""
    return sum(
        (t.filled * t.average_price for t in trades if t.is_sale and not t.settled(day)),
        ZERO,
    )
```

已结算资金 = 券商现金 − `unsettled_proceeds`。按 T+1 算下来，盘中只有当天的卖出没结算；收盘以后，当天的卖出到下一个开盘时已经结算，开仓单最早也只能在那时下，所以都算已结算。

考虑过：

- 交易记录里存结算状态，每天由定时任务改成已结算：多一个定时任务，忘了跑就错；结算日是固定的，现算就行
- 券商给的已结算资金：Alpaca 不报，见 proposal「需要人决定」第 1 条

### D7 账户数据：执行模块给出 Account，流程拼成风控和紧急退出的输入

券商接口加一个读取账户的操作：

```python
@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    quantity: int
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    equity: Decimal
    cash: Decimal
    previous_close_equity: Decimal  # Alpaca 的 last_equity
    positions: tuple[BrokerPosition, ...]  # 股数大于 0 的


class Broker(Protocol):
    def account(self) -> BrokerAccount:
        """券商账户；超时或断线时抛 BrokerUnavailableError。"""
        ...
```

执行模块给出：

```python
@dataclass(frozen=True, slots=True)
class HeldPosition:
    symbol: str
    sector: str
    quantity: int
    average_cost: Decimal
    stop_price: Decimal
    emergency_threshold: Decimal
    entry_price_today: Decimal | None


@dataclass(frozen=True, slots=True)
class Account:
    equity: Decimal
    settled_cash: Decimal
    previous_close_equity: Decimal
    positions: tuple[HeldPosition, ...]
    last_trade_days: Mapping[str, date]
```

`ExecutionService.account()` 先读券商账户（失败就抛错，不读存储），再读这些持仓的订单和全部有成交的交易记录，交给纯函数 `account_of` 算：

- 每只券商持仓用 `opening_order` 找开仓订单，没有就不列出；`entry_price_today` 是当前时段里创建、有成交的开仓订单中最近的那笔的成交均价，不在盘中时为 None
- `last_trade_days` 是每只股票有成交的交易记录里最晚的成交日

`all_positions` 保留给对账用，适配器可以和 `account` 共用一次券商调用。

M2 的「下单流程」「紧急退出流程」加上行情拼出两个输入，大致是：

```python
account = execution.account()
snapshot = AccountSnapshot(
    equity=account.equity,
    settled_cash=account.settled_cash,
    positions=tuple(
        Position(
            symbol=p.symbol,
            sector=p.sector,
            quantity=p.quantity,
            average_cost=p.average_cost,
            last_price=quotes[p.symbol].last_price,
            stop_price=p.stop_price,
        )
        for p in account.positions
    ),
    last_trade_days=account.last_trade_days,
)
holdings = tuple(
    Holding(
        symbol=p.symbol,
        sector=p.sector,
        emergency_threshold=p.emergency_threshold,
        previous_close=quotes[p.symbol].previous_close,
        last_price=quotes[p.symbol].last_price,
        entry_price_today=p.entry_price_today,
    )
    for p in account.positions
)
exits = emergency.check(holdings, account.equity, account.previous_close_equity)
```

考虑过：

- 执行模块直接给出 `AccountSnapshot`：要把行情传进来；紧急退出要的紧急阈值、今天开仓成交的均价、前一交易日收盘时的权益，`AccountSnapshot` 都没有，只能给风控的 contract 加上风控用不到的字段
- 全部放在流程里拼：已结算资金、开仓订单怎样找都是业务规则，不该写在工作流里；工作流也碰不到券商
- 没有开仓订单的持仓报错：一只来历不明的持仓会让所有方案的风控检查都失败，包括退出方案。不列出它，开仓已经因为对账不一致暂停，退出不受影响

### D8 交易记录的存储

```python
class TradeStore(Protocol):
    def get(self, client_order_id: str) -> Trade | None:
        """这条交易记录；没有时为 None。"""
        ...

    def traded(self) -> tuple[Trade, ...]:
        """累计成交大于 0 的全部交易记录。"""
        ...

    def save(self, trade: Trade) -> None:
        """保存这条交易记录的最新状态。"""
        ...
```

- `PostgresTradeStore` 和另外两个存储一样只做转接：`traded` 用 `latest(traded=True)`
- 迁移 `v0002_create_trade_records.py` 建 `execution_trades`，列和 D1 一致，写法和 v0001 相同
- `tests/fakes/trades.py` 的 `InMemoryTradeStore` 按键覆盖；存储的测试同时跑两种实现

## Risks / Trade-offs

- [交易记录和订单分两次写，中间出错时交易记录先于订单] → 这是有意的顺序（D2）：之后的跟进和对齐让订单追上，交易记录不会落后
- [每次保存订单和止损保护都多一次交易记录的读写] → 每天几十次变化；内容没变只多一次查询
- [`traded()` 读全部有成交的交易记录] → 几年也只有几千条；以后变慢再加成交日的筛选列，接口不变
- [`Order`、`StopOrder` 加了必填字段，旧版本读不出来] → 还没部署过，数据库里没有旧数据
- [Alpaca 改单会生成新订单，累计成交可能只算替换以后的] → 端口说明写明累计包括改单之前的成交，由「Alpaca 模拟盘」的适配器负责
- [止损单的成交日用发现的时刻] → 偏晚只会让结算和交易间隔偏保守（D6）
- [模拟盘的费用是估算的，和实际不一样] → 模拟盘本来就不收费；估算用的就是风控的每笔佣金，盈亏和风控按同一个口径算

## Migration Plan

- 数据库：迁移 v0002 建 `execution_trades`；还没部署过，没有旧数据要迁移
- 回滚：revert 代码即可，多出来的表旧代码不会用到
