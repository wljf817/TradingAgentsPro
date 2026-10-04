# Tasks

## 1. 模块和配置

- [x] 1.1 先写测试 `tests/modules/emergency/test_config.py`：spec「紧急退出配置」的「默认配置」「熔断比例超出范围」两个场景。同时建好 `tests/modules/emergency/__init__.py`、`tests/modules/emergency/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [x] 1.2 新建 `src/tradingagentspro/modules/emergency/`：`__init__.py`（模块说明，导出 `EmergencyConfig`）、`config.py`（`EmergencyConfig`，design D7）、`domain/__init__.py`。验证：1.1 的测试通过；`uv run lint-imports` 和 `uv run pytest tests/architecture` 通过（新模块按模块内部分层和纯区域的规则检查）

## 2. 判定的纯逻辑

- [x] 2.1 先写测试。`tests/modules/emergency/builders.py` 放 spec 的基准数据：纽交所样例日历、2026-10-14 15:00Z、AAA 和 BBB 两只持仓、前一交易日收盘时的权益和当前权益。`tests/modules/emergency/domain/test_exit.py`：
  - `reference_price`：spec「个股跌幅」参考价表的两行；今天没有开仓成交、前一交易日收盘价也为空时为空
  - `drop_triggered`：跌幅等于紧急阈值时触发、差 0.01 时不触发；最新价高于参考价时不触发；最新价为空、参考价为空时不触发
  - `daily_loss_tripped`：单日亏损等于熔断线时触发、差 0.01 时不触发；盈利时不触发
  - `close_plan`：spec「代码触发的清仓方案」字段表的每一行；股票和交易日都相同时方案 ID 相同，股票或交易日不同时方案 ID 不同
  - `emergency_exits`：spec「判定的时机和输入」判定结果表的两行；熔断已触发时，同时触及个股跌幅的股票只出现一次，没有行情的持仓也在；没有持仓时为空
  - 性质测试（design D8）：每只股票最多出现一次；熔断已触发时恰好是全部持仓、原因都是组合熔断；没有触发时恰好是触及个股跌幅的持仓；方案 ID 只由股票和交易日决定

  验证：运行 `uv run pytest tests/modules/emergency/domain`，确认因为名字不存在而失败
- [x] 2.2 实现 `domain/exit.py`（`Holding`、`ExitReason`、`EmergencyExit`、`reference_price`、`drop_triggered`、`daily_loss_tripped`、`close_plan`、`emergency_exits`，design D2、D5、D7），并从 `__init__.py` 导出（测试只从包根 import）。验证：2.1 的测试通过；`uv run pytest tests/architecture` 通过（纯区域的规则）；`uv run mypy` 通过

## 3. 判定用例和熔断状态

- [x] 3.1 先写测试 `tests/modules/emergency/test_service.py`，用测试时钟和纽交所样例日历新建 `EmergencyService`，每个 spec 场景一个测试：
  - 「判定的时机和输入」四个场景
  - 「个股跌幅」六个场景
  - 「组合熔断」五个场景
  - 「熔断状态」三个场景
  - 「代码触发的清仓方案」四个场景
  - 「紧急退出配置」的「改熔断比例」

  验证：运行这个测试文件，确认因为 `EmergencyService` 不存在而失败
- [x] 3.2 实现 `service.py` 的 `EmergencyService`（`check`、`breaker_tripped`，design D3、D4），并从 `__init__.py` 导出。验证：3.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. 清仓方案接上风控

- [x] 4.1 写测试 `tests/modules/emergency/test_close_plan_risk.py`（design D8），用 `tests/modules/risk/builders.py` 的基准账户和 `risk_service`：
  - `check` 判定结果里的清仓方案：结论为通过，最终股数是可卖数量，生效的有效期是当天收盘
  - 再判定一次得到同一个方案，`check` 返回同一个结果，有效预留只有一份
  - `recheck` 按实时持仓给出最终股数
  - 熔断已触发时，用 `breaker_tripped()` 作为熔断输入：开仓方案被拒绝，原因是"单日亏损熔断"；清仓方案照常通过

  验证：这个测试文件通过（只检查已有的风控行为能接上本模块，不改风控代码）

## 5. 收尾

- [x] 5.1 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 5.2 核对 propose 时改过的文档：完整版 Proposal「持仓与退出」的紧急风险退出触发规则、「风控引擎与执行」的"交易时段"一句，和 spec 一致；`docs/实现路线.md` 的进度清单有「紧急撤单（修改：订单状态机）」。验证：逐条对照，没有不一致
- [x] 5.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
