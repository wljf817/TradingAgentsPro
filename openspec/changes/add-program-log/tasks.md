# Tasks

## 1. 人的决定

- [x] 1.1 人对 proposal「需要人决定」表态以后：同意推荐做法的不改；不同意的先改 spec 和 design（第 1 条改风控检查的说明，第 2 条改「系统的动作」的启动、停止、崩溃恢复和 design D7，第 3 条改 design D11 和 2.3，第 4 条不涉及代码）。验证：`openspec validate add-program-log --strict` 通过，proposal、spec、design、tasks 里说法一致

## 2. 程序日志模块和表

- [x] 2.1 先写测试（design D1、D2、D10）：新建 `tests/fakes/program_log.py`（`InMemoryProgramLogStore`）；新建 `tests/modules/programlog/test_repo.py`，连数据库覆盖 program-log spec「程序日志的条目」「只追加」「写入失败」除「巡检里写条目出错」以外的场景（同时写入用两个线程，重启用同一个数据库新建存储，写入失败用连不上的地址和没升级的空数据库），以及 `latest` 只看给定的动作、没有时为 None；新建 `test_service.py`，用内存存储和测试时钟检查时间来自时钟、各参数原样进入条目；`tests/bootstrap/test_database.py`、`test_app.py` 里表的清单加上 `programlog_entries`，补 state-store spec「表的归属和迁移」三个场景里程序日志表的部分。每个测试的说明写明对应的场景。验证：要开着 Docker（`open -a OrbStack`）；确认新测试因为 `modules/programlog` 不存在而失败
- [x] 2.2 实现（design D1、D2）：`modules/programlog/` 的 `domain/entry.py`、`ports.py`、`repo.py`、`service.py`、`__init__.py`（只导出包根的名字，说明写明以后的模块怎样写）；迁移 `bootstrap/migrations/versions/v0004_create_program_log.py`（写明每一列和索引，不 import 业务代码）。验证：2.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports`、`uv run pytest tests/architecture tests/modules/programlog tests/bootstrap/test_database.py` 通过
- [x] 2.3 `deploy/README.md` 加一节「查看程序日志」：psql 的连法和 design D11 的三条查询。验证：在 2.1 用的测试数据库上写几条条目，三条查询都能执行、结果符合说明

## 3. 通知模块写条目

- [x] 3.1 先写测试（design D8）：`tests/modules/notification/test_service.py` 覆盖 discord-notification spec「告警」「结束确认」修改和新增的场景，以及 program-log spec「系统的动作」里经由通知模块的部分：告警写条目（对象、全文），发不出去时有错误日志、条目写明没有发出去和原因；两种确认请求发出去写请求确认（输出是消息标识、说明有截止时刻，紧急退出另有原因和当前持仓），发不出去写明原因并照旧抛出通知不可用；结束确认写条目（对象是方案 ID、输入是消息标识），没改成时有错误日志、条目写明原因。验证：确认因为 `alert`、`finish_confirmation` 的新参数和构造参数不存在而失败
- [x] 3.2 实现：`NotificationService` 的构造参数加 `ProgramLog`，`alert(subject, text)`、`finish_confirmation(plan_id, message_id, outcome)`；所有调用方按 program-log spec「系统的动作」给出告警的对象和方案 ID（`order_flow.py`、`patrol.py`、`emergency_flow.py`、`health.py`、`reconciliation.py`、`bootstrap/app.py`），各测试的 builder 给通知模块一个程序日志；`integrations/discord/notifier.py` 去掉推送告警、请求确认、确认消息改为三条 INFO（design D4），删掉断言这三条的测试断言。验证：3.1 的测试通过；`tests/modules/notification`、`tests/integrations/discord`、`tests/workflows`、`tests/bootstrap` 原有的测试只改构造参数和这三条断言，全部通过；`uv run mypy`、`uv run lint-imports` 通过

## 4. 流程共用的条目

- [x] 4.1 先写测试 `tests/workflows/test_entries.py`（design D5、D6）：`log_order_change` 只在状态或累计成交变了时写，说明有股票、方向、前后状态、累计成交和委托股数，有成交时有成交均价和累计费用；`order_changes` 写出块里跟进过、有变化的订单，没变化的不写，块里抛错时不写；`release` 在有有效预留时释放并写条目、没有时只释放不写（用内存预留存储）。验证：确认因为 `workflows/entries.py` 不存在而失败
- [x] 4.2 实现 `workflows/entries.py`：上面三个函数，以及方案、风控结果的文字（从 `order_flow.py` 移过来的 `_trade`、`_result` 这类，下单流程和紧急退出共用）。验证：4.1 的测试通过；`uv run mypy`、`uv run ruff check`、`uv run lint-imports` 通过

## 5. 下单流程

- [x] 5.1 先写测试：`tests/workflows/test_order_flow.py`、`test_order_workflow.py` 覆盖 order-flow spec「日志」的四个场景，以及 program-log spec 里经由下单流程的场景（风控检查的通过、调整、拒绝；再校验的通过、拒绝；人工拒绝时释放；已经没有预留的方案被拒绝；提交订单、券商拒绝受理、已经有订单时交给巡检；等开盘；录入方案；确认、拒绝、超时；没有送达的按下不写；告警；请求确认、确认请求发不出去；结束确认、结束确认没改成；等重试只写 Python 日志）。原来断言动作的 `caplog` 改为断言内存存储里的条目，技术问题的 `caplog` 断言不变。验证：确认新断言失败
- [x] 5.2 实现（design D3、D6、D9）：`order_flow.py` 的 `check_plan`、`settle`、`timing`、`_attempt`、`void_expired` 写条目、调用 `entries.release`，去掉 design D4 表里要去掉的日志；`TradeServices` 加 `log`；`order_workflow.py` 的 `OrderPlanIntake.submit` 写录入方案、启动失败时用 `entries.release`。验证：5.1 的测试通过；`tests/workflows` 全部通过；`uv run mypy`、`uv run ruff check` 通过

## 6. 巡检

- [x] 6.1 先写测试 `tests/workflows/test_patrol.py`：session-patrol spec「无保护仓位的告警」「订单和止损单变化的日志」的全部场景，以及 program-log spec 的巡检里写条目出错、订单结束时释放剩下的预留、全部成交时不写释放、巡检看到订单成交、止损单成交、无保护仓位和解除、没有变化的一轮。验证：确认新断言失败
- [x] 6.2 实现（design D3、D5、D6）：`patrol.py` 的 `_track_orders` 用 `entries.log_order_change`，`_log_stop` 加成交、先写再记下，`_alert`、`_forget` 写无保护仓位和解除，`_sync` 用 `entries.release`；`SessionPatrol` 的构造参数加 `log`。验证：6.1 的测试通过；`tests/workflows/test_patrol_thread.py` 用 shell 循环连续跑 20 次都通过

## 7. 紧急退出

- [x] 7.1 先写测试：`tests/workflows/test_emergency_*.py` 覆盖 emergency-exit-flow spec「紧急退出的人工确认」「告警」修改和新增的场景，以及 program-log spec 的紧急退出的清仓方案、紧急退出作废方案时释放、撤单、熔断撤买单、紧急退出的确认、熔断、紧急风险退出、等撤单的轮次；原来断言动作的 `caplog` 改为断言条目。验证：确认新断言失败
- [x] 7.2 实现（design D3、D5、D6、D9）：`emergency_flow.py` 的 `cancel_buys` 写熔断和撤单、撤单包在 `entries.order_changes` 里；`advance` 第一次时在拿锁之前写紧急风险退出；`_advance` 写释放、撤单、风控检查、再校验、提交订单；`_settle` 写确认、拒绝、超时；`report` 用 `entries.release`；去掉 design D4 表里要去掉的日志。验证：7.1 的测试通过；`tests/workflows` 全部通过

## 8. 健康和收盘对账

- [x] 8.1 先写测试：`tests/workflows/test_health.py` 覆盖 health-monitoring spec「状态变化的告警和日志」「恢复后的对账」的全部场景；`tests/workflows/test_reconciliation.py` 覆盖 close-reconciliation spec「结果消息」的全部场景；以及 program-log spec 的巡检里进入降级模式、降级模式里的变化、退出降级模式、告警发不出去、收盘对账。验证：确认新断言失败
- [x] 8.2 实现（design D3、D5、D9）：`health.py` 的 `HealthMonitor` 构造参数加 `log`，`_report` 写三种条目，`_recover` 包 `entries.order_changes`、写对账和退出降级模式；`reconciliation.py` 的 `close_reconcile_if_due` 加 `log`，包 `entries.order_changes`、写对账；去掉 design D4 表里要去掉的日志。验证：8.1 的测试通过；`tests/workflows` 全部通过

## 9. 启动和停止

- [x] 9.1 先写测试：`tests/bootstrap/test_startup.py`、`test_app.py` 覆盖 program-log spec 的正常启动、正常停止、启动失败也写停止、读环境变量失败时不写、上一次运行崩溃、上一次正常停止、启动时处于降级模式、停机期间成交启动对账时写、启动对账，以及 bootstrap spec「日志」的「动作只写程序日志」；启动对账、第一次健康判定原有的 `caplog` 断言不变。验证：要开着 Docker；确认新测试失败
- [x] 9.2 实现（design D7）：`startup.py` 加 `record_startup`，`reconcile_at_startup` 包 `entries.order_changes`、写对账，`judge_health_at_startup` 降级时写进入降级模式；`app.py` 在第 2 步建 `ProgramLog`、写启动、登记写停止，`_assemble` 把它交给通知模块和各流程，`TradingApp` 加 `log`；`logs.py` 只改说明（design D4）。验证：9.1 的测试通过；`uv run pytest` 全部通过，每个测试结束以后没有留下运行中的 `session-patrol` 线程

## 10. 收尾

- [x] 10.1 对照本变更的九份 delta spec，逐条列出每个新增和修改的场景由哪个测试覆盖，没有遗漏；修改的要求里原有的场景仍有测试。验证：对应表写在完成报告里
- [x] 10.2 运行全部检查：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run lint-imports`、`openspec validate --all --strict`。验证：全部通过；`git status` 里没有 `tests/` 下的文件被暂存
