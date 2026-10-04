# Tasks

## 1. 模块和配置

- [x] 1.1 先写测试 `tests/modules/health/test_config.py`：spec「健康配置」的「默认配置」「配置不大于 0」两个场景。同时建好 `tests/modules/health/__init__.py`、`tests/modules/health/domain/__init__.py`。验证：运行这个测试文件，确认因为模块不存在而失败
- [x] 1.2 新建 `src/tradingagentspro/modules/health/`：`__init__.py`（模块说明，导出 `HealthConfig`）、`config.py`（`HealthConfig`：数据延迟上限、调度器心跳超时，都是 `timedelta` 并且大于 0）、`domain/__init__.py`。验证：1.1 的测试通过；`uv run lint-imports` 和 `uv run pytest tests/architecture` 通过

## 2. 健康检查和恢复后的第一个交易日

- [x] 2.1 先写测试。`tests/modules/health/builders.py` 放 spec 的基准数据：纽交所样例日历、2026-10-14 15:00Z、五项都正常的基准信号。`tests/modules/health/domain/test_health.py`：
  - `abnormal_checks`：spec「健康检查」表的每一行都有异常和正常两种情况；数据延迟和调度器心跳超时等于上限时正常、多 1 秒时异常；数据延迟从开盘起算（昨天的行情、从来没有行情）；盘外不检查数据延迟、照样检查调度器心跳；多项同时异常
  - `recovery_day`：spec「恢复后的第一个交易日」表的每一行，包括提前收盘的交易日

  验证：运行 `uv run pytest tests/modules/health/domain`，确认因为名字不存在而失败
- [x] 2.2 实现 `domain/health.py` 的 `HealthCheck`、`HealthSignals`、`abnormal_checks`、`recovery_day`（design D1、D2、D5），并从 `__init__.py` 导出（测试只从包根 import）。验证：2.1 的测试通过；`uv run pytest tests/architecture` 通过（纯区域的规则）；`uv run mypy` 通过

## 3. 降级模式的状态

- [x] 3.1 先写测试 `tests/modules/health/domain/test_health.py`：
  - `HealthStatus.judged`、`reconciled`：spec「进入和退出降级模式」状态表的每一行；进入时刻经过等待对账再回到降级不变，回到正常时清掉；`degraded` 在降级和等待对账时为真
  - 状态机性质测试（design D7）：随机交错判定（有或没有异常的检查）、对账完成和拨动时间，和按表写的参考模型对照

  验证：确认因为 `HealthMode`、`HealthStatus` 不存在而失败
- [x] 3.2 实现 `domain/health.py` 的 `HealthMode`、`HealthStatus`（design D3），并从 `__init__.py` 导出。验证：3.1 的测试通过，并且用 `event` 确认性质测试的样例里出现过"等待对账时对账完成回到正常"和"等待对账时又有异常"；`uv run mypy` 通过

## 4. 健康监控用例

- [x] 4.1 先写测试 `tests/modules/health/test_service.py`，用测试时钟和纽交所样例日历新建 `HealthService`，每个 spec 场景一个测试：
  - 「健康检查」九个场景
  - 「进入和退出降级模式」八个场景。「进程重启以后重新判定」用新建一个健康监控表示重启
  - 「恢复后的第一个交易日」三个场景
  - 「健康配置」的「改数据延迟上限」

  验证：运行这个测试文件，确认因为 `HealthService` 不存在而失败
- [x] 4.2 实现 `service.py` 的 `HealthService`（`check`、`reconciled`、`status`，design D4），并从 `__init__.py` 导出。验证：4.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 文档和收尾

- [x] 5.1 人同意 proposal「需要人决定」第 1 条之后，在完整版 Proposal「故障处理」里"顺延到恢复后的第一个交易日"那一条后面补上：恢复后的第一个交易日是退出降级模式以后第一个收盘的交易日，盘前或盘中恢复是当天，收盘后或休市日恢复是下一个交易日。验证：这句话和 spec「恢复后的第一个交易日」一致，其他内容没有改动
- [x] 5.2 对照 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 5.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
