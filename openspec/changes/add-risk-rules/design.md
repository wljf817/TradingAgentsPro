# Design

## Context

现状：

- `shared` 已有 Clock 协议和交易日历（`TradingCalendar`）；`contracts` 和 `modules` 只有包说明
- 还没有安装 Pydantic
- 纯区域规则（`tests/architecture/rules.py`）：
  - `contracts` 和 `config` 可以 import pydantic
  - `domain` 不能用第三方包，但可以 import `shared`、`contracts`、本模块的 `config`
  - 标准库白名单已经包含 `decimal`、`enum`、`dataclasses`、`typing`、`collections`，本变更不需要扩充
- import-linter 的 module-layers 契约：模块里只能有 `__init__.py`、`service`、`repo`、`ports`、`domain`、`config`，每一层都可以没有

动机见 proposal.md 的 Why。

以下内容已在临时环境验证（2026-10-02）：

- Pydantic 2.13.5 和本项目的 mypy 2.3.1 可以一起使用。启用 `pydantic.mypy` 插件并设 `init_typed = true` 后，给 Decimal 字段传 float，mypy 报 `arg-type`
- Pydantic 默认的宽松模式在运行时会把 float `0.1` 转成 `Decimal('0.1')`，不报错
- Ruff 的 RUF032（用浮点字面量构造 Decimal）在已选的 `RUF` 规则组里，已经生效

### 模块边界

```
contracts（Pydantic，跨模块传递）
  trade_plan.py  TradeAction, OrderType, TradePlan          <- 人工录入（M2）/ PM（以后）
  account.py     Position, AccountSnapshot                  <- 状态库（首次检查）/ 券商（再校验）
  risk.py        RiskOutcome, RiskViolation, RiskDecision   -> 人工确认、执行模块、状态库

modules/risk
  config.py      RiskConfig：阈值和默认值（Pydantic）
  domain/
    request.py   OpeningReservation, SellReservation, RiskRequest（frozen dataclass）
    exposure.py  计划风险、市值、可卖数量、执行交易日等计算
    rules.py     RiskRule 协议；开仓类和减仓类规则；OPENING_RULES、EXIT_RULES
    engine.py    evaluate_plan(request) -> RiskDecision
  __init__.py    导出 evaluate_plan、RiskRequest、两种预留、RiskConfig
```

本次只涉及 `risk` 一个模块。跨模块传递的 Pydantic 模型是上面三组 contracts。预留属于风控模块自己（变更 3 由它存储），用 domain 里的 dataclass，不放进 contracts。

## Goals / Non-Goals

**Goals:**

- 首次检查和下单前再校验调用同一个纯函数，只是输入的账户数据不同
- 新增规则只要实现接口并登记，不改已有规则，也不改判定逻辑
- 每条规则都可以单独测试，用 Hypothesis 检查单调性

**Non-Goals:**

- 不考虑性能：一次判定最多几十次规则检查
- 按「编程风格」省略的情况：
  - 账户数据不校验：它来自本系统和券商，重复的股票、负数之类不处理
  - 权益为 0 或负数：不特别处理，比例上限小于等于 0，开仓自然被拒绝
  - 调整后的股数远小于请求（比如只剩 1 股）：照常调整，由人工确认把关
  - 配置项之间的关系，比如紧急阈值的默认值不在上下限之间：不校验
  - 最近成交日早于交易日历的覆盖范围：交易日历自己会报错，不另外处理
  - 方案写的板块和持仓记录的板块不一致：不处理，新买入部分按方案的板块计算

## Decisions

### D1 本变更只有纯逻辑，输入全部作为参数传入

风控模块这次只有 `config.py` 和 `domain/`，没有 service 和 ports。`evaluate_plan` 是纯函数：输入是 `RiskRequest`，输出是 `RiskDecision`。

`RiskRequest` 是 frozen dataclass，包含：

- `plan: TradePlan`
- `account: AccountSnapshot`
- `opening_reservations: tuple[OpeningReservation, ...]`
- `sell_reservations: tuple[SellReservation, ...]`
- `daily_loss_breaker_tripped: bool`
- `now: datetime`
- `calendar: TradingCalendar`
- `config: RiskConfig`

账户数据由调用方读取后传入，风控不定义"读取账户"的端口：

- 首次检查用状态库的数据，再校验用券商的实时数据，两者都整理成同一个 `AccountSnapshot`
- 从哪里读、什么时候读由 workflow 决定（M2），风控只做计算

考虑过：风控定义 `AccountSource` 端口，自己读取账户数据

- 没有采用：两个数据来源要么需要两个端口，要么要传一个模式参数；而且风控会因此承担编排 I/O 的工作，这属于 workflows

变更 3 加入 service 和预留的存储端口，service 负责组装 `RiskRequest`、调用这里的判定、保存预留。

### D2 contracts：只校验不可信的数据

所有模型都设 `ConfigDict(frozen=True, extra="forbid")`。

**交易方案**（`trade_plan.py`）：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `symbol` | `str` | 非空 |
| `sector` | `str` | 非空 |
| `action` | `TradeAction` | `buy`、`add`、`reduce`、`close` |
| `quantity` | `int \| None` | 大于 0 |
| `order_type` | `OrderType` | `limit`、`market` |
| `limit_price` | `Decimal \| None` | 大于 0 |
| `stop_price` | `Decimal \| None` | 大于 0 |
| `emergency_threshold` | `Decimal \| None` | 大于 0 且小于 1 |

- spec「交易方案的格式」表里按动作区分的要求，用一个 `model_validator(mode="after")` 检查
- `TradeAction` 提供 `is_opening`，买入和加仓为真
- 方案最终来自模型输出，是不可信的数据，所以在边界上校验格式
- 板块写在方案里：M2 人工录入时填写；以后由组装方案的 workflow 从股票池填入，不由 PM 填写

**账户数据**（`account.py`）：

- `Position`：`symbol`、`sector`、`quantity: int`、`average_cost`、`last_price`、`stop_price`（都是 `Decimal`）
- `AccountSnapshot`：`equity`、`settled_cash`（`Decimal`）、`positions: tuple[Position, ...]`、`last_trade_days: Mapping[str, date]`
- 数据来自本系统和券商，可信，不加约束

**风控结果**（`risk.py`）：

- `RiskOutcome`：`passed`、`adjusted`、`rejected`
- `RiskViolation`：`rule: str`（规则名称，和 spec 一致，比如"已结算资金"）、`message: str`（带具体数字的说明）
- `RiskDecision`：`outcome`、`quantity: int | None`、`emergency_threshold: Decimal | None`、`violations: tuple[RiskViolation, ...]`
- 由风控自己产生，不加校验

规则名称直接用中文：人工确认的消息要显示它，代码里没有按名称分支的地方。

以后的变更给 `TradePlan` 加字段：变更 3 加方案 ID，变更 4 加有效期和允许的价格偏离。PM 的输出模型另外定义（包括理由、引用的 Result ID、投资论点），由 workflow 转换成 `TradePlan`。

考虑过：把预留也放进 contracts

- 没有采用：预留由风控模块自己存储和使用，不跨模块

考虑过：板块不放在方案里，另外传一个"股票到板块"的映射

- 没有采用：多一个输入，而且检查一个方案只需要这一只股票的板块

### D3 数值类型：Decimal 和整数股，不做包装类型

- 金额、价格、比例用 `Decimal`；股数用 `int`
- 不定义 `Money`、`Price`、`Quantity` 之类的类型，也不用 `NewType`
- 本变更只有加、减、乘和比较，没有除法，所以不需要舍入规则。二分查找在整数股数上进行

为什么股数用 `int`：只交易整股，整数运算本来就精确。用 `Decimal` 表示股数，每个地方都要额外检查它是整数。

为什么不做包装类型：

- `NewType` 做运算后会退回 `Decimal`，每一步都要重新包装
- 自定义类要重载运算符，代码量大
- 要防的主要是混入 float，现在已经有三道检查：
  - mypy：函数签名和 Pydantic 插件的 `init_typed`
  - 运行时：`Decimal` 和 float 相加直接报 `TypeError`
  - Ruff：RUF032

mypy 设置（`pyproject.toml`）：

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

Pydantic 在运行时仍会把 float 转成 Decimal（见 Context）。本项目的代码由 mypy 拦住；解析模型输出时怎么处理数字，到接入 PM 时再定。

约定的文字同步修改：

- `openspec/config.yaml` 的 `context`："金额、价格、数量用 Decimal"改为"金额、价格和比例用 Decimal，股数用 int（只交易整股）"
- `shared/__init__.py` 的说明去掉"Decimal 数值类型"

考虑过：股数也用 Decimal，给以后的零碎股留余地

- 没有采用：零碎股不在计划内；真要支持时只影响股数的类型和取整，改动范围清楚

### D4 算法

公式见 spec「计划风险和市值的算法」。实现上：

- 合并后的计划风险按 spec 的展开式计算，不先算合并后的均价，避免除法
- 算出负数记 0 是按整只股票算的，不是按每一笔分别算。加仓时，已有持仓的浮盈可以抵掉新买入部分的风险，这正是"按合并后的数量和新止损价重算"的含义
- 计算函数放在 `domain/exposure.py`，规则和以后变更 3 计算预留都用它们：
  - 已有持仓的计划风险
  - 合并后的计划风险
  - 这只股票的持仓市值、板块市值
  - 可卖数量
  - 执行交易日：`calendar.session_at(now)` 有值就用它，否则用 `calendar.next_session_after(now)`
  - 生效的紧急阈值

### D5 规则接口和判定

每条规则是一个函数，满足下面的协议：

```python
class RiskRule(Protocol):
    def __call__(self, request: RiskRequest, quantity: int) -> RiskViolation | None: ...
```

- 返回 `None` 表示按这个股数执行能通过；否则返回违规，`rule` 是规则名称
- `rules.py` 定义 `OPENING_RULES` 和 `EXIT_RULES` 两个元组。以后新增规则就是写一个函数，再加进对应的元组

判定（`engine.py`）：

```
请求股数 q：清仓取可卖数量，其他取方案的股数
  |
  v
按 1 股检查，有违规?  -- 是 --> 拒绝，原因 = 1 股时的全部违规
  | 否
  v
按 q 股检查，没有违规? -- 是 --> 通过，股数 q
  | 否
  v
二分查找：lo = 1（通过），hi = q（不通过），找到能通过的最大股数
  --> 调整，股数 = lo，原因 = q 股时的全部违规
```

- 生效的紧急阈值：开仓类方案通过或调整时，填方案的值，没填时填默认值；其他情况为空
- 清仓时可卖数量为 0，1 股的检查就不通过，结论为拒绝，不需要单独处理
- 正确性依赖每条规则的单调性：spec 写成要求，并用 Hypothesis 对已登记的每条规则检查

考虑过：每条规则自己算出"最多能买多少股"，判定取最小值

- 没有采用：每条规则都要写反解公式，合并风险在记 0 时还是分段函数，容易出现差一股的错误；新增规则的门槛也更高。二分查找只需要规则回答"这个股数行不行"

考虑过：每条规则声明自己是"可调整"还是"只能拒绝"

- 没有采用：用 1 股检查就能区分。比如资金连 1 股都不够，同样应该拒绝

### D6 Proposal 的规则清单在哪里实现

| Proposal「风控引擎与执行」 | 本变更 |
| --- | --- |
| 已结算的可用资金 | 规则"已结算资金"；已结算资金由输入提供 |
| 最多持仓数 | 规则"最多持仓数" |
| 单只仓位上限 | 规则"单只仓位上限" |
| 单笔和合计风险上限 | 规则"单笔计划风险""组合合计风险" |
| 板块集中度 | 规则"板块集中度" |
| 同一股票的最短交易间隔 | 规则"最短交易间隔" |
| 必须带止损价 | 由方案格式保证；规则"止损价"检查它低于限价 |
| 紧急阈值在配置范围内，没写时用默认值 | 规则"紧急阈值" |
| 单日亏损熔断触发后禁止开仓 | 规则"单日亏损熔断"；熔断状态由输入提供 |
| 卖出数量不超过可卖数量 | 规则"可卖数量" |
| 不会增加风险 | 由方案格式（退出不带止损价）和"可卖数量"保证，不单独写规则 |
| （新增）同一股票有未完成的开仓方案 | 规则"未完成的开仓方案"，见 proposal「需要人决定」第 4 条 |

### D7 来自其他变更的输入

| 输入 | 由谁提供 | 说明 |
| --- | --- | --- |
| 已结算资金 | 订单变更（交易记录） | 交易日的现金减去未结算的卖出款；卖出按 T+1 交易日结算，每日对账时和券商核对 |
| 最近成交日 | 订单变更（交易记录） | 每只股票最近一次成交（买入、卖出、止损）所在的交易日 |
| 熔断已触发 | 紧急风险退出变更 | 盘中监控单日亏损，触发后当天一直保持 |
| 板块 | 方案和持仓里自带 | 板块分类的来源到股票池（M3）再定 |
| 开仓预留、卖出预留 | 变更 3 | 根据已通过方案的最终股数，用 D4 的计算函数得出 |

可卖数量不用券商返回的可卖数量：券商端止损单会占用股数，按券商的数字算，有止损单的持仓永远卖不出去。卖出前由执行模块先修改或撤销止损单（「订单生命周期」）。

### D8 阈值配置

`modules/risk/config.py` 定义 `RiskConfig`：Pydantic 模型，`frozen=True`，每项都有默认值（spec「风控阈值配置」的表）。字段名：

- `max_trade_risk_ratio`
- `max_total_risk_ratio`
- `max_position_ratio`
- `max_sector_ratio`
- `max_positions`
- `cost_buffer_ratio`
- `min_trade_interval_sessions`
- `emergency_threshold_min`
- `emergency_threshold_max`
- `emergency_threshold_default`

每项只加"大于 0"的约束，写明单位，不校验配置项之间的关系。M2 由 bootstrap 从配置文件读取，测试里直接构造。

### D9 测试

目录镜像 `src/`：

```
tests/contracts/test_trade_plan.py           spec「交易方案的格式」
tests/modules/risk/builders.py               spec 的基准数据和方案的构造函数
tests/modules/risk/test_config.py            spec「风控阈值配置」
tests/modules/risk/domain/test_exposure.py   spec「计划风险和市值的算法」
tests/modules/risk/domain/test_rules.py      开仓类、减仓类规则，预留怎么计入
tests/modules/risk/domain/test_engine.py     判定
tests/modules/risk/domain/strategies.py      Hypothesis 生成数据的策略
```

- 每个新目录各放一个 `__init__.py`
- `builders.py` 提供 spec 里的基准数据（`baseline_account()`、`baseline_plan(**changes)`、`baseline_request(**changes)`），交易日历用 `tests/fakes/calendar.py` 的样例日历
- Hypothesis 生成：0 到 4 只持仓的账户、四种动作的合法方案、开仓预留和卖出预留、熔断状态、样例日历范围内的当前时刻和最近成交日。检查三条性质：
  - 已登记的每条规则都满足单调性
  - 结论为调整时，最终股数通过全部规则，多 1 股至少有一条不通过
  - 结论为通过时，请求股数通过全部规则；结论为拒绝时，1 股就不通过
- 每个 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [以后新增的规则不满足单调性，二分查找给出错误的股数] → spec 把单调性写成要求；Hypothesis 性质测试遍历 `OPENING_RULES` 和 `EXIT_RULES` 里的每条规则，新规则登记后自动被检查
- [默认阈值没有经过回测] → 都是配置项；M2 在模拟盘运行时观察，再调整
- [同一股票有未完成的开仓方案时一律拒绝，可能挡住合理的追加] → M3 的调度器会在新案件排队时作废还没提交订单的开仓方案（proposal「需要人决定」第 5 条），之后只有订单已经在盘中挂着时才会拒绝；前一笔结束（成交、撤单或作废）后 PM 可以再提，等待时间通常不超过一个交易日
- [已结算资金由上游计算，算错会造成 good faith violation] → 订单变更测试结算逻辑；每日对账时和券商核对
- [Pydantic 在运行时把 float 转成 Decimal，不报错] → 本项目的代码由 mypy 拦住；解析模型输出的方式到接入 PM 时再定

## Migration Plan

只新增代码和一个运行依赖 `pydantic`，不涉及部署和数据迁移；需要回滚时直接 revert。
