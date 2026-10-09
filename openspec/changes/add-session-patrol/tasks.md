# Tasks

## 1. Proposal

- [ ] 1.1 人对 proposal「需要人决定」第 1 条表态以后，改完整版 Proposal「风控引擎与执行 · 额度预留」里预留释放的那一句：同意就按推荐做法改，不同意按人的决定改（简版不动）。验证：`git diff` 只有这一句；specs 和 design 不用跟着改（risk-reservations spec 已经包括订单过期）

## 2. 执行模块和风控的只读查询

- [ ] 2.1 先写测试（design D5、D6、D13）：新建 `tests/modules/execution/test_query_service.py`，用 `tests/fakes/` 的券商、止损单、存储和 `builders.py` 的基准数据，覆盖 `open_orders`（只有未结束的，按创建的先后，不调用券商）、`order`（已经结束的也返回，没有订单时为 None）、`symbols_to_protect`（券商的持仓加上当前止损单没结束或无保护的股票，去重、按代码排序；止损单已经结束、不是无保护、券商也没有持仓的股票不在里面；读全部持仓失败时抛 `BrokerUnavailableError`）；`tests/modules/risk/test_service.py` 补 `active_plan_ids`（有效预留在里面；释放了的、全部成交的、被风控拒绝没有预留的都不在）。验证：确认这些测试因为方法不存在而失败
- [ ] 2.2 在 `modules/execution/service.py` 加 `open_orders`、`order`、`symbols_to_protect`，在 `modules/risk/service.py` 加 `active_plan_ids`（design D5、D6），不改任何已有方法。验证：2.1 的测试通过；`uv run pytest tests/modules` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 3. 巡检的一轮：跟进订单和同步预留

- [ ] 3.1 先写测试（design D4、D9、D13）：新建 `tests/workflows/__init__.py`、`tests/workflows/builders.py`（按 spec「巡检的开始、间隔和停止」的基准数据建执行模块、风控、`NotificationService` + `FakeNotifier`、一把 `RLock` 和巡检；预留直接写进 `InMemoryReservationStore`；P5 的方案）和 `tests/workflows/test_patrol.py` 的第一部分：`PatrolConfig` 的「默认配置」「间隔不大于 0」；直接调用 `run_round` 覆盖 spec「每一轮」的「部分成交到全部成交」（这一部分只检查订单和预留，止损单留给 4.1）「提交时被拒的订单也释放预留」「重启后补上停机期间的成交」（用同一套存储重新建执行模块和巡检，先 `reconcile` 再 `run_round`）「还没有订单的方案不动」「跟进时券商超时」，「巡检的开始、间隔和停止」的「收盘以后照样跟进」，「订单和止损单变化的日志」的「订单成交写日志」（`caplog`），「一轮里的失败」的「跟进一笔订单时出了意外的错」（券商替身抛一个意外的异常；止损单照常对齐的部分留给 4.1）。每个测试的说明写明对应的 spec 场景。验证：确认因为 `tradingagentspro.workflows.patrol` 不存在而失败
- [ ] 3.2 新建 `src/tradingagentspro/workflows/patrol.py`：`PatrolConfig`；`SessionPatrol` 的构造、`run_round` 的第 1、2 步、每一项的出错处理和订单变化的日志（design D4、D7、D9、D10），第 3 步先留空；`workflows/__init__.py` 的说明改为流程包括 DBOS 工作流和盘中巡检这样的进程内循环，README 目录分层的 workflows 一行跟着改。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（workflows 只 import 模块的包根）

## 4. 巡检的一轮：对齐止损单、告警和锁

- [ ] 4.1 先写测试 `tests/workflows/test_patrol.py` 的第二部分（design D3、D5、D8、D9、D10、D13）：spec「每一轮」的「部分成交到全部成交」补上止损单的检查、「止损单被券商撤销后重挂」「有持仓、还没有止损单的股票」「挂单被拒以后下一轮补挂」「没有持仓也没有订单」（券商只收到一次读取全部持仓）；「一轮里的失败」的「跟进一笔订单时出了意外的错」补上止损单照常对齐；「巡检的开始、间隔和停止」的「周末照样对齐止损单」；「无保护仓位的告警」的八个场景（告警文字检查股票、`2026-10-14 15:00 UTC` 这样的时刻和「不能加仓」；「重启以后再告警一次」用同一套存储新建巡检；「告警发不出去」用 `FakeNotifier.fail_next`，检查错误日志和之后不重发）；「订单和止损单变化的日志」的「换了一张止损单写日志」「没有变化不写日志」；「一轮里的失败」的「读全部持仓失败时这一轮不对齐」「状态库出错」（存储替身抛错，检查 ERROR 和 `run_round` 正常返回）；「执行模块和风控的调用串行」的三个场景（另一个线程拿着锁时券商没有收到查询、放开以后才跟进；通知替身阻塞在告警里时测试线程 `acquire(blocking=False)` 成功；同一线程再次拿锁）。验证：确认新的测试因为第 3 步还没实现而失败，第一部分的测试仍然通过
- [ ] 4.2 实现 `run_round` 的第 3 步：读要对齐的股票、逐只 `protect`、止损单变化的日志、无保护告警的记录和去重、`unprotected_text`、读持仓失败的 WARNING（design D5、D8、D9、D10）；锁按项拿，告警在锁外。验证：`tests/workflows/test_patrol.py` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 巡检的线程

- [ ] 5.1 先写测试 `tests/workflows/test_patrol_thread.py`（design D11、D13）：间隔设得很短，用替身里的 `threading.Event` 同步，不按墙上时间断言具体时刻。覆盖 spec「巡检的开始、间隔和停止」的「开始后立即做第一轮」「每一轮做完以后等间隔」（一轮做完以后才开始等，两轮不重叠）「等下一轮时停止」（间隔设长，`stop` 立即返回）「做某一项时停止」（券商替身在按 P1 查询时阻塞，`stop` 在另一个线程里调用，放行以后 P1 已保存、P5 没有被跟进，`stop` 才返回），「巡检配置」的「修改间隔」（检查等待用的是配置的间隔），「一轮里的失败」里 `run_round` 抛出意外异常以后照常做下一轮；另外检查线程名是 `session-patrol`、是守护线程、`stop` 以后已经结束，没有开始过就 `stop` 什么都不做。每个测试都有超时，失败时不会卡住。验证：确认因为 `start`、`stop` 不存在而失败
- [ ] 5.2 实现 `SessionPatrol.start`、`stop` 和线程的循环（design D11）：停止的 `Event`、每一项开始之前检查、`join` 不设超时、循环外层的出错处理。验证：用 shell 循环把 5.1 的测试连续跑 20 次，都通过；`uv run mypy`、`uv run ruff check` 通过

## 6. 启动和停止

- [ ] 6.1 先改 `tests/bootstrap/test_app.py`（要开着 Docker，design D12、D13）：`NORMAL_ORDER` 在「DBOS 已启动」和「启动完成」之间加「盘中巡检已开始」，停止的顺序最前面加「已关闭：盘中巡检」；「启动和停止 DBOS」的顺序；`start` 返回的 `TradingApp.lock` 是巡检用的那把锁（spec「巡检和以后的流程共用一把锁」）；新增「启动以后巡检跟进停机前提交的订单」（状态库里先写一笔已提交的 P1 订单和 100 股的预留，`FakeAlpaca` 里 P1 全部成交、持仓 BBB 100 股；用 `start` 和 `ExitStack` 直接启动，有超时地轮询预留直到不再有效，再关闭）和「停止时巡检正在跟进订单」（`FakeAlpaca` 让按 P1 的查询阻塞到测试放行；没有这样的钩子就在 `tests/integrations/alpaca/server.py` 里加一个）。已有的测试照常通过，不依赖巡检做没做第一轮。验证：确认新的和改过的测试因为还没接线而失败
- [ ] 6.2 改 `src/tradingagentspro/bootstrap/app.py`（design D12）：`_assemble` 建 `RLock` 和巡检放进 `_Parts`；`start` 在启动 DBOS 之后加「开始盘中巡检」一步并登记停止；`TradingApp` 加 `lock`；模块说明里「launch 之后开始盘中巡检」改为已经做了。验证：`tests/bootstrap` 全部通过，每个测试结束以后没有留下运行中的 `session-patrol` 线程；`uv run pytest` 全部通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 7. 收尾

- [ ] 7.1 对照 `specs/session-patrol/spec.md` 和 `specs/bootstrap/spec.md`，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 7.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
- [ ] 7.3 部署以后在部署机器上看一个交易日（需要在部署机器上操作，按 deploy/README.md 更新代码并重启主程序）：`journalctl -u tradingagentspro` 里有「盘中巡检已开始，间隔 15 秒」，盘中和盘后都没有巡检的 ERROR；有持仓时启动后有每只持仓的止损单日志。验证：日志符合；不符时先改 spec 和 design，再改代码和 3 到 6 组的测试
