# Tasks

## 1. 降级模式规则

- [x] 1.1 先写测试（design D4）：
  - `tests/modules/risk/builders.py`：`baseline_request` 增加 `"degraded": False`；`tests/modules/risk/domain/strategies.py`：`risk_requests` 增加 `degraded=draw(st.booleans())`
  - `tests/modules/risk/domain/test_rules.py`：risk-rules spec「降级模式下禁止开仓」（买入和加仓都拒绝，没有最终股数）、「降级模式和熔断同时出现」（原因依次是"单日亏损熔断"和"降级模式"）；「退出不受开仓类规则限制」加上 `degraded=True`
  - `tests/modules/risk/domain/test_recheck.py`：pre-order-recheck spec「再校验时处于降级模式」「降级模式下退出照常再校验」的判定

  验证：运行 `uv run pytest tests/modules/risk/domain`，确认因为 `RiskRequest` 没有 `degraded` 而失败
- [x] 1.2 实现 design D1：`domain/request.py` 的 `RiskRequest` 在 `daily_loss_breaker_tripped` 后面增加 `degraded`；`domain/rules.py` 增加 `degraded_mode`，登记在 `OPENING_RULES` 的最后。验证：1.1 的测试和 `tests/modules/risk/domain` 的全部测试通过，包括单调性和确定性的性质测试；`uv run pytest tests/architecture` 通过

## 2. 风控用例的输入

- [x] 2.1 先写测试 `tests/modules/risk/test_service.py`：
  - `_check`、`_recheck` 改为按名字传 `daily_loss_breaker_tripped` 和 `degraded`，默认都为假
  - 「降级模式下禁止开仓」：处于降级模式时检查基准方案，BBB 没有预留
  - 「再校验时处于降级模式」：基准方案通过并预留后，处于降级模式时再校验，结论为拒绝，BBB 没有有效预留
  - 「降级模式下退出照常再校验」：清仓 AAA 通过并预留后，处于降级模式时再校验，结论为通过，最终股数 100

  验证：确认因为 `check`、`recheck` 不接受 `degraded` 而失败
- [x] 2.2 实现 design D2：`service.py` 的 `check`、`recheck` 增加 `degraded`，两个布尔参数改为只能按名字传，`_request` 把 `degraded` 放进 `RiskRequest`。`tests/modules/emergency/test_close_plan_risk.py` 的六处调用改为按名字传（`degraded=False`）。验证：
  - 2.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 文档和收尾

- [x] 3.1 人同意 proposal「需要人决定」第 1 条之后，在完整版 Proposal「故障处理」的"暂停开仓和加仓"一条补上：风控拒绝开仓类方案，已经确认、还没下单的开仓方案在下单前再校验时作废。验证：这句话和 delta spec 一致，其他内容没有改动
- [x] 3.2 对照三份 delta spec，确认每个新增或修改的场景都有对应的测试，测试名或说明里写明场景；修改过的要求里原有的场景，测试仍然通过。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 3.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
