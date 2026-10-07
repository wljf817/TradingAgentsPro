# Design

## Context

现状：

- `modules/risk/config.py` 的 `RiskConfig`：单笔风险比例 0.01、合计风险比例 0.05、单只仓位比例 0.20、板块比例 0.40
- `modules/emergency/config.py` 的 `EmergencyConfig`：熔断比例 0.03
- 规则和判定只读配置里的值，不另外写死这几个数
- risk-rules 和 emergency-exit 两份 spec 的场景都写"使用默认阈值（默认配置）和下面的基准数据"，场景里的数字（81、190、123、180、熔断线 3,000 等）是按旧默认值算的
- 测试里用到默认比例的地方：
  - `tests/modules/risk/builders.py` 的 `baseline_config`：默认值，只把每笔佣金改为 0。额度预留和下单前再校验的测试也用它
  - `tests/modules/risk/domain/strategies.py`：`commission_configs`、`risk_requests`、`accounts_within_limits` 直接构造 `RiskConfig`，比例用默认值
  - `tests/modules/risk/test_service.py`：性质测试 `test_holdings_plus_reservations_never_exceed_the_limits` 用 `DEFAULT_CONFIG = RiskConfig()`
  - `tests/modules/emergency/builders.py` 的 `emergency_service`：默认配置；`tests/modules/emergency/domain/test_exit.py` 已经显式传 0.03

动机见 proposal.md 的 Why，要求见 specs/risk-rules/spec.md 和 specs/emergency-exit/spec.md。

### 模块边界

只改风控和紧急退出两个模块的 `config.py`，两个模块之间不新增依赖。模块之间传递的 Pydantic 模型（`TradePlan`、`AccountSnapshot`、`RiskDecision`）都不变。

```
modules/risk/config.py        RiskConfig 四个比例的默认值
modules/emergency/config.py   EmergencyConfig.daily_loss_breaker_ratio 的默认值
```

## Goals / Non-Goals

**Goals:**

- 权益 2,000 的账户按默认值能正常开仓
- 已有场景和测试的数字一个都不改
- 已有的性质测试仍然能让各条上限起作用，不因为默认值放宽而失去检验能力

**Non-Goals:**

按「编程风格」省略的情况：

- 比例的上限校验（比如板块比例不能超过 1）：配置只有我们自己写

## Decisions

### D1 新默认值和它们在 2,000 账户上的效果

| 配置项 | 新默认值 | 权益 2,000 时 |
| --- | --- | --- |
| 单只仓位比例 | 0.70 | 一只股票最多 1,400 |
| 单笔风险比例 | 0.05 | 一只股票的计划风险最多 100 |
| 合计风险比例 | 0.10 | 组合合计风险最多 200，约等于两只满额风险的持仓 |
| 板块比例 | 1.00 | 不会比「已结算资金」更早限制股数，见下文 |
| 熔断比例 | 0.07 | 单日亏损 140 时清仓全部持仓 |

往返佣金 4 不超过成交额的 1%，所以一笔开仓的成交额在 400 到 1,400 之间。计划风险 = 成交额 × 止损距离 + 成交额 × 0.005 + 4 ≤ 100，所以：

- 1,400 的仓位，止损距离最多约 6.4%
- 1,000 的仓位，止损距离最多约 9.1%

单只仓位上限和单笔风险上限在大仓位时差不多同时起作用（见 risk-rules spec 的场景「小资金账户按默认阈值开仓」）：仓位越大，止损就要设得越近。

**板块比例 1.00 等于不限制板块集中度。** 原因：股数通过「已结算资金」时，方案和同板块预留的市值之和不超过已结算资金，已结算资金不超过现金，现金加上全部持仓的市值就是权益。所以同板块的持仓、预留和方案的市值加起来不超过权益，板块规则也一定通过。股数特别大时，它会和「已结算资金」一起出现在不通过的原因里，但从来不会单独决定最终股数。这条规则仍然保留，配置调小就重新生效。

**熔断 7% 和个股紧急阈值配合：**

- 1,400 的仓位跌到默认紧急阈值 8% 时，亏 112，占权益 5.6%，先触发个股清仓，不会熔断
- 只有这只股票一下子跌到 10%（比如开盘跳空），才会直接熔断
- 合计风险上限 10% 高于熔断线 7%：所有止损同一天触发之前，熔断会先清仓。熔断本来就是兜这种情况的

考虑过：熔断跟着仓位上限按比例放大，0.03 × 0.70 / 0.20 ≈ 0.10

- 没有采用：7% 是人定的；10% 意味着一天亏 200 才停手，相对 2,000 的本金太多

### D2 已有场景固定旧值，不重算

risk-rules spec 的基准数据写明单笔风险比例 0.01、合计风险比例 0.05、单只仓位比例 0.20、板块比例 0.40，不用默认值。emergency-exit spec 的基准数据写明熔断比例 0.03。和「费用估算」把每笔佣金固定为 0 是同一个做法。新默认值由两个新增场景和「不提供配置时使用默认值」覆盖。

考虑过：按新默认值重算全部场景

- 没有采用：权益 100,000 的基准数据下，仓位、单笔风险、合计风险、板块的场景（81、190、189、123、180 等）大多不再触及上限，要改基准数据重新设计，工作量大，也容易改错，换来的只是数字变了

**额度预留和下单前再校验的 spec 不改。** 它们的场景写"使用 risk-rules spec 的默认阈值和基准数据"，但写明的原因只有已结算资金、可卖数量、佣金占比、有效期、价格偏离、跌破止损价、熔断和降级模式，结论在新旧比例下都一样：这些场景里四条上限在旧比例下都没有不通过，放宽以后也不会。

「实时额度变大时不超过确认过的股数」是唯一的例外：请求 300 股时，旧比例下单笔计划风险也不通过。但这个场景只写了最终股数 100，没写原因，最终股数由已结算资金决定，新旧比例下都一样。这两份 spec 的测试用的是 `baseline_config`，D3 已经把它固定为旧比例。

### D3 测试里的配置统一固定旧比例

`tests/modules/risk/builders.py` 里定义一组旧比例，下面这些地方都用它：

- `baseline_config`：旧比例，每笔佣金 0
- `strategies.py` 的 `commission_configs`、`risk_requests`、`accounts_within_limits`：旧比例，其余照旧
- `test_service.py` 的 `DEFAULT_CONFIG`：旧比例，每笔佣金仍用默认值 2

这样已有测试的数字和数据分布都和现在一样。只有 `test_config.py` 的默认值断言和两个新场景用真正的默认值。

`tests/modules/emergency/builders.py` 的 `emergency_service`：熔断比例默认固定为 0.03，传入的配置项可以覆盖它（「改熔断比例」传 0.05，新场景传默认值）。

考虑过：性质测试直接用新默认值

- 没有采用：板块比例 1.00 时板块上限永远不会先起作用，`_assert_within_limits` 对板块的断言就检验不了什么；`accounts_within_limits` 按比例算最低权益，数据分布也会变

## Risks / Trade-offs

- [以后的变更直接用 `RiskConfig()`，按新默认值写场景，和用旧比例的基准数据混在一起] → 两份 spec 的基准数据和 `builders.py` 都写明"不用默认值"；新场景要用默认值时，在 GIVEN 里明确写出来，和这次的两个新场景一样
- [单只仓位 70% 让单只股票的跳空风险变大，单笔风险上限管不住跳空] → 熔断 7% 兜底；跳空风险本来就只能靠单笔和合计风险控制，这是放宽时已经接受的代价
