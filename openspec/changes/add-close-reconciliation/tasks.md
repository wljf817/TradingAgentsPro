# Tasks

## 1. 人的决定

- [ ] 1.1 人对 proposal「需要人决定」第 1 条表态以后：同意就不改；不同意「收盘时没有持仓清零」时，删掉 spec「连续天数」表里那一行和「连续天数」「结果消息」里收盘时没有持仓的两个场景，design D5、D6 里 `counted` 改为等于 `clean`。场景清单那一半不进代码，不用改。验证：`openspec validate add-close-reconciliation --strict` 通过，spec、design、tasks 里说法一致

## 2. 状态库的历史读取

- [x] 2.1 先写测试（design D7、D11）：在 `tests/modules/statestore/test_repo.py` 补 state-store spec「读取一段时间里的版本」的四个场景（按时刻读历史、T 以后没有新版本、写入时刻正好是 T、空表），用测试时钟控制每个版本的写入时刻。验证：要开着 Docker；确认这些测试因为 `VersionedStore.history` 不存在而失败
- [x] 2.2 在 `src/tradingagentspro/modules/statestore/repo.py` 加 `VersionedStore.history(since)`（design D7：`DISTINCT ON` 的那部分包成子查询再 `UNION ALL`，按键、版本号排序），不改已有方法。验证：2.1 的测试通过；`uv run pytest tests/modules/statestore`、`uv run mypy`、`uv run ruff check` 通过

## 3. 收盘对账的纯逻辑

- [x] 3.1 先写测试（design D3、D4、D5、D6、D11）：新建 `tests/modules/execution/domain/test_close.py`，用样例日历和直接构造的订单、止损保护覆盖：`close_day`（spec「收盘对账的时机」表的每一行：盘中、收盘后不到延迟、20:09:59Z 和 20:10:00Z 的边界、下一个交易日开盘前 13:00Z 和开盘时 13:30Z、半日市 2026-11-27 的 18:09:59Z 和 18:10Z、周六补做周五、感恩节当天、延迟改为 30 分钟）；`close_window_start`（2026-10-14 是 2026-10-13 20:10Z，2026-10-19 是 2026-10-16 20:10Z，2026-11-27 是 2026-11-25 21:10Z）；`leftover_orders`（对账以后已结束和仍未结束的都在，带对账以后的状态）；`new_stops`（序号变大的才算，以前没有记录的算 0，改单不算）；`unprotected_spells`（同一段的多个版本只算一条，两段两条，时间段开始时已经无保护的算，开始以前结束的不算——用 `history` 给出的版本序列表达）；`close_streak` 和 `CloseReconciliation.clean`、`counted`（spec「连续天数」表的三行，上一个交易日没有记录、有记录但连续天数 0）。每个测试的说明写明对应的 spec 场景。验证：确认因为 `domain/close.py` 不存在而失败
- [x] 3.2 新建 `src/tradingagentspro/modules/execution/domain/close.py`（design D3 到 D6 的 dataclass 和纯函数），在执行模块包根导出。验证：3.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture` 通过（纯区域白名单不变）

## 4. 存储和迁移

- [x] 4.1 先写测试（design D6、D7、D11）：`tests/modules/execution/test_repo.py` 补收盘对账记录的存储（按交易日保存和读取、没有记录时为空、spec「收盘对账记录」的「记录原样读回」、用同一个数据库新建存储后读到）和止损保护存储的 `all`、`history`（内存实现带测试时钟，和数据库实现一起参数化）；`tests/bootstrap/test_database.py` 和 `tests/bootstrap/test_app.py` 里表的清单加上 `execution_close_reconciliations`，列和主键照 design D6，表名属于执行模块（state-store spec「表的归属和迁移」）。验证：要开着 Docker；确认新测试因为存储和迁移不存在而失败
- [x] 4.2 实现（design D6、D7）：`ports.py` 加 `CloseReconciliationStore`，`ProtectionStore` 加 `all`、`history`；`repo.py` 加表 `execution_close_reconciliations`、`PostgresCloseReconciliationStore`，`PostgresProtectionStore` 加 `all`、`history`；新建 `bootstrap/migrations/versions/v0003_create_close_reconciliations.py`（编号已被占用时顺延）；`tests/fakes/` 新建收盘对账记录的内存存储，`InMemoryProtectionStore` 加可选的时钟和 `all`、`history`；包根导出新名字。验证：4.1 的测试通过；`uv run pytest tests/modules tests/bootstrap/test_database.py` 通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 执行模块的收盘对账用例

- [x] 5.1 先写测试（design D1、D3、D4、D5、D11）：`tests/modules/execution/test_config.py` 补 spec「收盘对账配置」的默认值和非法值；新建 `tests/modules/execution/test_close_service.py`，用 `tests/fakes/` 的券商、止损单、带测试时钟的内存止损保护存储和内存记录存储，覆盖 spec「收盘对账的时机」里经由 `close_due` 的场景（含「一个交易日只做一次」「开盘以前重启时补做」「开盘以后不再补做」「修改延迟」）、「收盘对账的步骤」的两个场景、「差异」的全部场景、「连续天数」里不经过巡检的场景、「收盘对账记录」的「重启以后接着算」、「收盘对账的失败」里的「对账读挂单时断线」「对账跟进订单时超时」和读持仓超时时抛券商不可用、不保存记录。"重启"用同一套存储新建执行模块、先对账一次；无保护的时间段用测试时钟在对应时刻让对齐被拒、再补挂。每个测试的说明写明 spec 场景。验证：确认因为 `close_due`、`close_reconcile` 和配置项不存在而失败
- [x] 5.2 实现（design D1、D3、D4、D6）：`ExecutionConfig` 加 `close_reconcile_delay`；`ExecutionService` 加构造参数 `closes`、`close_due`、`close_reconcile`，不改任何已有方法；`tests/modules/execution/builders.py` 的 `execution_service` 和 `tests/workflows/builders.py` 传入内存记录存储；`bootstrap/app.py` 的 `_assemble` 传入 `PostgresCloseReconciliationStore`。验证：5.1 的测试通过；`uv run pytest` 全部通过（要开着 Docker）；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 6. 巡检触发和结果消息

- [x] 6.1 先写测试（design D2、D8、D9、D11）：新建 `tests/workflows/test_reconciliation.py`，用巡检的 `tests/workflows/builders.py`（加上内存记录存储和带时钟的止损保护存储）覆盖 spec「巡检里的收盘对账」除停止以外的四个场景（锁的两个场景用另一个线程拿锁、用会阻塞的通知替身）、「结果消息」的七个场景（检查推送的文字和 `caplog` 的级别、全文；「推送发不出去」用 `FakeNotifier.fail_next`）、「收盘对账的失败」里的「读持仓超时，下一轮再做」「券商到开盘都不可用」「保存记录时数据库出错」（记录存储替身抛一次错，检查 ERROR 和下一轮）、「收盘对账的时机」的「周末补做周五的」和「连续天数」的「降级模式不清零」（经过 `run_round`）；`close_text` 每一种行（六类差异、没有持仓、暂停开仓、恢复开仓）和 `startup_text`；`tests/workflows/test_patrol_thread.py` 补「停止时正在收盘对账」（券商替身读全部挂单时阻塞到测试放行，放行以后记录已保存、结果已推送，`stop` 才返回；有超时）。验证：确认因为 `workflows/reconciliation.py` 不存在而失败，巡检原有的测试仍然通过
- [x] 6.2 实现（design D2、D8、D9）：新建 `workflows/names.py`，把 `patrol.py` 里的中文名移过去；新建 `workflows/reconciliation.py`（`close_reconcile_if_due`、`close_text`、`startup_text`）；`patrol.py` 的 `run_round` 最后加「收盘对账」一项，先看停止信号、包在 `_item` 里。验证：`tests/workflows` 全部通过，`test_patrol_thread.py` 用 shell 循环连续跑 20 次都通过；`tests/bootstrap` 仍然全部通过（启动时刻在盘外的测试，比如「盘外 OpenD 没开」，巡检第一轮会做收盘对账：基准数据没有持仓，只多一条 INFO 和一条推送，测试不能依赖它做没做）；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过（workflows 只 import 模块的包根）

## 7. 启动对账的告警和端到端

- [x] 7.1 先写测试（design D10、D11，要开着 Docker）：`tests/bootstrap/test_app.py` 补 bootstrap spec「启动对账」的四个场景：没有不一致时 `FakeGateway` 没有收到告警；来历不明的持仓、不认得的挂单时收到告警，文字含不一致和已暂停开仓，并且日志里告警在「Discord 已连接」之后、「DBOS 已启动」之前；告警发不出去（`FakeGateway` 发送报错）时有错误日志、启动完成。另加端到端：基准数据（空状态库、Alpaca 没有持仓和挂单），启动时刻改为 2026-10-14 20:15Z，用 `start` 和 `ExitStack` 启动，有超时地轮询状态库直到有 2026-10-14 的收盘对账记录（没有差异、有持仓的股票数 0、连续天数 0），检查 `FakeGateway` 收到这条结果，再关闭。验证：确认新测试因为还没接线而失败
- [x] 7.2 实现（design D10）：`bootstrap/app.py` 在「启动 Discord」之后、「启动 DBOS」之前按启动对账的结果推送告警；`bootstrap/startup.py` 的模块说明改为启动对账的告警由 `app.py` 发出。验证：`tests/bootstrap` 全部通过，每个测试结束以后没有留下运行中的 `session-patrol` 线程；`uv run pytest` 全部通过

## 8. 收尾

- [x] 8.1 对照 `specs/close-reconciliation/spec.md`、`specs/bootstrap/spec.md`、`specs/state-store/spec.md`，确认每个场景都有对应的测试，测试名或说明里写明场景。验证：逐条列出场景和测试的对应关系，没有遗漏
- [x] 8.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过
