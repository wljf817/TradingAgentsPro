# Design

## Context

现状（变更 2 到 5 之后）：

- 交易成本只有一个配置项 `RiskConfig.cost_buffer_ratio`（0.005），以 `buffer_ratio: Decimal` 参数一层层往下传，用在四处：
  - `domain/exposure.py`：`holding_risk`（已有持仓的计划风险）、`merged_risk`（合并后的计划风险）
  - `domain/rules.py`：`settled_cash` 规则；`_merged_risk`、`total_risk` 把缓冲比例传给上面两个函数
  - `domain/reservation.py`：`opening_reservation` 的资金和风险
  - `service.py`：计算有效预留占用的额度时传缓冲比例
- `domain/engine.py` 的 `decide(request, rules, requested)` 依赖规则的单调性，用二分查找最大股数。首次检查（`evaluate_plan`）和再校验（`domain/recheck.py`）都调用它
- 测试大多用 `RiskConfig()` 的默认值：`builders.py` 的 `baseline_request`、`risk_service`，以及各测试文件里直接构造的配置。Hypothesis 的 `risk_requests()` 生成部分随机的配置
- 账户是 moomoo US。人看到每笔订单收 1 到 2 美元；公开资料说美国居民零佣金、零平台费，两者对不上

动机见 proposal.md 的 Why。

### 模块边界

只改风控模块。模块之间传递的 Pydantic 模型（`TradePlan`、`AccountSnapshot`、`RiskDecision`）的字段都不变，执行模块不受影响。

```
modules/risk
  config.py              RiskConfig 增加 commission_per_order、max_round_trip_commission_ratio
  domain/exposure.py     holding_risk、merged_risk 改为接收 RiskConfig，加上佣金
  domain/rules.py        settled_cash 加上买入佣金；_merged_risk、total_risk 改为传 RiskConfig
  domain/reservation.py  opening_reservation 改为接收 RiskConfig，资金加上买入佣金
  domain/engine.py       decide 最后增加佣金占比检查
  service.py             计算预留占用的额度时传 RiskConfig

contracts/risk.py        RiskDecision 的文档注释：violations 补上佣金占比的拒绝
```

## Goals / Non-Goals

**Goals:**

- 佣金怎么计入全部在纯函数里，可以单独测试
- 已有场景和测试的数字一个都不改，佣金的效果由新增场景覆盖
- 判定方法依赖的单调性不被破坏

**Non-Goals:**

按「编程风格」省略的情况：

- 部分成交之后，预留的资金和风险仍然计入买入那一笔佣金：最多多占 2 美元
- 一笔订单分几次成交，只算一笔佣金：订单最多挂到当天收盘（变更 5），券商按笔收
- 减仓、清仓的佣金不计入任何检查：卖出不受开仓类规则限制
- 计划风险里止损卖出的滑点，仍按买入的成交额计算：沿用原公式
- 已有持仓的计划风险只计一笔卖出佣金，不管之后分几次卖
- 佣金占比用限价算成交额，不用实际成交价

## Decisions

### D1 佣金单独成项，缓冲比例保留

```python
class RiskConfig(BaseModel):
    ...
    # 滑点等按成交额计算的缓冲 = 成交额乘以这个比例；不含佣金
    cost_buffer_ratio: Decimal = Field(default=Decimal("0.005"), gt=0)
    # 每笔订单的佣金和平台费，买入、卖出各算一笔
    commission_per_order: Decimal = Field(default=Decimal(2), ge=0)
    # 开仓方案买卖两笔佣金不超过成交额乘以这个比例
    max_round_trip_commission_ratio: Decimal = Field(default=Decimal("0.01"), gt=0)
```

缓冲比例的名字和默认值都不变，只是注释里的含义从"费用和滑点"改为"滑点等，不含佣金"。

考虑过：缓冲比例改名为滑点比例，并从已结算资金和预留的资金里去掉。限价买单不会比限价成交得更贵，所以资金检查里的缓冲其实只是余量

- 没有采用：资金相关的场景数字要全部重算（10,050、5,025、2,010、37,990 等，三份 spec 里有十几处），而 0.5% 的余量影响很小。改名还要改配置名和全部引用，行为上没有好处

考虑过：现在就调低缓冲比例

- 没有采用：滑点要在 M2 模拟盘上观察之后再定

### D2 佣金计入哪里、计几笔

| 量 | 计入的佣金 | 理由 |
| --- | --- | --- |
| 已有持仓的计划风险 | 1 笔 | 买入的佣金已经付过，只剩止损卖出的一笔 |
| 合并后的计划风险 | 2 笔 | 方案买入一笔，止损卖出一笔。整只股票只有一张止损单，加仓也是一笔卖出 |
| 已结算资金规则 | 1 笔 | 买入 |
| 开仓预留的资金 | 1 笔 | 买入 |
| 开仓预留的风险 | 1 笔 | 合并后的减去已有持仓的，自然只剩买入那一笔 |

```python
def holding_risk(position: Position, config: RiskConfig) -> Decimal:
    q, cost = position.quantity, position.average_cost
    risk = q * (cost - position.stop_price) + q * cost * config.cost_buffer_ratio
    return max(ZERO, risk + config.commission_per_order)


def merged_risk(holding, quantity, limit_price, stop_price, config) -> Decimal:
    # 原来的展开式，最后加上 2 * config.commission_per_order，再和 0 取大
    ...
```

佣金对一个方案来说是常数，不随股数变化。所以每条规则"股数越小越容易通过"的性质不受影响，判定方法不用改。

### D3 佣金占比放在 decide 的最后，不做成规则

佣金占比和规则的方向相反：股数越小越通不过。把它登记成规则，二分查找就会找错最大股数，所以放在 `decide` 得出最终股数之后检查：

```python
def decide(request: RiskRequest, rules: Sequence[RiskRule], requested: int) -> RiskDecision:
    ...  # 1 股不通过就拒绝；请求股数通过就是通过；否则二分得出调整的股数
    if (violation := _commission_ratio(request, quantity)) is not None:
        return _rejected(at_requested + (violation,))  # 通过时 at_requested 为空
    return _approved(request, outcome, quantity, at_requested)


def _commission_ratio(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """开仓方案：往返佣金不超过成交额乘以上限；减仓类方案不检查。"""
```

- 首次检查和再校验都调用 `decide`，一处改动两边都生效。再校验因此拒绝时，原有的"拒绝就作废、释放全部预留"照常执行，不用另外处理
- 原因列表是请求股数下不通过的规则加上「佣金占比」。PM 能看出方案是因为什么被调小、又因为太小被拒绝。没有生效的紧急阈值和有效期，和其他拒绝一致
- 直接按最终股数判断就够了：最终股数是规则允许的最大股数，最大的都不划算，更小的只会更不划算
- `_rejected` 从现有"1 股都通不过"的分支里抽出来，两个拒绝分支共用

考虑过：只在风控调整时检查，不管 PM 写的小单

- 没有采用：PM 写 7 股和风控调成 7 股，佣金占比一样，同样不划算

### D4 函数改为接收整份 RiskConfig

`holding_risk`、`merged_risk`、`opening_reservation` 的最后一个参数从 `buffer_ratio: Decimal` 改为 `config: RiskConfig`。

- 现在要用两个值（缓冲比例和每笔佣金），传整份配置和现有的 `effective_emergency_threshold(plan, config)` 一致。`exposure.py` 本来就 import 了 `RiskConfig`，纯区域的规则不受影响
- `reservation.py` 要新 import `RiskConfig`，它和 config 同属这个模块的纯区域，符合 module-layers 契约

考虑过：再加一个 `commission: Decimal` 参数

- 没有采用：每个调用处都要同时传两个值，容易漏传

### D5 已有场景的基准数据每笔佣金为 0

- 三份 spec 的基准数据都写明「每笔佣金为 0，不用默认值」。佣金的场景单独写明每笔佣金 2
- 测试：`builders.py` 增加 `baseline_config(**changes)`，返回每笔佣金为 0 的 `RiskConfig`；`baseline_request`、`risk_service` 和各测试里直接构造的 `RiskConfig(...)` 都改用它。佣金的测试传 `baseline_config(commission_per_order=Decimal(2))`
- 性质测试要覆盖带佣金的情况：
  - `risk_requests()` 的配置加上随机的每笔佣金（0 到 10 美元）和往返佣金占比上限（0.005、0.01、0.05 中选一个）。单调性和"调整后是最大股数"两个已有的性质测试由此覆盖佣金，另外新增"佣金占比按能通过规则的最大股数判断"
  - `risk_requests()` 生成的方案大多 1 股就被规则拒绝，很少走到佣金占比，所以另外新增一个性质测试：账户用 `accounts_within_limits`，方案用 `passable_opening_plans`，配置用随机佣金，和已有的一致性测试共用同一套断言
  - `test_service.py` 里"持仓加预留永不超限"的性质测试改用默认配置（每笔佣金 2），`_assert_within_limits` 用同一份配置算资金和风险
  - `accounts_within_limits` 改为按默认配置计算持仓风险，和上一条保持一致

考虑过：全部场景按默认的 2 美元重算

- 没有采用：三份 spec 有几十处按公式算出来的数字，重算容易出错，评审也难核对。这些场景测的是各自的规则，和佣金无关

### D6 默认值

- 每笔佣金 2 美元：人看到 1 到 2 美元，取最高值。公开资料和人看到的不一致，以人看到的为准，配置可以改
- 往返佣金占比上限 0.01：每笔 2 美元时，最小成交额是 400 美元。对照现有阈值，权益 1 万美元时单只仓位上限是 2,000 美元，正常大小的方案不会被挡，挡掉的是调出来的碎单
- M7 实盘用实际费用核对这两个默认值

### D7 止损价不低于限价的方案不要求单调性（实现时发现）

单调性的性质测试加上佣金之后，找到了反例：

- 买入方案的止损价高于限价时，计划风险里每股那一项是负数
- 原来没有持仓时，整个式子是负数，记为 0，所以碰巧单调
- 加上固定的两笔佣金之后，1 股时是正数、可能超限，2 股时又变回 0、可以通过

有持仓时，这个问题以前就存在：持仓那一项是正数，再加上随股数减小的负数，同样不单调，只是性质测试一直没有碰到。

这种方案在任何股数下都被「止损价」规则拒绝，`decide` 在 1 股时就给出拒绝，不会用到二分查找，所以结果不受影响。处理方式：

- spec「规则的单调性和扩展」写明这种方案除外
- 单调性的性质测试只生成止损价低于限价的开仓方案
- `RiskRule` 的说明补上这个例外，代码不改

考虑过：让计划风险相关的规则在止损价不低于限价时直接通过，交给「止损价」规则拒绝

- 没有采用：规则之间互相依赖，而这种方案本来就一定会被拒绝

## Risks / Trade-offs

- [实际费用结构不明：人看到的和公开资料不一致，也不确定是否和股数有关] → 默认取较高的 2 美元，偏保守；配置可以改；M7 核对
- [权益很小时佣金占比会挡掉大部分方案：权益 2,000 美元时，单只仓位上限正好是 400 美元] → M7 定上线门槛时一起看小资金的权益规模，或者调低上限
- [旧场景的基准数据每笔佣金为 0，不覆盖默认配置下的行为] → 新增的佣金场景都用 2 美元；性质测试使用随机佣金和默认配置
- [资金检查里保留 0.5% 的缓冲，略多占资金] → 影响很小，见 D1

## Migration Plan

预留的数据库实现在 M2，目前没有持久化的数据，不需要迁移。需要回滚时直接 revert。

## Open Questions

- 实际费用由哪几项组成、是否随股数变化：人可以在 moomoo App 的订单费用明细里查看。只影响默认值，以及以后要不要再加每股费率，不影响本变更的 spec 和任务
