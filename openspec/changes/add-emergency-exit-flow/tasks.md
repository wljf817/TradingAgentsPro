# Tasks

## 1. 前置和 Proposal

- [ ] 1.1 确认三个前置变更都已经归档、代码在 main 上：「下单流程」（`workflows/order_flow.py` 有 `TradeServices`，`workflows/account_snapshot.py` 有 `account_snapshot`）、「收盘对账」（`workflows/patrol.py` 的 `run_round` 最后一项是收盘对账，有 `workflows/names.py`）、「改单未生效时不再改单」。验证：`ls openspec/changes` 里没有 `add-order-flow`、`add-close-reconciliation`、`update-stop-pending-modify`，`grep -n "class TradeServices\|def account_snapshot\|_close_reconcile" src/tradingagentspro/workflows/*.py` 都有；不满足就停下，不开始本变更
- [ ] 1.2 人对 proposal「需要人决定」两条表态以后，改完整版 Proposal：同意就按推荐做法改「订单生命周期」表的「止损单挂单失败」「加仓」两行和「持仓与退出」的「紧急风险退出的触发」；不同意按人的决定改，并同步改 specs/emergency-exit-flow/spec.md（「要清仓的股票」）和 design D2、D4（简版不动）。验证：`git diff docs/` 只有这三处（不同意时另有 spec 和 design 的对应改动）；`openspec validate add-emergency-exit-flow --strict` 通过

## 2. 风控的只读查询

- [ ] 2.1 先写测试：`tests/modules/risk/test_service.py` 补 `active_plans`（design D9）：只返回有效预留的方案（`TradePlan`），释放了的、全部成交的不在里面，没有预留时为空。验证：确认因为 `active_plans` 不存在而失败
- [ ] 2.2 在 `src/tradingagentspro/modules/risk/service.py` 加只读的 `RiskService.active_plans`，不改已有方法。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 3. 判定

- [ ] 3.1 先写测试（design D2、D3、D4、D7、D12）：新建 `tests/workflows/emergency_builders.py`（spec 的基准数据：`FakeBroker` 的权益、前一交易日收盘时的权益、AAA 和 BBB 的持仓和均价；AAA、BBB 全部成交的开仓订单直接写进订单存储；`execution.protect` 挂上 AAA-STOP-1、BBB-STOP-1 以后清掉调用记录；`FakeQuoteSource` 的 AAA、BBB 行情；真实的执行模块（错开打开）、风控（内存预留）、紧急退出、健康监控、行情、`NotificationService` + `FakeNotifier`、`FakeClock`、`RLock`、`TradeServices`、`EmergencyFlow`；P2 到 P7 的辅助函数）和 `tests/workflows/test_emergency_flow.py` 的第一部分，直接调用 `holdings_of` 和 `EmergencyFlow.judge`：`holdings_of` 的有行情、没有最新价、没有行情；盘外返回 None、不读账户不取行情；读账户超时返回 None、写 WARNING、不取行情；行情不可用时写 WARNING、只按熔断给出全部持仓；行情源缺 AAA 时 WARNING 写明 AAA；没有持仓时不取行情；今天有开仓成交时从成交均价算（P7）；本地备用止损（无保护、错开中、价格高于止损价不触发、没有最新价不判断）；同时触及时原因按组合熔断、个股跌幅、本地备用止损取第一个、方案相同；当天的清仓方案已有终态订单时不在结果里，有未结束订单时不在结果里、记录找回且不算新的触发；当天触发过、还没有清仓单的股票价格回升以后仍在结果里；更早交易日的记录被删掉；熔断已触发时给出 `Breaker`；`snapshot` 和 `account_snapshot` 的结果相同。每个测试的说明写明对应的 spec 场景或 design 条目。验证：确认因为 `tradingagentspro.workflows.emergency_flow` 不存在而失败
- [ ] 3.2 新建 `src/tradingagentspro/workflows/emergency_flow.py` 的第一部分：`ExitCause`、`Trigger`、`Breaker`、`Judgement`、`_Record`、`holdings_of`、`EmergencyFlow.__init__` 和 `judge`（design D2、D3、D4、D7），只 import 模块的包根、contracts、shared 和 workflows 里的 `order_flow`、`account_snapshot`。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过

## 4. 清仓、熔断和告警

- [ ] 4.1 先写测试第二部分（design D5 到 D8），直接调用 `judge` 以后的 `cancel_buys`、`advance`、`report`：`advance` 的每一步——已有清仓单什么都不做；作废这只股票有效预留、没有订单的方案（P3、P4 释放，P5 和当天的清仓方案不动，之后再校验 P3 得到"方案已结束"）；有挂着的订单时撤单、已经都有撤单要求时不再撤；没有挂着的订单时检查、再校验、提交（清仓股数是实时可卖数量，撤单期间有成交时是 40）；风控拒绝时不下单、告警「没有可卖的股数」、记录结束、之后不再告警；提交时券商超时订单状态未知；`execution.order` 出错时异常抛出（由巡检的 `_item` 处理）。`cancel_buys`：只在有还没撤的买单时撤、熔断告警每个交易日一次、写撤掉的买单数。`report`：清仓单全部成交、已拒绝、已过期的结果告警各一次；收盘以后还没有清仓单的告警一次并释放清仓方案的预留；盘中没有订单的不动。告警文字：开始紧急退出的三种原因（个股跌幅写参考价、最新价、一位小数的跌幅和紧急阈值，组合熔断，本地备用止损写无保护仓位或错开中和两个价格）和三种结尾、熔断、结果、没有可卖的股数、到收盘还没下单；每条告警同时有一条内容相同的 WARNING；发不出去时写错误日志、照常继续、不重发。锁：另一个线程拿着锁时 `advance` 等待；`FakeNotifier` 阻塞时测试线程 `acquire(blocking=False)` 成功。验证：确认新的测试因为方法不存在而失败，第一部分仍然通过
- [ ] 4.2 实现 `EmergencyFlow.cancel_buys`、`advance`、`report` 和告警文字（design D5 到 D8）：锁里做事、锁外推送，动作和订单状态的中文名用 workflows 已有的对照（是私有名字时移到 `workflows/names.py` 共用，原来的调用方改为从那里 import）。验证：`tests/workflows/test_emergency_flow.py` 全部通过；`uv run pytest tests/workflows` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 接到巡检

- [ ] 5.1 先改巡检的测试（design D1、D10、D12）：`tests/workflows/builders.py` 的 `World` 加上 `FakeQuoteSource`（默认没有行情）、紧急退出、健康监控、行情、`TradeServices` 和 `EmergencyFlow`，`FakeBroker` 的权益和前一交易日收盘时的权益设为 10,000，巡检用新的构造参数（`restart` 同样重建）；`tests/workflows/test_patrol.py` 的「没有持仓也没有订单」改为断言第 1 到 3 步只读了一次全部持仓、另外只有一次读账户（说明里注明 design 的 Risks）；新增测试：一轮里紧急退出在对齐止损单之后、收盘对账之前（用会记录调用顺序的替身或 `caplog`），盘外一轮不读账户；`tests/workflows/test_patrol_thread.py` 补 spec「做某一项时停止」（止损单替身撤 AAA-STOP-1 时阻塞到测试放行，放行以后 AAA 的告警已推送、BBB 没有处理，`stop` 才返回；有超时）。验证：确认新的测试因为巡检还没调用紧急退出而失败，巡检原有的其余测试照常通过
- [ ] 5.2 改 `src/tradingagentspro/workflows/patrol.py`：构造参数加 `emergency: EmergencyFlow`，`run_round` 在 `_align_stops` 之后、`_close_reconcile` 之前调用 `_emergency_exit`（design D1），模块和类的说明加上紧急退出。验证：`uv run pytest tests/workflows` 全部通过，`test_patrol_thread.py` 用 shell 循环连续跑 20 次都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过
- [ ] 5.3 写 spec 场景的测试（design D12）：在 `tests/workflows/test_emergency_flow.py` 第三部分，经 `world.patrol.run_round()` 覆盖 specs/emergency-exit-flow/spec.md 里「巡检里的紧急退出」（「做某一项时停止」已在 5.1）、「判定的输入」「要清仓的股票」「一只股票的清仓」「单日亏损熔断」「告警」「重启以后」「一轮里的失败」「执行模块和风控的调用串行」的全部场景；"之后一轮"拨 `FakeClock` 15 秒，券商的回报用替身安排；"重启"用同一套存储和替身重建执行模块（再对账一次）、风控、紧急退出、`EmergencyFlow` 和巡检；"处理 AAA 时读状态库出错"用订单存储替身抛一次错。和实现不符时先看 spec 和 design，确定是实现的错再改实现，是文档的错先改文档。验证：每个场景至少一个测试，测试的说明写明场景名；`uv run pytest tests/workflows` 全部通过

## 6. bootstrap

- [ ] 6.1 先写测试（要开着 Docker，design D10、D12）：`tests/bootstrap/test_app.py` 新增「盘中紧急清仓」（`FakeAlpaca` 持有 AAA 50 股、有挂着的 AAA-STOP-1，状态库里写好 AAA 全部成交的开仓订单和止损保护，`FakeOpenD` 加 AAA 的行情：前一交易日收盘价 100、最新价 92；用 `start` 和 `ExitStack` 启动，有超时地轮询直到 `FakeAlpaca` 收到撤 AAA-STOP-1 的请求、`FakeGateway` 收到开始紧急退出的告警；再让止损单撤销，轮询到 AAA 50 股的市价卖单，客户端订单 ID 是 `close_plan` 给出的 ID；关闭）；需要时让 `FakeOpenD` 能返回别的股票的行情。已有的 bootstrap 测试在盘中启动的，巡检第一轮会多读一次 Alpaca 账户：断言具体请求的按需改。验证：确认「盘中紧急清仓」因为还没接线而失败
- [ ] 6.2 改 `src/tradingagentspro/bootstrap/app.py`：`EmergencyConfig()` 只建一个，交给紧急退出模块和 `EmergencyFlow`；在 `NotificationService` 之后建 `EmergencyFlow(services, notification, clock, calendar, emergency_config)`，交给 `SessionPatrol`（design D10）；模块说明加上紧急退出。验证：`uv run pytest tests/bootstrap` 全部通过，每个测试结束以后没有留下运行中的 `session-patrol` 线程；`uv run pytest` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 收尾

- [ ] 7.1 对照 specs/emergency-exit-flow/spec.md 和 specs/bootstrap/spec.md，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
