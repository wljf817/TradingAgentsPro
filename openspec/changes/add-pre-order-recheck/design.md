# Design

## Context

现状（变更 2、3 之后）：

- `evaluate_plan(request)` 是纯函数，内部选规则组、定请求股数，再调用 `decide(request, rules, requested)`。`decide` 已经是可以单独调用的函数
- `RiskService.check` 读出全部有效预留，组装 `RiskRequest`，判定，有最终股数时保存 `Reservation(plan, decision)`。已有预留的方案直接返回保存的结果（变更 3 D4）
- `Reservation` 有 `fill` 和 `release`，预留股数是 `decision.quantity`
- 规则只看 `RiskRequest` 里的数据：其他方案的预留由 service 传入，"未完成的开仓方案"只看传入的开仓预留里有没有这只股票
- `exposure.execution_day(now, calendar)` 给出执行交易日；交易日历有 `session(day)`、`session_at(moment)`

动机见 proposal.md 的 Why。

### 模块边界

```
contracts（Pydantic，跨模块传递）
  trade_plan.py  TradePlan 增加 expires_at、max_price_deviation   <- PM / 人工录入
  account.py     AccountSnapshot（不变）                           <- 调用方传入：首次检查用状态库，再校验用券商
  risk.py        RiskDecision 增加 expires_at                      -> 人工确认、执行模块（变更 5）

modules/risk
  config.py              增加 price_deviation_min / max / default
  domain/exposure.py     增加 execution_session、effective_expiry、effective_price_deviation
  domain/rules.py        增加规则 price_deviation_range，登记到开仓类
  domain/engine.py       通过或调整时结果带上生效的有效期
  domain/reservation.py  Reservation 增加 rechecked 和 recheck(decision)
  domain/recheck.py      新增：recheck_plan、plan_ended、OutsideSessionError
  service.py             RiskService 增加 recheck
  __init__.py            增加导出 OutsideSessionError
```

本次只涉及 `risk` 一个模块。再校验收 `AccountSnapshot`、实时最新价（`Decimal`）和方案 ID，返回 `RiskDecision`，和首次检查是同一个结果模型。

## Goals / Non-Goals

**Goals:**

- 再校验复用首次检查的规则和判定，不另写一套规则
- 作废的判断（有效期、价格偏离、跌破止损价）是纯函数，可以单独测试
- 再校验之后，持仓加全部有效预留仍然不超限，和变更 3 的性质测试是同一条性质

**Non-Goals:**

- 读取实时数据失败（断线、超时、没有报价）：由 M2 的下单工作流处理，这时不调用再校验，也不下单。再校验的输入都是必填的
- 按「编程风格」省略的情况：
  - 首次检查时方案的有效期已经过去，或者早于执行交易日开盘：不拒绝，到再校验时作废
  - 有效期没有上限
  - 首次检查和再校验之间修改了配置：再校验按新配置的默认值计算允许价格偏离
  - 清仓再校验时，实时可卖数量比预留多（中间另一笔加仓成交）：按实时可卖数量下单，预留随之变大
  - 方案已经下单、部分成交后又被调用再校验：不检查，属于调用方的错误
  - 有效期不要求是 UTC：带时区的时刻直接比较，结果和时区无关

## Decisions

### D1 方案的两个新字段

```python
class TradePlan(BaseModel):
    ...
    expires_at: AwareDatetime | None = None
    max_price_deviation: Decimal | None = Field(default=None, gt=0, lt=1)
```

- 两个字段都可以不填。有效期四种动作都可以填；允许价格偏离只有买入、加仓可以填，减仓、清仓填了时，`_check_fields_for_action` 报格式错误，和止损价、紧急阈值的写法一样
- `AwareDatetime` 让 Pydantic 拒绝不带时区的时刻

考虑过：两个字段必填

- 没有采用：PM 要自己算"执行交易日收盘"，盘后的方案还要算下一个交易日，容易出错。默认值由风控按交易日历算出，PM 只在需要时覆盖

### D2 生效的有效期在首次检查时算出，存进风控结果

```python
def execution_session(now: datetime, calendar: TradingCalendar) -> TradingSession:
    """盘中是当天的交易时段，否则是下一个开盘的交易时段。"""


def effective_expiry(plan: TradePlan, now: datetime, calendar: TradingCalendar) -> datetime:
    """方案写了就用方案的，没写时是执行交易日的收盘时刻。"""
```

- `execution_day` 改为返回 `execution_session(now, calendar).day`，最短交易间隔规则不变
- `engine._approved` 给通过和调整的结果填 `expires_at`；拒绝时为 `None`。四种动作都填
- 生效的允许价格偏离不存：`effective_price_deviation(plan, config)` 只依赖方案和配置，再校验时现算

为什么存有效期：默认值取决于首次检查的时刻。盘后检查的方案，默认到下一个交易日收盘；如果到再校验时再算，就丢掉了"什么时候形成的"。执行模块（变更 5）也要用它决定订单挂多久，人工确认的消息里也要显示。

考虑过：存在 `Reservation` 里，不放进 `RiskDecision`

- 没有采用：人工确认和执行模块拿到的是 `RiskDecision`，而 `Reservation` 只在风控模块内部使用

### D3 新规则「允许价格偏离」

```python
def price_deviation_range(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """生效的允许价格偏离在下限和上限之间（含两端）；方案没填时用默认值。"""
```

- 写法和 `emergency_threshold` 相同，登记到 `OPENING_RULES`，放在 `emergency_threshold` 后面
- 和股数无关，单调性自然成立
- `RiskConfig` 增加 `price_deviation_min = 0.01`、`price_deviation_max = 0.10`、`price_deviation_default = 0.03`，都 `gt=0`

### D4 预留记录保留首次检查的结果，另存再校验的结果

```python
@dataclass(frozen=True, slots=True)
class Reservation:
    plan: TradePlan
    decision: RiskDecision  # 首次检查的结果
    rechecked: RiskDecision | None = None  # 最近一次再校验的结果（通过或调整）
    filled: int = 0
    released: bool = False

    @property
    def quantity(self) -> int:
        """预留股数：最近一次再校验的最终股数；没有再校验过时是首次检查的最终股数。"""

    def recheck(self, decision: RiskDecision) -> Self:
        """再校验通过或调整，预留股数改为它的最终股数；已结束的预留原样返回。"""
```

- `check` 遇到已有预留的方案，仍然返回 `decision`，spec「再校验后重复检查仍返回首次检查的结果」成立，变更 3 D4 的理由不变
- 再校验拒绝时调用已有的 `release()`
- `opening_reservation`、`sell_reservation` 用 `remaining`，`remaining` 用 `quantity`，所以缩小后的股数自动计入后续检查

考虑过：再校验后直接替换 `decision`

- 没有采用：重复检查会返回再校验的结果，改变了 risk-reservations spec「同一方案重复检查」的含义，还丢掉了首次检查的结果

### D5 再校验的纯逻辑

```python
class OutsideSessionError(RuntimeError):
    """不在常规交易时段内调用再校验。"""


def recheck_plan(
    request: RiskRequest, reservation: Reservation, last_price: Decimal
) -> RiskDecision:
    """request.plan 是 reservation.plan；request 里的预留已经去掉这个方案自己的。"""


def plan_ended() -> RiskDecision:
    """没有有效预留的方案：拒绝，原因是"方案已结束"。"""
```

`recheck_plan` 的流程：

```
当前时刻 >= 生效的有效期?                         -- "有效期"
开仓类: |实时价 - 限价| / 限价 > 生效的允许偏离?    -- "价格偏离"
开仓类: 实时价 <= 止损价?                         -- "跌破止损价"
  |
  +-- 有任何一项 --> 拒绝，原因是这几项，不重跑规则
  |
  v
规则组：开仓类或减仓类（和 evaluate_plan 相同）
请求股数：清仓是 sellable_quantity（实时账户、去掉自己后的卖出预留）；其他是 reservation.quantity
  |
  v
decide(request, rules, requested)，结果的 expires_at 换成 reservation.decision.expires_at
```

- 作废时不重跑规则：方案已经不会下单，规则的原因只会干扰 PM 看作废的原因
- 生效的有效期沿用首次检查的：再校验时重新算会得到当天收盘，和首次检查的值在没过期时相同，但直接沿用不需要这个推理
- 生效的紧急阈值由 `decide` 按方案和配置重新给出，和首次检查相同
- 选规则组的代码和 `evaluate_plan` 重复一行，不为它抽函数

### D6 service 的再校验

```python
def recheck(
    self,
    plan_id: UUID,
    account: AccountSnapshot,
    last_price: Decimal,
    daily_loss_breaker_tripped: bool,
) -> RiskDecision: ...
```

```
now = clock.now()
calendar.session_at(now) 为空? -- 是 --> 抛 OutsideSessionError，不读也不写预留
  |
  v
store.get(plan_id) 没有，或者不是有效预留? -- 是 --> 返回 plan_ended()，不写存储
  |
  v
用除这个方案以外的全部有效预留组装 RiskRequest（plan 是预留里的方案）
  |
  v
decision = recheck_plan(request, reservation, last_price)
有最终股数? -- 是 --> store.save(reservation.recheck(decision))
           -- 否 --> store.save(reservation.release())
  |
  v
返回 decision
```

- `check` 和 `recheck` 组装 `RiskRequest` 的部分抽成一个私有方法，参数里多一个"要去掉的方案 ID"
- 按方案 ID 找方案，不让调用方再传一次方案：方案内容以风控保存的为准，避免传入的方案和预留的方案不一致
- 实时最新价单独传入，不从账户数据里取：新建仓的股票没有持仓，账户数据里没有它的价格。退出方案用不到这个价格，参数仍然必填，调用方本来就要读报价
- 重复调用：调整后再调用，请求股数是缩小后的股数，结果不变；作废后再调用，结果是"方案已结束"。所以 M2 的 DBOS step 重跑时不会放大股数，也不会重复释放

考虑过：再校验时不在盘中就返回"等待"，由风控决定等到什么时候

- 没有采用：等待属于下单工作流（DBOS 持久化休眠），风控不读时间以外的状态。不在盘中调用是调用方的错误

### D7 一次只处理一个方案

和变更 3 D5 一样：M1 只有同步调用，测试按两种先后顺序各跑一遍 spec「再校验和检查同时进行」。真正的串行做法放到 M2，`check` 和 `recheck` 用同一个锁或同一个并发为 1 的队列。

### D8 测试

```
tests/contracts/test_trade_plan.py               risk-rules spec「交易方案的格式」的新场景
tests/modules/risk/builders.py                   增加 recheck 的辅助：按基准账户和限价再校验
tests/modules/risk/domain/strategies.py          方案随机带有效期和允许价格偏离
tests/modules/risk/domain/test_exposure.py       生效的有效期、生效的允许价格偏离
tests/modules/risk/domain/test_rules.py          「允许价格偏离」规则的场景
tests/modules/risk/domain/test_engine.py         结果里的生效有效期
tests/modules/risk/domain/test_reservation.py    rechecked 和 recheck(decision) 的状态变化
tests/modules/risk/domain/test_recheck.py        有效期、价格偏离、跌破止损价、请求股数（直接构造请求和预留）
tests/modules/risk/test_service.py               pre-order-recheck spec 的场景、risk-reservations spec 的新场景、性质测试
tests/modules/risk/test_config.py                新阈值的默认值
```

- service 测试用 `FakeClock` 拨动时间来模拟盘后检查、开盘再校验
- 性质测试扩展变更 3 的 `test_holdings_plus_reservations_never_exceed_the_limits`：步骤里加入"再校验已检查过的第几个方案"，用同一份账户、实时价等于限价（市价单的退出方案用任意正数）。每一步之后：
  - 持仓加全部有效预留仍然不超限
  - 买入、加仓、减仓的再校验最终股数不超过再校验前的预留股数
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [盘后方案开盘时价格波动大，默认 3% 的偏离可能让方案频繁作废] → PM 可以为这类方案写更大的允许偏离，最多 10%；作废只是退回 PM 重新判断，不会造成亏损
- [再校验调整后不重新确认，下单股数和人确认的不同] → 只会变少；M2 的通知里标明"再校验时调整"及原因
- [预留在确认到下单之间一直占着额度] → 有效期限制了占用时间，默认最长到执行交易日收盘
- [开盘瞬间的"最新成交价"可能还是盘前价] → M2 接 moomoo 时确认报价字段和开盘后的调用时机，见 Open Questions
- [`TradePlan`、`RiskDecision` 增加字段] → `TradePlan` 新字段可选，已有测试不用改；`RiskDecision` 的新字段只由 engine 构造，测试里直接构造的地方（`test_reservation.py`）一并补上

## Migration Plan

只新增代码和可选字段，`RiskDecision` 多一个必填字段。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- M2 的下单工作流在开盘后多久调用再校验，用 moomoo 报价里的哪个字段作为实时最新价：不影响本变更的 spec 和任务
