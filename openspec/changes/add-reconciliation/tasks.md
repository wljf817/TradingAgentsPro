# Tasks

## 1. 找不一致的纯逻辑

- [ ] 1.1 先写测试：
  - `tests/modules/execution/domain/test_reconciliation.py`：
    - `stray_orders`：reconciliation spec「不认得的挂单」表的五行，包括同一个客户端订单 ID 的两笔订单只报券商订单号不同的那一笔
    - `extra_stops`：「多出来的止损单」表的三行，以及没有挂着止损单的股票不报；不一致时返回这只股票挂着的全部止损单
    - `position_explained`：有累计成交的买单、未结束的买单（包括状态未知、累计成交 0）、只有一股没成交就结束的买单、只有卖单
    - `Reconciliation.clean`：三类都为空时为真，任何一类不为空时为假
  - `tests/modules/execution/domain/test_order.py`：`end_missing` 在截止时刻之前记为已撤销、截止时刻及之后记为已过期，累计成交和成交均价保留，终态的订单不变

  验证：运行 `uv run pytest tests/modules/execution/domain`，确认因为模块和方法不存在而失败
- [ ] 1.2 实现 `domain/reconciliation.py`（`WorkingOrder`、`Listing`、`Reconciliation`、`NotReconciledError`、`ReconcileMismatchError`、`stray_orders`、`extra_stops`、`position_explained`）和 `Order.end_missing`（design D2–D4、D6）。验证：1.1 的测试通过；`uv run pytest tests/architecture` 通过（纯区域的规则）；`uv run mypy` 通过

## 2. 接口和测试替身

- [ ] 2.1 `ports.py`：`Broker` 增加 `working_orders`、`positions`，`StopBroker` 增加 `working_stops`，`ProtectionStore` 增加 `active`（design D6）。测试替身（design D7）：
  - `tests/fakes/broker.py`：`FakeBroker` 增加 `working_orders`、`positions`、`add_working`；`lose` 增加 `listed` 参数；新的读取记入 `calls`，可以用 `fail_next` 安排失败
  - `tests/fakes/stops.py`：`FakeStopBroker` 增加 `working_stops`、`add_working`；`lose` 增加 `listed` 参数；`working_stops` 可以安排失败
  - `tests/fakes/protections.py`：`InMemoryProtectionStore` 增加 `active`

  验证：全部已有测试通过；`uv run mypy` 通过（替身满足新的接口）

## 3. 对账流程

- [ ] 3.1 先写测试 `tests/modules/execution/test_reconciliation_service.py`。测试直接调用 `reconcile`；需要重启的场景用同一套存储重新创建执行用例：
  - spec「对账」六个场景。「读取券商数据时断线」和「跟进订单时断线，对账照样完成」的最后一句，留到 5.1 测
  - 「不认得的挂单」五个场景
  - 「多出来的止损单」三个场景
  - 「来历不明的持仓」前两个场景

  验证：运行这个测试文件，确认因为 `reconcile` 不存在而失败
- [ ] 3.2 实现 `ExecutionService.reconcile`：先读全量，再比对，再跟进每笔未结束的订单、对齐需要对齐的股票，最后找来历不明的持仓，并记下最近一次完成的对账（design D1–D3、D5 的 `_last`）。读取失败时抛 `BrokerUnavailableError`，不改变任何东西。验证：3.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 结束查不到的订单和止损单

- [ ] 4.1 先写测试 `test_reconciliation_service.py`：
  - spec「结束查不到的订单和止损单」六个场景
  - 「来历不明的持仓」第三个场景
  - 不对账时跟进和对齐的行为不变：order-lifecycle spec「券商受理过的订单查不到」和 stop-protection spec「受理过的止损单查不到」仍然通过

  验证：确认新场景失败
- [ ] 4.2 实现 design D4：`_track`、`_advance`、`_align` 接收 `Listing`，对外的 `track`、`protect` 传 `None`；受理过却查不到时，按 `Listing` 结束订单或止损单。验证：4.1 的测试和全部已有测试通过

## 5. 对账之前不接受新订单，有不一致时暂停开仓

- [ ] 5.1 先写测试：
  - `test_reconciliation_service.py`：spec「对账之前不接受新订单」四个场景、「有不一致时暂停开仓」五个场景，以及「读取券商数据时断线」和「跟进订单时断线，对账照样完成」的最后一句
  - `tests/modules/execution/test_service.py`：order-lifecycle spec 修改后的「提交订单」：基准数据里执行模块已经对过账，原有场景照常通过

  验证：确认新场景失败
- [ ] 5.2 实现 `submit` 的两条检查（design D5）；`__init__.py` 导出 `WorkingOrder`、`Reconciliation`、`NotReconciledError`、`ReconcileMismatchError`。测试的执行用例跟着改（design D7）：
  - `tests/modules/execution/builders.py`：`execution_service` 增加 `reconciled` 参数，默认新建以后先对账一次，再清掉替身的调用记录
  - 已有测试里的"重启"不对账；重启以后要提交新方案的测试，先对账

  验证：5.1 的测试和全部已有测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 性质测试

- [ ] 6.1 `tests/modules/execution/test_protection_service.py` 的止损保护状态机（design D7）：
  - 增加"对账"这一步；重启之后先对账
  - 读取失败也可以安排：`working_orders`、`positions`、`working_stops`
  - 每次完成的对账都没有不一致
  - 提交卖单遇到 `NotReconciledError` 时跳过，和 `OrderPausedError` 一样

  验证：`uv run pytest tests/modules/execution/test_protection_service.py` 通过，并且确认样例里出现过对账完成和对账失败（用 `event` 统计）
- [ ] 6.2 `tests/modules/execution/test_service.py` 的订单状态机：重启之后先对账，经过同样的调用记录；每次完成的对账都没有不一致。验证：`uv run pytest tests/modules/execution/test_service.py` 通过

## 7. 文档和收尾

- [ ] 7.1 人同意 proposal「需要人决定」第 1、2 条之后，在完整版 Proposal「订单生命周期」表的"每日对账"一行补上：账户只给系统用，券商那边系统解释不了的挂单和持仓都算不一致；之后的对账没有不一致时恢复开仓。验证：这一行和 reconciliation spec 一致，其他行没有改动
- [ ] 7.2 对照两份 delta spec，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.3 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`，全部通过
