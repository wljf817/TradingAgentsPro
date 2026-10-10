# Tasks

## 1. 前置和 Proposal

- [x] 1.1 确认「盘中巡检」已经 apply：`bootstrap/app.py` 有 `TradingApp.lock` 和装配的 `RLock`，`ExecutionService` 有 `order(plan_id)`，`tests/workflows/` 已经存在。验证：`grep -n "def order\b\|lock" src/tradingagentspro/modules/execution/service.py src/tradingagentspro/bootstrap/app.py` 有这些；没有就停下，不开始本变更
- [x] 1.2 人对 proposal「需要人决定」第 1 条表态以后，改完整版 Proposal「订单生命周期」表的「每日对账」「止损单挂单失败」两行：同意就按推荐做法加那两句括号，不同意按人的决定改，并同步改 specs/order-flow/spec.md「提交的结果」和 design D6（简版不动）。验证：`git diff` 只有这两行（不同意时另有 spec 和 design 的对应改动）

## 2. 风控用的账户数据

- [x] 2.1 先写测试 `tests/workflows/test_account_snapshot.py`（design D7、D15）：`price_of` 的三种取法（有最新价、没有最新价用前一交易日收盘价、没有行情用均价）；`account_snapshot` 的权益、已结算资金、最近成交日和每只持仓的各项；`read_snapshot` 只取持仓股票加上额外的股票、一次取完、没有股票时不取行情、行情不可用时持仓用均价并各写一条 WARNING、行情源缺一只时只给那一只写 WARNING、读账户失败时 `BrokerUnavailableError` 原样抛出。用 `FakeBroker`、执行模块的 builders 和 `FakeQuoteSource`。每个测试的说明写明对应 spec「风控用的账户数据」的场景。验证：确认因为 `tradingagentspro.workflows.account_snapshot` 不存在而失败
- [x] 2.2 新建 `src/tradingagentspro/workflows/account_snapshot.py`：`price_of`、`account_snapshot`、`read_snapshot`（design D7），只 import 模块的包根和 contracts。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 下单流程的每一步（不连 DBOS）

- [x] 3.1 先写测试第一部分（design D1、D2、D4、D14、D15）：新建 `tests/workflows/order_builders.py`（spec 的基准数据：`FakeBroker` 的账户和 AAA 持仓、AAA 已全部成交的开仓订单写进订单存储、AAA 的止损保护、`FakeQuoteSource` 的 AAA 和 BBB 行情、真实的执行模块、风控（内存预留）、紧急退出、健康监控、行情、`NotificationService` + `FakeNotifier`、`FakeClock`、`RLock`、`TradeServices`、`OrderFlow`；方案 P1 到 P4）和 `tests/workflows/test_order_flow.py` 的第一部分：`OrderFlowConfig` 的「默认配置」「重试间隔不大于 0」；`check_plan` 覆盖 spec「提交方案」里不涉及启动工作流的场景（通过、降级拒绝、熔断拒绝、降级下退出通过、调整、读账户超时）和「别的调用拿着锁时检查等待」；「查持仓的板块」的三个场景（用真实的 `PlanCommand` 和 `FakeCall`，同删掉的 `tests/bootstrap/test_order_flow.py` 的做法）；`request_confirmation`（四种动作、截止时刻和等待的秒数、发不出去时返回 None 并写 ERROR、请求时不拿锁）；`settle` 的四种结果（释放、报告已超时、日志）；`timing` 的三行和「周五盘后等到周一」「确认时已经过了有效期」。熔断用 `EmergencyService.check` 触发，降级用 `HealthService.check` 传异常的信号。验证：确认因为 `tradingagentspro.workflows.order_flow` 不存在而失败
- [x] 3.2 新建 `src/tradingagentspro/workflows/order_flow.py` 的第一部分：`TradeServices`、`OrderFlowConfig`、`check_plan`、step 的结果类型（`Requested`、`Timing`、`AttemptKind`、`Attempt`、`Stage`、`PlanEnd`）、`OrderFlow` 的 `request_confirmation`、`settle`、`timing`，日志（design D1、D2、D4、D14）。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（`order_flow.py` 不 import dbos）
- [x] 3.3 先写测试第二部分（design D5、D6、D8、D12）：`attempt` 的每一种结果：已经有订单直接交给巡检；再校验通过下单（委托股数、限价）、调整后照常下单；再校验拒绝作废（价格偏离、降级、熔断，预留已释放，告警文字有每条规则）；开仓方案没有当天的最新价、行情不可用、读账户超时时 `RETRY` 并写 WARNING；退出方案不取这只股票的行情；`NotReconciledError`、`OrderPausedError`、`UnprotectedPositionError` 的 `RETRY`；`OutsideSessionError` 的 `NEXT_OPEN`；`ReconcileMismatchError` 作废、释放、告警，退出方案照常；券商拒绝受理时告警、不释放；提交时券商超时状态未知、不告警；401 和状态库出错时 ERROR、`alert_error` 为真才告警、`alerted` 的值；`void_expired`（有订单不释放、告警写最近一次没能下单的原因）；「再校验和提交之间别人拿不到锁」「请求确认时不拿锁」；告警发不出去时照常返回。验证：确认新的测试因为方法不存在而失败，第一部分仍然通过
- [x] 3.4 实现 `OrderFlow.attempt`、`void_expired` 和告警文字（design D5、D6、D8）：读数据不拿锁，再校验和提交同一次拿锁，告警在锁外。验证：`tests/workflows/test_order_flow.py` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 4. DBOS 工作流

- [x] 4.1 先写测试 `tests/workflows/test_order_workflow.py` 的第一部分（要开着 Docker，design D2、D3、D9、D15）：夹具用 `empty_database` 构造 DBOS（`enable_patching`）、`install(flow, sleep=拨钟的替身)`、launch，结束时 destroy，检查没有留下运行中的工作流线程；工具函数：有超时地等工作流结束、等事件变为 `waiting`。经 `OrderPlanIntake.submit` 和 `deliver_decision` 覆盖 spec：「提交方案」的「通过后启动下单流程」「启动下单流程失败」「同一个方案提交两次」；「请求人工确认」两个场景；「确认的结果」五个场景（超时用 1 秒的确认超时）；「按钮的结果只送给在等确认的方案」五个场景（「按钮和超时同时」在工作流得出超时以后直接 `DBOS.send`，检查 `FakeNotifier` 记下已超时、没有订单）；「等开盘」四个场景（检查休眠替身收到的秒数）；「下单前再校验和提交」「提交的结果」里要走完整个流程的场景（重试用拨钟的休眠替身推进时间）；「告警」四个场景；「日志」的「盘中下单的日志」。验证：确认因为 `tradingagentspro.workflows.order_workflow` 不存在而失败
- [x] 4.2 新建 `src/tradingagentspro/workflows/order_workflow.py`：`order_plan` 工作流和 `_execute`、各 step、`install`、`OrderPlanIntake`（`held_sector`、`submit`）、`deliver_decision`、`NotAwaitingConfirmationError`（design D1、D2、D3、D9）。验证：4.1 的测试通过；把 4.1 连续跑 5 次都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（只有这个文件 import dbos）
- [x] 4.3 先写重启和恢复的测试，再按需要修正实现（design D10、D15）：spec「持久化和恢复」的六个场景和「日志」的「重启以后不重复写日志」：跑完的工作流改回 `PENDING` 再 `DBOS._recover_pending_workflows()`，检查没有重复的请求确认、提交、告警、日志；替身抛 `BaseException` 的子类模拟进程在 `settle`、休眠、券商受理以后退出，再 destroy、重新构造、install、launch；「等确认时重启」按 design D15 先验证旧线程不会抢走消息；「程序更新以后恢复」在两次 launch 之间多登记一个测试用的工作流，并有不打开 patching 的对照。验证：这些测试通过，连续跑 5 次都通过；`uv run pytest tests/workflows` 全部通过

## 5. bootstrap

- [x] 5.1 先改测试（要开着 Docker，design D11、D13、D15）：`tests/bootstrap/externals.py` 的 `World` 加休眠替身（拨钟，可以安排抛 `BaseException` 的子类）、`FakeOpenD` 可以加别的股票的行情；`tests/bootstrap/test_app.py` 新增「录入并确认一个买入方案」「启动时恢复等开盘的方案」「程序更新以后恢复」（多登记一个工作流再启动）「停止时有方案在等确认」（monkeypatch 把装配用的确认超时改成 1 秒）；已有的启动、停止测试照常通过；删除 `tests/bootstrap/test_order_flow.py`（它的场景已经在 3.1 搬到 `tests/workflows/`，「按下以前留下的确认按钮」对应 order-flow spec「方案没有下单流程」，已在 4.1 覆盖）；`tests/bootstrap/test_cli.py` 加 `entry`：`main` 返回以后用它的返回值调用 `os._exit`（monkeypatch 成记录调用的替身）、`--version` 时不调用。验证：确认新的测试因为还没接线而失败
- [x] 5.2 改 `src/tradingagentspro/bootstrap/app.py`：`Externals` 加 `sleep`（`SYSTEM_EXTERNALS` 用 `DBOS.sleep`），`_assemble` 建 `TradeServices`、`OrderPlanIntake`、用 `deliver_decision` 建 `DiscordNotifier`、在通知之后建 `OrderFlow`，`start` 在 launch 之前 `install`，DBOS 配置加 `enable_patching`；模块说明里「以后的流程变更在 start 里加几行」改为已经做了；删除 `bootstrap/order_flow.py`；`bootstrap/cli.py` 加 `entry`；`pyproject.toml` 的 `tradingagentspro` 入口改为 `tradingagentspro.bootstrap.cli:entry`，然后 `uv sync`（design D10、D11、D13）。验证：`tests/bootstrap` 全部通过；`uv run tradingagentspro --version` 照常打印版本号、退出码 0；`uv run pytest` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 收尾

- [x] 6.1 对照 specs/order-flow/spec.md 和 specs/bootstrap/spec.md，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
