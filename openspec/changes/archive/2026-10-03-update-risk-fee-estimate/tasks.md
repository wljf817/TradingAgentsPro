# Tasks

## 1. 佣金配置和测试的基准配置

- [x] 1.1 先写测试 `tests/modules/risk/test_config.py`：
  - 默认值增加每笔佣金 2、往返佣金占比上限 0.01
  - 新场景「佣金配置的取值范围」：每笔佣金 -1、往返佣金占比上限 0 时配置报错；每笔佣金 0 时配置成功

  验证：运行 `uv run pytest tests/modules/risk/test_config.py`，确认因为配置项不存在而失败
- [x] 1.2 `config.py`：增加 `commission_per_order`（默认 2，不小于 0）和 `max_round_trip_commission_ratio`（默认 0.01，大于 0），改写 `cost_buffer_ratio` 的注释（design D1）。验证：1.1 的测试和全部已有测试通过
- [x] 1.3 `tests/modules/risk/builders.py` 增加 `baseline_config(**changes)`，返回每笔佣金为 0 的配置；`baseline_request`、`risk_service`，以及 `tests/modules/risk/` 下直接构造的 `RiskConfig(...)` 都改用它。`risk_requests()` 的随机配置留到 5.1 再改（design D5）。验证：全部已有测试通过，数字一个都不改

## 2. 计划风险和已结算资金计入佣金

- [x] 2.1 先写测试：
  - `tests/modules/risk/domain/test_exposure.py`：新场景「计划风险计入佣金」：每笔佣金 2 时，基准方案 529、AAA 1,052、加仓 AAA 合并后 1,331.5
  - `tests/modules/risk/domain/test_rules.py`：新场景「已结算资金计入买入佣金」（已结算资金 2,012，调整为 40 股）、「单笔计划风险计入买卖佣金」（300 股调整为 189 股）
  - 已有测试里调用 `holding_risk`、`merged_risk`、`opening_reservation` 的地方，改为传配置

  验证：运行 `uv run pytest tests/modules/risk`，确认因为参数类型不对或者数字不对而失败
- [x] 2.2 实现（design D2、D4）：
  - `domain/exposure.py`：`holding_risk` 加一笔佣金、`merged_risk` 加两笔佣金，最后一个参数改为 `config: RiskConfig`
  - `domain/rules.py`：`settled_cash` 加上一笔买入佣金；`_merged_risk`、`total_risk` 改为传配置
  - `domain/reservation.py`：`opening_reservation` 的参数改为 `config: RiskConfig`，资金公式留到 3.2；`service.py` 改为传配置

  验证：
  - 2.1 的测试和全部已有测试通过，包括单调性性质测试
  - `uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 预留的资金计入买入佣金

- [x] 3.1 先写测试 `tests/modules/risk/domain/test_reservation.py`：
  - 新场景「新建仓的预留计入佣金」：资金 5,027，市值 5,000，风险 529
  - 新场景「加仓的预留计入加仓买入的佣金」：成交 25 股之后，剩余 25 股，资金 1,308.5，市值 1,300，风险 108.5

  验证：运行 `uv run pytest tests/modules/risk/domain/test_reservation.py`，确认资金一项失败
- [x] 3.2 `domain/reservation.py`：`opening_reservation` 的资金加上一笔买入佣金。验证：3.1 的测试和全部已有测试通过

## 4. 佣金占比

- [x] 4.1 先写测试：
  - `tests/modules/risk/domain/test_engine.py`：新场景
    - 「调整后佣金占比等于上限时仍是调整」：已结算资金 404，调整为 8 股
    - 「调整后佣金占比超过上限时拒绝」：已结算资金 403，拒绝，原因是「已结算资金」和「佣金占比」，没有最终股数、生效的紧急阈值和有效期
    - 「请求的股数本身太小时拒绝」：7 股拒绝，原因只有「佣金占比」；8 股通过
    - 「1 股都通不过规则时不检查佣金占比」：已结算资金 50，原因只有「已结算资金」
    - 「退出方案不检查佣金占比」：减仓 AAA 1 股，限价 110，通过
  - `tests/modules/risk/test_service.py`：新场景「再校验调小股数后佣金占比过高时作废」：实时已结算资金 404 时调整为 8 股，预留剩 8 股；再校验时实时已结算资金 403，拒绝，原因是「已结算资金」和「佣金占比」，BBB 没有有效预留

  验证：运行 `uv run pytest tests/modules/risk`，确认这几个场景失败
- [x] 4.2 实现（design D3）：
  - `domain/engine.py`：把拒绝的结果抽成 `_rejected`；增加 `_commission_ratio`，只检查开仓方案；`decide` 得出最终股数之后检查，不通过时拒绝，原因是请求股数下不通过的规则加上「佣金占比」
  - `contracts/risk.py`：`RiskDecision` 的文档注释，violations 补上佣金占比拒绝时的原因列表

  验证：
  - 4.1 的测试和全部已有测试通过
  - `uv run mypy`、`uv run ruff check` 通过

## 5. 性质测试覆盖佣金

- [x] 5.1 `tests/modules/risk/domain/strategies.py`：`risk_requests()` 的配置加上随机的每笔佣金（0 到 10 的整数）和往返佣金占比上限（0.005、0.01、0.05 中选一个）。`test_engine.py` 的 `test_decision_is_consistent_with_the_registered_rules` 改为：结论为拒绝时，要么 1 股就有规则不通过，要么是开仓方案，原因包括「佣金占比」，并且满足新场景「佣金占比按能通过规则的最大股数判断」；另外用 `accounts_within_limits`、`passable_opening_plans` 和随机佣金新增一个性质测试，共用同一套断言，保证常常走到佣金占比（design D5）。验证：`uv run pytest tests/modules/risk/domain` 通过，包括 `test_rules.py` 的单调性性质测试
- [x] 5.2 `tests/modules/risk/test_service.py`：「持仓加预留永不超限」的性质测试改用默认配置（每笔佣金 2），`risk_service` 显式传入这份配置，`_assert_within_limits` 用同一份配置算资金和风险；`strategies.py` 的 `accounts_within_limits` 按默认配置计算持仓风险。验证：`uv run pytest tests/modules/risk/test_service.py` 通过

## 6. 收尾

- [x] 6.1 对照三份 delta spec，确认每个新增场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
