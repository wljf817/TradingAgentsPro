# Tasks

## 1. 前置

- [ ] 1.1 确认「下单流程」「收盘对账」已经 apply（「紧急退出流程」如果也已经 apply，一并对照）：`workflows/reconciliation.py` 里有 `close_text`、`startup_text` 共用的三类不一致的行的函数，`run_round` 最后一项是收盘对账，`bootstrap/app.py` 在 Discord 连上以后推送启动对账的告警。那个函数是私有的就改为公开（design D7），不改它的输出。验证：`uv run pytest tests/workflows tests/bootstrap` 通过；和 design「Context」不符的地方先改 design 再往下做

## 2. 健康信号和告警文字

- [ ] 2.1 先写测试（design D3、D4、D7、D9）：新建 `tests/workflows/test_health.py`，用 `FakeQuoteSource`、`FakeClock`、真实的 `MarketDataService` 和 `HealthService`，直接调用 `HealthMonitor.signals`：health-monitoring spec「健康信号」表的每一行（模型、预算按正常；心跳等于时钟的现在；券商取参数；行情取到时是 SPY 的更新时刻）；取不到行情时写 WARNING（含原因），沿用上一次取到的时刻，还没取到过时为 None；「一次取不到行情按上一次取到的时刻」「启动以后还没取到过行情」「行情停更」「盘外取不到行情不算异常」「一轮做得久不算调度器心跳异常」里不经过巡检的部分（直接 `health.check(monitor.signals(...))`）。另测 `checks_text`（五项按表的顺序、只列给出的项）、`degraded_text`（巡检和启动两种第一行）、`recovered_text`（没有不一致；有不一致时每一条一行、最后一行暂停开仓），文字和 design D7 的样例一致。每个测试的说明写明 spec 场景。验证：确认因为 `workflows/health.py` 不存在而失败
- [ ] 2.2 新建 `src/tradingagentspro/workflows/health.py`：`HealthMonitor` 的构造和 `signals`（design D3、D4）、`checks_text`、`degraded_text`、`recovered_text`（design D7）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（只 import 模块的包根、`shared.clock` 和 `workflows/reconciliation.py`）

## 3. 判定、告警和恢复后的对账

- [ ] 3.1 先写测试（design D5、D6、D9），在 `tests/workflows/test_health.py` 里用 `tests/fakes/` 的券商、止损单、存储和 `FakeNotifier`，真实的 `ExecutionService`（已经对账一次）、`NotificationService` 和一把 `RLock`，直接调用 `HealthMonitor.judge(broker_connected)`：
  - health-monitoring spec「状态变化的告警和日志」表的每一行：日志级别和内容（`caplog`）、有没有推送、推送的文字；「告警发不出去」用 `FakeNotifier` 报错，检查错误日志、状态照常变化、之后不重发
  - 「恢复后的对账」表的每一行：降级 → 等待对账不对账（券商没有收到读取全部挂单）；等待对账 → 等待对账时对账、状态回到正常、推送；等待对账 → 降级不对账；「恢复后的对账有不一致」（之后 `health.status.degraded` 为假，`execution.submit` 一个买入方案报对账不一致）；「对账时券商又断开」用 `FakeBroker.fail_next("working_orders")`；「恢复后的对账出了意外的错」替换读挂单的方法抛错，检查异常抛出、状态仍是等待对账、没有推送
  - 锁：另一个线程拿着锁时不需要对账的判定照样做完；需要对账时等锁，放开以后才对账；推送告警时通知替身阻塞，测试线程 `acquire(blocking=False)` 成功

  每个测试的说明写明 spec 场景。验证：确认因为 `judge` 不存在而失败
- [ ] 3.2 在 `workflows/health.py` 实现 `HealthMonitor.judge`、`_report`、`_recover`（design D5、D6）：判定和推送不拿锁，对账和 `reconciled()` 在同一次拿锁里，对账的 `BrokerUnavailableError` 写 WARNING，别的异常往外抛。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check` 通过

## 4. 巡检接线

- [ ] 4.1 先写测试（design D1、D2、D9）：`tests/workflows/builders.py` 的 `World` 加上 `FakeQuoteSource`（SPY 的更新时刻每次都是时钟的现在之前 10 秒）、`MarketDataService`、`HealthService`、`HealthMonitor`，巡检带上它，`restart` 时新建健康监控；在 `tests/workflows/test_health.py` 里调用 `run_round` 覆盖 spec 里经过巡检的场景：「一轮对齐止损单以后判定，正常时不告警」（这一轮券商只收到一次读取全部持仓）、「判定时不拿锁」「读全部持仓超时算券商断开」「跟进订单超时不算券商断开」「一次取不到行情按上一次取到的时刻」「券商断开时进入降级并告警」「降级期间不重复告警」「降级期间异常的检查变了只写日志」「各项恢复以后只写日志」「等待对账时又有检查异常只写日志」「数据延迟来回抖动」「券商偶发一次超时」「各项恢复以后下一轮对账」「断线期间的变化在对账以前已经补上」「对账时别的调用拿着锁」「收盘以后数据延迟不再检查」「程序启动时处于降级模式」（先用 `judge_health_at_startup` 判定一次）、「读全部持仓时认证错误」「恢复后的对账出了意外的错」（检查 ERROR 写明健康判定出错）；收盘对账仍在健康判定之后（同一轮里恢复后的对账先于收盘对账读全部挂单）。`tests/workflows/test_patrol_thread.py` 补「停止时正在恢复后的对账」：读全部挂单阻塞到测试放行，放行以后状态回到正常、告警已推送，`stop` 才返回（有超时）。验证：确认新测试因为巡检还没接上而失败，`tests/workflows/test_patrol.py` 原有的测试仍然通过
- [ ] 4.2 改 `src/tradingagentspro/workflows/patrol.py`（design D1、D2）：构造参数加 `health: HealthMonitor`；`_align_stops` 返回读全部持仓的结果（`True`、`False`、`None`）；`run_round` 在第 3 步之后加 `_judge_health`，先看停止信号、结果为 `None` 时不判定、包在 `_item("健康判定")` 里；收盘对账仍是最后一项；模块和类的说明写上健康判定。验证：`uv run pytest tests/workflows` 全部通过，`test_patrol_thread.py` 用 shell 循环连续跑 20 次都通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 启动时的判定和告警

- [ ] 5.1 先写测试（design D8、D9）：`tests/bootstrap/test_startup.py` 改为用 `HealthMonitor` 调用 `judge_health_at_startup(health, monitor)`：信号表（券商按连接，其余同 health-monitoring spec「健康信号」）、降级时的 WARNING 用检查的中文名、取到的行情时刻记在 `HealthMonitor` 里给之后的判定用。`tests/bootstrap/test_app.py`（要开着 Docker）补 bootstrap spec「第一次健康判定」的场景：盘中行情正常和盘外 OpenD 没开时 `FakeGateway` 没有收到健康的告警；盘中 OpenD 没开、开盘后行情一直没有更新时收到一条，文字写明启动时处于降级模式、数据延迟、已暂停开仓，并且在日志的「Discord 已连接」之后、「DBOS 已启动」之前；「启动对账和健康都要告警」两条告警的先后；「健康的告警发不出去」有错误日志、启动完成；「巡检第一轮仍是降级时不再告警」有超时地等巡检做完第一轮，`FakeGateway` 只有一条健康告警。验证：确认新测试因为还没接线而失败
- [ ] 5.2 实现（design D8）：`bootstrap/startup.py` 的 `judge_health_at_startup` 改用 `monitor.signals(broker_connected=True)`、日志用 `checks_text`，模块说明改为告警由 `app.py` 发出；`bootstrap/app.py` 的 `_assemble` 建 `HealthMonitor` 放进 `_Parts` 并交给巡检，`start` 在启动对账的告警之后、「启动 DBOS」之前，第一次判定是降级时推送 `degraded_text(..., at_startup=True)`。验证：`tests/bootstrap` 全部通过，每个测试结束以后没有留下运行中的 `session-patrol` 线程；`uv run pytest` 全部通过

## 6. 收尾

- [ ] 6.1 对照 `specs/health-monitoring/spec.md` 和 `specs/bootstrap/spec.md`，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [ ] 6.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
