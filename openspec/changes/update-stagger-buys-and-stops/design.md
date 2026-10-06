# Design

## Context

现状（`add-alpaca-paper-broker` 之后）：

- 对齐止损单（`ExecutionService._align`）：先查询当前止损单并按回报更新，再查询券商持仓，用 `stop_target(position, orders)` 算止损目标，按 `next_action` 挂单、改单、撤单或放弃。止损目标的股数为 0 时，挂着的撤单，还没挂出去的放弃，并且解除无保护仓位
- 卖单先让出股数（`_track`）：还没提交出去、没有撤单要求的卖单，先对齐止损单，`stop_yields` 为真才提交；有撤单要求的不提交，也就不用先对齐
- 买单直接提交；跟进之后对齐，按合计持仓改单或挂单
- 本地备用止损（`backup_stop_due`）：无保护仓位、盘中、有止损价、最新价不高于止损价
- 止损保护（`Protection`）是执行模块的 frozen dataclass，整条记录经 pydantic `TypeAdapter` 存成 JSONB（state-store spec）
- `ExecutionConfig` 有两项：状态未知的等待时长、立即重试次数

动机见 proposal.md 的 Why，要求见 specs/stop-protection/spec.md 和 specs/order-lifecycle/spec.md。

### 模块边界

只改执行模块，不新增也不修改跨模块传递的 Pydantic 模型：

```
modules/execution
  config.py              ExecutionConfig 增加 stagger_buys_and_stops，默认 False
  domain/protection.py   stop_target 增加 stagger 参数；StopTarget、Protection 增加 staggered；
                         新增 stop_withdrawn；backup_stop_due 加上错开中
  service.py             _track 在开关打开时对买单也先对齐；_align 把开关传给 stop_target，记下错开中
```

`ExecutionConfig` 是执行模块自己的配置（pydantic），由 bootstrap 构造后注入；`StopTarget`、`Protection` 是执行模块 domain 里的 dataclass，不出模块。

## Goals / Non-Goals

**Goals:**

- 开关关闭时行为和现在逐字一致：已有的测试一个不改都通过
- 开关打开时，券商那边同一只股票不会同时挂着买单和止损单，包括超时、重启、提前撤单交错的情况
- 错开靠已有的对齐规则完成，不新增止损单的处理分支

**Non-Goals:**

按「编程风格」省略的情况：

- 加仓买单挂着时提交的减仓、清仓卖单，在 Alpaca 上被拒：不处理，见 proposal「需要人决定」第 2 条
- 受理过、又一直查不到的买单（状态未知）不会自己结束，这段时间止损单一直不挂：等对账把它结束（reconciliation spec「结束查不到的订单和止损单」），之后对齐就挂上；本地备用止损在这段时间照常盯着
- 撤掉还在等止损单撤掉的买单时，止损单的撤单请求可能已经在券商那边生效，这次对齐看到的还是挂着的止损单，不会立即补挂：下一次对齐（定时对齐或对账）看到它结束就挂新的，spec 场景「撤掉还在等止损单撤掉的买单后，挂回止损单」
- 开关在运行中切换：只在启动时设定

## Decisions

### D1 一项执行配置，由启动时设定

`ExecutionConfig.stagger_buys_and_stops: bool = False`。「启动与装配」按券商设定：Alpaca 为 True，moomoo 为 False。

考虑过：

- 写成券商端口的一个属性（比如 `StopBroker` 声明能不能和买单同时挂着）：不会配错，但端口、两个测试替身和 Alpaca 适配器都要改；测试两种行为还得换替身。配置项只改执行模块，测试直接传配置
- 不分券商，一律错开：人在 `add-alpaca-paper-broker` 里否决了，实盘加仓期间不能没有券商止损单

### D2 错开靠止损目标为 0 实现

开关打开、这只股票有没结束的买单时，止损目标的股数为 0。`next_action` 已经把目标 0 处理成"挂着的撤单、还没挂出去的放弃、其他不动"；买单都结束以后，目标恢复，止损单已经结束，`next_action` 给出挂单，`Protection.new_stop` 用序号加 1。不需要新的处理分支。

```python
def stop_target(position: int, orders: Iterable[Order], *, stagger: bool = False) -> StopTarget:
    orders = tuple(orders)
    selling = sum(
        o.quantity - o.filled
        for o in orders
        if o.side is OrderSide.SELL and not o.status.is_terminal
    )
    covered = max(0, position - selling)
    buying = stagger and any(o.side is OrderSide.BUY and not o.status.is_terminal for o in orders)
    return StopTarget(
        quantity=0 if buying else covered,
        stop_price=stop_price_of(orders),
        uncovered=max(0, selling - position),
        staggered=buying and covered > 0,
    )
```

- `StopTarget.staggered`：这次目标因为错开改为 0、不错开时会大于 0。新买入一股还没成交时持仓为 0，不算错开中
- 没结束的买单包括待提交：买单在等止损单撤掉的时候就是待提交，这时目标必须已经是 0
- `stagger` 默认 False，已有的调用（测试里的 `stop_target(100, orders)`）不用改；`_align` 传 `self._config.stagger_buys_and_stops`
- 目标为 0 时照常解除无保护仓位。开关打开时这不会掩盖无保护：无保护仓位不能提交新的买单，所以错开开始时总是有保护的

考虑过：新增一种处理"让给买单"，在 `next_action` 里单独判断。和目标为 0 的处理完全一样，只是多一个分支和一组测试

### D3 买单提交之前先对齐，等止损单撤掉

`_track` 里"先对齐再决定能不能提交"的条件，从"还没提交出去的卖单"扩大到"开关打开时还没提交出去的买单"，有撤单要求的仍然不提交、也不先对齐：

```python
if order.unsent and not order.cancel_wanted and self._waits_for_stop(order.side):
    protection, target = self._align(order.symbol, now, listing)
    aligned = True
    if order.side is OrderSide.SELL:
        may_place = target is not None and stop_yields(protection, target)
    else:
        may_place = target is not None and stop_withdrawn(protection)
```

```python
def stop_withdrawn(protection: Protection | None) -> bool:
    """止损单已经撤掉：没有止损单，或者当前止损单已经结束、还没挂出去。"""
    stop = protection.stop if protection else None
    return stop is None or stop.status in (StopStatus.ENDED, StopStatus.PENDING)
```

- `_waits_for_stop(side)`：卖单总是为真；买单在开关打开时为真
- `target is None` 表示这次对齐没做完（止损单状态未知还没满等待时长、查询持仓失败），和卖单一样不提交
- 对齐时目标已经是 0，还没挂出去的止损单已经放弃（变成已经结束），所以对齐之后实际只剩"没有止损单或已经结束"两种；`PENDING` 写进判断，和 spec 的定义一致

考虑过：直接用 `stop_yields(protection, target)`。目标为 0 时结果几乎一样，但它还会因为卖单超出持仓（未覆盖的股数）挡住买单，这和买单无关；两个概念分开，spec 也分开写

### D4 错开中记在止损保护上

`Protection.staggered: bool = False`。`_align` 在 `_act` 之后按这次的止损目标设定：

```python
protection = self._act(protection, target, now)
protection = protection.with_staggered(target.staggered)
if is_aligned(protection.stop, target) or target.quantity == 0:
    protection = protection.mark_protected()
```

- 只在对齐走完时更新：止损单状态未知还没满等待时长、查询持仓失败时提前返回，保留原来的值
- 跟着整条记录存进 JSONB，重启后还在。旧记录没有这个字段，`TypeAdapter` 按默认值读成 False
- 不算进 `Protection.is_active`：错开中的股票有持仓，对账按券商持仓对齐它；它的买单没结束，也会被跟进

考虑过：

- 不存，在 `backup_stop_due` 里按订单现算（开关打开、有没结束的买单、有止损价）：不知道持仓。一只股票清仓以后又新买入、还没成交时，`stop_price_of` 仍然给出上一段持仓的止损价，备用止损会对着 0 股触发。`backup_stop_due` 跟着行情频繁调用，不能每次查券商持仓；对齐时本来就查了持仓
- 复用无保护仓位：会禁止加仓，还会告警，都不对

### D5 本地备用止损加上错开中

```python
def backup_stop_due(protection, stop_price, last_price, in_session) -> bool:
    return (
        protection is not None
        and (protection.unprotected_since is not None or protection.staggered)
        and in_session
        and stop_price is not None
        and last_price <= stop_price
    )
```

签名不变，`ExecutionService.backup_stop_due` 不变。止损价仍按 `stop_price_of`：加仓第一次成交后就换成加仓方案的，和券商止损单的规则一致。

告警由调用方按无保护仓位发出（stop-protection spec「挂单或改单被拒」），错开中不是无保护仓位，不用改代码就不告警。

### D6 和其他流程的关系

- **卖单**：开关打开、有买单没结束时，卖单的止损目标也是 0，止损单撤掉以后卖单就提交，在 Alpaca 上被拒（省略清单第一条）。开关关闭时和现在一样
- **提前撤单**：有撤单要求的买单不先对齐、不提交，结束后跟进时的对齐恢复止损目标，按已有规则挂回
- **紧急退出流程**：备用止损触发后，先 `cancel_orders(symbol)` 撤掉买单，买单结束后止损目标恢复为持仓，可能先挂上一张止损单；清仓卖单提交前再按「卖单先让出股数」撤掉它。多一次挂单和撤单，换来中间有券商止损单
- **对账**：对齐时同样按开关算目标。当日买单收盘时都已结束，收盘后的对账里每只持仓照常有止损单
- **交易记录**：每次错开撤掉的止损单照常有一条交易记录（结束，没有成交），之后挂的是序号加 1 的新止损单

### D7 测试

```
tests/modules/execution/domain/test_protection.py   stop_target 的 stagger、StopTarget.staggered、
                                                    stop_withdrawn、backup_stop_due 加上错开中
tests/modules/execution/test_config.py              新配置项的默认值和打开
tests/modules/execution/test_stagger_service.py     新增：spec 场景
tests/modules/execution/test_repo.py                止损保护的 staggered 存取；没有这个字段的旧记录
tests/modules/execution/test_protection_service.py  新增打开开关、会加仓的状态机
```

- `test_stagger_service.py` 和其他执行测试文件一样，自己建一套执行测试环境，配置打开开关；每个新增场景一个测试：stop-protection spec「买单和止损单错开」九个、「本地备用止损的触发」两个、「保存止损保护」一个，order-lifecycle spec「先查询再提交」一个。「止损目标」的两个新场景是纯计算，放 `test_protection.py`；「执行配置」的两个场景放 `test_config.py`
- 测试替身不用改：假券商没有防自成交保护，由状态机的不变式检查不会同时挂着
- 状态机 `StaggeredStopMachine` 继承 `StopProtectionMachine`，配置打开开关：
  - 增加两步：持有时提交加仓（股数随机），加仓订单成交随机股数
  - 增加不变式：券商那边 BBB 不会同时有挂着的买单和挂着的止损单
  - 已有的不变式照样成立：最多一张挂着的止损单，止损单和卖单合计不超过持仓
  - 结束时：按打开开关算的止损目标对齐；有买单没结束、持仓大于 0 时是错开中，否则不是
  - 用 `event` 统计样例里出现过加仓提交给券商、错开以后挂回止损单
- 原来的 `StopProtectionMachine` 不变，继续检查开关关闭时的行为

## Risks / Trade-offs

- [错开期间券商那边没有止损单，程序停了就没有保护] → 只在模拟盘打开；买单是当日单，最晚当天收盘结束；这段时间本地备用止损按同一个止损价盯着。券商止损单本来也只在常规交易时段触发，盘中程序在线时两者效果接近
- [本地备用止损靠行情和程序判断，比券商止损单慢] → 分钟级，模拟盘可以接受；行情断了由健康监控进入降级
- [模拟盘和实盘不同：实盘加仓期间一直有券商止损单] → 开关把差别限定在一处；M7 确认 moomoo 没有同样的限制
- [每次加仓多一次撤单和一次挂单，止损单序号和交易记录多一条] → 每次加仓多两三次券商调用，Alpaca 每分钟 200 次的限频以内
- [收盘时撤掉的买单结束后，止损单在收盘后挂出] → Alpaca 收盘后受理长期有效单，开盘前是已受理；收盘后持仓不变，不需要改单

## Migration Plan

- 没有数据库迁移：止损保护多的字段在 JSONB 里，旧记录读成 False
- 开关默认关闭，「启动与装配」打开之前行为不变
- 回滚：revert 代码即可。已经写入的记录多出的 `staggered` 字段，旧代码读取时 `TypeAdapter` 会忽略（标准库 dataclass 默认忽略多余字段）
