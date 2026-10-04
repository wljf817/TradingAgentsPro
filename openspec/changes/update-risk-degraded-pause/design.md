# Design

## Context

现状：

- `RiskRequest` 有一项 `daily_loss_breaker_tripped`；开仓类规则 `daily_loss_breaker` 在它为真时拒绝，和股数无关
- `RiskService.check(plan, account, daily_loss_breaker_tripped)` 和 `recheck(plan_id, account, last_price, daily_loss_breaker_tripped)` 由调用方传入熔断状态，`_request` 把它放进 `RiskRequest`
- 再校验结论为拒绝时，方案作废，释放预留
- 健康监控的 `HealthStatus.degraded` 给出是否处于降级模式（`add-degraded-mode`）
- 调用风控的测试：`tests/modules/risk/test_service.py` 的 `_check`、`_recheck` 两个辅助函数，`tests/modules/emergency/test_close_plan_risk.py` 里的六处直接调用；纯逻辑的测试都通过 `baseline_request` 和 `risk_requests` 构造请求

动机见 proposal.md 的 Why，要求见三份 delta spec。

### 模块边界

只改风控模块。不新增也不修改跨模块传递的 Pydantic 模型。风控不 import 健康监控：是否处于降级模式由调用方传入，和熔断状态由调用方从紧急退出模块取来一样。

```
modules/risk
  domain/request.py  RiskRequest 增加 degraded
  domain/rules.py    增加 degraded_mode，登记到 OPENING_RULES
  service.py         check、recheck 增加 degraded；两个布尔参数只能按名字传
```

## Goals / Non-Goals

**Goals:**

- 降级模式和熔断走同一条路：一条开仓类规则、一项输入，首次检查和再校验都检查
- 已有场景的数字和结论都不变

**Non-Goals:**

- 降级期间挂着的买单：不撤，见 proposal 的 Non-goals
- 降级模式的原因（哪项检查异常）写进拒绝原因：说明里只写"系统处于降级模式"。具体原因在健康监控的状态和程序日志里

## Decisions

### D1 一条新的开仓类规则

```python
def degraded_mode(request: RiskRequest, quantity: int) -> RiskViolation | None:
    """不处于降级模式。"""
    if not request.degraded:
        return None
    return RiskViolation(rule="降级模式", message="系统处于降级模式，暂停开仓")
```

- 登记在 `OPENING_RULES` 的最后，排在 `daily_loss_breaker` 后面；原因列表按登记顺序，熔断和降级同时出现时先列熔断
- 和股数无关，满足规则的单调性；已有的单调性性质测试遍历 `OPENING_RULES`，自动覆盖它
- 减仓类规则不变，降级模式下退出照常

考虑过：放进执行模块提交订单的检查，像对账有不一致时暂停开仓那样

- 没有采用：对账状态在执行模块里，所以那条检查放在执行模块；降级状态在健康监控里，和熔断一样作为风控的输入更直接。放在风控，首次检查就能拒绝，人不用去确认一个注定下不了单的方案

考虑过：熔断和降级合成一个输入"开仓是否暂停"

- 没有采用：拒绝原因要分清是熔断还是降级，PM 和人看到的处理不同

### D2 两个布尔参数只能按名字传

```python
def check(
    self,
    plan: TradePlan,
    account: AccountSnapshot,
    *,
    daily_loss_breaker_tripped: bool,
    degraded: bool,
) -> RiskDecision: ...


def recheck(
    self,
    plan_id: UUID,
    account: AccountSnapshot,
    last_price: Decimal,
    *,
    daily_loss_breaker_tripped: bool,
    degraded: bool,
) -> RiskDecision: ...
```

- 两个相邻的布尔参数，按位置传很容易传反，而且读调用的地方看不出哪个是哪个
- 两个都没有默认值：漏传应该在类型检查时报出来，不能悄悄当作"没有降级"
- `RiskRequest` 是 dataclass，`degraded` 紧跟在 `daily_loss_breaker_tripped` 后面；构造它的地方（`_request`、测试的 `baseline_request` 和 `risk_requests`）都按名字传

### D3 再校验时遇到降级模式

不另做处理：重跑规则时「降级模式」不通过，结论为拒绝，已有的再校验流程就会作废方案、释放预留（proposal「需要人决定」第 1 条）。再校验按 1 股检查就不通过，所以原因只有"降级模式"和 1 股时同样不通过的其他规则。

### D4 测试

```
tests/modules/risk/builders.py              baseline_request 增加 "degraded": False
tests/modules/risk/domain/strategies.py     risk_requests 随机生成 degraded
tests/modules/risk/domain/test_rules.py     「降级模式下禁止开仓」「降级模式和熔断同时出现」的判定；
                                            「退出不受开仓类规则限制」加上处于降级模式
tests/modules/risk/domain/test_recheck.py   「再校验时处于降级模式」「降级模式下退出照常再校验」的判定
tests/modules/risk/test_service.py          _check、_recheck 按名字传两个布尔参数，增加 degraded；
                                            降级模式下首次检查不预留、再校验后没有有效预留
tests/modules/emergency/test_close_plan_risk.py
                                            六处调用改为按名字传
```

- 已有的性质测试（确定性、单调性、预留不超额等）用 `risk_requests` 生成请求，随机的降级状态让它们也覆盖新规则
- 每个新增或修改的 spec 场景至少对应一个测试，测试名里写明场景

## Risks / Trade-offs

- [短暂的降级（比如开盘时一两分钟行情延迟）也会让已经确认的开仓方案作废] → 已由「需要人决定」第 1 条取舍；数据延迟从开盘起算，有 60 秒宽限；PM 之后的案件可以重新提出
- [改了 `check`、`recheck` 的签名] → 现在只有测试在调用；M2 的工作流按新签名写

## Migration Plan

只改函数签名和增加一条规则，没有持久化的数据要迁移。需要回滚时直接 revert。
