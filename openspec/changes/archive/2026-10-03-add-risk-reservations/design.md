# Design

## Context

现状：

- 风控模块只有 `config.py` 和 `domain/`。`evaluate_plan(request)` 是纯函数，预留是 `RiskRequest` 的两个输入：
  - `OpeningReservation(symbol, sector, cash, market_value, risk)`
  - `SellReservation(symbol, quantity)`
- 规则只读这些字段：资金、风险、板块市值、股票（计入持仓名额，同一股票有开仓预留时拒绝）、卖出股数
- `TradePlan` 没有 ID
- `shared` 有 `Clock` 协议，`tests/fakes/` 有 `FakeClock` 和纽交所样例日历
- `pyproject.toml` 禁用了 `uuid.uuid4`，ID 要通过 ID 生成端口获取（端口还没有建）

动机见 proposal.md 的 Why。

### 模块边界

```
contracts（Pydantic，跨模块传递）
  trade_plan.py  TradePlan 增加 plan_id: UUID          <- 人工录入（M2）/ 组装方案的 workflow（以后）
  account.py     AccountSnapshot（不变）                <- 调用方传入
  risk.py        RiskDecision（不变）                   -> 调用方

modules/risk
  domain/reservation.py  Reservation（frozen dataclass）：方案、风控结果、累计成交、是否已释放
                         按剩余股数和当前持仓算出 OpeningReservation / SellReservation
  ports.py               ReservationStore：get、active、save
  service.py             RiskService：check、record_fill、release
  __init__.py            增加导出 RiskService、ReservationStore、Reservation
```

本次只涉及 `risk` 一个模块。跨模块传递的 Pydantic 模型：

- `check` 收 `TradePlan` 和 `AccountSnapshot`，返回 `RiskDecision`
- `record_fill` 和 `release` 只收方案 ID 和股数

`Reservation` 只在风控模块内部使用。包根导出它，是为了让存储的实现（现在是 `tests/fakes/`）能引用它。

## Goals / Non-Goals

**Goals:**

- 变更 2 的规则、判定和阈值一行不改。service 只是在纯函数外面加一层：读预留、判定、写预留
- 预留的状态变化和额度计算都是纯函数，可以单独测试
- 和持仓相比，预留占用的额度只会和真实情况一致或者偏多，不会偏少。加仓部分成交、止损价改变时也是这样

**Non-Goals:**

- 不考虑性能：有效预留最多十几份，每次检查全部读出
- 按「编程风格」省略的情况：
  - 累计成交股数超过预留股数，或者比上次小：不校验，数据来自券商
  - 账户在检查前读取，预留在检查时读取；两次读取之间恰好处理完一笔成交时，这笔成交少算一次。窗口只有毫秒级
  - 清仓方案通过后持仓又增加（比如另一笔加仓成交）：卖出预留只覆盖检查时的可卖数量
  - 券商端止损单成交，使持仓少于卖出预留：可卖数量算出负数，结论就是拒绝，不另外处理
  - 同一个方案 ID 第二次检查时，传入的方案内容不同：不比较，直接返回第一次的结果
  - 释放和上报成交插在一次检查的中间：只会让这次检查多算预留，不加限制

## Decisions

### D1 预留记录：方案、风控结果、累计成交、是否已释放

```python
@dataclass(frozen=True, slots=True)
class Reservation:
    plan: TradePlan
    decision: RiskDecision  # 通过或调整，最终股数不为空
    filled: int = 0
    released: bool = False
```

- 预留股数是 `decision.quantity`，剩余股数是预留股数减 `filled`
- 有效预留：没有释放，并且剩余股数大于 0。只有"有效"和"结束"两种状态，所以不单独存状态字段，由 `released` 和 `filled` 推出
- 状态变化写成返回新记录的方法：`fill(filled)` 和 `release()`。记录已经结束时原样返回
- 存整个方案和风控结果，原因有两个：
  - 重复检查时要返回第一次的结果（D4）
  - 计算额度要用方案的股票、板块、动作、限价和止损价

考虑过：只存剩余股数，每次成交时减去这次新成交的股数

- 没有采用：同一笔成交上报两次就会减两次。上报累计值天然幂等，moomoo 订单的 dealt_qty 本来就是累计值

### D2 预留占用的额度：每次检查时按剩余股数和当前持仓计算

公式见 spec「预留占用的额度」。实现上，`domain/reservation.py` 提供两个函数，计算都调用 `exposure.py` 已有的函数：

- `opening_reservation(reservation, account, buffer_ratio) -> OpeningReservation`：资金、市值、风险（`merged_risk` 减 `holding_risk`，负数记 0）
- `sell_reservation(reservation) -> SellReservation`：股数为剩余股数

规则拿到的仍然是 `OpeningReservation` 和 `SellReservation`，不需要改。

考虑过：通过时把资金、市值、风险算好存下来，成交时按比例缩小

- 没有采用：加仓第一次成交后，整只股票的止损价就改成了新的，已有持仓的风险随之变化，存下来的风险就对不上了。用 spec 场景「加仓的预留按当前持仓计算增加的风险」的数据：

| 时刻 | CCC 持仓的计划风险 | 预留的风险：存好的数按比例缩小 | 预留的风险：按当前持仓重算 |
| --- | --- | --- | --- |
| 加仓前：100 股，止损价 45 | 525 | 0 | 0 |
| 成交 25 股后：125 股，止损价 48 | 331.5 | 0 | 106.5 |
| 全部成交后：150 股，止损价 48 | 438 | — | — |

  按比例缩小会少算 106.5；重算后 331.5 + 106.5 正好等于全部成交后的 438

增加的部分为负数时记 0：加仓成交前，券商端的止损单还是旧止损价，方案也可能被作废，所以不能提前按新止损价把风险额度让给别的方案。

### D3 service 的三个操作

```python
class RiskService:
    def __init__(
        self, store: ReservationStore, clock: Clock, calendar: TradingCalendar, config: RiskConfig
    ) -> None: ...

    def check(
        self, plan: TradePlan, account: AccountSnapshot, daily_loss_breaker_tripped: bool
    ) -> RiskDecision: ...

    def record_fill(self, plan_id: UUID, filled_quantity: int) -> None: ...

    def release(self, plan_id: UUID) -> None: ...
```

`check`：

```
store.get(plan_id) 有记录? -- 是 --> 返回记录里的风控结果（D4）
  | 否
  v
读出全部有效预留：开仓类按 D2 算出 OpeningReservation，减仓类算出 SellReservation
  |
  v
evaluate_plan(RiskRequest(..., now=clock.now(), calendar, config))
  |
  v
有最终股数（通过或调整）? -- 是 --> store.save(Reservation(plan, decision))
  |
  v
返回风控结果
```

`record_fill` 和 `release`：按方案 ID 取记录；没有记录就什么都不做，有记录就保存 `fill(n)` 或 `release()` 的结果。

- 账户数据和熔断状态由调用方传入，和变更 2 D1 一致：首次检查用状态库的数据，再校验用券商的实时数据
- "现在"由 service 从 Clock 读取，再作为参数传给 domain
- 没有记录时不报错：调用方（作废、人工拒绝、订单结束）不需要先判断这个方案有没有预留

### D4 同一方案重复检查时返回第一次的结果

M2 以后，检查会在 DBOS 的 step 里运行。step 在保存预留之后、记录完成之前崩溃，恢复时会重跑：

- 如果重新判定，方案会看到自己的预留，以"未完成的开仓方案"被拒绝
- 调用方于是认为方案被拒绝了，不会再释放它；这份预留永远占着额度

所以已有记录的方案直接返回保存的结果。被拒绝的方案没有记录，重跑时重新判定；拒绝不留下任何状态，重新判定没有副作用。

考虑过：靠 DBOS 事务的"只执行一次"保证

- 没有采用：这取决于 M2 怎么把 repo 接进 DBOS 的事务，现在还没定。在 service 里按方案 ID 去重只要两行，也不依赖这个决定

### D5 一次只检查一个方案

- M1 只有同步调用，测试按两种先后顺序各跑一遍 spec「同时提交的两个方案」
- 真正的串行做法放到 M2：根据工作流是同步还是异步，在进程内锁和并发为 1 的 DBOS 队列之间选择，并补上并发测试
- 只有 `check` 需要串行。`record_fill` 和 `release` 插在检查中间时，这次检查只会多算预留（见 Non-Goals）

考虑过：现在就在 service 里加 `threading.Lock`

- 没有采用：M2 的工作流是同步还是异步还没定，如果是异步，要换成 asyncio 的锁

### D6 方案 ID

- `TradePlan` 增加必填字段 `plan_id: UUID`
- 由创建方案的一方生成：M2 人工录入时由录入的 workflow 生成，以后由组装方案的 workflow 生成，都通过 ID 生成端口获取。风控只接收 ID
- 测试：
  - `builders.py` 用计数器给每个方案一个不同的 ID：`UUID(int=1)`、`UUID(int=2)`……（`uuid4` 被 Ruff 禁用）
  - `strategies.py` 用 `st.uuids()`

考虑过：方案 ID 不放进 `TradePlan`，作为 `check` 的另一个参数

- 没有采用：方案还要经过人工确认、执行模块和交易记录，ID 跟着方案走，不会在传递时配错

### D7 事件和调用方

本变更只提供两个操作，由产生事件的变更调用：

| 事件 | 调用 | 由哪个变更接入 |
| --- | --- | --- |
| 方案作废：再校验时超过有效期或价格偏离过大 | `release` | 变更 4 |
| 方案作废：同一股票有新案件排队 | `release` | M3 调度器 |
| 人工拒绝、确认超时 | `release` | 人工确认（M2） |
| 订单成交（部分或全部） | `record_fill`（累计成交股数） | 变更 5 |
| 订单撤销、过期、被拒 | `release` | 变更 5 |
| 重启或断线恢复后，对账发现停机期间的成交和订单结束 | `record_fill`、`release` | 变更 7 |
| 紧急退出前，同一股票还没下单的退出方案 | `release` | 变更 8 |

成交时先更新持仓，再调用 `record_fill`。这样两次写入之间，同一笔成交只会被多算，不会漏算。

### D8 存储端口

```python
class ReservationStore(Protocol):
    def get(self, plan_id: UUID) -> Reservation | None: ...
    def active(self) -> tuple[Reservation, ...]: ...  # 全部有效预留
    def save(self, reservation: Reservation) -> None: ...  # 保存这个方案的最新状态
```

- `tests/fakes/reservations.py`：`InMemoryReservationStore`，按方案 ID 存在 dict 里
- M2 的 `repo.py` 按"只追加不覆盖"实现：每次 `save` 写入一个新版本，读取时取每个方案的最新版本
- 测试里的"重启"：用同一个存储重新创建 `RiskService`

### D9 测试

```
tests/contracts/test_trade_plan.py              risk-rules spec「没有方案 ID」
tests/modules/risk/builders.py                  方案都带方案 ID（D6）；增加 risk_service(store, **changes)
tests/modules/risk/domain/strategies.py         方案带方案 ID；公开账户和方案的策略，供性质测试使用
tests/fakes/reservations.py                     预留存储的内存实现
tests/modules/risk/domain/test_reservation.py   spec「预留占用的额度」，记录的状态变化
tests/modules/risk/test_service.py              spec 其余的场景，以及下面的性质测试
```

- service 测试用 `FakeClock(BASELINE_NOW)`、纽交所样例日历和内存存储
- Hypothesis 性质测试：
  - 前提：账户一开始就满足各项上限。先生成持仓，再按持仓算出满足组合合计风险和板块集中度所需的最低权益，权益在它之上生成；不用过滤，避免大部分样例被丢掉
  - 依次检查任意一串方案，中间随机释放一些预留。方案混入多半能通过的开仓方案，否则大多被拒绝，测不到多份预留叠加
  - 每一步之后，持仓加上全部有效预留，仍然满足：已结算资金、组合合计风险、板块集中度、最多持仓数；每只股票的卖出预留之和不超过持仓股数
  - 这条性质直接检查本变更的目的：后续方案按剩余额度检查，合计不会超限
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [调用方漏了释放，预留一直占着额度，后续方案被无故调整或拒绝] → D7 列出每个结束事件和接入它的变更，各变更的 spec 写明调用。建议变更 7 的每日对账核对"每份有效预留都对应一个还没结束的方案或订单"，到时候再定
- [M1 没有测试并发] → 串行做法和并发测试放到 M2（D5），M2 拆变更时列入
- [加仓预留的风险随持仓变化，同一份预留在不同时刻占用的额度不同] → 这是有意的（D2）：结果只会和真实风险一致或者偏多
- [已经释放的方案再检查一次，仍然返回第一次的结果] → 方案 ID 不复用；释放后再检查同一个方案，说明调用方有错误
- [`TradePlan` 新增必填字段] → 目前只有测试构造方案，一并修改

## Migration Plan

只新增代码，并给 `TradePlan` 加一个必填字段。还没有持久化的数据，不需要迁移；需要回滚时直接 revert。

## Open Questions

- M2 的串行做法用进程内锁，还是并发为 1 的 DBOS 队列：取决于 M2 的工作流是同步还是异步，不影响本变更的 spec 和任务
