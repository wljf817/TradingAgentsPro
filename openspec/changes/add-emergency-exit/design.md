# Design

## Context

现状（变更 1 到 7 之后）：

- 风控（`RiskService`）的 `check` 和 `recheck` 都把"单日亏损熔断是否已经触发"作为输入，熔断后拒绝开仓；减仓和清仓只检查可卖数量，不受熔断限制
  - 清仓不写股数，请求股数是可卖数量；可卖数量为 0 时拒绝
  - 同一个方案 ID 已经有预留时，`check` 直接返回第一次的结果；预留已经结束时，`recheck` 给出"方案已结束"
  - 再校验只能在常规交易时段调用；减仓和清仓不因为价格偏离作废
  - 方案的紧急阈值只检查范围（0.03 到 0.20，默认 0.08），从什么价格算起留给本变更
- 执行模块（`ExecutionService`）按方案 ID 建订单，同一个方案不会建第二笔。清仓的卖单提交前，先把止损单撤掉；未结束卖单的合计超过持仓时不提交
- `backup_stop_due` 只给出本地备用止损该不该触发；触发后怎样生成方案留给本变更（止损保护的 design D8）
- 交易方案（`TradePlan`）要求板块，执行模块没有板块；持仓数据（`Position`）里有

动机见 proposal.md 的 Why，要求见 specs/emergency-exit/spec.md。

### 模块边界

新增紧急退出模块，不改风控和执行模块，不新增也不修改跨模块传递的 Pydantic 模型。判定结果里的方案是已有的 `TradePlan`，交给风控检查。

```
modules/emergency
  __init__.py      导出公开的名字
  domain/exit.py   Holding、ExitReason、EmergencyExit，以及 reference_price、drop_triggered、
                   daily_loss_tripped、close_plan、emergency_exits
  service.py       EmergencyService：check、breaker_tripped
  config.py        EmergencyConfig：熔断比例
```

没有 `ports.py` 和 `repo.py`：判定需要的数据来自行情、券商和执行模块的订单，由调用方拼好传入，和风控读取账户数据的方式一样；熔断状态放在内存里（D4）。

## Goals / Non-Goals

**Goals:**

- 判定全部由纯函数完成，service 只负责读当前时刻、找交易时段、记下熔断状态
- 重复判定不重复下单：只靠方案 ID，不另存"已经处理过哪些股票"
- 金额比较用乘法，不做除法，结果精确

**Non-Goals:**

按「编程风格」省略的情况：

- 停牌、涨跌停暂停交易时市价卖单被拒：当天不重试（proposal「需要人决定」第 7 条）
- 盘中出入金让权益变化，被算成盈亏：不处理，账户只给系统用，出入金很少
- 熔断当天进程重启：熔断状态丢失，下一次判定重新算（D4）
- 行情延迟或过时：本模块不检查行情的时间，由健康监控处理
- 同一只股票在一次判定的输入里出现两次、前一交易日收盘时的权益不大于 0：不检查
- 收盘前片刻才触发、卖单到截止时刻还没成交：按已有规则记为已过期；第二天参考价换成新的前一交易日收盘价，不会因为昨天的跌幅再触发，止损单照常覆盖

## Decisions

### D1 新建紧急退出模块

架构图里紧急风险退出属于"不经过主流程的保护"这个节点，和风控引擎、执行模块是不同的节点，所以单独成模块。它做的事是"从行情和账户生成方案"，和风控的"检查方案"方向相反。

考虑过：放进风控模块，风控已经有熔断输入和紧急阈值的配置

- 没有采用：风控只检查别人交来的方案，不生成方案；而且一个变更只改一个节点

考虑过：放进执行模块，和本地备用止损放在一起

- 没有采用：执行模块不生成方案，也没有板块（止损保护的 design D8）

### D2 参考价和个股跌幅

```python
def reference_price(holding: Holding) -> Decimal | None:
    """今天有开仓成交时是成交均价，否则是前一交易日收盘价；都没有时为 None。"""


def drop_triggered(holding: Holding) -> bool:
    """参考价和最新价都有，并且 最新价 ≤ 参考价 ×（1 − 紧急阈值）。"""
```

- "跌幅 ≥ 紧急阈值"等价于"最新价 ≤ 参考价 ×（1 − 紧急阈值）"，参考价大于 0。用乘法，Decimal 结果精确；除法会按精度舍入，边界值可能判错
- 今天开仓成交的均价由调用方给出。最短交易间隔至少 1 个交易日，同一只股票当天最多一笔开仓订单，所以"这笔订单"是唯一的

考虑过：参考价取持仓均价

- 没有采用：会和止损价重复（proposal「需要人决定」第 1 条）

### D3 只在常规交易时段判定

```python
def check(self, holdings, equity, previous_close_equity) -> tuple[EmergencyExit, ...]:
    now = self._clock.now()
    session = self._calendar.session_at(now)
    if session is None:
        return ()
    if daily_loss_tripped(equity, previous_close_equity, self._config.daily_loss_breaker_ratio):
        self._tripped_on = session.day
    return emergency_exits(holdings, self._tripped_on == session.day, session.day)
```

- 时段之外直接返回，不看权益，熔断也不会触发（proposal「需要人决定」第 3 条）
- 开盘后的第一次判定用的就是开盘后的价格，盘前的跌幅如果还在，这时触发

### D4 熔断状态放在内存里，保持到收盘

```python
self._tripped_on: date | None = None  # 熔断触发的交易日；新建以后还没触发过时为 None


def breaker_tripped(self) -> bool:
    session = self._calendar.session_at(self._clock.now())
    return session is not None and session.day == self._tripped_on
```

- 只在触发的那个交易时段内为真：开仓只在盘中下单，收盘以后形成的方案在下一个交易日执行（proposal「需要人决定」第 6 条）
- 触发以后不再看权益，回升也不解除；第二天自然失效，不需要清掉
- 不需要存储：熔断以后全部持仓都会清仓，亏损已经实现，权益不会再大幅回升。重启以后第一次判定，单日亏损通常仍在熔断线以上，熔断会重新触发
  - 例外是清仓时卖在了熔断价之上，单日亏损回到熔断线以内，重启后当天又能开仓。这需要当天重启，而且开仓仍要经过全部风控规则和人工确认
- 同一个 service 实例由调用方串行调用，和风控、执行模块一样

考虑过：存进存储，重启后沿用

- 没有采用：要多一个端口和一张表，只为了上面那个例外

### D5 代码触发的清仓方案

```python
def close_plan(symbol: str, sector: str, day: date) -> TradePlan:
    """这只股票在这个交易日的代码触发的清仓方案：市价单，卖出全部可卖数量。"""
    return TradePlan(
        plan_id=uuid5(_CLOSE_PLAN_NAMESPACE, f"{symbol}/{day.isoformat()}"),
        symbol=symbol,
        sector=sector,
        action=TradeAction.CLOSE,
        order_type=OrderType.MARKET,
    )
```

- `_CLOSE_PLAN_NAMESPACE` 是写在 domain 里的固定 UUID。`uuid5` 是确定性的，回放时能重现，不在 Ruff 的禁用名单里（只禁 `uuid1`、`uuid4`），`uuid` 也已经在纯区域的白名单里
- 同一只股票、同一个交易日总是同一个方案 ID，重复判定时：
  - 风控的 `check` 返回第一次的结果，不重复预留
  - 方案已经结束时，`recheck` 给出"方案已结束"
  - 执行模块不会为它建第二笔订单
- 市价单：紧急退出要先卖出来，限价单可能卖不掉
- 不填有效期：风控给出执行交易日的收盘时刻。判定只在盘中，所以就是当天收盘，和订单的截止时刻一致

考虑过：方案 ID 从 ID 生成端口取随机值，另存"已经为哪些股票生成过"

- 没有采用：要多一个端口和存储，才能做到重复判定不重复下单

考虑过：方案 ID 里带上原因（个股跌幅、组合熔断、备用止损）

- 没有采用：同一只股票会有两三个清仓方案。后一个被前一个的卖出预留挡住，结果是拒绝，没有坏处，但每次都要多走一遍风控、多一条拒绝记录

### D6 判定结果怎样接到风控和执行模块

本变更只给出判定结果。M2 的紧急退出工作流按股票串行编排，大致如下：

```
常规交易时段内，每次行情更新或定时（频率 M2 定）：
  exits = emergency.check(holdings, equity, previous_close_equity)
  熔断已触发：撤掉全部还挂着的买单                         紧急撤单变更
  对每个 exit：
    撤掉这只股票还挂着的买单和卖单，等它们结束、释放预留     紧急撤单变更
    作废这只股票还没下单的退出方案，释放预留                 M3 的调度器
    decision = risk.check(exit.plan, 券商账户, emergency.breaker_tripped())
    拒绝（比如可卖数量为 0）：记日志，跳过
    decision = risk.recheck(plan_id, 券商账户, 实时价, emergency.breaker_tripped())
    order = execution.submit(exit.plan, decision)
      OrderPausedError、NotReconciledError：等一会儿，从 recheck 重来
    跟进到终态，上报成交、释放预留（已有的下单工作流）
    通知
```

- 紧急退出走同一个再校验入口，有效期用默认值（下单前再校验「需要人决定」第 6 条）。退出不检查价格偏离，所以再校验实际只看有效期和实时的可卖数量
- 不需要人工确认：首次检查之后直接再校验、下单
- 其他方案的风控检查和再校验，熔断输入同样用 `emergency.breaker_tripped()`
- 本地备用止损触发后，用 `close_plan(symbol, sector, 当前交易日)` 生成方案，从 `risk.check` 开始走同一条流程。和紧急退出同一天触发时，是同一个方案

### D7 接口

```python
class ExitReason(StrEnum):
    STOCK_DROP = "stock_drop"  # 个股跌幅
    DAILY_LOSS_BREAKER = "daily_loss_breaker"  # 组合熔断


@dataclass(frozen=True, slots=True)
class Holding:
    """判定紧急退出用的一只持仓。数据来自行情、券商和执行模块的订单，不加约束。

    - emergency_threshold：这只股票最近一次有成交的开仓方案的生效紧急阈值
    - previous_close、last_price：前一交易日收盘价、最新价；没有行情时为 None
    - entry_price_today：今天开仓成交的均价；今天没有开仓成交时为 None
    """

    symbol: str
    sector: str
    emergency_threshold: Decimal
    previous_close: Decimal | None
    last_price: Decimal | None
    entry_price_today: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EmergencyExit:
    """要清仓的一只股票：代码触发的清仓方案和原因。"""

    plan: TradePlan
    reason: ExitReason


def daily_loss_tripped(equity: Decimal, previous_close_equity: Decimal, ratio: Decimal) -> bool:
    """前一交易日收盘时的权益 − 当前权益 ≥ 前一交易日收盘时的权益 × ratio。"""


def emergency_exits(
    holdings: Iterable[Holding], breaker_tripped: bool, day: date
) -> tuple[EmergencyExit, ...]:
    """熔断已触发时是全部持仓，原因是组合熔断；否则是触及个股跌幅的持仓。按传入的顺序。"""


class EmergencyConfig(BaseModel):
    # 单日亏损达到前一交易日收盘时的权益乘以这个比例时熔断
    daily_loss_breaker_ratio: Decimal = Field(default=Decimal("0.03"), gt=0, lt=1)


class EmergencyService:
    def __init__(
        self, clock: Clock, calendar: TradingCalendar, config: EmergencyConfig
    ) -> None: ...
    def check(
        self, holdings: Iterable[Holding], equity: Decimal, previous_close_equity: Decimal
    ) -> tuple[EmergencyExit, ...]: ...
    def breaker_tripped(self) -> bool: ...
```

- `Holding` 和 `EmergencyExit` 是本模块的 frozen dataclass，由包根导出，和执行模块的 `WorkingOrder`、`Reconciliation` 一样；不放进 contracts，因为只有 M2 的工作流在模块之外用它们
- 包根导出：`EmergencyConfig`、`EmergencyExit`、`EmergencyService`、`ExitReason`、`Holding`、`close_plan`、`daily_loss_tripped`、`drop_triggered`、`emergency_exits`、`reference_price`。纯函数导出是为了测试只从包根 import

### D8 测试

```
tests/modules/emergency/builders.py            基准数据：纽交所样例日历、2026-10-14 15:00Z、AAA、BBB、
                                               两个权益；新建 EmergencyService 的辅助函数
tests/modules/emergency/domain/test_exit.py    参考价表的两行、个股跌幅的边界和缺数据、单日亏损的边界、
                                               清仓方案的字段和方案 ID、判定结果表的两行；性质测试
tests/modules/emergency/test_service.py        spec 里和时刻、熔断状态有关的场景
tests/modules/emergency/test_config.py         spec「紧急退出配置」
tests/modules/emergency/test_close_plan_risk.py
                                               清仓方案接上风控：用风控的测试辅助检查和再校验
```

- 性质测试（Hypothesis）：任意持仓（有的缺行情、有的今天有开仓成交）、任意熔断状态。每只股票最多出现一次；熔断已触发时恰好是全部持仓、原因都是组合熔断；没有触发时恰好是 `drop_triggered` 为真的持仓；方案 ID 只由股票和交易日决定
- `test_close_plan_risk.py` 不对应 spec 场景，检查 D6 的前半段：
  - 用风控测试的基准账户，`check` 清仓方案，结论为通过，最终股数是可卖数量，生效的有效期是当天收盘
  - 再判定一次得到同一个方案，`check` 返回同一个结果，有效预留只有一份
  - `recheck` 按实时持仓给出最终股数
  - 熔断已触发时，开仓方案被拒绝、清仓方案照常通过
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [参考价是前一交易日收盘价，隔夜跳空低开超过阈值时，开盘就清仓，可能卖在当天最低点] → 这正是紧急风险事件的定义；阈值由 PM 按这只股票的波动设，范围 3% 到 20%
- [熔断时全部清仓，大盘急跌后反弹会错过] → 人决定的取舍（proposal「需要人决定」第 2 条）；熔断比例可以配置
- [代码触发的清仓当天只有一次，被拒以后不重试] → 卖单结束以后，止损单恢复覆盖全部持仓；M2 按订单结果告警
- [判定依赖调用方给对数据：生效的紧急阈值、今天开仓成交的均价] → M2 拆变更时写清来源：今天开仓成交的均价从执行模块的订单得出；生效的紧急阈值和止损价取自同一笔开仓订单
- [一次清仓多只股票，moomoo 限频：下单每 30 秒 15 次，改单和撤单每 30 秒 20 次] → 最多 8 只持仓，每只撤一次止损单、下一次卖单，在限频以内；M7 的适配器按限频排队

## Migration Plan

只新增一个模块。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- 下面几项只影响 M2 的接入，不影响本变更的 spec 和任务：
  - Alpaca 账户的 `last_equity` 是不是前一交易日收盘时的权益，接入时实测
  - 判定的频率：每次行情推送都判定，还是定时判定
