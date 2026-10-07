# Tasks

## 1. 风控默认值

- [ ] 1.1 先写测试（design D3）：
  - `tests/modules/risk/builders.py`：定义一组旧比例（单笔风险 0.01、合计风险 0.05、单只仓位 0.20、板块 0.40）；`baseline_config` 用它，每笔佣金仍为 0。说明里写明这是 spec 基准数据的比例，不是默认值
  - `tests/modules/risk/domain/strategies.py`：`commission_configs`、`risk_requests`、`accounts_within_limits` 构造的 `RiskConfig` 都用这组旧比例，其余配置项照旧
  - `tests/modules/risk/test_service.py`：`DEFAULT_CONFIG` 改为旧比例加默认的每笔佣金，名字和注释跟着改，不再说"默认配置"
  - `tests/modules/risk/test_config.py`：「不提供配置时使用默认值」断言四个比例是 0.05、0.10、0.70、1.00，其余照旧
  - `tests/modules/risk/domain/test_engine.py`：risk-rules spec「小资金账户按默认阈值开仓」，用 `RiskConfig()` 和权益 2,000、已结算资金 2,000、没有持仓的账户，三步各自断言结论、最终股数和原因

  验证：运行 `uv run pytest tests/modules/risk tests/modules/emergency`，确认只有默认值断言和新场景失败，其余全部通过
- [ ] 1.2 实现：`src/tradingagentspro/modules/risk/config.py` 的四个默认值改为 0.05、0.10、0.70、1.00，注释的含义不变。验证：1.1 的测试和 `tests/modules/risk`、`tests/modules/emergency` 的全部测试通过

## 2. 熔断默认值

- [ ] 2.1 先写测试（design D3）：
  - `tests/modules/emergency/builders.py`：`emergency_service` 的熔断比例默认固定为 0.03，传入的配置项可以覆盖；说明跟着改
  - `tests/modules/emergency/test_config.py`：「默认配置」断言 0.07
  - `tests/modules/emergency/test_service.py`：emergency-exit spec「按默认熔断比例判定」，用 `EmergencyConfig()` 构造用例，当前权益 93,000.01 时结果为空、熔断没有触发；93,000 时 AAA 和 BBB 都因"组合熔断"清仓

  验证：运行 `uv run pytest tests/modules/emergency`，确认只有默认配置和新场景失败
- [ ] 2.2 实现：`src/tradingagentspro/modules/emergency/config.py` 的熔断比例默认值改为 0.07，注释不变。验证：2.1 的测试和 `tests/modules/emergency`、`tests/modules/risk` 的全部测试通过

## 3. 收尾

- [ ] 3.1 对照两份 delta spec，确认新增场景和改了数值的场景都有对应的测试，测试名或说明里写明场景；修改过的要求里原有的场景，测试仍然通过。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 3.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`，全部通过
